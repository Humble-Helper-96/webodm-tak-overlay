"""
api.py — TAK Incident Overlay (v0.6.0)
All HTTP view functions. Registered as MountPoints in plugin.py.

Endpoints:
    POST  upload/                      Start a new job
    GET   jobs/                        List all jobs (for archive section)
    GET   status/(?P<job_id>[^/]+)/    Poll a specific job's status
    POST  cancel/(?P<job_id>[^/]+)/    Cancel a running job
    GET   download/(?P<job_id>[^/]+>/  Download completed MBTiles file
    POST  delete/(?P<job_id>[^/]+>/    Delete a completed/failed/cancelled job
"""

import io
import os
import logging

from PIL import Image as PilImage

from django.http import JsonResponse, FileResponse, Http404
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt

from . import archive

log = logging.getLogger(__name__)

MAX_PHOTOS         = 75
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

@csrf_exempt
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

    # ── Validate photo list ────────────────────────────────────
    images = request.FILES.getlist('images[]')
    if not images:
        return _err('Please select at least one photo.')
    if len(images) > MAX_PHOTOS:
        return _err(
            f'Maximum {MAX_PHOTOS} photos per job. '
            f'You selected {len(images)}. Please remove some and try again.'
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
        job_id = archive.create_job(incident_name, tz_offset_minutes=tz_offset_minutes)
        images_dir = archive.get_images_dir(job_id)
    except Exception as e:
        log.exception('TAK Overlay: failed to create job record: %s', e)
        return _err('Failed to create job record. Please try again.')

    # ── Validate + save in one pass ────────────────────────────
    # Read each file once via _read_upload_bytes (which uses
    # temporary_file_path() for large files), validate the bytes, and
    # write them to the working directory. If any file fails validation
    # we delete the job (which cleans up any files saved so far) and
    # return the operator-facing error.
    saved_paths = []
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

            dest = os.path.join(images_dir, img.name)
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
    try:
        from . import pipeline
        pipeline.start(job_id, saved_paths)
        log.info(
            'TAK Overlay: pipeline started for job %s',
            job_id,
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
            # Task.DoesNotExist is the common case after cleanup;
            # AttributeError covers WebODM API drift on running_progress.
            log.debug('TAK Overlay: status_view could not read task progress: %s', e)

    return _ok(
        job_id=          job['job_id'],
        status=          job['status'],
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

@csrf_exempt
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
    Strip characters that could break or inject into the
    Content-Disposition header. Whitelist alphanumerics, dot, dash,
    underscore, and space.
    """
    safe = ''.join(c for c in name if c.isalnum() or c in '._- ')
    return safe or 'overlay.mbtiles'


@login_required
def download_view(request, job_id):
    """
    GET /plugins/tak_incident_overlay/download/<job_id>/

    Streams the completed MBTiles file as a download attachment.
    The filename in the Content-Disposition header is the sanitized display name.
    """
    job = archive.get_job(job_id)
    if job is None:
        raise Http404('Job not found.')
    if job['status'] != 'completed':
        return _err('Job is not completed yet.', 400)

    mbtiles_path = archive.get_mbtiles_path(job)
    if not os.path.exists(mbtiles_path):
        return _err(
            'Output file not found. It may have been automatically purged after 72 hours.',
            404
        )

    safe_name = _safe_filename(job.get('filename') or 'overlay.mbtiles')
    response = FileResponse(
        open(mbtiles_path, 'rb'),
        content_type='application/octet-stream',
    )
    response['Content-Disposition'] = f'attachment; filename="{safe_name}"'
    log.info('TAK Overlay: serving download for job %s (%s)', job_id, safe_name)
    return response


# ── Delete ─────────────────────────────────────────────────────────────────────

@csrf_exempt
@login_required
def delete_view(request, job_id):
    """
    POST /plugins/tak_incident_overlay/delete/<job_id>/

    Deletes a completed, failed, or cancelled job and its MBTiles file.
    Running jobs must be cancelled first.

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
