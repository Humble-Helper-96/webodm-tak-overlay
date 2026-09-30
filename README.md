# TAK Incident Overlay
**v0.7.13 | WebODM Coreplugin**

> ⚠️ **This is a plugin for [WebODM](https://github.com/OpenDroneMap/WebODM),
> not a standalone application.** WebODM must be installed and running before
> this plugin can be used. See Requirements below.

Converts GPS-tagged drone photos into a georeferenced **GeoTIFF** overlay
ready for import into TAK clients. Designed for on-scene personnel with no
WebODM experience — provide photos and an incident name, the plugin handles
everything else.

## How It Works

1. Operator uploads GPS-tagged JPEG drone photos via browser
2. Plugin validates format and GPS EXIF data per photo
3. WebODM resizes images and runs ODM photogrammetry processing
4. GDAL pipeline reprojects the orthophoto to WGS84 and exports a JPEG-compressed GeoTIFF with an internal alpha mask
5. Operator downloads the GeoTIFF and imports it into their TAK client
6. Real-time processing status with discrete phase labels and a node health indicator

## Features

**Processing & Output**
- Upload up to 150 GPS-tagged JPEG drone photos per job (300 in High-capacity mode)
- Automatic GPS EXIF validation — bad photos rejected before processing
- WebODM-native image resize (2048 px default, 4000 px in High-Resolution mode) before ODM dispatch
- **Standard pipeline** (fast-orthophoto) — typical end-to-end time ~3 minutes for a 65-photo job on reference hardware (Lenovo M920q, i5-8500)
- **High-Resolution mode** — pins orthophoto to 2.5 cm/px GSD with native-resolution input; ~10 min for 65 photos
- **Terrain correction mode** — runs full Structure-from-Motion pipeline for geometric accuracy over varied terrain; ~35 min for 65 photos (~42 min combined with High-Resolution)
- Up to three concurrent jobs — the processing node queues automatically
- Single deliverable per job: **GeoTIFF** (3-band RGB, JPEG-compressed at quality 85, internal alpha mask, WGS84/EPSG:4326)
- Output files are small (~6–10 MB for a 65-photo job regardless of mode) due to JPEG compression
- Local timezone stamping on output filenames
- Auto-purge of completed jobs after 72 hours

**Job Controls**
- **High-capacity mode** — raises photo limit from 150 to 300 per job
- **High-Resolution mode** — 4000 px image resize + 2.5 cm/px orthophoto resolution; visibly finer ground detail at ~2× standard runtime
- **Terrain correction** — disables fast-orthophoto so ODM runs the full SfM pipeline (dense MVS + textured mesh + orthorectification); corrects geometric error from terrain variation and tall vertical features
- **Save WebODM task** — retains the WebODM project after pipeline completes for inspection; direct link appears in the archive row; purged at 72 hours with the job record

**User Experience**
- Real-time processing status with discrete phase labels:
  Queued → Processing → Finalizing → Reprojecting → Exporting GeoTIFF
- Processing node online/offline indicator in header with real-time health probe
- Single-page UI with infra-TAK design language (dark only)
- Field Guide with image capture guidance, upload workflow, and TAK client import instructions

## Compatibility

Tested against WebODM 3.2.2 with NodeODX (ODM 3.7.4).
Output verified to import cleanly into both **CloudTAK** and **TAKAware**.
Other TAK clients that support GeoTIFF raster overlays should work
but have not been formally tested.

## Requirements

This plugin requires a working WebODM installation. It is not a standalone app.

- **[WebODM](https://github.com/OpenDroneMap/WebODM)** 3.2.2 or later —
  installed and running via Docker
- **Docker and Docker Compose** — required by WebODM
- **A processing node** — NodeODX recommended, must be configured and online
  before jobs can run

## Installation

See [DEPLOYMENT.md](DEPLOYMENT.md) for full step-by-step installation
instructions including volume mount configuration and upload size settings.

For offline installs, download the `.tar.gz` from the
[Releases](../../releases) page.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for detailed version history and technical changes.
