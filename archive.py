"""
archive.py — TAK Incident Overlay (v0.8.4)
Job index management, archive directory, and 72-hour auto-purge.

Directory layout (all under settings.MEDIA_ROOT):
    tak_incident_overlay/
    ├── index.json                        ← job records
    ├── working/<job_id>/                 ← temp space during processing
    │   ├── images/                       ← uploaded photos
    │   └── wgs84.tif                     ← GDAL intermediate (deleted on cleanup)
    ├── <sanitized_display_name>.tif      ← final RGB GeoTIFF deliverable (one per job)
    └── <sanitized_display_name>.mbtiles  ← legacy MBTiles from pre-v0.7.8 jobs (not produced
                                            by current pipeline; still cleaned up on purge)

Job record schema:
    {
        "job_id":              str (UUID4),
        "incident_name":       str (operator input),
        "display_name":        str ("{incident_name} YYYY-MM-DD HHMM"),
        "filename":            str ("{display_name}_{job_id[:8]}.mbtiles", legacy field
                                    retained for backward-compat cleanup of pre-v0.7.8 files),
        "geotiff_filename":    str ("{display_name}_{job_id[:8]}.tif",  filesystem-safe;
                                    UUID suffix added v0.7.13 to prevent same-minute
                                    same-name collisions),  # v0.7+
        "status":              "running" | "completed" | "failed" | "cancelled",
        "phase":               str (current processing phase label, v0.7.2+),
        "created_at":          str (ISO 8601, UTC),
        "completed_at":        str | null,
        "webodm_task_id":      str | null,
        "webodm_project_id":   int | null,   # retained only when retain_task=True (v0.7.6+)
        "retain_task":         bool,         # if True, WebODM project is not auto-deleted (v0.7.6+)
        "quality_mode":        bool,         # if True, high-resolution run (4000px resize +
                                             # 2.5 cm/px orthophoto-resolution); UI label
                                             # is "High-Resolution mode" (v0.7.8 — repurposed)
        "terrain_correction":  bool,         # if True, fast-orthophoto is disabled and the
                                             # full SfM pipeline runs (dense MVS + mesh +
                                             # textured orthorectification). Corrects for
                                             # varied terrain and tall vertical features.
                                             # Reference hardware (M920q i5-8500), 65-photo
                                             # job at 3 threads: ~35 min vs ~3 min default.
                                             # (v0.7.8+)
        "file_size_bytes":     int | null,   # legacy MBTiles size; always null for v0.7.8+ jobs
        "geotiff_size_bytes":  int | null,   # RGB GeoTIFF size (v0.7+)
        "gsd_cm_per_px":       float | null, # ground sample distance, metric (v0.8.4+)
        "area_m2":             float | null, # covered area, metric (v0.8.4+)
        "error":               str | null
    }
"""

import os
import json
import uuid
import shutil
import fcntl
import logging
from datetime import datetime, timezone, timedelta

from django.conf import settings

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

ARCHIVE_SUBDIR  = 'tak_incident_overlay'
WORKING_SUBDIR  = 'working'
INDEX_FILENAME  = 'index.json'
SETTINGS_FILENAME = 'settings.json'
PURGE_HOURS     = 72  # default retention, overridden by settings.json

# Client-side resize targets (v0.8.2, Workstream A). The single source of
# truth for the longest-side pixel target in both modes — plugin.py reads
# these into the template context for the browser's resize worker, api.py
# uses them to verify uploaded photo dimensions, and pipeline.py uses them
# to decide WebODM's own resize_to. Previously each of those three modules
# re-hardcoded the literals 2048/4000 independently; they agreed only by
# coincidence, and changing one wouldn't have changed the others.
RESIZE_TARGET_STANDARD = 2048
RESIZE_TARGET_HIGH_RES = 4000

# Default settings
DEFAULT_SETTINGS = {
    'global': {
        'retention_hours': 72,    # auto-purge after N hours (24/48/72/168/720)
        'thread_percent': 50,     # percentage of node CPU threads for ODM
    },
    'users': {}  # keyed by username: {units, time_format, highres_default, save_task_default}
}


# ── Directory helpers ──────────────────────────────────────────────────────────

def get_archive_dir():
    """
    Return the plugin's archive directory path, creating it if needed.
    Always points to <MEDIA_ROOT>/tak_incident_overlay/.
    """
    path = os.path.join(settings.MEDIA_ROOT, ARCHIVE_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def get_working_dir(job_id):
    """
    Return the temp working directory for a job, creating it if needed.
    Caller is responsible for cleaning this up after the job finishes.
    """
    path = os.path.join(get_archive_dir(), WORKING_SUBDIR, job_id)
    os.makedirs(path, exist_ok=True)
    return path


def get_images_dir(job_id):
    """Return the subdirectory inside the working dir where uploaded photos go."""
    path = os.path.join(get_working_dir(job_id), 'images')
    os.makedirs(path, exist_ok=True)
    return path


def get_mbtiles_path(job):
    """
    Return the full path where a legacy MBTiles file *would* live for this job.

    As of v0.7.8 the pipeline no longer produces MBTiles, but this helper is
    retained so delete_job() and purge_expired_jobs() can still clean up
    .mbtiles files left on disk by pre-v0.7.8 jobs. For new jobs the path
    returned by this function will not exist on disk.
    """
    return os.path.join(get_archive_dir(), job['filename'])


def get_geotiff_path(job):
    """
    Return the full path to the final RGB GeoTIFF file for a completed job.
    Returns None if this job pre-dates v0.7 and has no geotiff_filename.
    """
    name = job.get('geotiff_filename')
    if not name:
        return None
    return os.path.join(get_archive_dir(), name)


def cleanup_working_dir(job_id):
    """Delete the working directory for a job. Safe to call if it doesn't exist."""
    path = os.path.join(get_archive_dir(), WORKING_SUBDIR, job_id)
    if os.path.exists(path):
        shutil.rmtree(path)
        log.info('TAK Overlay: cleaned up working dir for job %s', job_id)


# ── Index file helpers ─────────────────────────────────────────────────────────

def _index_path():
    return os.path.join(get_archive_dir(), INDEX_FILENAME)


def _ensure_index():
    """Create an empty index file if it doesn't exist yet."""
    path = _index_path()
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump([], f)
    return path


def _read_index(f):
    f.seek(0)
    content = f.read().strip()
    if not content:
        return []
    return json.loads(content)


def _write_index(f, jobs):
    f.seek(0)
    f.truncate()
    json.dump(jobs, f, indent=2, default=str)
    f.flush()
    os.fsync(f.fileno())


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Settings file helpers (v0.8.1) ─────────────────────────────────────────────

def _settings_path():
    return os.path.join(get_archive_dir(), SETTINGS_FILENAME)


def _ensure_settings():
    """Create a default settings file if it doesn't exist yet."""
    path = _settings_path()
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)
    return path


def _read_settings(f):
    f.seek(0)
    content = f.read().strip()
    if not content:
        return dict(DEFAULT_SETTINGS)
    data = json.loads(content)
    # Merge with defaults to ensure all keys exist
    merged = dict(DEFAULT_SETTINGS)
    for key in merged:
        if key in data:
            if isinstance(merged[key], dict):
                merged[key].update(data[key])
            else:
                merged[key] = data[key]
    return merged


def _write_settings(f, settings_data):
    f.seek(0)
    f.truncate()
    json.dump(settings_data, f, indent=2, default=str)
    f.flush()
    os.fsync(f.fileno())


def get_settings():
    """
    Read the full settings structure. Returns the merged settings dict
    with 'global' and 'users' blocks.
    """
    path = _ensure_settings()
    with open(path, 'r') as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return _read_settings(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def get_user_settings(username):
    """
    Returns the settings for a specific user, merged with defaults.
    """
    all_settings = get_settings()
    user_defaults = {
        'units': 'metric',
        'time_format': '24h',
        'highres_default': False,
        'save_task_default': False,
    }
    user_data = all_settings.get('users', {}).get(username, {})
    user_defaults.update(user_data)
    return user_defaults


def save_user_settings(username, user_settings):
    """
    Save per-user settings. Only updates the user's block; global settings
    are untouched.
    """
    path = _ensure_settings()
    with open(path, 'r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            settings_data = _read_settings(f)
            if 'users' not in settings_data:
                settings_data['users'] = {}
            settings_data['users'][username] = user_settings
            _write_settings(f, settings_data)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    log.info('TAK Overlay: saved user settings for %s', username)


def save_global_settings(global_settings):
    """
    Save global (system-wide) settings. Only staff users may call this.
    """
    path = _ensure_settings()
    with open(path, 'r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            settings_data = _read_settings(f)
            settings_data['global'].update(global_settings)
            _write_settings(f, settings_data)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    log.info('TAK Overlay: saved global settings: %s', global_settings)


def get_retention_hours():
    """
    Return the configured retention hours from settings.
    Falls back to PURGE_HOURS (72) if settings can't be read.
    """
    try:
        s = get_settings()
        return s.get('global', {}).get('retention_hours', PURGE_HOURS)
    except Exception:
        return PURGE_HOURS


def get_thread_percent():
    """
    Return the configured thread percentage from settings.
    Falls back to 50 if settings can't be read.
    """
    try:
        s = get_settings()
        return s.get('global', {}).get('thread_percent', 50)
    except Exception:
        return 50


def _dir_size_bytes(path):
    """Total size in bytes of every file under path, recursively. 0 if path doesn't exist."""
    total = 0
    if not os.path.isdir(path):
        return 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def get_disk_usage():
    """
    Current disk use of the GeoTIFF archive and of retained WebODM tasks
    (roadmap §9.2: "Show the current disk use of the archive and saved
    tasks beside the [retention] setting" — a GeoTIFF is ~6-10 MB but a
    saved WebODM task is ~630 MB, so retention length matters a lot more
    when operators leave "Save WebODM task" on).

    Returns {"archive_bytes": int, "saved_tasks_bytes": int, "saved_tasks_count": int}.
    archive_bytes covers every file directly in the archive dir (GeoTIFFs,
    legacy MBTiles, sidecars, index/settings — all small next to the
    GeoTIFFs, not worth excluding). saved_tasks_bytes sums the WebODM
    project directory (MEDIA_ROOT/project/<id>/) for every job that still
    has retain_task=True and a webodm_project_id on record — these live
    outside the plugin's own archive dir entirely.
    """
    archive_bytes = _dir_size_bytes(get_archive_dir())

    saved_tasks_bytes = 0
    saved_tasks_count = 0
    try:
        for job in get_all_jobs():
            if job.get('retain_task') and job.get('webodm_project_id'):
                project_dir = os.path.join(
                    settings.MEDIA_ROOT, 'project', str(job['webodm_project_id'])
                )
                size = _dir_size_bytes(project_dir)
                if size:
                    saved_tasks_bytes += size
                    saved_tasks_count += 1
    except Exception as e:
        log.warning('TAK Overlay: get_disk_usage could not total saved tasks: %s', e)

    return {
        'archive_bytes': archive_bytes,
        'saved_tasks_bytes': saved_tasks_bytes,
        'saved_tasks_count': saved_tasks_count,
    }


# ── Photo sidecar file (v0.8.3) ─────────────────────────────────────────────

def get_photos_sidecar_path(job_id):
    """Return the path to the photo sidecar file for a job."""
    return os.path.join(get_archive_dir(), '{}_photos.json'.format(job_id))


def save_photos_sidecar(job_id, points):
    """
    Save the photo point list to a sidecar file next to the output.
    The sidecar file is <archive_dir>/<job_id>_photos.json.
    Points is a list of {name, lat, lon, time, used} dicts.

    Locked the same way as index.json/settings.json (fcntl.flock) — there is
    normally one writer per job-lifecycle stage (upload, then pipeline's
    used-flag update at completion), but the lock still protects against a
    concurrent read (status_view polling) seeing a half-written file, and
    against two stages racing if that assumption is ever wrong.
    """
    path = get_photos_sidecar_path(job_id)
    try:
        if not os.path.exists(path):
            open(path, 'a').close()
        with open(path, 'r+') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                f.truncate()
                json.dump(points, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        log.info('TAK Overlay: saved photo sidecar for job %s (%d points)', job_id, len(points))
    except Exception as e:
        log.warning('TAK Overlay: could not save photo sidecar for job %s: %s', job_id, e)


def read_photos_sidecar(job_id):
    """
    Read the photo sidecar file for a job.
    Returns the list of points, or None if the file doesn't exist.
    """
    path = get_photos_sidecar_path(job_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r') as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                content = f.read().strip()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return json.loads(content) if content else None
    except Exception as e:
        log.warning('TAK Overlay: could not read photo sidecar for job %s: %s', job_id, e)
        return None


def update_photos_sidecar(job_id, mutate_fn):
    """
    Read-modify-write the sidecar under a single exclusive lock, so the read
    and the write can't interleave with another writer (e.g. two pipeline
    runs, or a read racing the update). mutate_fn(points) is called with the
    current point list and must mutate it in place; nothing is written if
    the sidecar doesn't exist yet. Used by pipeline.py to set each point's
    `used` flag after ODM completes, instead of its own unguarded
    open/read/open/write.

    Returns the (possibly mutated) point list, or None if there was no
    sidecar to update.
    """
    path = get_photos_sidecar_path(job_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r+') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read().strip()
                points = json.loads(content) if content else []
                mutate_fn(points)
                f.seek(0)
                f.truncate()
                json.dump(points, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return points
    except Exception as e:
        log.warning('TAK Overlay: could not update photo sidecar for job %s: %s', job_id, e)
        return None


def delete_photos_sidecar(job_id):
    """Delete the photo sidecar file for a job. Safe to call if it doesn't exist."""
    path = get_photos_sidecar_path(job_id)
    if os.path.exists(path):
        os.remove(path)
        log.info('TAK Overlay: deleted photo sidecar for job %s', job_id)


def _sanitize_filename(name):
    """
    Make a string safe to use as a filename.
    Replaces spaces with underscores, strips characters not in [A-Za-z0-9._-].
    """
    name = name.replace(' ', '_')
    safe = ''.join(c for c in name if c.isalnum() or c in '._-')
    return safe or 'job'


# ── WebODM project cleanup ─────────────────────────────────────────────────

def _delete_webodm_project_by_id(project_id):
    """
    Delete a retained WebODM project (and its tasks) by primary key.
    Called when a retain_task job is manually deleted or auto-purged.
    Silently skips if project_id is None or the project no longer exists.
    Runs in the webapp/Celery context where the Django ORM is available.
    """
    if not project_id:
        return
    try:
        from app.models import Project
        deleted, _ = Project.objects.filter(pk=project_id).delete()
        if deleted:
            log.info('TAK Overlay: deleted retained WebODM project %s', project_id)
        else:
            log.debug('TAK Overlay: WebODM project %s already gone', project_id)
    except Exception as e:
        log.warning('TAK Overlay: could not delete WebODM project %s: %s', project_id, e)


# ── Public API ─────────────────────────────────────────────────────────────────

def create_job(incident_name, tz_offset_minutes=0, retain_task=False,
               quality_mode=False, terrain_correction=False):
    """
    Create a new job record in running state.
    Returns the job_id (UUID string).

    Args:
        incident_name      (str): Operator-supplied incident name or number.
        tz_offset_minutes  (int): Signed minutes east of UTC from the browser
                                   (JS getTimezoneOffset() * -1). Used to
                                   localise the timestamp in display_name and
                                   filename so they reflect the operator local
                                   time rather than server UTC.
                                   e.g. AKDT = -480, EST = -300, UTC = 0.
                                   Defaults to 0 (UTC stamp) if not supplied.
        retain_task        (bool): If True, the WebODM project/task is NOT
                                   auto-deleted when the job completes. It will
                                   be cleaned up by purge_expired_jobs() at 72h.
        quality_mode       (bool): If True, the high-resolution variant runs:
                                   image resize raised to 4000 px and
                                   orthophoto-resolution pinned to 2.5 cm/px.
                                   Reference hardware (M920q i5-8500), 65-photo
                                   job at 4 threads: ~10 min vs ~3 min default;
                                   output 9.0 MB vs 6.1 MB. UI label:
                                   "High-Resolution mode".
        terrain_correction (bool): If True, fast-orthophoto is disabled and the
                                   full SfM pipeline runs (dense MVS, mesh,
                                   textured orthorectification). Corrects for
                                   varied terrain and tall vertical features.
                                   Reference hardware (M920q i5-8500), 65-photo
                                   job at 3 threads: ~35 min vs ~3 min default;
                                   ~42 min combined with quality_mode. UI label:
                                   "Terrain correction".
    """
    job_id = str(uuid.uuid4())
    utc_now  = datetime.now(timezone.utc)
    local_dt = utc_now + timedelta(minutes=tz_offset_minutes)
    # YYMMDD, no time-of-day: short, underscore-friendly, and stays valid
    # across platforms with no colons/spaces for a client to mangle. This
    # makes display_name only day-precise, so two jobs with the same
    # incident name on the same day (a quick retry is the realistic case)
    # collide more often than the old minute-precise stamp — the UUID
    # suffix below is what actually keeps geotiff_filename unique, not
    # this timestamp, so that's fine.
    display_name = '{}_{}'.format(incident_name, local_dt.strftime('%y%m%d'))
    # Suffix the first 8 chars of the job UUID onto the filename base so
    # display_name colliding (see above) never causes one job's output to
    # overwrite another's — deleting either job would otherwise remove the
    # shared file out from under the surviving record. display_name (what
    # the operator sees in the UI) is unchanged.
    safe_base = '{}_{}'.format(_sanitize_filename(display_name), job_id[:8])
    filename         = '{}.mbtiles'.format(safe_base)
    geotiff_filename = '{}.tif'.format(safe_base)

    record = {
        'job_id':             job_id,
        'incident_name':      incident_name,
        'display_name':       display_name,
        'filename':           filename,
        'geotiff_filename':   geotiff_filename,
        'status':             'running',
        'phase':              'Queued',
        'created_at':         _now_iso(),
        'completed_at':       None,
        'webodm_task_id':     None,
        'webodm_project_id':  None,
        'retain_task':        retain_task,
        'quality_mode':       quality_mode,
        'terrain_correction': terrain_correction,
        'file_size_bytes':    None,
        'geotiff_size_bytes': None,
        'error':              None,
    }

    path = _ensure_index()
    with open(path, 'r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            jobs = _read_index(f)
            jobs.insert(0, record)   # newest first
            _write_index(f, jobs)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    log.info('TAK Overlay: created job %s ("%s")', job_id, display_name)
    return job_id


def update_job(job_id, **kwargs):
    """
    Update one or more fields on a job record.
    Example: update_job(job_id, status='completed', file_size_bytes=41234567)
    """
    path = _ensure_index()
    with open(path, 'r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            jobs = _read_index(f)
            for job in jobs:
                if job['job_id'] == job_id:
                    job.update(kwargs)
                    break
            else:
                log.warning('TAK Overlay: update_job called for unknown job_id %s', job_id)
            _write_index(f, jobs)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def get_job(job_id):
    """
    Return the job record dict for job_id, or None if not found.
    """
    path = _ensure_index()
    with open(path, 'r') as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            jobs = _read_index(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return next((j for j in jobs if j['job_id'] == job_id), None)


def get_all_jobs():
    """
    Return all job records, newest first.
    """
    path = _ensure_index()
    with open(path, 'r') as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return _read_index(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def get_running_job():
    """
    Return the first job with status='running', or None.
    Used by the UI to restore state after a page reload.
    """
    for job in get_all_jobs():
        if job['status'] == 'running':
            return job
    return None


def mark_completed(job_id, geotiff_path, gsd_cm_per_px=None, area_m2=None):
    """
    Mark a job as completed. Records the GeoTIFF file size.

    Args:
        job_id        (str): Job UUID.
        geotiff_path  (str): Path to the final RGB GeoTIFF file.
        gsd_cm_per_px (float|None): Ground sample distance in cm/px,
                                   from pipeline._compute_geotiff_stats()
                                   (v0.8.4). None if it couldn't be computed —
                                   the job panel just omits the row.
        area_m2       (float|None): Covered area in square metres, same
                                   source. Stored in metric only, per
                                   roadmap §9.3 ("store and send metric
                                   only") — the frontend's Units.formatGSD/
                                   formatArea convert for display.

    v0.7.8: MBTiles output removed. Only GeoTIFF size is recorded now.
    Legacy field `file_size_bytes` (previously MBTiles size) is cleared on
    completion so the frontend doesn't display stale data from earlier jobs.
    """
    try:
        geotiff_size = os.path.getsize(geotiff_path)
    except OSError:
        geotiff_size = None

    update_job(
        job_id,
        status='completed',
        completed_at=_now_iso(),
        file_size_bytes=None,
        geotiff_size_bytes=geotiff_size,
        gsd_cm_per_px=gsd_cm_per_px,
        area_m2=area_m2,
    )
    log.info(
        'TAK Overlay: job %s completed — geotiff %s bytes',
        job_id, geotiff_size,
    )


def mark_failed(job_id, error_message):
    """Mark a job as failed with an error message."""
    update_job(
        job_id,
        status='failed',
        completed_at=_now_iso(),
        error=str(error_message),
    )
    log.error('TAK Overlay: job %s failed — %s', job_id, error_message)


def mark_cancelled(job_id):
    """Mark a job as cancelled."""
    update_job(
        job_id,
        status='cancelled',
        completed_at=_now_iso(),
    )
    log.info('TAK Overlay: job %s cancelled', job_id)


def delete_job(job_id):
    """
    Delete a job record and all its associated files (GeoTIFF, legacy MBTiles
    from pre-v0.7.8 jobs, and the working dir).
    If the job had retain_task=True, also deletes the retained WebODM project.
    Safe to call even if files don't exist.
    """
    path = _ensure_index()
    with open(path, 'r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            jobs = _read_index(f)
            target = next((j for j in jobs if j['job_id'] == job_id), None)
            if target:
                # Remove MBTiles
                mbtiles = get_mbtiles_path(target)
                if os.path.exists(mbtiles):
                    os.remove(mbtiles)
                    log.info('TAK Overlay: deleted MBTiles for job %s', job_id)
                # Remove GeoTIFF (v0.7+)
                geotiff = get_geotiff_path(target)
                if geotiff and os.path.exists(geotiff):
                    os.remove(geotiff)
                    log.info('TAK Overlay: deleted GeoTIFF for job %s', job_id)
                # Remove photo sidecar (v0.8.3)
                delete_photos_sidecar(job_id)
                # Remove working dir
                cleanup_working_dir(job_id)
                # Remove retained WebODM project (v0.7.6+)
                if target.get('retain_task') and target.get('webodm_project_id'):
                    _delete_webodm_project_by_id(target['webodm_project_id'])
                # Remove from index
                jobs = [j for j in jobs if j['job_id'] != job_id]
                _write_index(f, jobs)
            else:
                log.warning('TAK Overlay: delete_job called for unknown job_id %s', job_id)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def purge_expired_jobs():
    """
    Delete all jobs older than the configured retention period.
    Removes GeoTIFF files, legacy MBTiles (pre-v0.7.8 jobs), working dirs,
    retained WebODM projects (if any), and index entries.
    Returns the number of jobs purged.
    """
    retention_hours = get_retention_hours()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
    path = _ensure_index()

    with open(path, 'r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            jobs = _read_index(f)
            to_purge = []
            to_keep  = []

            for job in jobs:
                created = datetime.fromisoformat(job['created_at'])
                if created < cutoff:
                    to_purge.append(job)
                else:
                    to_keep.append(job)

            for job in to_purge:
                mbtiles = get_mbtiles_path(job)
                if os.path.exists(mbtiles):
                    os.remove(mbtiles)
                geotiff = get_geotiff_path(job)
                if geotiff and os.path.exists(geotiff):
                    os.remove(geotiff)
                # Remove photo sidecar (v0.8.3)
                delete_photos_sidecar(job['job_id'])
                cleanup_working_dir(job['job_id'])
                # Clean up retained WebODM project (v0.7.6+)
                if job.get('retain_task') and job.get('webodm_project_id'):
                    _delete_webodm_project_by_id(job['webodm_project_id'])
                log.info('TAK Overlay: purged expired job %s ("%s")',
                         job['job_id'], job['display_name'])

            if to_purge:
                _write_index(f, to_keep)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    return len(to_purge)
