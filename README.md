# TAK Incident Overlay
**v0.6.0 | WebODM Coreplugin**

> ⚠️ **This is a plugin for [WebODM](https://github.com/OpenDroneMap/WebODM), 
> not a standalone application.** WebODM must be installed and running before 
> this plugin can be used. See Requirements below.

Converts GPS-tagged drone photos into a georeferenced MBTiles raster overlay
ready for import into TAK clients. Designed for on-scene personnel with no
WebODM experience — provide photos and an incident name, the plugin handles
everything else.

## How It Works

1. Operator uploads GPS-tagged JPEG drone photos via browser
2. Plugin validates format and GPS EXIF data per photo
3. WebODM resizes images and runs ODM with fast-orthophoto processing
4. GDAL pipeline reprojects output and converts to MBTiles with zoom pyramid
5. Operator downloads the `.mbtiles` file and imports into their TAK client

## Features

- Upload up to 75 GPS-tagged JPEG drone photos per job
- Automatic GPS EXIF validation — bad photos are rejected before processing
- WebODM-native image resize to 2048px before ODM dispatch
- Fast-orthophoto pipeline — typical end-to-end time 3–4 minutes
- MBTiles output with zoom levels 16–21, PNG tiles, alpha channel preserved
- Local timezone stamping on output filenames
- Auto-purge of completed jobs after 72 hours
- Single-page UI with light/dark mode

## Requirements

This plugin requires a working WebODM installation. It is not a standalone app.

- **[WebODM](https://github.com/OpenDroneMap/WebODM)** 2.9.4 or later — 
  installed and running via Docker
- **Docker and Docker Compose** — required by WebODM
- **A processing node** — NodeODX recommended, must be configured and online
  before jobs can run

## Installation

See [DEPLOYMENT.md](DEPLOYMENT.md) for full step-by-step installation 
instructions including volume mount configuration and upload size settings.

For offline installs, download the `.tar.gz` from the 
[Releases](../../releases) page.

## Compatibility

Tested against WebODM 3.2.2 and 3.2.3 with NodeODX engine.
Output verified for import into ATAK and CloudTAK.
Other TAK clients that support MBTiles raster overlays should work
but have not been formally tested.
