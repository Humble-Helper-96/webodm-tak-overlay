#!/usr/bin/env python3
"""
test_archive.py — run inside the webapp container to verify archive.py

Usage:
    docker exec webapp python /webodm/coreplugins/tak_incident_overlay/test_archive.py

Expected output:
    [PASS] get_archive_dir creates directory
    [PASS] create_job returns a UUID
    [PASS] get_job retrieves the record
    [PASS] update_job sets webodm_task_id
    [PASS] get_running_job finds the running job
    [PASS] mark_completed sets status and file_size_bytes
    [PASS] get_all_jobs returns newest first
    [PASS] mark_failed sets status and error
    [PASS] delete_job removes the record
    [PASS] purge_expired_jobs removes old jobs
    [PASS] get_settings returns default retention_hours
    [PASS] get_settings returns default thread_percent
    [PASS] get_settings returns default thread_count of None (v0.8.6)
    [PASS] get_thread_count returns None by default (v0.8.6)
    [PASS] get_user_settings returns defaults for an unknown user
    [PASS] save_user_settings persists per-user values
    [PASS] get_retention_hours reflects saved global setting
    [PASS] get_thread_percent reflects saved global setting
    [PASS] save_global_settings does not disturb per-user settings
    [PASS] get_thread_count reflects saved exact-core override
    [PASS] thread_percent is untouched by setting thread_count
    [PASS] get_thread_count returns None after clearing the override
    [PASS] read_photos_sidecar returns None before any sidecar is saved
    [PASS] read_photos_sidecar returns what save_photos_sidecar wrote
    [PASS] update_photos_sidecar mutates and persists the sidecar
    [PASS] update_photos_sidecar write is visible to a fresh read
    [PASS] update_photos_sidecar is a no-op when there is no sidecar yet
    [PASS] delete_job removes the photo sidecar
    [PASS] purge_expired_jobs removes the photo sidecar along with the job
    All tests passed.
"""

import os
import sys
import django

# Bootstrap Django so settings.MEDIA_ROOT is available
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'webodm.settings')
sys.path.insert(0, '/webodm')
django.setup()

# Now import archive — path is relative to coreplugins dir on sys.path
sys.path.insert(0, '/webodm/coreplugins/tak_incident_overlay')
import archive

PASS = '\033[92m[PASS]\033[0m'
FAIL = '\033[91m[FAIL]\033[0m'
errors = 0


def check(label, condition, detail=''):
    global errors
    if condition:
        print(f'{PASS} {label}')
    else:
        print(f'{FAIL} {label}' + (f' — {detail}' if detail else ''))
        errors += 1


# ── Test 1: archive dir creation ───────────────────────────────────────────────
d = archive.get_archive_dir()
check('get_archive_dir creates directory', os.path.isdir(d), d)

# ── Test 2: create_job ─────────────────────────────────────────────────────────
job_id = archive.create_job('Test Incident Alpha')
check('create_job returns a UUID', len(job_id) == 36 and job_id.count('-') == 4, job_id)

# ── Test 3: get_job ────────────────────────────────────────────────────────────
job = archive.get_job(job_id)
check('get_job retrieves the record',
      job is not None and job['status'] == 'running' and job['incident_name'] == 'Test Incident Alpha')

# ── Test 4: update_job ─────────────────────────────────────────────────────────
archive.update_job(job_id, webodm_task_id=42)
job = archive.get_job(job_id)
check('update_job sets webodm_task_id', job['webodm_task_id'] == 42)

# ── Test 5: get_running_job ────────────────────────────────────────────────────
running = archive.get_running_job()
check('get_running_job finds the running job',
      running is not None and running['job_id'] == job_id)

# ── Test 6: mark_completed ─────────────────────────────────────────────────────
# Create a dummy mbtiles file to simulate a completed job
mbtiles_path = archive.get_mbtiles_path(job)
with open(mbtiles_path, 'wb') as f:
    f.write(b'FAKE_MBTILES_DATA_FOR_TESTING')

archive.mark_completed(job_id, mbtiles_path)
job = archive.get_job(job_id)
check('mark_completed sets status and file_size_bytes',
      job['status'] == 'completed' and job['file_size_bytes'] == 29)

# ── Test 7: get_all_jobs ordering ──────────────────────────────────────────────
job_id_2 = archive.create_job('Test Incident Beta')
all_jobs = archive.get_all_jobs()
check('get_all_jobs returns newest first',
      len(all_jobs) >= 2 and all_jobs[0]['job_id'] == job_id_2,
      f'first job: {all_jobs[0]["incident_name"] if all_jobs else "none"}')

# ── Test 8: mark_failed ────────────────────────────────────────────────────────
archive.mark_failed(job_id_2, 'WebODM task failed: out of memory')
job2 = archive.get_job(job_id_2)
check('mark_failed sets status and error',
      job2['status'] == 'failed' and 'out of memory' in (job2['error'] or ''))

# ── Test 9: delete_job ─────────────────────────────────────────────────────────
archive.delete_job(job_id)
check('delete_job removes the record', archive.get_job(job_id) is None)
check('delete_job removes the MBTiles file', not os.path.exists(mbtiles_path))

# Clean up job 2
archive.delete_job(job_id_2)

# ── Test 10: purge_expired_jobs ────────────────────────────────────────────────
from datetime import datetime, timezone, timedelta

old_id = archive.create_job('Expired Job')
# Manually backdate the created_at field to 73 hours ago
from archive import _ensure_index, _read_index, _write_index
import fcntl

path = _ensure_index()
with open(path, 'r+') as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    try:
        jobs = _read_index(f)
        for j in jobs:
            if j['job_id'] == old_id:
                old_time = datetime.now(timezone.utc) - timedelta(hours=73)
                j['created_at'] = old_time.isoformat()
        _write_index(f, jobs)
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)

purged = archive.purge_expired_jobs()
check('purge_expired_jobs removes old jobs',
      purged == 1 and archive.get_job(old_id) is None,
      f'purged={purged}')

# ── Test 11: settings.json defaults (v0.8.1) ───────────────────────────────────
default_global = archive.get_settings().get('global', {})
check('get_settings returns default retention_hours',
      default_global.get('retention_hours') == 72, default_global)
check('get_settings returns default thread_percent',
      default_global.get('thread_percent') == 50, default_global)
check('get_settings returns default thread_count of None (v0.8.6)',
      default_global.get('thread_count') is None, default_global)
check('get_thread_count returns None by default (v0.8.6)',
      archive.get_thread_count() is None, archive.get_thread_count())

# ── Test 12: per-user settings (v0.8.1) ────────────────────────────────────────
test_user = 'test_archive_user'
defaults = archive.get_user_settings(test_user)
check('get_user_settings returns defaults for an unknown user',
      defaults == {'units': 'metric', 'time_format': '24h',
                   'save_task_default': False},
      defaults)

archive.save_user_settings(test_user, {
    'units': 'imperial', 'time_format': '12h',
    'save_task_default': True,
})
saved = archive.get_user_settings(test_user)
check('save_user_settings persists per-user values',
      saved.get('units') == 'imperial' and saved.get('time_format') == '12h',
      saved)

# ── Test 13: global settings — retention and thread percent (v0.8.1) ──────────
archive.save_global_settings({'retention_hours': 48, 'thread_percent': 25})
check('get_retention_hours reflects saved global setting',
      archive.get_retention_hours() == 48, archive.get_retention_hours())
check('get_thread_percent reflects saved global setting',
      archive.get_thread_percent() == 25, archive.get_thread_percent())
check('save_global_settings does not disturb per-user settings',
      archive.get_user_settings(test_user).get('units') == 'imperial')

# Restore defaults so this test file doesn't leave the node mid-job-run on 25%
archive.save_global_settings({'retention_hours': 72, 'thread_percent': 50})

# ── Test 13b: global settings — exact thread_count override (v0.8.6) ──────────
archive.save_global_settings({'thread_count': 4})
check('get_thread_count reflects saved exact-core override',
      archive.get_thread_count() == 4, archive.get_thread_count())
check('thread_percent is untouched by setting thread_count',
      archive.get_thread_percent() == 50, archive.get_thread_percent())

# Clearing it (set back to None) must revert to percent-based behavior.
archive.save_global_settings({'thread_count': None})
check('get_thread_count returns None after clearing the override',
      archive.get_thread_count() is None, archive.get_thread_count())

# ── Test 13c: global settings — thread_count_ceiling (v0.8.7) ─────────────────
check('get_thread_count_ceiling returns None by default',
      archive.get_thread_count_ceiling() is None, archive.get_thread_count_ceiling())

archive.save_global_settings({'thread_count_ceiling': 4})
check('get_thread_count_ceiling reflects saved value',
      archive.get_thread_count_ceiling() == 4, archive.get_thread_count_ceiling())
check('thread_count is untouched by setting thread_count_ceiling',
      archive.get_thread_count() is None, archive.get_thread_count())

archive.save_global_settings({'thread_count_ceiling': None})
check('get_thread_count_ceiling returns None after clearing it',
      archive.get_thread_count_ceiling() is None, archive.get_thread_count_ceiling())

# ── Test 14: photo sidecar CRUD (v0.8.3/v0.8.4) ────────────────────────────────
sidecar_job_id = archive.create_job('Sidecar Test Job')
check('read_photos_sidecar returns None before any sidecar is saved',
      archive.read_photos_sidecar(sidecar_job_id) is None)

points = [
    {'name': 'DJI_0001.JPG', 'lat': 61.2, 'lon': -149.9, 'time': '2026:01:01 10:00:00'},
    {'name': 'DJI_0002.JPG', 'lat': 61.21, 'lon': -149.91, 'time': '2026:01:01 10:00:05'},
]
archive.save_photos_sidecar(sidecar_job_id, points)
read_back = archive.read_photos_sidecar(sidecar_job_id)
check('read_photos_sidecar returns what save_photos_sidecar wrote',
      read_back is not None and len(read_back) == 2 and
      read_back[0]['name'] == 'DJI_0001.JPG', read_back)

# ── Test 15: update_photos_sidecar (v0.8.4 used-flag update) ──────────────────
def _mark_first_used(pts):
    for p in pts:
        p['used'] = (p['name'] == 'DJI_0001.JPG')

updated = archive.update_photos_sidecar(sidecar_job_id, _mark_first_used)
check('update_photos_sidecar mutates and persists the sidecar',
      updated is not None and updated[0]['used'] is True and updated[1]['used'] is False,
      updated)
reread = archive.read_photos_sidecar(sidecar_job_id)
check('update_photos_sidecar write is visible to a fresh read',
      reread[0]['used'] is True and reread[1]['used'] is False, reread)

no_sidecar_job_id = archive.create_job('No Sidecar Job')
check('update_photos_sidecar is a no-op when there is no sidecar yet',
      archive.update_photos_sidecar(no_sidecar_job_id, _mark_first_used) is None)
archive.delete_job(no_sidecar_job_id)

# ── Test 16: sidecar cleanup on delete_job and purge_expired_jobs ─────────────
archive.delete_job(sidecar_job_id)
check('delete_job removes the photo sidecar',
      archive.read_photos_sidecar(sidecar_job_id) is None)

purge_sidecar_job_id = archive.create_job('Expired Sidecar Job')
archive.save_photos_sidecar(purge_sidecar_job_id, points)
path = _ensure_index()
with open(path, 'r+') as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    try:
        jobs = _read_index(f)
        for j in jobs:
            if j['job_id'] == purge_sidecar_job_id:
                old_time = datetime.now(timezone.utc) - timedelta(hours=73)
                j['created_at'] = old_time.isoformat()
        _write_index(f, jobs)
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
archive.purge_expired_jobs()
check('purge_expired_jobs removes the photo sidecar along with the job',
      archive.read_photos_sidecar(purge_sidecar_job_id) is None)

# ── Summary ────────────────────────────────────────────────────────────────────
print()
if errors == 0:
    print('\033[92mAll tests passed.\033[0m')
else:
    print(f'\033[91m{errors} test(s) failed.\033[0m')
    sys.exit(1)
