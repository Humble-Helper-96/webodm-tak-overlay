# TAK Incident Overlay — Deployment Guide
**Plugin version:** v0.7.13
**WebODM target:** 3.2.2 (Docker install)
**Last updated:** 2026-06-03

---

## Before You Start

This guide assumes:

- WebODM 3.2.2 is already installed and running on the dedicated WebODM machine
- You have SSH access (or direct terminal access) to that machine
- At least one ODM processing node (NodeODX) is registered in WebODM and showing online
- You have the WebODM admin login

If WebODM is not yet installed, set that up first — this guide covers the plugin only.

---

## Step 1 — Find Your WebODM Directory

Before anything else, locate the folder where WebODM is installed. It contains
a `docker-compose.yml` file. If you're not sure where it is, this command will
find it:

```bash
find / -name "docker-compose.yml" -path "*/WebODM/*" 2>/dev/null
```

Once you have the path, set it as a variable so every command in this guide
works without substitution:

```bash
# Replace the path below with your actual WebODM directory
export WEBODM=/path/to/your/WebODM
```

You can confirm you have the right directory:

```bash
ls $WEBODM/docker-compose.yml    # should exist
ls $WEBODM/coreplugins/          # should exist
```

> **Note:** `$WEBODM` is only set for your current terminal session. If you
> close the terminal and come back, run the `export` line again before
> continuing.

---

## Step 2 — Download the Plugin

```bash
cd $WEBODM/coreplugins

git clone https://github.com/Humble-Helper-96/webodm-tak-overlay tak_incident_overlay
```

If git is not available, download and extract the release archive instead:

```bash
cd $WEBODM/coreplugins

wget https://github.com/Humble-Helper-96/webodm-tak-overlay/archive/refs/tags/v0.7.13.tar.gz
tar -xzf v0.7.13.tar.gz
mv webodm-tak-overlay-0.7.13 tak_incident_overlay
rm v0.7.13.tar.gz
```

### Confirm the files are there

```bash
ls tak_incident_overlay/
```

You should see at least these files:

```
__init__.py   manifest.json   plugin.py   api.py
pipeline.py   archive.py      templates/
```

If any are missing, re-download before continuing.

---

## Step 3 — Configure WebODM

Two settings need to be added before the plugin will work correctly: one to
allow large photo uploads, and one to make output files survive container
restarts.

### Allow large uploads

Open the WebODM environment file:

```bash
nano $WEBODM/.env
```

Add this line at the bottom:

```
DATA_UPLOAD_MAX_MEMORY_SIZE=314572800
```

Save and close (`Ctrl+X`, then `Y`, then `Enter`).

> This raises the upload limit to 300 MB, which covers the standard 150-photo
> batch. If you regularly use High-Capacity mode (300 photos), double this
> value to `629145600`.

### Keep output files after restarts

Open the Docker Compose file:

```bash
nano $WEBODM/docker-compose.yml
```

Find the `webapp:` section. Under its `volumes:` block, add this line:

```yaml
      - ./app_data/media/tak_incident_overlay:/webodm/app/media/tak_incident_overlay
```

Do the same under the `worker:` section — both need it.

Save and close.

> Without this, the GeoTIFF files the plugin generates will be lost the next
> time the containers restart.

---

## Step 4 — Restart WebODM

```bash
cd $WEBODM
docker compose down
docker compose up -d
```

Wait about 30 seconds, then check that everything came back up:

```bash
docker compose ps
```

All containers (`db`, `broker`, `worker`, `webapp`, `nginx`) should show as `Up`.

> **Note on compose vs `docker restart`:** If your install has a separate
> `docker-compose.nodeodm.yml` and a `COMPOSE_FILE` env var, `docker compose`
> may complain about an unfilled `node-odm` image. In that case, restart only
> the plugin-affected containers directly:
> `docker restart webapp worker`

---

## Step 5 — Verify the Plugin Loaded

1. Open WebODM in a browser
2. Look for **TAK Overlay** in the left navigation sidebar
3. Click it — the plugin UI should open

If the menu item is missing, check the startup logs:

```bash
docker logs webapp 2>&1 | grep -i "tak_incident"
```

A successful load looks like one of:

```
Found plugin: tak_incident_overlay (v0.7.13)
INFO Registered [coreplugins.tak_incident_overlay.plugin]
```

(WebODM versions vary in log wording.)

---

## Step 6 — Run a Test Job

Use a small set of GPS-tagged JPEGs (5–10 images, clear sky, good overlap
between photos).

1. Enter a location name in the **Location of Incident** field
2. Select your test images
3. Leave all toggles at their defaults (High-Resolution off, Terrain correction off)
4. Click **Process**

The status bar should move through these phases in order:

```
Queued → Processing → Finalizing → Reprojecting → Exporting GeoTIFF → Completed
```

With 5–10 images in standard mode, this takes roughly 2–4 minutes on
reference-class hardware (Lenovo M920q with Intel i5-8500). Slower or
faster CPUs scale accordingly. When complete, the GeoTIFF Download button
appears in the job row.

If the job fails, see [Troubleshooting](#troubleshooting) below.

---

## Upgrading from an Earlier Version

### From any v0.7.x release (v0.7.7 or later)

No data migration needed — the job record format is unchanged across v0.7.7+.

If you installed via git:

```bash
cd $WEBODM/coreplugins/tak_incident_overlay
git fetch origin
git checkout v0.7.13
```

If you installed via archive: delete the folder and re-run Step 2.

Then restart:

```bash
docker restart webapp worker
```

If your install supports it, the standard compose form also works:

```bash
cd $WEBODM
docker compose restart webapp worker
```

### From v0.6.x or earlier

The job record format and output set both changed substantially. Old MBTiles
files on disk will continue to be cleaned up by the 72-hour auto-purge but
the new plugin no longer produces MBTiles.

1. Let any active jobs finish (or cancel them)
2. Back up existing records if needed:
   ```bash
   cp $WEBODM/app_data/media/tak_incident_overlay/index.json ~/tak_index_backup.json
   ```
3. Delete the old index:
   ```bash
   rm $WEBODM/app_data/media/tak_incident_overlay/index.json
   ```
4. Install v0.7.13 plugin files (Step 2)
5. Restart webapp and worker (Step 4)

The plugin creates a fresh index automatically on first use.

---

## Making Changes Later

| What changed                | What to do |
|---|---|
| Any `.py` file              | `docker restart webapp worker` |
| `app.html` only             | Upload the file, then hard-reload the browser (`Ctrl+Shift+R`) |
| `.env` setting              | `cd $WEBODM && docker compose down && docker compose up -d` |
| `docker-compose.yml` mounts | `cd $WEBODM && docker compose down && docker compose up -d` |

---

## Troubleshooting

### Plugin menu item is missing

The plugin failed to load at startup. Check:

```bash
docker logs webapp 2>&1 | grep -i "error\|tak"
```

Common causes: a file is missing from the plugin folder, or the files have
wrong permissions. Fix permissions with:

```bash
sudo chmod -R 755 $WEBODM/coreplugins/tak_incident_overlay
```

Then restart webapp and worker.

### Node status dot stays grey or red

The plugin cannot reach the ODM processing node. In WebODM, go to
**Administration → Processing Nodes** and confirm the node shows as online.
If it's offline, restart NodeODX and wait for it to reconnect.

### Job stays in "Queued" and never starts

NodeODX is reachable but its queue is full, or it went offline after the job
was submitted. Check the node status in WebODM's Processing Nodes panel. If
it looks healthy, wait — NodeODX will process queued jobs in order.

### Upload fails or browser shows an error on submit

The upload size limit may not have been applied. Confirm the `.env` change
from Step 3 is saved, and that you did a full `docker compose down && up`
(not just a restart). Also check available disk space:

```bash
df -h $WEBODM/app_data/
```

### Output files disappeared after a restart

The volume mount from Step 3 was not added, or was added incorrectly.
Re-check both the `webapp` and `worker` sections in `docker-compose.yml`,
then do a full `docker compose down && up`.

### Images rejected: "No GPS EXIF data"

The photos do not contain embedded GPS coordinates. TAK overlays require
georeferenced images — the drone must have had GPS lock during the flight.
To check a specific image:

```bash
exiftool photo.jpg | grep GPS
```

If no GPS lines appear, those images cannot be used with this plugin.

### Images rejected: "Not a valid JPEG"

Only JPEG files are accepted. RAW, HEIC, PNG, and TIFF files will be
rejected at upload. Convert to JPEG before submitting.

### Permission errors removing the plugin folder

If you need to delete the plugin folder and see `Permission denied` on
`__pycache__` files, those were written by the container running as root.
Use `sudo`:

```bash
sudo rm -rf $WEBODM/coreplugins/tak_incident_overlay
```

---

*For architecture details and known issues, see the repository's source files
and inline docstrings.*
