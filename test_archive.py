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

# ── Summary ────────────────────────────────────────────────────────────────────
print()
if errors == 0:
    print('\033[92mAll tests passed.\033[0m')
else:
    print(f'\033[91m{errors} test(s) failed.\033[0m')
    sys.exit(1)
