# Changelog

All notable changes to this plugin are documented here. Dates are local to
the Municipality of Anchorage OEM (UTC−8/−9). Version numbers follow the
informal `MAJOR.MINOR.PATCH` scheme; until 1.0 the MINOR/PATCH split is
loose.

---

## [0.8.9] — 2026-10-06

### Changed
- **Softened the Terrain Correction running-state banner** from "Do not start live video streams on this system until this job completes" to "Live video streaming on this system may be impacted until this job completes." Reason: on the reference host, CPU is isolated between the processing node (cpuset 2–5) and MediaMTX (cores 0–1), verified under load — streams are not actually blocked by a terrain correction job. Terrain correction's dense reconstruction does share the GPU with MediaMTX's NVENC transcoding (same card, both use CUDA/NVENC), plus memory and disk I/O, so degradation is possible — but a flat "do not start" was a prohibition the evidence didn't support. The banner now informs rather than prohibits. No other wording elsewhere in the plugin, guide content, or docs told operators not to stream during terrain correction (a prior removal is referenced in the v0.7.13 CHANGELOG entry and a pipeline.py comment, but that text no longer exists in the running UI).
- **Removed the standalone High-Resolution toggle and folded it into Terrain Correction.** Side-by-side runs showed little visible image-quality difference between Standard and High-Resolution mode, so the second control wasn't earning its place in the UI. Terrain Correction now always runs at the former High-Resolution target (4000 px resize, 2.5 cm/px orthophoto-resolution) — its own runtime (~35 min) already dwarfs the ~7 min High-Resolution used to add, so bundling it costs nothing extra in practice. `api.upload_view` forces `quality_mode` on whenever `terrain_correction` is True server-side, regardless of what a client posts, so the two flags can't drift apart even from a stale cached frontend. The "HIGH-RESOLUTION ON BY DEFAULT" per-user setting is removed from Settings and from the settings schema (`archive.py`'s `get_user_settings`/`DEFAULT_SETTINGS`) — existing installs with a stale `highres_default` key in `settings.json` are unaffected, it's simply no longer read.
- **Settings UI: Core Count fields restyled to match the app's dark theme.** The exact-core-count and core-count-ceiling number inputs (added in v0.8.6/v0.8.7) rendered with the browser's default white background and native spinner arrows, clashing with the rest of the dark-themed Settings panel. They now use the same `.input` styling as every other text field in the app, and the invalid-state border/error text color now references the app's actual `--red` theme variable instead of a hardcoded fallback that didn't match it.
- **Removed the "about 35 min" estimate from the Terrain Correction toggle's main-page hint.** That figure only holds for roughly a 65-photo job and reads as a flat promise next to the toggle regardless of how many photos are actually selected. Runtime estimates belong in the Field Guide, where they're already correctly qualified by photo count (one guide passage wasn't — fixed to say "~35 min for a 65-photo job; scales with photo count" instead of a bare "~35 min").

### Fixed
- **Terrain Correction's (ⓘ) info button opened the wrong guide section.** It pointed at Section 02 "Terrain and Altitude" (flight-planning advice about setting altitude from the target, not the takeoff point) instead of Section 04 "Job Options," which actually documents the toggle. Both the High-Capacity and Save WebODM Task rows already linked to Job Options — Terrain Correction's link was the one outlier, left over from before the toggles were reorganized. Now matches the other two.

### Changed
- **Removed GSD and AREA from the selected-job details panel.** Reported GSD remained visibly inaccurate on real jobs despite the v0.7.13 reprojection-pixel-size fix, and area wasn't actionable information in this panel — both just added noise. The underlying `gsd_cm_per_px`/`area_m2` computation and job-record fields (`pipeline.py`/`archive.py`) are untouched and still populated; only the UI display was removed, along with the now-dead `Units.formatGSD`/`formatArea` helpers.
- **Added a JOB TIME stat** to the same panel for completed jobs: total elapsed time from photo submission (job record creation) through the GeoTIFF being ready for export (job completion), formatted HH:MM:SS to match WebODM's own per-task duration column.

---

## [0.8.8] — 2026-10-05

### Fixed
- **Header badge and `ping` endpoint showed "V0.8.5" on v0.8.6 and v0.8.7.** `plugin.py` hardcoded `'plugin_version': '0.8.5'` (rendered by `templates/app.html` as `V{{ plugin_version }}`) and `JsonResponse({'status': 'ok', 'version': '0.8.5'})` as separate string literals that were never bumped alongside `manifest.json`'s version when 0.8.6 and 0.8.7 shipped, so the installed version and the displayed version silently diverged. Fixed by making `manifest.json` the single source of truth: `plugin.py` now reads `version` from its own `manifest.json` once at import time (path resolved relative to `__file__`, not the working directory) into a module constant, `PLUGIN_VERSION`, and both the template context and the `ping` response use it. Falls back to `'unknown'` — never raises — if the manifest can't be read or parsed, so a missing version can't break page load. Bumping `manifest.json` is now the only per-release step needed to keep the displayed version correct.

---

## [0.8.7] — 2026-10-05

### Fixed
- **v0.8.6's `thread_count` server-side validation was not actually a safety net on a cpuset-restricted node.** It clamped the admin-submitted exact-core-count override against `_probe_node_cpu_cores()` — a direct re-read of the same `/info` `cpuCores` field whose unreliability on a container node (physical host core count, not the cpuset-limited count) motivated v0.8.6 in the first place. In practice this meant an admin could still enter 5 or 6 on a box whose real limit was 4 and have it pass validation cleanly, right back into the overshoot v0.8.6 was meant to fix. The plugin has no way to read the processing node's actual cgroup/cpuset from inside the `webapp` container, so probing harder isn't an option — `/info` reporting physical cores instead of container-limited cores is a NodeODM-side limitation.

### Added
- **`thread_count_ceiling` global setting** (int or null) — a staff-settable trusted max for `thread_count` validation, independent of the raw node probe. When unset, validation and runtime clamping fall back to the probe exactly as in v0.8.6 (no regression for admins who haven't hit this edge case); when set, it's used instead, so an admin who knows the real cpuset limit (e.g. 4 on a 6-physical-core host) can make that limit the one actually enforced. Settings UI: a new "CORE COUNT CEILING" field next to the exact-core-count control, with its own validation and a "use auto-detected" clear action; the exact-count field's client-side max now checks this ceiling instead of the raw node probe when one is set.
- `archive.py`: `get_thread_count_ceiling()`, mirroring the existing `get_thread_count()`/`get_thread_percent()` accessors.
- `api.py`: `settings_view` POST accepts `thread_count_ceiling` in the same `global` body, validated as a positive integer or null. `thread_count` validation now checks this ceiling (falling back to the raw probe when unset) via a new `_effective_thread_ceiling()` helper, and honors a ceiling change submitted in the *same* request as a `thread_count` change rather than validating against the stale on-disk value.
- `pipeline.py`'s `max-concurrency` calculation re-clamps `thread_count` against `thread_count_ceiling` (falling back to the raw node probe) at job-run time, independent of whatever was true when `thread_count` was originally saved — so lowering the ceiling later protects jobs immediately, not just newly-saved settings.

---

## [0.8.6] — 2026-10-05

### Added
- **Exact core-count override for ODM processing concurrency.** Settings → System now has a second, independent control alongside the existing 25/50/75% buttons: a numeric "exact core count" field that, when set, wins outright over the percentage setting for `max-concurrency`. Motivation: percent-based concurrency is computed against the processing node's reported `cpuCores` (NodeODM's `/info` endpoint), but on a node running in a Docker container with a `cpuset` restriction (e.g. `cpuset: "2,3,4,5"`, 4 cores), `/info` reports the *physical host's* core count, not the cpuset-limited count the container can actually use — confirmed on a reference box where `nproc` inside the container returns 4 but `/info` reports 6. A percent setting can therefore compute a thread count that exceeds the container's real CPU limit and risks ODM queue stalls; the previous 50% default happened to be safe on that specific host only by coincidence of the two core counts involved, not by design. Operators who know their node's real usable core count can now just say "use exactly N" instead. Selecting one control visibly deselects the other; a "use percentage instead" action clears the override and reverts to percent-based behavior. `pipeline.py` logs which mode produced the running job's `max-concurrency`.
- `archive.py`: new `thread_count` global setting (int or null) and `get_thread_count()`, additive alongside the existing `thread_percent`/`get_thread_percent()`. A settings.json with no `thread_count` key behaves exactly as v0.8.5 (defaults to percent-based, `thread_percent` default unchanged at 50).
- `api.py`: `settings_view` POST accepts an optional `thread_count` in the same `global` body as `retention_hours`/`thread_percent`. Validated as a positive integer, clamped against the processing node's actual reported `cpuCores` (probed server-side, not trusted from the client) — rejects 0, negative, non-integer, or out-of-range values with a clear error.

### Changed
- `pipeline.py`'s `max-concurrency` calculation now checks `thread_count` first; if set, it's used directly (clamped to the node's reported cores when known) and `thread_percent` is ignored. Falls back to the existing percent-of-reported-cpuCores math when `thread_count` is unset, and to the long-standing fixed value of 3 when neither is available — unchanged from v0.8.5.

---

## [0.8.5] — 2026-10-01

### Fixed
- **Critical: plugin failed silently on load.** `plugin.py`'s `index()` view rendered `user_settings` into the page template with `{{ user_settings|safe }}` — `|safe` only stops Django from HTML-escaping the value, it does not serialize it. A raw Python dict falls back to `str(dict)` (single-quoted strings, capitalized `True`/`False`/`None`), which is valid Python but not valid JavaScript. The browser hit a `ReferenceError` on that line, which killed the rest of the inline `<script>` before anything registered — the page loaded but nothing on it worked (no job list, no node status, no settings), with no errors visible server-side. Fixed by serializing with `json.dumps()`. Diagnosed from a HAR capture of a production instance showing zero API calls after page load.
- **Fixed 23 findings** from a full audit of the v0.8.4 implementation against the v0.8 roadmap and the v0.7.13 baseline, plus 3 additional interaction bugs found during follow-up verification (largest-camera-group selection picking the wrong group, used/unused photo matching breaking on duplicate filenames, GPS data falling out of lockstep with photos across multiple processing jobs).
- **Reprojection pixel size no longer hardcoded.** `pipeline.py`'s WGS84 reprojection step (`gdalwarp -tr`) used a fixed constant (`0.000000449°`), calibrated for one mid-latitude deployment — every job was resampled onto the same output pixel grid regardless of its actual source resolution. This made the job panel's reported GSD effectively constant per deployment latitude, decoupled from WebODM's own "Average GSD" for that task, and most visibly wrong on lower-resolution sensors (e.g. thermal), where the fixed grid silently upsampled the orthophoto and reported a finer GSD than the source data actually had. The reprojection now reads the source orthophoto's real pixel size and centroid latitude via `gdalinfo` and derives `-tr` per job, so output resolution — and the GSD readout — tracks the actual source data.
- **Guide/Settings popup windows flashed the main page.** Opening `?guide=1` or `?settings=1` in a new window briefly showed the full main UI before jumping to the targeted section. Fixed with a synchronous `<head>` script that tags `<html>` with a `popup-guide`/`popup-settings` class before the body parses, paired with CSS that hides `#main-view` for that class.

### Changed
- **Mixed-camera handling redesigned around explicit operator selection**, replacing the automatic "keep largest group" heuristic that could discard the wrong camera's photos (confirmed in the field on an Autel 640T RGB+thermal payload, where the heuristic kept the thermal images and purged the RGB set). Photos are grouped by pixel dimensions + EXIF Make/Model (unchanged primary signal); the operator now sees a checkbox picker and chooses one or more groups to process. Each selected group runs as its own sequential WebODM job — zero backend changes needed, since the existing one-group-per-request upload contract already supported it.
- **Non-JPEG sidecar files** (e.g. thermal radiometric data, flight logs) are now excluded from a photo selection with a notice, instead of blocking the whole upload.
- **Job naming** changed from `Incident Name YYYY-MM-DD HHMM` to `IncidentName_Prefix_YYMMDD` — underscore-joined, platform-agnostic filename prefix in place of full camera+dimensions text, date-only (no time-of-day) in `YYMMDD` form. When two camera groups share an identical EXIF camera string (and therefore no distinguishing prefix), the job name falls back to camera+dimensions to avoid a collision.
- **Photo map moved to a pop-out window**, mirroring the Guide/Settings pop-outs, reclaiming the sidebar space it used to occupy.
- **UI layout**: processing-option toggles moved above the photo selection field; the flight-path preview map was shrunk, then removed entirely in favor of the pop-out photo map; the selected-job details panel is now pinned to a fixed height instead of pushing the job list around; sidebar widened (360px → 420px) and rebalanced between the scrolling job list and the fixed details panel; Start Process button restyled solid green; photo-selection field contrast improved against the dark background.

### Added
- **37-assertion regression suite** (`test_camera_grouping.js`) covering the mixed-camera redesign: all required grouping/selection scenarios, the real-world Autel 640T RGB+thermal pattern, and the shared-camera-string naming collision.

---

## [0.8.4] — 2026-09-30

### Added
- **Post-job quality warning** (Workstream C). After ODM completes, the pipeline reads `odm_report/shots.geojson` to determine which photos were used in the reconstruction. When more than 30% of photos were not used, a warning is shown: "⚠ 82 / 125 photos used — possible low overlap."
- **Used/unused photo map** (Workstream E, part 2). The photo map now shows which photos ODM used (filled green dots) and which were not used (hollow red rings). Shape and color both change for color-blind accessibility.
- **Photo map legend**: "● USED 82", "○ NOT USED 43", "— FLIGHT PATH".
- **Quality warning in job details panel** and in the status flash when a job completes.
- **Used photo count in status API**: `status_view` returns `used_count`, `total_count`, and `quality_warning`.

### Changed
- **pipeline.py** reads `odm_report/shots.geojson` after ODM completes and updates the sidecar file with `used` flags for each photo point.
- **api.py** `status_view` returns the used photo count and quality warning.
- **app.html** `renderJobDetails` shows the quality warning and draws the used/unused photo map for completed jobs.

---

## [0.8.3] — 2026-09-30

### Added
- **Mixed-camera detection** (Workstream B). While preparing photos, the browser reads each photo's pixel size and camera model from EXIF. If there is more than one group, the operator is prompted to keep the largest group or cancel.
- **GPS pre-check** (Workstream B). Photos without GPS EXIF are flagged in seconds, not after upload. The operator can remove them before submitting.
- **Duplicate file detection** (Workstream B). Duplicate file names within a selection are flagged.
- **Flight path preview** (Workstream E, part 1). An overhead map of the flight path drawn from photo GPS metadata. Each photo is a dot on the path, joined by a line. Shown before upload so the operator can check coverage.
- **Photo sidecar file** (`<archive_dir>/<job_id>_photos.json`). The point list is saved next to the output, not in `index.json`. Purged with the job at retention time.
- **Photo points in status API**. `status_view` returns the photo points from the sidecar file.
- **Minimal EXIF parser** in the browser. Reads GPS lat/lon, DateTimeOriginal, camera model, and dimensions from JPEG files. No external libraries.

### Changed
- **api.py** accepts `photo_points` on upload and saves them to the sidecar file.
- **archive.py** has `save_photos_sidecar()`, `read_photos_sidecar()`, and `delete_photos_sidecar()` functions. Sidecar files are cleaned up on job delete and purge.
- **Photo picker UI** shows camera count and GPS status (e.g., "GPS OK · 88 / 88" or "GPS OK · 45 / 88" in red).

---

## [0.8.2] — 2026-09-30

### Added
- **Client-side photo resize** (Workstream A). Photos are resized in the browser before upload using a Web Worker with OffscreenCanvas. A 150-photo job uploads a few hundred MB instead of well over 1 GB.
- **APPn metadata preservation**. All APPn segments (EXIF GPS, DJI XMP, Autel data) are copied from the original JPEG into the resized JPEG, right after the SOI marker. This mirrors WebODM's own resize behavior.
- **"Preparing photos" phase** in the progress panel. Shows resize progress (e.g., "Preparing photos 42/150") before upload begins.
- **Resize stat cell** in the running panel showing original vs resized size (e.g., "1.72 GB → 142 MB").
- **Server-side dimension verification**. The server checks each photo's dimensions when `client_resized=true`. If any photo exceeds the target, the server falls back to its own resize.
- **Graceful fallback**. If the browser cannot do client-side resize (no Worker/OffscreenCanvas support), or if any photo fails, the original is uploaded and the server resizes as before.
- **Resize targets passed from plugin.py** so browser and server share one value (2048 standard, 4000 high-res).

### Changed
- **pipeline.py** accepts `client_resized` parameter. When True, sets `resize_to=-1` to skip server-side resize.
- **api.py** reads `client_resized` from POST data, verifies photo dimensions, and passes the flag to the pipeline.
- **plugin.py** passes `resize_target_standard` and `resize_target_high_res` into the template.

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
