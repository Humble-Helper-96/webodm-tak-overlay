# Changelog

All notable changes to this plugin are documented here. Dates are local to
the Municipality of Anchorage OEM (UTC−8/−9). Version numbers follow the
informal `MAJOR.MINOR.PATCH` scheme; until 1.0 the MINOR/PATCH split is
loose.

---

## [0.7.10] — 2026-06-03

### Changed
- **Processing threads option restricted to {2, 4, 6}** (was {2, 4, 6, 8}).
  Default remains 4. The 8-thread option was removed for hardware safety on
  smaller processing nodes where 8 threads can exceed available cores and
  cause queue stalls. The backend re-validates regardless of frontend state,
  so any spoofed value falls back to 4.
- **Runtime estimates refreshed** against final reference-hardware
  measurements (Lenovo M920q with Intel i5-8500, 4 threads, 65-photo
  Sutwick dataset): defaults ~3 min, High-Resolution mode ~10 min, Terrain
  correction ~35 min, both modes combined ~42 min. Previous estimates were
  noticeably more optimistic for the Terrain-heavy modes; the new numbers
  reflect how the i5-8500 actually handles dense MVS workloads.
- **Field Guide shortened.** Section 1 paragraphs consolidated, Section 2
  toggle descriptions collapsed into a compact bullet list, Section 3
  prose tightened. Removed the "Not recommended during active sUAS live
  streams" warnings from the High-Resolution and Terrain correction
  toggles so the plugin reads as broadly applicable rather than tied to
  one operational context. Same removal applied in the field guide.

### Fixed
- **Filename sanitization at download time.** `api._safe_filename` previously
  allowed spaces through its whitelist as a defensive filter. `archive._sanitize_filename`
  was the only place spaces actually got replaced with underscores, which left
  any edge-case record (older index entries, manual edits, paths that bypassed
  the create_job sanitizer) downloading with spaces in the filename. The
  download path now mirrors the archive sanitizer — spaces become underscores
  unconditionally.
- **Field guide cleanup:** typo fixes ("estabish" → "establish"), a malformed
  `<p>` tag closed properly, indentation normalized.
- **Section 3 title broadened** from "Import to CloudTAK" to "Import to TAK",
  with an explicit compatibility note covering both CloudTAK and TAKAware.
- **Stale "Both files" reference** in the 72-hour purge callout corrected to
  "The GeoTIFF" (was inherited from the pre-v0.7.8 MBTiles+GeoTIFF era).
- **Awkward "Section 3" self-reference** in the import instructions rewritten
  to refer to the Downloads section by name.
- **Stale "4-band RGBA" wording** in the `download_geotiff_view` docstring
  updated to reflect the v0.7.9 JPEG-compressed 3-band RGB + internal-mask
  output.

### Added
- **New Field Guide bullet documenting Processing threads** — how many
  cores to choose under what host workload conditions.
- **Terminal-style bullet list styling** (`.guide-bullets`) for the toggle
  reference in Section 2.

---

## [0.7.9] — 2026-06-02

### Changed
- **GeoTIFF compression switched from lossless LZW to JPEG (quality 85,
  YCBCR).** Band 4 alpha is now preserved via a GeoTIFF internal 1-bit
  mask (`-mask 4` plus `--config GDAL_TIFF_INTERNAL_MASK YES`). Output
  files dropped ~97–99% in practice — defaults went from 250 MB to 6 MB on
  the 65-photo Sutwick reference dataset, and the worst-case both-modes
  output went from 1.2 GB to 9 MB. **Trade-off:** output is now lossy
  (imperceptible at TAK viewing zooms but not pixel-exact; not suitable as
  forensic evidence).
- **Runtime accounting baseline changed.** Times documented from v0.7.9
  onward measure the full plugin pipeline (queue → archive write),
  including the GDAL post-processing steps. Earlier ad-hoc comparisons
  used the WebODM task UI's "Processing Time" which only counts the ODM
  step itself. The shift in reported defaults runtime (3 min → 5 min on
  the same dataset) reflects this accounting change, not a regression.

### Verified
- Output GeoTIFFs confirmed to import cleanly into both **CloudTAK** and
  **TAKAware**.

---

## [0.7.8] — 2026-06-02

### Added
- **High-Resolution mode toggle** (UI label; internal name `quality_mode`).
  Raises image resize from 2048 px to 4000 px (native DJI Mini 2 sensor)
  and pins orthophoto-resolution to 2.5 cm/px. Visibly finer ground detail
  at roughly 2× runtime.
- **Terrain correction toggle.** Disables fast-orthophoto so ODM runs the
  full Structure-from-Motion pipeline (dense MVS + textured mesh +
  orthorectification). Corrects geometric error from varied terrain and
  tall vertical features. ~3× runtime cost.
- **Processing threads radio group** — operator-selectable CPU thread
  count for ODM processing. Initial allowed values: {2, 4, 6, 8} (8 later
  removed in v0.7.10). Default 4. Backend validates regardless of
  frontend state.

### Changed
- **Quality mode repurposed.** Through v0.7.7, the "Quality mode" toggle
  disabled fast-orthophoto. In v0.7.8 it was rebranded "High-Resolution
  mode" and switched to controlling resize + GSD instead. The fast-
  orthophoto behavior moved to the new Terrain correction toggle.
- **UI toggle order reorganized** to group processing-quality options:
  High-capacity → High-Resolution mode → Terrain correction → Processing
  threads → Save WebODM task.
- **MBTiles output removed.** GeoTIFF is now the sole deliverable. Pipeline
  phase list shortened to Queued → Processing → Finalizing → Reprojecting
  → Exporting GeoTIFF. Legacy MBTiles cleanup logic retained in `archive.py`
  so pre-v0.7.8 jobs are still purged properly.

### Fixed
- **`showFlash()` bug** where error flashes were never actually visible.
  Function was setting `el.style.display = ''` which fell back to the CSS
  rule `.flash { display: none }`. Changed to `display = 'block'`. This
  fixed the silent failure when operators selected more than 150 photos
  without enabling High-capacity mode.

---

## [0.7.7] — 2026-04-29

### Added
- **Quality mode toggle** (later repurposed in v0.7.8). At this stage it
  disabled fast-orthophoto, running the full SfM pipeline for higher-
  quality output.

---

## [0.7.6]

### Added
- **Save WebODM task toggle.** When enabled, the WebODM project is not
  auto-deleted at job completion. It remains accessible in WebODM until
  the 72-hour purge job cleans up the corresponding archive record. A
  direct link to the WebODM task appears in the archive row.

---

## [0.7.5]

### Added
- **High-capacity mode toggle.** Raises the per-job photo limit from 150
  to 300.

---

## [0.7.2]

### Added
- **Discrete phase labels** during processing. Plugin now reports
  intermediate states (Reprojecting, Exporting GeoTIFF, etc.) via
  `archive.update_job(phase=...)` rather than a single "Standby" message.
- **Processing node status indicator** in the header. Real-time online/
  offline state via a node health probe.

---

## [0.7.1]

### Added
- **GeoTIFF output** (4-band RGBA in EPSG:4326) alongside the existing
  MBTiles. Useful for QGIS/ArcGIS/TAK server tile workflows. Initially
  LZW-compressed; switched to JPEG in v0.7.9.

### Changed
- **MBTiles zoom range widened** from 15–21 to 13–21 via expanded
  gdaladdo overview factors.

---

## [0.7.0]

### Changed
- Initial v0.7 line — substantial reworking of the GDAL pipeline and job
  record format. Job records from v0.6.x or earlier are not readable by
  v0.7.x.

---

## [0.6.0]

### Changed
- **TASK_OPTIONS simplified** to `auto-boundary:true` and
  `fast-orthophoto:true` only, with all other options left at NodeODX
  defaults. Replaced the over-specified v0.5.1 preset that had been
  triggering validation errors.
- **WebODM-native image resize introduced** (`resize_to=2048`,
  `pending_action=RESIZE`). Resize acts as a natural gate between image
  staging and ODM dispatch, resolving a long-standing race condition where
  `process_task` could fire before images were fully staged.

---

## [0.5.1]

### Notes
- Last release before the v0.6.0 simplification. Used an over-specified
  TASK_OPTIONS preset (`skip-3dmodel`, `skip-report`,
  `orthophoto-resolution:5`, `feature-quality:ultra`,
  `min-num-features:20000`) which produced validation errors on some
  datasets.

---
