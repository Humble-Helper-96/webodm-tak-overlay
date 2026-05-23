# TAK Incident Overlay — Changelog

## v0.7.7 (2026-05-22)

### New Features

**Quality Mode Toggle**
- New toggle in the upload/control panel: "Quality mode — full SfM pipeline, 15–25 min"
- When enabled, `fast-orthophoto` is omitted from the WebODM task options, running the full Structure from Motion pipeline instead
- Produces a more accurate and geometrically consistent orthophoto at the cost of significantly longer runtime (~15–25 min vs ~3–5 min for standard mode)
- An amber warning appears below the toggle when enabled: "Not recommended during active sUAS live stream operations."
- All other task options remain at NodeODX defaults; no additional ODM options are set
- Field Guide section 02 updated with a full explanation of the two modes, runtime expectations, and the live stream caution

### Technical Changes

**pipeline.py**
- `start()`: added `quality_mode=False` parameter
- `_run_pipeline()`: added `quality_mode=False` parameter
- `TASK_OPTIONS` is now built conditionally: `fast-orthophoto: true` is only appended when `quality_mode=False`
- Log line at pipeline start notes quality mode when active

**archive.py**
- Job record schema: added `quality_mode` (bool, default False)
- `create_job()`: added `quality_mode=False` parameter; stored in record at creation time

**api.py**
- `upload_view()`: parses `quality_mode` from POST data
- Passes `quality_mode` to `archive.create_job()` and `pipeline.start()`
- Version bumped to v0.7.7

**app.html**
- Quality mode toggle added below Save WebODM Task toggle
- Toggle shows amber warning line on enable; hides on disable
- `onProcessClick()` appends `quality_mode` flag to FormData
- Field Guide section 02: added paragraph covering fast vs quality mode, runtime difference, and live stream warning
- Version comment bumped to v0.7.7

**plugin.py / manifest.json**
- Version bumped to 0.7.7

### Deployment

```bash
cd ~/WebODM/coreplugins
tar -xzf tak_incident_overlay_v0_7_7.tar.gz
cd ~/WebODM
docker compose restart webapp worker
```

`app.html` is template-only — upload and hard-reload (`Ctrl+Shift+R`) without a container restart if deploying separately.
No database migrations needed. Backward compatible with all prior job archive records (`quality_mode` defaults to False via `.get('quality_mode')`).

---

## v0.7.6 (2026-05-22)

### New Features

**WebODM Task Retention (operator toggle)**
- New toggle in the upload/control panel: "Save WebODM task — auto-purge with job at 72h"
- When enabled, the WebODM project and task are NOT deleted after the pipeline completes
- The task remains accessible in the WebODM dashboard for inspection, re-export, or manual review
- A "WebODM" link button appears in the archive row for each retained completed job, opening the WebODM project page in a new tab
- Retained projects are cleaned up automatically by `purge_expired_jobs()` at the same 72-hour window as the job record — no separate timer needed

**Unique WebODM Task Names**
- WebODM project and task names now include an 8-character job ID suffix: `TAK {display_name} [{job_id[:8]}]`
- Prevents name collisions in the WebODM UI when multiple jobs share the same incident name and timestamp

### Technical Changes

**archive.py**
- Job record schema: added `retain_task` (bool, default False) and `webodm_project_id` (int | null, formalized from implicit)
- `create_job()`: added `retain_task=False` parameter; stored in record at creation time
- Added `_delete_webodm_project_by_id(project_id)` helper — imports `app.models.Project` and deletes by pk; silently skips if already gone
- `delete_job()`: calls `_delete_webodm_project_by_id()` for jobs with `retain_task=True` and a `webodm_project_id`
- `purge_expired_jobs()`: same — cleans up retained WebODM project before removing the job record

**pipeline.py**
- `start()`: added `retain_task=False` parameter, passed through to `_run_pipeline()`
- `_run_pipeline()`: added `retain_task=False` parameter
- Project name: `f"TAK {display_name} [{job_id[:8]}]"` (was `f"TAK {display_name}"`)
- Task name: `f"{display_name} [{job_id[:8]}]"` (was `display_name`)
- `finally` block: skips `_delete_webodm_project()` when `retain_task=True`; logs retention instead

**api.py**
- `upload_view()`: parses `retain_task` from POST data (`'true'`/`'false'`)
- Passes `retain_task` to `archive.create_job()` and `pipeline.start()`
- Version bumped to v0.7.6

**app.html**
- Retain-task toggle added below high-capacity toggle, matching same iOS-style toggle styling
- Toggle label: "Save WebODM task — auto-purge with job at 72h" with a dim one-liner description below
- `onProcessClick()`: appends `retain_task` flag to FormData
- Archive row: if `job.retain_task && job.webodm_project_id`, renders a blue "WebODM" link button (opens `/dashboard/project/{id}/` in new tab)
- Version comment bumped to v0.7.6

**plugin.py / manifest.json**
- Version bumped to 0.7.6

### Deployment

```bash
cd ~/WebODM/coreplugins
tar -xzf tak_incident_overlay_v0_7_6.tar.gz
cd ~/WebODM
docker compose restart webapp worker
```

`app.html` is template-only — upload and hard-reload (`Ctrl+Shift+R`) without a container restart if deploying separately.
No database migrations needed. Backward compatible with all prior job archive records (`retain_task` defaults to False for old records via `.get('retain_task')`).

---

## v0.7.5 (2026-05-22)

### Changes

**Raised Standard Photo Limit to 150**
- Default per-job limit increased from 100 to 150 photos
- High-capacity mode limit unchanged at 300

**Simplified High-Capacity Toggle (single toggle, no confirmation step)**
- Removed the two-step opt-in (toggle + confirmation checkbox) introduced in v0.7.3
- High-capacity mode is now a single toggle; enabling it immediately raises the limit from 150 to 300
- A one-line amber warning appears below the toggle when high-capacity is enabled: "Processing 300 images may impact concurrent sUAS video streams and will significantly extend job runtime."
- `onCapacityConfirmed()` JS function removed; `getEffectiveLimit()` and `isHighCapacityActive()` simplified accordingly
- Removed `.capacity-warning`, `.capacity-warning__title`, `.capacity-warning__body`, and `.capacity-toggle--confirm` CSS classes from use (retained in stylesheet to avoid breaking any external references)

### Technical Changes

**api.py**
- `MAX_PHOTOS` constant raised from 100 to 150
- Comment updated to reflect single-toggle UI
- Version bumped to v0.7.5

**app.html**
- `MAX_PHOTOS_DEFAULT` JS variable updated to 150
- Drop zone hint updated: "max 150 photos"
- High-capacity toggle block simplified: warning panel and confirmation toggle replaced with inline one-liner
- `onHighCapacityToggled()` now shows/hides the one-liner (`capacity-warning-line`) only
- Field Guide "Limit" callout updated to reference 150 as the default
- Version comment bumped to v0.7.5

**plugin.py / manifest.json**
- Version bumped to 0.7.5

### Deployment

```bash
cd ~/WebODM/coreplugins
tar -xzf tak_incident_overlay_v0_7_5.tar.gz
cd ~/WebODM
docker compose restart webapp worker
```

No database migrations needed. Backward compatible with all prior job archive records.

---

## v0.7.4 (2026-05-22)

### Bug Fixes

**node-status Endpoint — Direct Probe (v0.7.2 intent now correctly implemented)**

The `node-status` endpoint in v0.7.2–v0.7.3 described a direct HTTP health probe in its CHANGELOG entry but was never actually implemented that way. The live code used `node.is_online()` (WebODM's cached heartbeat, ~2-minute lag) and retained the invalid `.filter(enabled=True)` query that was also described as removed in v0.7.2.

v0.7.4 corrects both issues:

- **Removed** `ProcessingNode.objects.filter(enabled=True)` — `ProcessingNode` has no `enabled` field in WebODM 3.2.2; this query would raise `FieldError: Cannot resolve keyword 'enabled'` on some installs. Query is now simply `.order_by('id').first()`.
- **Replaced** `node.is_online()` with a direct `GET http://<hostname>:<port>/info` probe using `requests` with a 2-second timeout. Node is considered online if the response is HTTP 200; any other response or connection error is treated as offline.
- **Hostname and port** are read dynamically from the `ProcessingNode` DB record — no hardcoded values.
- **Detection lag** reduced from ~2 minutes (WebODM heartbeat timeout) to ~2–5 seconds (direct probe round-trip).

### Technical Changes

**api.py**
- `node_status_view()`: removed `.filter(enabled=True)`, replaced `node.is_online()` with `requests.get('http://{hostname}:{port}/info', timeout=2)`, added `log.debug` probe result line
- Docstring updated to accurately describe the direct probe behavior
- Version bumped to v0.7.4

**plugin.py / manifest.json**
- Version bumped to 0.7.4

### Deployment

```bash
cd ~/WebODM/coreplugins
tar -xzf tak_incident_overlay_v0_7_4.tar.gz
cd ~/WebODM
docker compose restart webapp worker
```

No database migrations needed. No settings changes needed. Backward compatible with all prior job archive records.

---

## v0.7.3 (2026-05-05)

### New Features

**High-Capacity Mode (Operator-Acknowledged)**
- New toggle below the photo picker allows raising the per-job limit from 100 to 300 photos
- Two-step opt-in to prevent accidental activation:
  1. Operator enables "High-capacity mode (up to 300 images)"
  2. Warning panel appears explaining operational impact on concurrent sUAS video streams
  3. Operator must check "I understand the operational impact" to actually unlock the higher limit
- The "max N photos" hint under the photo picker dynamically updates between 100 and 300 based on toggle state
- Selections exceeding the active limit are cleared with a flash message; operator is told they can enable high-capacity mode to allow more
- Disabling either toggle reverts the limit to 100 immediately and clears any over-limit selection

**Backend Enforcement**
- Upload endpoint accepts a `high_capacity=true|false` form field
- Backend re-validates count against either `MAX_PHOTOS` (100) or `MAX_PHOTOS_HIGH` (300) — frontend trust is for UX only
- High-capacity activations are logged at INFO level: `TAK Overlay: high-capacity mode ENABLED for upload — N photos, incident="..."`
- Out-of-bounds requests return descriptive error messages including the active limit

### Technical Changes

**api.py**
- Added `MAX_PHOTOS_HIGH = 300` constant
- `upload_view()` parses `high_capacity` from request POST data
- Effective per-job limit computed dynamically: `MAX_PHOTOS_HIGH if high_capacity else MAX_PHOTOS`
- Distinct error messages for the two limit cases (default vs high-capacity)
- INFO log entry whenever high-capacity mode is used

**templates/app.html**
- New `.capacity-block`, `.capacity-toggle`, `.capacity-warning` CSS classes
- iOS-style toggle switches matching the dark infra-TAK aesthetic (green when active)
- Amber-bordered warning panel with operational impact text
- Confirmation toggle inside the warning panel, only relevant when first toggle is on
- New JavaScript helpers: `getEffectiveLimit()`, `isHighCapacityActive()`, `onHighCapacityToggled()`, `onCapacityConfirmed()`, `revalidateSelection()`
- `onFilesSelected()` now uses `getEffectiveLimit()` instead of hardcoded 100
- `updateDropZone()` renders dynamic max-photos hint
- `onProcessClick()` appends `high_capacity` flag to FormData
- Field Guide "Limit" callout updated to mention high-capacity mode

**plugin.py / manifest.json**
- Version bumped to 0.7.3

### Operational Notes

- The 10–15 minute estimate in the warning text assumes ~70 photos per minute throughput; actual time scales with image content complexity
- Logs make high-capacity usage auditable after the fact — useful for post-incident review

---

## v0.7.2 (2026-05-03)

### New Features

**Node Status Indicator (Real-Time)**
- Added colored status dot in the page header (upper right) showing processing node online/offline state
- Dot color: green = online, red = offline
- Label text: "node-odx-1 · online" or "node-odx-1 · offline"
- Polls every 5 seconds for near-real-time feedback
- Uses direct health probe (`GET http://node-odx-1:3000/info`) instead of WebODM's cached heartbeat
  - Result: ~2-5 second detection lag instead of 2-minute lag
  - Immunity to WebODM's internal heartbeat timeout

**Discrete Phase Tracking**
- Job processing now displays explicit phase labels instead of a generic "Standby" message
- Phase label appears below the Standby title during processing
- Phases: **Queued** → **Processing** → **Finalizing** → **Reprojecting** → **Exporting GeoTIFF** → **Building MBTiles** → **Building Overviews**
- Each phase corresponds to a pipeline transition, giving operators visibility into what's happening
- Phase label cleared on job completion/cancellation/failure

### Technical Changes

**archive.py**
- Added `phase` field to job record schema (initialized to `'Queued'`)
- `update_job()` accepts arbitrary kwargs, so phase updates work without schema migrations

**pipeline.py**
- Seven `archive.update_job(job_id, phase='...')` calls inserted at key transitions
  - After WebODM task creation: `phase='Queued'`
  - First time ODM status becomes RUNNING: `phase='Processing'`
  - ODM status 40 (completed): `phase='Finalizing'`
  - Before gdalwarp: `phase='Reprojecting'`
  - Before gdal_translate (GeoTIFF): `phase='Exporting GeoTIFF'`
  - Before gdal_translate (MBTiles): `phase='Building MBTiles'`
  - Before gdaladdo: `phase='Building Overviews'`

**api.py**
- `status_view()` now includes `phase` field in JSON response
- New endpoint: `GET /plugins/tak_incident_overlay/node-status/`
  - Returns: `{"ok": true, "online": true/false, "name": "node-odx-1"}`
  - Directly probes node's `/info` endpoint (port 3000) with 2-second timeout
  - No dependency on WebODM's ProcessingNode heartbeat
  - Requires: `import requests` (already in Django/WebODM stack)

**plugin.py**
- Version bumped to 0.7.2
- Registered new `node-status/` mount point

**manifest.json**
- Version bumped to 0.7.2

**app.html**
- Added `.node-status`, `.node-dot`, `.node-label` CSS classes for styling
- Added `.phase-label` CSS class for the phase text display
- Node status dot and label added to header (flexbox layout with "powered by webODM" text)
- Phase label `<div>` added inside the Standby panel, hidden until phase updates arrive
- JavaScript `checkNodeStatus()` function fetches `/node-status/` and updates dot color + label text
- JavaScript `updatePhaseLabel(phase)` function displays/hides the phase text
- `pollStatus()` updated to call `updatePhaseLabel(data.phase)` on each poll
- `resetToIdle()` calls `updatePhaseLabel('')` to clear phase on job completion
- Node status polling: `setInterval(checkNodeStatus, 5000)` — every 5 seconds
- Version comment bumped to v0.7.2

### Bug Fixes

- **Fixed ProcessingNode query:** Removed invalid `.filter(enabled=True)` that was causing "Cannot resolve keyword 'enabled'" errors on startup
  - WebODM's ProcessingNode model doesn't have an `enabled` field
  - Query now simple: `ProcessingNode.objects.order_by('id').first()`

### Testing Notes

- Node status indicator updates within 2–5 seconds of a container stop/start
- Phase labels advance smoothly through the pipeline during normal operation
- No measurable system load from 5-second polling (12 requests/min, ~1KB payload each)
- Phase tracking works independently of node status — a job can continue processing even if node indicator flickers

### Deployment

Extract tarball and restart containers:
```bash
cd ~/WebODM/coreplugins
tar -xzf tak_incident_overlay_v0_7_2.tar.gz
cd ~/WebODM
docker compose restart webapp worker
```

No database migrations needed. No settings changes needed. Backward compatible with v0.7.x jobs in the archive.

---

## v0.7.1 (2026-04-28)

### Changes vs v0.7.0

**Zoom Range Expansion**
- MBTiles base zoom: 21 (65% outsize in gdal_translate)
- Overview factors: `2 4 8 16 32 64 128 256` (was `2 4 8 16 32`)
- Result: zoom levels 13–21 coverage (was 16–21)
- Operators can now zoom out further without blank tiles

**New GeoTIFF Export (v0.7+)**
- Second deliverable format alongside MBTiles
- 4-band RGBA GeoTIFF in EPSG:4326 (WGS84)
- LZW-compressed, tiled format
- Useful for QGIS, ArcGIS, or TAK server tile workflows
- Separate download button in the UI
- File size: ~45–50 MB for typical 70-photo job

**User-Visible Changes**
- `app.html` rewritten with infra-TAK design system
  - JetBrains Mono for labels/metadata
  - DM Sans for body text
  - Dark-mode-only UI (light/dark toggle in header)
  - Two-column layout (upload/status on left, Field Guide on right)
- Field Guide expanded with flight pattern guidance
  - Standard: lawnmower grid
  - High Detail: double grid rotated 90°
  - Altitude: 60–100m AGL recommended
  - Overlap: 75% frontal, 65% side

**Bug Fixes (v0.7.1 patch)**
- Fixed function name mismatch: `_export_rgb_geotiff` definition matched call site
- Removed invalid `-co ALPHA=YES` from GeoTIFF creation (alpha preserved automatically via gdalwarp -dstalpha)
- User edits to header styling and section labels retained

---

## v0.7.0 (2026-04-27)

### Changes vs v0.6.0

**Photo Limit Increase**
- MAX_PHOTOS: 75 → 100
- Allows larger incident batches without re-submission

**Output Zoom Range**
- Base: zoom 21 (gdal_translate -outsize 50%)
- Overviews: factors `2 4 8 16 32` cover zoom 16–21
- (Expanded to 13–21 in v0.7.1)

**New RGB GeoTIFF Deliverable**
- In addition to MBTiles, plugin now exports a 3-band RGB GeoTIFF
- EPSG:4326 (WGS84), LZW-compressed, tiled
- Separate download endpoint: `download-geotiff/<job_id>/`
- archive.py tracks both `file_size_bytes` and `geotiff_size_bytes`

**UI Redesign**
- Infra-TAK design system implemented
- Two-column layout
- Field Guide section with three subsections:
  1. Image Capture (altitude, overlap, flight patterns, platforms, lighting)
  2. Upload & Process (workflow steps, runtime expectations)
  3. Import to CloudTAK (both file formats, app compatibility, purge note)
- Dark theme default with light/dark toggle

**archive.py Enhancements**
- Added `geotiff_filename` field to job schema
- `get_geotiff_path(job)` helper function
- `mark_completed()` accepts optional `geotiff_path` parameter
- Backward compatible with v0.6 jobs (v0.6 jobs return None for geotiff_path)

---

## v0.6.0 (2026-04-15)

### Initial Production Release

**Core Features**
- WebODM coreplugin for converting drone photos to MBTiles overlay
- Async Celery pipeline: upload → WebODM resize (2048px) → ODM processing → GDAL export
- MBTiles output format with zoom levels 16–21
- CloudTAK import support (Overlays → Raster)
- 72-hour auto-purge of completed jobs

**WebODM Integration**
- Task options: `auto-boundary:true`, `fast-orthophoto:true`
- ~3–5 minute end-to-end runtime for 30–70 photos
- Resize mechanism: images capped at 2048px longest side (Pillow LANCZOS, EXIF preserved)
- Processing node auto-assignment

**GDAL Pipeline**
- gdalwarp: reproject to EPSG:4326 (WGS84) with -dstalpha
- gdal_translate: convert to MBTiles with PNG tiles (alpha preserved)
- gdaladdo: build zoom pyramid (factors 2 4 8 16 32)

**File Outputs**
- Single MBTiles file per job (~41 MB for typical incident)
- Stored at `<MEDIA_ROOT>/tak_incident_overlay/<sanitized_name>.mbtiles`
- Metadata: `type=overlay` (CloudTAK auto-detects as raster overlay)

**UI**
- Minimal single-page interface
- Sections: Upload, Status, Downloads, Field Guide
- Operator-friendly error messages
- 100-photo limit per job
- GPS EXIF validation on each photo

**Archive & Lifecycle**
- JSON index: `index.json` with all job records
- Working directories: `working/<job_id>/` for staging
- Auto-purge: jobs older than 72 hours deleted with files
- Graceful cleanup on cancel/fail

---

## Known Limitations

- **Node heartbeat lag (WebODM 3.2.2):** ProcessingNode.is_online() has ~2-minute timeout. v0.7.2 works around this with direct health probes.
- **Fast-orthophoto artifacts:** Vertical surfaces (walls, ridges) show segmentation due to 2.5D surface. Acceptable for geolocation use; consider Quality preset for damage assessment.
- **Single operator:** Process button disabled during job run; multi-operator simultaneous use undefined.
- **No job timeout:** If NodeODX hangs, pipeline continues indefinitely. Recommend 4-hour cap in future version.
- **Alaska-tuned pixel size:** GDAL `-tr 0.000000449` calibrated for ~61°N. Multi-region deployments should compute dynamically.

---

## Future Candidates

**v0.8 (near-term)**
- Job timeout (~4 hours)
- Auto-push to CloudTAK (direct import API)
- Dynamic `-tr` calculation based on centroid latitude

**v2.0 (medium-term)**
- Pre-upload image resize (normalize runtime across sensors)
- Quality preset toggle (no fast-orthophoto, real MVS, slower but better vertical detail)
- GPU acceleration (CUDA-enabled NodeODX)
- GSD table (sensor-specific estimates)
- COG output (Cloud-Optimized GeoTIFF for tile servers)
