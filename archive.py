"""
archive.py — TAK Incident Overlay (v0.7.0)
Job index management, archive directory, and 72-hour auto-purge.

Directory layout (all under settings.MEDIA_ROOT):
    tak_incident_overlay/
    ├── index.json                        ← job records
    ├── working/<job_id>/                 ← temp space during processing
    │   ├── images/                       ← uploaded photos
    │   └── wgs84.tif                     ← GDAL intermediate (deleted on cleanup)
    ├── <sanitized_display_name>.mbtiles  ← final MBTiles deliverable (one per job)
    └── <sanitized_display_name>.tif      ← final RGB GeoTIFF deliverable (one per job, v0.7+)

Job record schema:
    {
        "job_id":              str (UUID4),
        "incident_name":       str (operator input),
        "display_name":        str ("{incident_name} YYYY-MM-DD HHMM"),
        "filename":            str ("{display_name}.mbtiles", filesystem-safe),
        "geotiff_filename":    str ("{display_name}.tif",     filesystem-safe),  # v0.7+
        "status":              "running" | "completed" | "failed" | "cancelled",
        "phase":               str (current processing phase label, v0.7.2+),
        "created_at":          str (ISO 8601, UTC),
        "completed_at":        str | null,
        "webodm_task_id":      int | null,
        "file_size_bytes":     int | null,   # MBTiles size
        "geotiff_size_bytes":  int | null,   # RGB GeoTIFF size (v0.7+)
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
PURGE_HOURS     = 72


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
    """Return the full path to the final MBTiles file for a completed job."""
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


def _sanitize_filename(name):
    """
    Make a string safe to use as a filename.
    Replaces spaces with underscores, strips characters not in [A-Za-z0-9._-].
    """
    name = name.replace(' ', '_')
    safe = ''.join(c for c in name if c.isalnum() or c in '._-')
    return safe or 'job'


# ── Public API ─────────────────────────────────────────────────────────────────

def create_job(incident_name, tz_offset_minutes=0):
    """
    Create a new job record in running state.
    Returns the job_id (UUID string).

    Args:
        incident_name     (str): Operator-supplied incident name or number.
        tz_offset_minutes (int): Signed minutes east of UTC from the browser
                                  (JS getTimezoneOffset() * -1). Used to
                                  localise the timestamp in display_name and
                                  filename so they reflect the operator local
                                  time rather than server UTC.
                                  e.g. AKDT = -480, EST = -300, UTC = 0.
                                  Defaults to 0 (UTC stamp) if not supplied.
    """
    job_id = str(uuid.uuid4())
    utc_now  = datetime.now(timezone.utc)
    local_dt = utc_now + timedelta(minutes=tz_offset_minutes)
    display_name = '{} {}'.format(incident_name, local_dt.strftime('%Y-%m-%d %H%M'))
    safe_base = _sanitize_filename(display_name)
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


def mark_completed(job_id, mbtiles_path, geotiff_path=None):
    """
    Mark a job as completed. Records file sizes from the output files.

    Args:
        job_id        (str): Job UUID.
        mbtiles_path  (str): Path to the final MBTiles file (required).
        geotiff_path  (str, optional): Path to the final RGB GeoTIFF file.
                                       Pass None to skip GeoTIFF size recording
                                       (e.g. if the GeoTIFF step was skipped).
    """
    try:
        mbtiles_size = os.path.getsize(mbtiles_path)
    except OSError:
        mbtiles_size = None

    geotiff_size = None
    if geotiff_path:
        try:
            geotiff_size = os.path.getsize(geotiff_path)
        except OSError:
            geotiff_size = None

    update_job(
        job_id,
        status='completed',
        completed_at=_now_iso(),
        file_size_bytes=mbtiles_size,
        geotiff_size_bytes=geotiff_size,
    )
    log.info(
        'TAK Overlay: job %s completed — mbtiles %s bytes, geotiff %s bytes',
        job_id, mbtiles_size, geotiff_size,
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
    Delete a job record and its associated files (MBTiles + working dir).
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
                # Remove working dir
                cleanup_working_dir(job_id)
                # Remove from index
                jobs = [j for j in jobs if j['job_id'] != job_id]
                _write_index(f, jobs)
            else:
                log.warning('TAK Overlay: delete_job called for unknown job_id %s', job_id)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def purge_expired_jobs():
    """
    Delete all jobs older than PURGE_HOURS (72h).
    Removes MBTiles files, working dirs, and index entries.
    Returns the number of jobs purged.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=PURGE_HOURS)
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
                cleanup_working_dir(job['job_id'])
                log.info('TAK Overlay: purged expired job %s ("%s")',
                         job['job_id'], job['display_name'])

            if to_purge:
                _write_index(f, to_keep)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    return len(to_purge)
