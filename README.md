# TAK Incident Overlay — WebODM Plugin
**v0.6.0 | WebODM Coreplugin**

Converts GPS-tagged drone photos into a georeferenced MBTiles raster overlay 
ready for direct import into CloudTAK, ATAK, iTAK, or WinTAK.

## Features
- Upload up to 75 drone photos via browser
- Automatic GPS validation and image resize
- ODM processing with fast-orthophoto pipeline
- GDAL export to MBTiles with zoom pyramid
- Auto-purge of files older than 72 hours

## Requirements
- WebODM 2.9.4 or later
- Docker and Docker Compose
- A configured processing node (NodeODX recommended)

## Installation
See [DEPLOYMENT.md](DEPLOYMENT.md) for full installation instructions.
