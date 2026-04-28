# TAK Incident Overlay — Deployment Guide
**Plugin v0.6.0 | WebODM Coreplugin**

---

## Overview

This document covers how to package the TAK Incident Overlay plugin into a
deployable archive and install it on a new WebODM instance. It is intended for
anyone standing up a fresh WebODM host who needs to replicate the plugin from
a known-good installation.

---

## Part 1 — Building the Plugin Archive

### 1.1 — Required Files

The complete plugin consists of exactly these 7 files:

```
tak_incident_overlay/
├── __init__.py
├── manifest.json
├── plugin.py
├── archive.py
├── api.py
├── pipeline.py
└── templates/
    └── app.html
```

> **Do not include `__pycache__/`** — these are compiled bytecode artifacts
> generated at runtime by the Python interpreter. They are host-specific and
> will be regenerated automatically on first load.

### 1.2 — Create the Archive

From the machine with the working plugin installation, run:

```bash
cd ~/WebODM/coreplugins

tar -czf tak_incident_overlay_v0.6.0.tar.gz \
    --exclude='tak_incident_overlay/__pycache__' \
    tak_incident_overlay/
```

Verify the archive contents before transferring:

```bash
tar -tzf tak_incident_overlay_v0.6.0.tar.gz
```

Expected output:
```
tak_incident_overlay/
tak_incident_overlay/__init__.py
tak_incident_overlay/manifest.json
tak_incident_overlay/plugin.py
tak_incident_overlay/archive.py
tak_incident_overlay/api.py
tak_incident_overlay/pipeline.py
tak_incident_overlay/templates/
tak_incident_overlay/templates/app.html
```

Exactly 9 lines — 7 files + 2 directory entries. If you see any `.pyc` files,
re-run the tar command with the `--exclude` flag.

---

## Part 2 — Transferring the Archive

Copy the archive to the target machine using `scp`:

```bash
scp tak_incident_overlay_v0.6.0.tar.gz <user>@<target-ip>:~/
```

Or transfer via any method available (USB, shared network path, etc.).

---

## Part 3 — Installing on a New WebODM Instance

### Prerequisites

The target machine must have:

- WebODM installed and confirmed working (at least one successful job)
- Docker and Docker Compose available
- WebODM directory at `~/WebODM` (adjust paths below if different)
- A processing node configured and online (verify at Admin → Processing Nodes)

### 3.1 — Extract the Plugin

```bash
cd ~/WebODM/coreplugins
tar -xzf ~/tak_incident_overlay_v0.6.0.tar.gz
```

Verify the result:

```bash
tree -L 2 ~/WebODM/coreplugins/tak_incident_overlay
```

Expected:
```
tak_incident_overlay/
├── __init__.py
├── manifest.json
├── plugin.py
├── archive.py
├── api.py
├── pipeline.py
└── templates/
    └── app.html
```

### 3.2 — Add Volume Mounts to docker-compose.yml

The plugin directory must be bind-mounted into both the `webapp` and `worker`
containers. Without this, the plugin is destroyed on `docker compose down`.

Add the following line to the `volumes:` block of **both** the `webapp` and
`worker` service definitions in `~/WebODM/docker-compose.yml`:

```yaml
- ./coreplugins/tak_incident_overlay:/webodm/coreplugins/tak_incident_overlay:z
```

The quickest way to do this (assumes the standard WebODM compose file where
both services share the same `${WO_MEDIA_DIR}` volume line):

```bash
sed -i 's|      - ${WO_MEDIA_DIR}:/webodm/app/media:z|      - ${WO_MEDIA_DIR}:/webodm/app/media:z\n      - ./coreplugins/tak_incident_overlay:/webodm/coreplugins/tak_incident_overlay:z|g' \
    ~/WebODM/docker-compose.yml
```

Verify the mount appears in both services:

```bash
grep -n "tak_incident_overlay" ~/WebODM/docker-compose.yml
```

Expected: two hits, one in the `webapp` block and one in the `worker` block.

> **Note:** If your `docker-compose.yml` has been customized and the
> `${WO_MEDIA_DIR}` volume line differs, add the mount manually by editing
> the file directly.

### 3.3 — Set the Upload Size Limit

WebODM's default Django upload limit (2.5 MB per file) is too low for
multi-photo drone batches. Set it to 500 MB.

First, locate the settings override file. Depending on the WebODM version this
will be one of:

```bash
# Newer installs:
~/WebODM/webodm/settings_override.py

# Older installs:
~/WebODM/webodm/local_settings.py
```

Check which exists:

```bash
ls ~/WebODM/webodm/settings_override.py ~/WebODM/webodm/local_settings.py 2>/dev/null
```

Append the setting to whichever file is present:

```bash
# For settings_override.py:
echo "" >> ~/WebODM/webodm/settings_override.py
echo "DATA_UPLOAD_MAX_MEMORY_SIZE = 524288000" >> ~/WebODM/webodm/settings_override.py

# For local_settings.py:
echo "" >> ~/WebODM/webodm/local_settings.py
echo "DATA_UPLOAD_MAX_MEMORY_SIZE = 524288000" >> ~/WebODM/webodm/local_settings.py
```

Verify it landed on its own line:

```bash
cat ~/WebODM/webodm/settings_override.py
# or
cat ~/WebODM/webodm/local_settings.py
```

The last line should read exactly:
```
DATA_UPLOAD_MAX_MEMORY_SIZE = 524288000
```

### 3.4 — Restart WebODM

```bash
cd ~/WebODM
./webodm.sh restart
```

### 3.5 — Verify Registration

Once the stack is back up, confirm the plugin registered without errors:

```bash
docker logs webapp | grep -i tak
```

Expected lines:
```
INFO Added [[coreplugins.tak_incident_overlay.plugin]] plugin to database
INFO Registered [coreplugins.tak_incident_overlay.plugin]
```

If you see `ImportError` or `ModuleNotFoundError` instead, the plugin files
are missing or the volume mount did not apply — recheck Steps 3.1 and 3.2.

### 3.6 — Enable the Plugin in the UI

1. Open WebODM in a browser: `http://<host-ip>:8000`
2. Log in as an administrator
3. Navigate to **Administration → Plugins**
4. Find `tak_incident_overlay` in the list
5. Click **Enable**
6. The **TAK Overlay** item should appear in the left navigation sidebar

---

## Part 4 — Post-Install Verification

Run through this checklist before declaring the install complete.

```bash
# Plugin files present:
tree ~/WebODM/coreplugins/tak_incident_overlay

# Volume mounts in compose file:
grep -n "tak_incident_overlay" ~/WebODM/docker-compose.yml
# Expected: 2 hits

# Upload size limit set:
grep "DATA_UPLOAD_MAX_MEMORY_SIZE" ~/WebODM/webodm/settings_override.py
# Expected: DATA_UPLOAD_MAX_MEMORY_SIZE = 524288000

# Plugin registered in logs:
docker logs webapp | grep -i tak
# Expected: INFO Registered [coreplugins.tak_incident_overlay.plugin]

# Processing node online:
docker exec webapp python manage.py shell -c "
from nodeodm.models import ProcessingNode
for n in ProcessingNode.objects.all():
    print(f'id={n.id} host={n.hostname}:{n.port} online={n.is_online()}')
"
# Expected: a processing node listed with online=True

# All containers running:
docker ps
# Expected: webapp, worker, processing node, broker, db all Up
```

---

## Part 5 — Upgrade Procedure

To update the plugin files on an existing installation without a full
teardown:

```bash
# Extract new archive over existing files:
cd ~/WebODM/coreplugins
tar -xzf ~/tak_incident_overlay_vX.X.X.tar.gz

# Restart only the Python containers (no full stack restart needed):
cd ~/WebODM
docker compose restart webapp worker

# Verify new version registered:
docker logs webapp | grep -i tak
```

Template-only changes (`app.html`) do not require a container restart —
copy the file and hard-reload the browser (`Ctrl+Shift+R`).

---

## Quick Reference

| Item | Value |
|---|---|
| Plugin directory | `~/WebODM/coreplugins/tak_incident_overlay/` |
| Settings file (new installs) | `~/WebODM/webodm/settings_override.py` |
| Settings file (older installs) | `~/WebODM/webodm/local_settings.py` |
| Upload size limit | `DATA_UPLOAD_MAX_MEMORY_SIZE = 524288000` |
| Restart command | `cd ~/WebODM && ./webodm.sh restart` |
| Python-only restart | `docker compose restart webapp worker` |
| Plugin UI path | Administration → Plugins → tak_incident_overlay → Enable |
| Plugin nav item | TAK Overlay (left sidebar) |
| Job archive location | `/webodm/app/media/tak_incident_overlay/` (inside webapp container) |
| Auto-purge window | 72 hours |
