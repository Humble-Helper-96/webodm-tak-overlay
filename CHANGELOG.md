# TAK Incident Overlay — Changelog

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
