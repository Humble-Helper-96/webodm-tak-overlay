# Changelog

All notable changes to this plugin are documented here. Dates are local to
the Municipality of Anchorage OEM (UTC−8/−9). Version numbers follow the
informal `MAJOR.MINOR.PATCH` scheme; until 1.0 the MINOR/PATCH split is
loose.

---

## [0.8.1] — 2026-09-30

### Added
- **Settings window** (`?settings=1`). Pop-out window with three groups: Display (units, time format), Job defaults (high-res on by default, save WebODM task on by default), and System (auto-purge retention, processing thread percentage — admin only). Falls back to modal when pop-ups are blocked.
- **Per-user settings** stored on the server in `settings.json`, keyed by WebODM username. Settings follow the operator to any computer.
- **Global settings** for retention (24h/48h/72h/7d/30d) and thread percentage (25%/50%/75%), admin-only.
- **Settings API**: `GET settings/` returns global + user settings; `POST settings/` saves them with staff check on global changes.
- **Imperial units support** in the unit formatter. All lengths, heights, areas, and GSD values convert when the unit setting changes.
- **Live unit placeholder refresh** — when settings change, all `data-len` placeholders in both windows update without reload.
- **SETTINGS link** in the header nav, visible from v0.8.1.
- **Default toggles from settings** — high-res and save-task toggles initialize from per-user defaults.

### Changed
- **Thread percentage** read from `settings.json` instead of the `CONCURRENCY_PERCENT` constant in `pipeline.py`. Admin-configurable without code change.
- **Retention** read from `settings.json` instead of hardcoded 72h. Applies to all jobs including existing ones.
- **Log lines fixed**: `start()` no longer says "max_concurrency=3 — fixed, cpuset-pinned"; `_run_pipeline` says "Node reports N CPU threads" instead of "cores"; internal names renamed (`_get_node_cpu_cores` → `_get_node_cpu_threads`).
- **plugin.py** passes current user's settings into the template so the first render uses them.

---

## [0.8.0] — 2026-09-30

### Added
- **Complete GUI rework** following the infra-TAK UI design system. New layout with header metrics, main pane (form + Quick Reference), and right sidebar (job list grouped by day + selected job details panel).
- **Field Guide pop-out window** (`?guide=1`). Six sections: Image Capture, Terrain and Altitude, Upload and Process, Job Options, Import to TAK, Troubleshooting. Opens from ⓘ buttons beside options. Falls back to a modal when pop-ups are blocked.
- **Quick Reference card** on the main pane with altitude, base, overlap, photos, GPS, limit, and runtime guidance.
- **7-segment phase track** (PREPARE, UPLOAD, QUEUED, PROCESSING, FINALIZE, REPROJECT, EXPORT) with color-coded done/active/pending states.
- **Accent corner brackets** framing the progress panel while a job runs.
- **Node metrics in header**: NODE, QUEUE, CPU THREADS, MEM with threshold colors.
- **Job list grouped by day** in the sidebar with status dots, tag lines, and SELECTED/ACTIVE badges.
- **Selected job details panel** at the bottom of the sidebar with name, file, mode, status, size, created time, and action buttons.
- **Unit formatter** (`Units` object) with metric as the default. All guide text and UI hints route through formatter functions.
- **Design tokens** updated to match the infra-TAK design system spec verbatim.

### Changed
- **Process button** changed from filled green to outline style, fills on hover.
- **Font Awesome icons replaced** with Unicode characters (↓ ✕ ▶ ⧉ ⓘ).
- **Switch component** redesigned to 30×16 px square (3px radius) per spec.
- **Token names renamed** to match design system (`--accent-amber` → `--amber`, `--text-muted` → `--text-dim`, etc.).
- **Bullet lists in UI** converted to monospace rows.
- **No inline `style="color: …"`** for static values — all use CSS classes.
- **Scrollbars styled** per spec; minimum font size 9px; corners 3–4px.
- **Guide text rewritten** following ASD-STE100 rules: commands, one instruction per sentence, 20 words or fewer per instruction, active voice, one word for one meaning.

---

## [0.7.13] — 2026-07-19

### Fixed
- **`__init__.py` was empty**, so WebODM's plugin loader could never find the
  `Plugin` class on the package (`module 'coreplugins.tak_incident_overlay'
  has no attribute 'Plugin'`). This predates the 0.7.x line's tracked
  history — the plugin had likely never registered successfully on this
  deployment. Fixed to `from .plugin import Plugin`.

### Changed
- **`max-concurrency` is now computed dynamically instead of hardcoded.**
  The previous fixed value of 3 was calibrated for a specific host's
  cpuset pinning (cores 3,4,5) and silently under- or over-committed on
  any other machine. The pipeline now probes the primary ProcessingNode's
  `/info` endpoint for its reported `cpuCores` (same lookup pattern
  `api.node_status_view` already used for the header's online/offline
  indicator) and requests 50% of that (`CONCURRENCY_PERCENT`), rounded,
  minimum 1. Falls back to the old fixed value of 3 if the node is
  unreachable or doesn't report `cpuCores` (older NodeODM versions may
  omit the field) — never blocks a job on this lookup failing.

---

## [0.7.12] — 2026-07-19

### Added
- **Submitting operator granted visibility on saved WebODM projects.**
  The pipeline creates each WebODM project owned by the first superuser
  account, which made saved tasks (Save WebODM Task toggle) invisible to
  the operator who submitted the job — the archive row's WebODM deep link
  silently fell back to the bare dashboard. The submitting user's name is
  now threaded from `upload_view` through the Celery boundary (as a plain
  string), and after project creation the pipeline grants that user
  object-level `view_project` via django-guardian. Non-fatal by design: a
  failed grant logs a warning and never kills the job. Applies to jobs
  created from this version onward.
- **Live processing progress bar.** `status_view` has returned
  `webodm_progress` and `webodm_stage` since v0.7.2; the UI never rendered
  them. The standby panel now shows a green progress bar with the ODM
  stage label ("Feature extraction", "Densification", ...) during
  processing, falling back to the pipeline phase label during GDAL steps.
- **Running-job restore on page reload.** Reloading the page mid-job
  previously lost the active job — UI showed "Idle" while the job kept
  running server-side, with no progress display and no cancel path. The
  UI now checks for a running job at page load and resumes the locked
  form, status panel, and polling. Deliberately runs once at load (not in
  the 60 s archive refresh) so a job submitted from another machine can't
  hijack an idle operator's form mid-session.
- **Failed and cancelled jobs shown in the archive.** Previously only
  completed jobs rendered, so a job that failed while the operator was
  away left no visible trace. Failed rows are dimmed with a red Failed
  chip (error text in the tooltip) and a delete button; cancelled rows
  get a neutral chip. No download/WebODM actions on non-completed rows.
- **Pipeline watchdog.** Hard 3-hour ceiling on total pipeline runtime.
  A task that never reaches a terminal state (node death missed by
  WebODM's heartbeat, DB hiccup) previously pinned a Celery worker
  forever and silently consumed one of the three job slots; it now fails
  with an operator-readable timeout message.

### Fixed
- **Cancelled jobs no longer reported as failed.** The poll loop treated
  `TASK_CANCELLED` identically to `TASK_FAILED`, raising into the generic
  failure handler which stomped the archive's `cancelled` status with
  `failed` ("WebODM task ended with status 50"). Cancellation now exits
  the pipeline cleanly. Bonus: a task cancelled directly in WebODM (not
  via the plugin UI) now marks the plugin job record cancelled instead of
  leaving it "running" forever.
- **Output filename collisions.** `display_name` is minute-precise, so
  two jobs with the same incident name in the same minute (a quick retry)
  shared a `geotiff_filename` — the second job's output overwrote the
  first's, and deleting either job removed the shared file from under the
  surviving record. Filenames now carry a `_<job_id[:8]>` suffix; the
  operator-facing display name is unchanged.
- **Duplicate photo names within a job.** Photos were saved under their
  original names, so merging two SD cards with overlapping names
  (DJI_0001.JPG twice) silently overwrote — the job processed fewer
  images than selected, with no error. Saved images are now prefixed
  with a 4-digit upload index.
- **Partial output cleanup.** If the GDAL export died mid-write, a
  truncated .tif remained in the archive directory under the final
  filename until purge. Failure handlers now remove it.

### Changed
- **Processing threads selection removed.** The 2/4/6 radio group is
  gone from the UI and API; `max-concurrency` is fixed at 3, matching
  the NodeODX cpuset (cores 3,4,5).
- **CSRF protection enabled on POST endpoints.** `@csrf_exempt` removed
  from upload, cancel, and delete. The frontend has sent `X-CSRFToken`
  on all three since they were written, so the decorator was disabling a
  protection that was already paid for.
- **Version strings unified.** manifest.json, plugin.py (header render +
  ping endpoint), and all module headers now agree on 0.7.12.

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
