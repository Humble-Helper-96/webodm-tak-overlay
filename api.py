"""
api.py — TAK Incident Overlay (v0.8.0)
All HTTP view functions. Registered as MountPoints in plugin.py.

Endpoints:
    POST  upload/                              Start a new job
    GET   jobs/                                List all jobs (for archive section)
    GET   status/(?P<job_id>[^/]+)/            Poll a specific job's status
    POST  cancel/(?P<job_id>[^/]+)/            Cancel a running job
    GET   download-geotiff/(?P<job_id>[^/]+)/  Download completed GeoTIFF file
    POST  delete/(?P<job_id>[^/]+)/            Delete a completed/failed/cancelled job
    GET   node-status/                         Processing node online/offline status (v0.7.2)
"""

import io
import os
import logging

from PIL import Image as PilImage

from django.http import JsonResponse, FileResponse, Http404
from django.contrib.auth.decorators import login_required

from . import archive

log = logging.getLogger(__name__)

MAX_PHOTOS         = 150   # v0.7.5: raised from 100
MAX_PHOTOS_HIGH    = 300   # high-capacity mode (single-toggle, v0.7.5)
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg'}

# JPEG magic bytes (SOI marker)
JPEG_MAGIC = b'\xff\xd8\xff'

# EXIF GPS IFD tag
EXIF_GPS_IFD = 34853

# WebODM task status codes (app/models/task.py)
STATUS_QUEUED    = 10
STATUS_RUNNING   = 20
STATUS_FAILED    = 30
STATUS_COMPLETED = 40
STATUS_CANCELLED = 50

# WebODM pending action codes
PENDING_CANCEL = 1


# ── Response helpers ───────────────────────────────────────────────────────────

def _ok(**kwargs):
    return JsonResponse({'ok': True, **kwargs})


def _err(message, status=400):
    return JsonResponse({'ok': False, 'error': message}, status=status)


# ── Upload byte reader ─────────────────────────────────────────────────────────

def _read_upload_bytes(img):
    """
    Read the full contents of a Django UploadedFile, bypassing its
    read-state machine.

    Background: in batches of 70+ files >2.5 MB, Django's multipart
    upload handlers close some TemporaryUploadedFile handles before the
    view runs. Calling img.seek() / img.read() / img.chunks() then
    raises "seek of closed file".

    Workaround:
      * For TemporaryUploadedFile (files written to /tmp by the upload
        handler — anything > FILE_UPLOAD_MAX_MEMORY_SIZE, default 2.5 MB):
        open the temp file path directly with stdlib open(). The file
        on disk is intact regardless of the UploadedFile handle state.
      * For InMemoryUploadedFile (small files kept in memory): the
        handle is fine; use the normal API.
    """
    if hasattr(img, 'temporary_file_path'):
        with open(img.temporary_file_path(), 'rb') as f:
            return f.read()
    # InMemoryUploadedFile fallback
    img.seek(0)
    return img.read()


# ── Per-file validation ────────────────────────────────────────────────────────

def _validate_image_bytes(name, data):
    """
    Validate JPEG bytes already read into memory.

    Returns (ok: bool, error_message: str | None).
    """
    if len(data) == 0:
        return False, f'"{name}" is empty.'

    # JPEG magic bytes
    if not data.startswith(JPEG_MAGIC):
        return False, f'"{name}" does not appear to be a valid JPEG file.'

    # GPS EXIF check
    try:
        with PilImage.open(io.BytesIO(data)) as pil_img:
            exif = pil_img.getexif()
            if EXIF_GPS_IFD not in exif:
                return False, (
                    f'"{name}" is missing GPS data. '
                    f'Drone photos must have GPS for georeferencing.'
                )
    except Exception:
        return False, f'"{name}" could not be read as an image.'

    return True, None


# ── Upload ─────────────────────────────────────────────────────────────────────

@login_required
def upload_view(request):
    """
    POST /plugins/tak_incident_overlay/upload/

    Form fields:
        incident_name   str     required — incident name or number
        images[]        files   required — JPEG drone photos with GPS EXIF

    Returns JSON:
        {"ok": true,  "job_id": "<uuid>"}
        {"ok": false, "error": "<operator-friendly message>"}
    """
    if request.method != 'POST':
        return _err('POST required.', 405)

    # ── Validate incident name ─────────────────────────────────
    incident_name = request.POST.get('incident_name', '').strip()
    if not incident_name:
        return _err('Please enter an incident name or number before processing.')

    # ── Parse browser timezone offset ──────────────────────────
    # Browser sends tz_offset = JS Date.getTimezoneOffset() which is
    # minutes WEST of UTC (positive = behind UTC, negative = ahead).
    # e.g. AKDT (UTC-8) -> 480,  EST (UTC-5) -> 300,  CET (UTC+1) -> -60
    # We flip the sign to get minutes-east for datetime arithmetic.
    # Falls back to 0 (UTC) if missing or invalid.
    try:
        tz_offset_minutes = -int(request.POST.get('tz_offset', 0))
    except (ValueError, TypeError):
        tz_offset_minutes = 0

    # ── Parse high-capacity flag (v0.7.5) ──────────────────────
    # Operator enables a single toggle in the UI to raise the limit
    # from MAX_PHOTOS (150) to MAX_PHOTOS_HIGH (300). Backend re-validates
    # regardless of frontend state.
    high_capacity = request.POST.get('high_capacity', 'false').lower() == 'true'
    effective_limit = MAX_PHOTOS_HIGH if high_capacity else MAX_PHOTOS

    # ── Parse retain_task flag (v0.7.6) ────────────────────────
    # When enabled, the WebODM project is not auto-deleted after the
    # pipeline completes. It remains accessible in WebODM for up to
    # 72 hours until purge_expired_jobs() cleans it alongside the job.
    retain_task = request.POST.get('retain_task', 'false').lower() == 'true'

    # ── Parse quality_mode flag (v0.7.8 — repurposed) ──────────
    # UI label is "High-Resolution mode". When enabled, runs the high-res
    # variant: image resize raised to 4000 px and orthophoto-resolution
    # pinned to 2.5 cm/px. Runtime ~3× standard on reference hardware.
    quality_mode = request.POST.get('quality_mode', 'false').lower() == 'true'

    # ── Parse terrain_correction flag (v0.7.8) ─────────────────
    # When enabled, fast-orthophoto is omitted and ODM runs the full SfM
    # pipeline (dense MVS, mesh, textured orthorectification). Corrects for
    # varied terrain and tall vertical features at ~12× runtime cost on
    # reference hardware.
    terrain_correction = request.POST.get('terrain_correction', 'false').lower() == 'true'

    # ── Parse client_resized flag (v0.8.2) ─────────────────────
    # When the browser has already resized photos to the target size,
    # the server skips its own resize step. The server verifies each
    # photo's dimensions and falls back to server-side resize if any
    # photo exceeds the target.
    client_resized = request.POST.get('client_resized', 'false').lower() == 'true'

    # ── Validate photo list ────────────────────────────────────
    images = request.FILES.getlist('images[]')
    if not images:
        return _err('Please select at least one photo.')
    if len(images) > effective_limit:
        if high_capacity:
            return _err(
                f'High-capacity mode allows up to {MAX_PHOTOS_HIGH} photos per job. '
                f'You selected {len(images)}. Please remove some and try again.'
            )
        return _err(
            f'Maximum {MAX_PHOTOS} photos per job. '
            f'You selected {len(images)}. Please remove some and try again, '
            f'or enable high-capacity mode (up to {MAX_PHOTOS_HIGH}).'
        )

    if high_capacity:
        log.info(
            'TAK Overlay: high-capacity mode ENABLED for upload — %d photos, incident="%s"',
            len(images), incident_name,
        )

    # ── Cheap pre-check: extensions only ───────────────────────
    # Reject obvious wrong-type uploads before creating any job state.
    for img in images:
        ext = os.path.splitext(img.name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return _err(
                f'Only JPG/JPEG photos are supported. '
                f'"{img.name}" is not a JPEG. Please remove other file types.'
            )

    # ── Create job record ──────────────────────────────────────
    try:
        job_id = archive.create_job(incident_name, tz_offset_minutes=tz_offset_minutes,
                                    retain_task=retain_task, quality_mode=quality_mode,
                                    terrain_correction=terrain_correction)
        images_dir = archive.get_images_dir(job_id)
    except Exception as e:
        log.exception('TAK Overlay: failed to create job record: %s', e)
        return _err('Failed to create job record. Please try again.')

    # ── Determine resize target (v0.8.2) ──────────────────────
    # The browser sends client_resized=true when it has already resized
    # photos to the target. The server verifies dimensions and falls back
    # to server-side resize if any photo exceeds the target.
    resize_target = 4000 if quality_mode else 2048

    # ── Validate + save in one pass ────────────────────────────
    # Read each file once via _read_upload_bytes (which uses
    # temporary_file_path() for large files), validate the bytes, and
    # write them to the working directory. If any file fails validation
    # we delete the job (which cleans up any files saved so far) and
    # return the operator-facing error.
    saved_paths = []
    all_within_target = True
    try:
        for idx, img in enumerate(images, start=1):
            try:
                data = _read_upload_bytes(img)
            except Exception as e:
                log.warning(
                    'TAK Overlay: read failed for "%s" (file %d/%d) in job %s: %s',
                    img.name, idx, len(images), job_id, e,
                )
                archive.delete_job(job_id)
                return _err(
                    f'"{img.name}" could not be read from upload. '
                    f'Please try the upload again.'
                )

            ok, err_msg = _validate_image_bytes(img.name, data)
            if not ok:
                archive.delete_job(job_id)
                return _err(err_msg)

            # v0.8.2: Check if photo is within the target size.
            # If client_resized=true but a photo exceeds the target,
            # the server will resize it (fallback).
            if client_resized:
                try:
                    with PilImage.open(io.BytesIO(data)) as pil_img:
                        w, h = pil_img.size
                        longest = max(w, h)
                        if longest > resize_target:
                            all_within_target = False
                            log.info(
                                'TAK Overlay: photo "%s" is %dx%d (longest %d > target %d) — server will resize',
                                img.name, w, h, longest, resize_target,
                            )
                except Exception:
                    all_within_target = False

            # Prefix with the loop index (v0.7.13): merging photos from two
            # SD cards routinely produces duplicate names (DJI_0001.JPG twice),
            # and os.path.join with the raw name silently overwrites — the job
            # then processes fewer images than the operator selected, with no
            # error. ODM doesn't care about filenames; GPS EXIF is what matters.
            dest = os.path.join(images_dir, '{:04d}_{}'.format(idx, img.name))
            with open(dest, 'wb') as f:
                f.write(data)
            saved_paths.append(dest)

        log.info('TAK Overlay: saved %d images for job %s', len(saved_paths), job_id)
    except Exception as e:
        # Catch-all for unexpected errors during the validate-and-save loop
        # (disk full, permission denied, etc.). Mark job failed and clean up.
        log.exception('TAK Overlay: unexpected error saving images for job %s', job_id)
        archive.mark_failed(job_id, f'Failed to save uploaded images: {e}')
        archive.cleanup_working_dir(job_id)
        return _err('Upload failed while saving files. Please try again.')

    # ── Kick off async pipeline ────────────────────────────────
    # v0.8.2: If the browser resized photos and all are within the target,
    # tell the pipeline to skip server-side resize (resize_to=-1).
    try:
        from . import pipeline
        pipeline.start(job_id, saved_paths, retain_task=retain_task,
                       quality_mode=quality_mode,
                       terrain_correction=terrain_correction,
                       client_resized=(client_resized and all_within_target),
                       submitting_username=request.user.username)
        log.info(
            'TAK Overlay: pipeline started for job %s (client_resized=%s)',
            job_id, client_resized and all_within_target,
        )
    except Exception as e:
        log.exception('TAK Overlay: failed to start pipeline for job %s', job_id)
        archive.mark_failed(job_id, f'Failed to start pipeline: {e}')
        archive.cleanup_working_dir(job_id)
        return _err('Processing failed to start. Please try again.')

    return _ok(job_id=job_id)


# ── Job list ───────────────────────────────────────────────────────────────────

@login_required
def jobs_view(request):
    """
    GET /plugins/tak_incident_overlay/jobs/

    Returns all jobs (newest first) for the archive section.
    Also triggers 72-hour auto-purge on each call.

    Returns JSON:
        {"ok": true, "jobs": [ <job record>, ... ]}
    """
    try:
        archive.purge_expired_jobs()
    except Exception as e:
        # Purge failure shouldn't prevent the archive from rendering
        log.warning('TAK Overlay: purge_expired_jobs failed: %s', e)

    jobs = archive.get_all_jobs()
    return _ok(jobs=jobs)


# ── Status ─────────────────────────────────────────────────────────────────────

@login_required
def status_view(request, job_id):
    """
    GET /plugins/tak_incident_overlay/status/<job_id>/

    Returns current state of a job. Frontend polls this while a job is running.
    Fetches live progress from the WebODM task if one is assigned.

    Returns JSON:
        {
          "ok": true,
          "job_id":          str,
          "status":          "running"|"completed"|"failed"|"cancelled",
          "phase":           str (current pipeline phase, v0.7.2+),
          "display_name":    str,
          "webodm_progress": float (0.0–1.0) | null,
          "webodm_stage":    str | null,
          "file_size_bytes": int | null,
          "error":           str | null
        }
    """
    job = archive.get_job(job_id)
    if job is None:
        return _err('Job not found.', 404)

    # Fetch live progress from WebODM if the task has been created.
    # The Task object can disappear mid-poll (pipeline.py deletes the
    # parent Project in its finally block), so handle that quietly.
    webodm_progress = None
    webodm_stage    = None

    if job['status'] == 'running' and job.get('webodm_task_id'):
        try:
            from app.models import Task
            task = Task.objects.get(pk=job['webodm_task_id'])
            webodm_progress = float(task.running_progress or 0)
            webodm_stage    = _stage_label(task.status, webodm_progress)
        except Exception as e:
            log.debug('TAK Overlay: status_view could not read task progress: %s', e)

    return _ok(
        job_id=          job['job_id'],
        status=          job['status'],
        phase=           job.get('phase', ''),
        display_name=    job['display_name'],
        webodm_progress= webodm_progress,
        webodm_stage=    webodm_stage,
        file_size_bytes= job.get('file_size_bytes'),
        error=           job.get('error'),
    )


def _stage_label(status_code, progress):
    """Human-readable processing stage for the progress bar."""
    if status_code == STATUS_QUEUED:
        return 'Waiting in queue'
    if status_code == STATUS_RUNNING:
        if progress < 0.10: return 'Starting up'
        if progress < 0.30: return 'Feature extraction'
        if progress < 0.50: return 'Matching features'
        if progress < 0.70: return 'Densification'
        if progress < 0.88: return 'Building orthophoto'
        return 'Finishing up'
    return ''


# ── Cancel ─────────────────────────────────────────────────────────────────────

@login_required
def cancel_view(request, job_id):
    """
    POST /plugins/tak_incident_overlay/cancel/<job_id>/

    Cancels a running job. Signals the WebODM task to cancel and
    cleans up the working directory.

    Returns JSON:
        {"ok": true,  "job_id": "<uuid>"}
        {"ok": false, "error": "<message>"}
    """
    if request.method != 'POST':
        return _err('POST required.', 405)

    job = archive.get_job(job_id)
    if job is None:
        return _err('Job not found.', 404)
    if job['status'] != 'running':
        return _err('Job is not currently running.')

    # Signal WebODM to cancel the task if one was created.
    # If the task object is already gone (race with pipeline cleanup),
    # log and proceed — the archive cancellation below is what matters.
    if job.get('webodm_task_id'):
        try:
            from app.models import Task
            task = Task.objects.get(pk=job['webodm_task_id'])
            task.pending_action = PENDING_CANCEL
            task.save()
            log.info('TAK Overlay: sent cancel to WebODM task %s', job['webodm_task_id'])
        except Exception as e:
            log.warning('TAK Overlay: could not cancel WebODM task: %s', e)

    archive.mark_cancelled(job_id)
    archive.cleanup_working_dir(job_id)
    log.info('TAK Overlay: job %s cancelled by user', job_id)

    return _ok(job_id=job_id)


# ── Download ───────────────────────────────────────────────────────────────────

def _safe_filename(name):
    """
    Make a stored filename safe for the Content-Disposition header.
    Replaces spaces with underscores and strips anything outside
    [A-Za-z0-9._-]. Mirrors archive._sanitize_filename(), kept here as
    defense-in-depth so legacy records that somehow stored a space-bearing
    filename still serve with underscores.
    """
    name = name.replace(' ', '_')
    safe = ''.join(c for c in name if c.isalnum() or c in '._-')
    return safe or 'overlay.tif'


# ── GeoTIFF download ───────────────────────────────────────────────────────────

@login_required
def download_geotiff_view(request, job_id):
    """
    GET /plugins/tak_incident_overlay/download-geotiff/<job_id>/

    Streams the completed GeoTIFF file as a download attachment.
    The GeoTIFF is a 3-band RGB WGS84 raster with a 1-bit internal alpha
    mask, JPEG-compressed at quality 85 (v0.7.9+). Earlier v0.7.x jobs
    used 4-band RGBA LZW; the on-disk format differs but the download
    path is the same.

    Jobs created in v0.6 and earlier do not have a GeoTIFF — those return
    404 with a clear message.
    """
    job = archive.get_job(job_id)
    if job is None:
        raise Http404('Job not found.')
    if job['status'] != 'completed':
        return _err('Job is not completed yet.', 400)

    # v0.6 jobs in the index don't have geotiff_filename — return clean 404
    if not job.get('geotiff_filename'):
        return _err(
            'No GeoTIFF available for this job. '
            'Only jobs processed by plugin v0.7+ produce a GeoTIFF.',
            404,
        )

    geotiff_path = archive.get_geotiff_path(job)
    if not os.path.exists(geotiff_path):
        return _err(
            'Output file not found. It may have been automatically purged after 72 hours.',
            404,
        )

    safe_name = _safe_filename(job.get('geotiff_filename') or 'overlay.tif')
    response = FileResponse(
        open(geotiff_path, 'rb'),
        content_type='image/tiff',
    )
    response['Content-Disposition'] = f'attachment; filename="{safe_name}"'
    log.info('TAK Overlay: serving GeoTIFF download for job %s (%s)', job_id, safe_name)
    return response


# ── Settings (v0.8.1) ─────────────────────────────────────────────────────────

@login_required
def settings_view(request):
    """
    GET  /plugins/tak_incident_overlay/settings/  — read settings
    POST /plugins/tak_incident_overlay/settings/  — save settings

    GET returns:
        {
          "ok": true,
          "global": { "retention_hours": 72, "thread_percent": 50 },
          "user":   { "units": "metric", "time_format": "24h", ... },
          "is_staff": false
        }

    POST accepts:
        { "units": "metric", "time_format": "24h", "highres_default": false,
          "save_task_default": false,
          "global": { "retention_hours": 72, "thread_percent": 50 } }

    The "global" block is only applied if the user is staff.
    """
    if request.method == 'GET':
        username = request.user.username
        user_settings = archive.get_user_settings(username)
        global_settings = archive.get_settings().get('global', {})
        return _ok(
            **{'global': global_settings},
            user=user_settings,
            is_staff=request.user.is_staff,
        )

    if request.method == 'POST':
        try:
            import json
            body = json.loads(request.body)
        except (json.JSONDecodeError, TypeError):
            return _err('Invalid JSON body.')

        username = request.user.username

        # Per-user settings
        user_keys = {'units', 'time_format', 'highres_default', 'save_task_default'}
        user_updates = {}
        for key in user_keys:
            if key in body:
                user_updates[key] = body[key]

        # Validate units
        if 'units' in user_updates and user_updates['units'] not in ('metric', 'imperial'):
            return _err('Units must be "metric" or "imperial".')
        # Validate time_format
        if 'time_format' in user_updates and user_updates['time_format'] not in ('24h', '12h'):
            return _err('Time format must be "24h" or "12h".')

        if user_updates:
            # Merge with existing
            existing = archive.get_user_settings(username)
            existing.update(user_updates)
            archive.save_user_settings(username, existing)

        # Global settings — staff only
        if 'global' in body and isinstance(body['global'], dict):
            if not request.user.is_staff:
                return _err('Only staff users can change system settings.', 403)
            global_updates = {}
            if 'retention_hours' in body['global']:
                try:
                    rh = int(body['global']['retention_hours'])
                    if rh not in (24, 48, 72, 168, 720):
                        return _err('Retention must be 24, 48, 72, 168, or 720 hours.')
                    global_updates['retention_hours'] = rh
                except (ValueError, TypeError):
                    return _err('Invalid retention_hours value.')
            if 'thread_percent' in body['global']:
                try:
                    tp = int(body['global']['thread_percent'])
                    if tp not in (25, 50, 75):
                        return _err('Thread percent must be 25, 50, or 75.')
                    global_updates['thread_percent'] = tp
                except (ValueError, TypeError):
                    return _err('Invalid thread_percent value.')
            if global_updates:
                archive.save_global_settings(global_updates)

        # Return updated state
        user_settings = archive.get_user_settings(username)
        global_settings = archive.get_settings().get('global', {})
        return _ok(
            **{'global': global_settings},
            user=user_settings,
            is_staff=request.user.is_staff,
        )

    return _err('GET or POST required.', 405)


# ── Node status (v0.7.2) ───────────────────────────────────────────────────────

@login_required
def node_status_view(request):
    """
    GET /plugins/tak_incident_overlay/node-status/

    Returns the online/offline state of the primary processing node via a
    direct HTTP health probe to the node's /info endpoint. This bypasses
    WebODM's cached ProcessingNode heartbeat (which has a ~2-minute timeout
    in WebODM 3.2.2), giving ~2-5 second detection lag instead.

    Probe: GET http://<node.hostname>:<node.port>/info  (2-second timeout)
    Online if the probe returns HTTP 200; offline for any other response or
    connection error.

    Hostname and port are read dynamically from the first ProcessingNode
    record in the database — no hardcoded values.

    Returns JSON:
        {"ok": true, "online": true,  "name": "node-odx-1"}
        {"ok": true, "online": false, "name": "node-odx-1"}
        {"ok": true, "online": false, "name": "No node configured"}
    """
    try:
        import requests as _requests
        from nodeodm.models import ProcessingNode

        node = ProcessingNode.objects.order_by('id').first()
        if node is None:
            return _ok(online=False, name='No node configured')

        url = 'http://{}:{}/info'.format(node.hostname, node.port)
        try:
            resp = _requests.get(url, timeout=2)
            online = resp.status_code == 200
        except Exception:
            online = False

        log.debug('TAK Overlay: node probe %s -> online=%s', url, online)
        return _ok(online=online, name=node.hostname)

    except Exception as e:
        log.warning('TAK Overlay: node_status_view error: %s', e)
        return _ok(online=False, name='Unknown')


# ── Delete ─────────────────────────────────────────────────────────────────────

@login_required
def delete_view(request, job_id):
    """
    POST /plugins/tak_incident_overlay/delete/<job_id>/

    Deletes a completed, failed, or cancelled job and all its output files
    (GeoTIFF, plus legacy MBTiles from pre-v0.7.8 jobs). Running jobs must
    be cancelled first.

    Returns JSON:
        {"ok": true,  "job_id": "<uuid>"}
        {"ok": false, "error": "<message>"}
    """
    if request.method != 'POST':
        return _err('POST required.', 405)

    job = archive.get_job(job_id)
    if job is None:
        return _err('Job not found.', 404)
    if job['status'] == 'running':
        return _err('Cannot delete a running job. Cancel it first.')

    archive.delete_job(job_id)
    log.info('TAK Overlay: job %s deleted by user', job_id)
    return _ok(job_id=job_id)
