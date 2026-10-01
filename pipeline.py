"""
pipeline.py — TAK Incident Overlay plugin (v0.8.0)
Async WebODM task creation, polling, and GDAL export pipeline.

Entry point:  start(job_id, saved_paths)
              — called by api.py, returns immediately
Worker func:  _run_pipeline(job_id, saved_paths)
              — runs in Celery worker container

CRITICAL — async function self-containment:
  When `run_function_async` ships `_run_pipeline` to the Celery worker, the
  worker process does NOT inherit this module's top-level state. Module-level
  imports, constants, the `logger` instance, and other module-level functions
  are all invisible to the running worker. Any name `_run_pipeline` references
  must be defined inside its own body (or be a builtin).

  The contours coreplugin's `calc_contours` follows this same pattern: every
  import is inside, no module-level helpers are called from it. This file
  matches that structure: `_run_pipeline` is fully self-contained, with GDAL
  pipeline steps and cleanup defined as nested functions sharing scope via
  closure.

  `start()` is fine at module level because it runs in the webapp container,
  where Python module loading works normally.

Design decisions:
  - Polling loop for task completion (simpler/more debuggable than signals)
  - One new WebODM project per job (cleaner isolation, easier cleanup)
  - Delete WebODM project after the job finishes — unless retain_task=True
    (v0.7.6+), in which case it lives until the 72h auto-purge

v0.7.10 changes vs v0.7.9:
  - Removed the 8-thread option from the Processing threads radio group.
    Allowed values are now {2, 4, 6}; default remains 4. This keeps the
    plugin safer on smaller processing nodes where allowing 8 threads
    could exceed available cores and cause queue stalls.
  - Runtime estimates throughout the docs refreshed against final
    reference-hardware measurements (Lenovo M920q with Intel i5-8500,
    4 threads, 65-photo Sutwick dataset): defaults ~3 min, quality_mode
    ~10 min, terrain_correction ~35 min, both ~42 min. Older docs were
    noticeably more optimistic on the dense-MVS modes; this matches what
    the i5-8500 actually delivers under real load.
  - Field guide shortened (Section 1 paragraphs consolidated, Section 2
    toggle descriptions collapsed into a bullet list, Section 3 tightened).
    sUAS live-stream warnings removed from the High-Resolution and Terrain
    correction toggles and from the field guide, so the plugin reads as
    broadly applicable rather than tied to one operational context.
  - Field guide cleanup: typo fixes ("estabish" → "establish"), a malformed
    `<p>` tag closed properly, Section 3 title broadened from "Import to
    CloudTAK" to "Import to TAK" with TAKAware compatibility called out,
    stale "Both files" reference (from the pre-v0.7.8 MBTiles era) updated
    to "The GeoTIFF", and the awkward "Section 3" self-reference rewritten
    to point at the Downloads section by name.
  - api._safe_filename now mirrors archive._sanitize_filename — spaces
    become underscores at download time (was passing them through), so
    legacy records or any path that bypassed the create-time sanitizer
    still serve with a clean filename.

v0.7.9 changes vs v0.7.8:
  - GeoTIFF compression switched from lossless LZW to JPEG_QUALITY=85 with
    PHOTOMETRIC=YCBCR. Band 4 alpha is preserved via a GeoTIFF internal
    1-bit mask (stripped from the data bands by `-mask 4`). Output files
    are ~97-99% smaller in practice — measured on the 65-photo Sutwick
    dataset, defaults went from 250 MB to 6 MB and the worst-case both-modes
    output went from 1.2 GB to 9 MB. Trade-off: lossy compression
    (imperceptible at TAK viewing zooms, but the output is no longer
    pixel-exact and not suitable as forensic evidence). Verified to import
    cleanly into CloudTAK and TAKAware.
  - Runtime numbers documented from v0.7.9 onward measure the FULL plugin
    pipeline (queue → archive write), including GDAL reproject and GeoTIFF
    export. Earlier ad-hoc comparisons used the WebODM task UI's
    "Processing Time" which counts only the ODM step; the GDAL post-
    processing adds a small amount on top. Absolute times also vary with
    host hardware and system load — see the measurement table near
    _run_pipeline() for the reference-hardware baseline.

v0.7.8 changes vs v0.7.7:
  - MBTiles output removed entirely. GeoTIFF is the only deliverable.
    Phases: Queued → Processing → Finalizing → Reprojecting → Exporting GeoTIFF.
  - Quality mode repurposed: now means "high-res" — raises resize to 4000px
    and sets orthophoto-resolution to 2.5 cm/px. Used to mean "full SfM
    pipeline (no fast-orthophoto)" — fast-orthophoto stays enabled now.
  - max-concurrency: 4 baked in, matching the i5-8500-class hardware this
    plugin is deployed on.
  - app.html showFlash() bug fixed (was setting display: '' which fell back
    to CSS display:none — now sets display: 'block').

v0.7.2 changes vs v0.7.1:
  - Phase tracking: archive.update_job(phase=...) called at each pipeline
    transition so the frontend can display discrete state labels rather than
    a static Standby message.

v0.7.1 changes vs v0.6.0:
  - New _export_rgb_geotiff() step produces a 4-band RGBA GeoTIFF in EPSG:4326.
    Useful for QGIS/ArcGIS/TAK server tile workflows. Initially LZW + TILED;
    in v0.7.9 the compression became JPEG with internal alpha mask (see above).

Pre-stage path note (Session 7):
  WebODM's Task.process() scans the task ROOT directory, not an images/
  subdirectory:

      app/models/task.py  images_path = self.task_path()   # no arg = root
                          images = [os.path.join(images_path, i)
                                      for i in self.scan_images()]

  Fix: stage images at the task root directly. Output assets land at
  <task_root>/assets/odm_orthophoto/odm_orthophoto.tif — no collision.

TASK_OPTIONS rationale (current as of v0.7.8):
  Standard run uses two always-on options plus one conditional:
      auto-boundary:true              — crop output to actual flight area
      max-concurrency:<operator>      — operator-selected CPU thread count
                                          (default 4, allowed {2, 4, 6})
      fast-orthophoto:true            — ON by default (~80% time reduction
                                          via skipped MVS densification);
                                          OFF when terrain_correction enabled
  Quality mode (UI: "High-Resolution mode") adds:
      orthophoto-resolution: 2.5 cm/px (and bumps RESIZE_TO to 4000)
  Terrain correction simply removes fast-orthophoto from the list, letting
  ODM run the full SfM pipeline.

  History: v0.5.1 ran a much larger preset (skip-3dmodel, skip-report,
  orthophoto-resolution:5, feature-quality:ultra, min-num-features:20000)
  that was over-specified and triggered validation errors. v0.6.0 stripped
  it back to auto-boundary + fast-orthophoto only. v0.7.8 added the
  max-concurrency cap (initially hardcoded to 4; now operator-selectable),
  reintroduced orthophoto-resolution as a Quality-mode-only opt-in, and
  made fast-orthophoto conditional via the terrain_correction toggle.

WebODM resize mechanism (v0.6.0 — new):
  The WebODM GUI "Resize images" option is NOT an ODM task option — it never
  appears in Task.options. It is server-side pre-processing:
    1. Task created with resize_to=N field + pending_action=RESIZE
    2. Worker calls task.resize_images() — Pillow LANCZOS resize, EXIF preserved
       inline (no exiftool needed for JPEGs; Pillow carries GPS EXIF through)
    3. Worker clears pending_action, THEN assigns processing node and starts ODM

  This means resize acts as a natural gate between image staging and ODM
  dispatch — it fully resolves the Session 6 race condition (process_task
  firing before images are ready) as a side effect.

  resize_to targets the longest side of each image. DJI Mini 2 photos are
  4000x3000. Default mode uses resize_to=2048 (halves pixel count for faster
  processing — output GSD bottoms out around 4 cm regardless of any
  orthophoto-resolution setting). Quality mode uses resize_to=4000 (native
  sensor resolution) so the 2.5 cm/px orthophoto-resolution can actually be
  resolved from real data rather than upsampled.
"""

import logging

# Module-level logger is fine for start() — it runs in webapp, not worker.
# _run_pipeline recreates its own logger inside the function body.
logger = logging.getLogger('app.plugins.tak_incident_overlay')


# ---------------------------------------------------------------------------
# Public entry point — called by api.py from the webapp container
# ---------------------------------------------------------------------------

def start(job_id, saved_paths, retain_task=False, quality_mode=False,
          terrain_correction=False, client_resized=False, submitting_username=None):
    """
    Kick off the async pipeline.  Returns immediately — all real work happens
    in _run_pipeline() via Celery in the worker container.

    Args:
        job_id             (str): UUID from archive.create_job()
        saved_paths        (list[str]): Absolute paths to uploaded JPEG images on disk,
                                         inside archive.get_images_dir(job_id).
        retain_task        (bool): If True, the WebODM project is NOT deleted after
                                    the pipeline completes. It stays in WebODM for
                                    up to 72 hours until purge_expired_jobs() cleans it.
        quality_mode       (bool): If True, runs the high-resolution variant:
                                    image resize raised from 2048 to 4000 px and
                                    orthophoto-resolution pinned to 2.5 cm/px.
                                    Reference hardware (M920q i5-8500), 65-photo
                                    job at 4 threads: ~10 min vs ~3 min default;
                                    output file 9.0 MB vs 6.1 MB default.
                                    UI label: "High-Resolution mode".
        terrain_correction (bool): If True, fast-orthophoto is omitted from
                                    TASK_OPTIONS and ODM runs the full SfM
                                    pipeline (dense MVS, mesh, textured
                                    orthorectification). Corrects geometric
                                    error from varied terrain and tall vertical
                                    features. Reference hardware (M920q
                                    i5-8500), 65-photo job at 3 threads:
                                    ~35 min vs ~3 min default; ~42 min when
                                    combined with quality_mode.
                                    UI label: "Terrain correction".
        submitting_username (str|None): WebODM username of the operator who
                                    submitted the job (request.user.username
                                    from api.upload_view). Passed as a plain
                                    string — NOT a User object — because
                                    run_function_async serializes arguments
                                    across the Celery boundary. Used in
                                    _run_pipeline to grant that operator
                                    object-level view permission on the
                                    WebODM project via django-guardian, so
                                    saved tasks are visible in the dashboard
                                    without logging in as the superuser owner.
    """
    from app.plugins.worker import run_function_async
    logger.info(
        f"[TAK] {job_id}: Queuing pipeline with {len(saved_paths)} images"
        f"{' (retain_task=True)' if retain_task else ''}"
        f"{' (quality_mode=True)' if quality_mode else ''}"
        f"{' (terrain_correction=True)' if terrain_correction else ''}"
    )
    run_function_async(_run_pipeline, job_id, saved_paths, retain_task,
                       quality_mode, terrain_correction, client_resized,
                       submitting_username)


# ---------------------------------------------------------------------------
# Async worker function — runs in the Celery worker container.
# Everything _run_pipeline needs MUST be defined inside its own body.
# See the module-level docstring for the rationale.
# ---------------------------------------------------------------------------

def _run_pipeline(job_id, saved_paths, retain_task=False, quality_mode=False,
                  terrain_correction=False, client_resized=False,
                  submitting_username=None, progress_callback=None):
    """
    Full pipeline — runs asynchronously inside the Celery worker container.

    `progress_callback` is supplied by run_function_async and accepts
    (text_status, percent_0_100). We don't currently surface it to the
    operator (Section 2 polls WebODM's running_progress directly via
    api.status_view), but the parameter MUST exist or Celery raises
    TypeError on dispatch.

    Sequence:
      1. Create a WebODM project (one per job)
      2. Pre-stage images at the task ROOT directory (NOT 'images/' subdir)
      3. Create a WebODM task with pk=task_uuid, resize_to=2048 (or 4000 in
         quality_mode), pending_action=RESIZE — worker resizes images before
         ODM dispatch
      4. Poll until task reaches a terminal state
      5. Locate the orthophoto produced by WebODM/ODM
      6. gdalwarp        — reproject to EPSG:4326 (produces wgs84.tif)
      7. gdal_translate  — 3-band RGB GeoTIFF, JPEG compression, internal
                            alpha mask (v0.7.9; was 4-band RGBA LZW in v0.7.8)
      8. Mark job completed / failed in archive
      9. Delete WebODM project + task (unless retain_task)
     10. Delete working directory (always)
    """
    # =====================================================================
    # ALL imports inside — see module docstring
    # =====================================================================
    import logging
    import os
    import shutil
    import subprocess
    import time
    import uuid as _uuid

    from django.conf import settings
    from django.contrib.auth.models import User
    from app.models import Project, Task
    from app import pending_actions
    from coreplugins.tak_incident_overlay import archive

    logger = logging.getLogger('app.plugins.tak_incident_overlay')

    # =====================================================================
    # Constants
    # =====================================================================

    # WebODM task status integer constants
    # (Values per WebODM REST API docs → Task → Status Codes)
    TASK_QUEUED     = 10
    TASK_RUNNING    = 20
    TASK_FAILED     = 30
    TASK_COMPLETED  = 40
    TASK_CANCELLED  = 50
    TERMINAL_STATES = {TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED}

    # How often (seconds) to poll WebODM for task status while processing
    POLL_INTERVAL = 15

    # Watchdog: hard ceiling on total pipeline runtime. Worst legitimate
    # case is terrain_correction + quality_mode at ~42 min on reference
    # hardware; 3 hours leaves generous headroom for slower CPUs and big
    # photo sets. Without this, a task that never reaches a terminal state
    # (node dies in a way WebODM's heartbeat misses, DB hiccup) pins a
    # Celery worker forever and silently eats one of the 3 job slots.
    MAX_RUNTIME_SECONDS = 3 * 60 * 60

    # Percentage of the processing node's CPU threads to hand ODM via
    # max-concurrency (v0.8.1: read from settings.json, admin-configurable).
    # Previously a fixed value of 3 (v0.7.12 and earlier), then 50% (v0.7.13).
    # The setting is read from archive.get_thread_percent() so an admin can
    # adjust it without a code change.

    def _get_node_cpu_threads():
        """
        Query the primary WebODM ProcessingNode's /info endpoint for its
        reported cpuCores. Mirrors the same node-lookup and probe pattern
        api.node_status_view already uses for the header's online/offline
        indicator — first ProcessingNode record, direct HTTP probe, no
        hardcoded hostname.

        Returns int cpuCores, or None if no node is configured, the probe
        fails, or the field is missing/malformed. Callers must have a
        fallback for None — this must never raise into the pipeline.
        """
        try:
            import requests as _requests
            from nodeodm.models import ProcessingNode

            node = ProcessingNode.objects.order_by('id').first()
            if node is None:
                return None

            url = 'http://{}:{}/info'.format(node.hostname, node.port)
            resp = _requests.get(url, timeout=5)
            if resp.status_code != 200:
                return None

            cores = resp.json().get('cpuCores')
            return int(cores) if cores else None
        except Exception as exc:
            logger.warning(f"[TAK] {job_id}: Could not read node cpuCores — {exc}")
            return None

    _thread_percent = archive.get_thread_percent()
    _node_threads = _get_node_cpu_threads()
    if _node_threads:
        MAX_CONCURRENCY = max(1, round(_node_threads * _thread_percent / 100))
        logger.info(
            f"[TAK] {job_id}: Node reports {_node_threads} CPU threads — "
            f"using {_thread_percent}% = {MAX_CONCURRENCY} threads"
        )
    else:
        # Fallback if the node is unreachable or doesn't report cpuCores
        # (older NodeODM versions may omit the field). Matches the old
        # fixed default so behavior degrades to the previous known-safe
        # value rather than guessing.
        MAX_CONCURRENCY = 3
        logger.warning(
            f"[TAK] {job_id}: Node cpuCores unavailable — "
            f"falling back to max-concurrency={MAX_CONCURRENCY}"
        )

    # =====================================================================
    # TASK_OPTIONS — minimal proven set
    #
    # Always-on:
    #   auto-boundary:true            — crop output to actual flight area
    #   max-concurrency:MAX_CONCURRENCY — computed above as
    #                                    thread_percent of the node's
    #                                    reported cpuCores (v0.8.1: from settings).
    #
    # Conditional:
    #   fast-orthophoto:true          — added UNLESS terrain_correction is on.
    # =====================================================================
    TASK_OPTIONS = [
        {'name': 'auto-boundary',   'value': True},
        {'name': 'max-concurrency', 'value': MAX_CONCURRENCY},
    ]
    if not terrain_correction:
        TASK_OPTIONS.append({'name': 'fast-orthophoto', 'value': True})
    if quality_mode:
        TASK_OPTIONS.append({'name': 'orthophoto-resolution', 'value': 2.5})

    # Target longest side in pixels for pre-processing resize.
    # WebODM's server-side resize_image() uses Pillow LANCZOS and preserves
    # EXIF (including GPS) inline. resize_to=-1 disables resize.
    #
    # Default 2048: halves DJI Mini 2 4000x3000 to ~2000x1500. Faster runs;
    # output GSD floor around 4.3 cm regardless of orthophoto-resolution.
    # Quality mode 4000: native sensor resolution. Required to actually
    # resolve a 2.5 cm/px orthophoto.
    # v0.8.2: If the browser already resized photos to the target (and the
    # server confirmed all are within it), skip server-side resize.
    if client_resized:
        RESIZE_TO = -1
    else:
        RESIZE_TO = 4000 if quality_mode else 2048

    # =====================================================================
    # Nested helpers — share scope (subprocess, logger, etc.) via closure
    # GDAL commands locked from Spike 2 (Eagle River Road, AK, 2026-04-27)
    # =====================================================================

    def _reproject_to_wgs84(input_tif, output_tif):
        """
        Reproject orthophoto from native UTM to EPSG:4326 (required by all TAK clients).

        Key flags:
          -t_srs EPSG:4326          All TAK clients expect WGS84
          -dstalpha                 Preserves the alpha mask from the 4-band source
          -tr 0.000000449           Locks output pixel size in degrees; calibrated for
                                    mid-latitude deployment (≈ 2.5 cm/px). v2: compute dynamically from source GSD and centroid latitude
                                    from source GSD and centroid latitude.
          -co COMPRESS=LZW          Efficient intermediate file
          -co TILED=YES             Required for large rasters
        """
        result = subprocess.run(
            [
                'gdalwarp',
                '-t_srs', 'EPSG:4326',
                '-of',    'GTiff',
                '-co',    'COMPRESS=LZW',
                '-co',    'TILED=YES',
                '-dstalpha',
                '-tr',    '0.000000449', '0.000000449',
                input_tif,
                output_tif,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stderr:
            logger.debug(f"[TAK] gdalwarp stderr: {result.stderr.strip()}")

    def _export_rgb_geotiff(wgs84_tif, output_geotiff):
        """
        Export the WGS84 reprojected raster as a 3-band RGB GeoTIFF with
        JPEG compression and an internal 1-bit mask preserving the flight
        boundary alpha.

        JPEG doesn't natively support 4-band imagery — we strip the input's
        band 4 alpha into a GeoTIFF internal mask using `-b 1 -b 2 -b 3
        -mask 4`. GDAL_TIFF_INTERNAL_MASK=YES (passed via --config) tells
        GDAL to store that mask inside the same .tif rather than as a
        sidecar .msk file.

        JPEG_QUALITY=85 with PHOTOMETRIC=YCBCR is the standard "high
        quality" photo JPEG profile. Artifacts are imperceptible at the
        zoom levels TAK clients render overlays at, while file size drops
        by 1-2 orders of magnitude vs lossless LZW (used through v0.7.8).
        Measured on the 65-photo Sutwick dataset: default-mode output went
        from 250 MB to 6.0 MB; worst-case (both modes) went from 1.2 GB
        to 9.3 MB.

        Note that this is now a lossy export. Per-pixel values change
        slightly. Not suitable as forensic evidence; fine for situational
        awareness, geolocation reference, and CloudTAK/ATAK overlays.
        """
        result = subprocess.run(
            [
                'gdal_translate',
                '--config', 'GDAL_TIFF_INTERNAL_MASK', 'YES',
                '-b', '1', '-b', '2', '-b', '3',
                '-mask', '4',
                '-co', 'COMPRESS=JPEG',
                '-co', 'JPEG_QUALITY=85',
                '-co', 'PHOTOMETRIC=YCBCR',
                '-co', 'TILED=YES',
                wgs84_tif,
                output_geotiff,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stderr:
            logger.debug(f"[TAK] gdal_translate (geotiff) stderr: {result.stderr.strip()}")

    def _delete_webodm_project(project):
        """
        Delete the WebODM project and its task (cascade). Runs unconditionally
        in the finally block — both on success and failure.
        """
        if project is None:
            return
        try:
            project_id = project.id
            project.delete()   # cascades to Task, images, and asset files on disk
            logger.info(f"[TAK] {job_id}: Deleted WebODM project {project_id}")
        except Exception as exc:
            # Log but don't re-raise — cleanup failure must not mask pipeline result
            logger.warning(
                f"[TAK] {job_id}: Could not delete WebODM project — {exc}"
            )

    # =====================================================================
    # Main pipeline flow
    # =====================================================================

    job = archive.get_job(job_id)
    if job is None:
        logger.error(f"[TAK] _run_pipeline: job {job_id} not found in archive — aborting")
        return

    display_name = job['display_name']
    project = None  # kept in scope so finally block can always attempt cleanup

    # Final output path — resolved up front (v0.7.13) so the except blocks
    # can remove a partially-written GeoTIFF if the GDAL pipeline dies
    # mid-export. A truncated .tif left under the final filename won't be
    # served (job status != completed) but wastes disk until purge and
    # confuses anyone browsing the archive directory.
    geotiff_path = archive.get_geotiff_path(job)

    logger.info(
        f"[TAK] {job_id}: Pipeline starting — options={TASK_OPTIONS}, resize_to={RESIZE_TO}"
    )

    try:
        # ------------------------------------------------------------------
        # Step 1 — Create WebODM project (one per job)
        # ------------------------------------------------------------------
        user = User.objects.filter(is_superuser=True).first()
        if user is None:
            raise RuntimeError("No superuser account found — cannot create WebODM project")

        project = Project.objects.create(
            name=f"TAK {display_name} [{job_id[:8]}]",
            owner=user,
        )
        archive.update_job(job_id, webodm_project_id=project.id)
        logger.info(f"[TAK] {job_id}: Created WebODM project {project.id} — '{project.name}'")

        # ------------------------------------------------------------------
        # Grant the submitting operator visibility into this project.
        #
        # The project owner is the first superuser (above), NOT the operator
        # who submitted the job. Without an explicit object-level grant, the
        # operator cannot see the project/task in the WebODM dashboard even
        # with "Save WebODM task" enabled — the ?project_task_open= deep link
        # silently falls back to the bare dashboard.
        #
        # WebODM uses django-guardian for object permissions (the same
        # mechanism behind Administration → Object Permissions). Task
        # visibility is checked through the parent project's permissions,
        # so granting view_project is sufficient to see the task too.
        #
        # Non-fatal by design: a failed grant must never kill the job.
        # Skip if the submitter IS the superuser owner (already sees it).
        # ------------------------------------------------------------------
        if submitting_username and submitting_username != user.username:
            try:
                from guardian.shortcuts import assign_perm
                submitter = User.objects.filter(
                    username=submitting_username
                ).first()
                if submitter is not None:
                    assign_perm('view_project', submitter, project)
                    logger.info(
                        f"[TAK] {job_id}: Granted '{submitting_username}' "
                        f"view_project on project {project.id}"
                    )
                else:
                    logger.warning(
                        f"[TAK] {job_id}: Submitting user "
                        f"'{submitting_username}' not found in WebODM — "
                        f"no permission grant applied"
                    )
            except Exception:
                logger.exception(
                    f"[TAK] {job_id}: Permission grant for "
                    f"'{submitting_username}' failed — continuing anyway"
                )

        # ------------------------------------------------------------------
        # Step 2 — Pre-stage images at TASK ROOT.
        #
        # IMPORTANT: Task.objects.create() fires a Django post_save signal
        # that immediately dispatches WebODM's process_task Celery worker.
        # That worker calls task.process(), which scans the task ROOT
        # directory for images (task_path() with no argument).
        #
        # We pre-stage images before create() so scan_images() finds files
        # immediately. pk=task_uuid ties the directory to the Task record.
        #
        # NOTE: With pending_action=RESIZE set at create time, the worker
        # will run resize_images() BEFORE assigning a processing node and
        # before ODM dispatch. The resize step is a natural gate that
        # further ensures images are fully prepared before ODM sees them.
        # ------------------------------------------------------------------
        task_uuid = str(_uuid.uuid4())

        # Task ROOT directory — NOT a subdirectory. WebODM scans this directly.
        task_root = os.path.join(
            settings.MEDIA_ROOT,
            'project', str(project.id),
            'task',    task_uuid,
        )
        os.makedirs(task_root, exist_ok=True)

        for src in saved_paths:
            shutil.copy2(src, task_root)

        logger.info(
            f"[TAK] {job_id}: Pre-staged {len(saved_paths)} images -> {task_root}"
        )

        # ------------------------------------------------------------------
        # Step 3 — Create the WebODM task.
        #
        # Key fields:
        #   resize_to=RESIZE_TO          triggers WebODM's server-side resize
        #   pending_action=RESIZE        worker resizes before ODM dispatch
        #   auto_processing_node=True    worker assigns node after resize completes
        #
        # Worker execution order (from app/models/task.py):
        #   1. pending_action == RESIZE  → resize_images() → clear pending_action
        #   2. auto_processing_node      → find_best_available_node() → assign
        #   3. ODM processing begins
        # ------------------------------------------------------------------
        task = Task.objects.create(
            pk=task_uuid,
            project=project,
            name=f"{display_name} [{job_id[:8]}]",
            auto_processing_node=True,
            images_count=len(saved_paths),
            options=TASK_OPTIONS,
            resize_to=RESIZE_TO,
            pending_action=pending_actions.RESIZE,
        )

        # Record task ID so status_view can surface live progress to the frontend
        archive.update_job(job_id, webodm_task_id=str(task.id), phase='Queued')
        logger.info(
            f"[TAK] {job_id}: Created WebODM task {task.id} — "
            f"{len(saved_paths)} images staged, resize to {RESIZE_TO}px queued"
        )

        # ------------------------------------------------------------------
        # Step 4 — Poll for task completion
        #
        # Note: task will first show no status while resize runs, then
        # QUEUED, then RUNNING once ODM starts. All non-terminal states
        # including None are handled by the continue branch below.
        # ------------------------------------------------------------------
        logger.info(
            f"[TAK] {job_id}: Polling task {task.id} every {POLL_INTERVAL}s"
        )

        _phase_processing_set = False   # guard: only set Processing phase once
        _poll_started_at = time.time()  # watchdog baseline

        while True:
            time.sleep(POLL_INTERVAL)

            # Watchdog — bail out if the job has been running impossibly long
            if time.time() - _poll_started_at > MAX_RUNTIME_SECONDS:
                raise RuntimeError(
                    f"Job exceeded the maximum runtime of "
                    f"{MAX_RUNTIME_SECONDS // 3600} hours and was abandoned. "
                    f"Check the processing node's health and try again."
                )

            task.refresh_from_db()

            status   = task.status
            progress = float(getattr(task, 'running_progress', 0.0) or 0.0)

            logger.debug(
                f"[TAK] {job_id}: status={status} progress={progress:.1%}"
            )

            # Transition to Processing once the node picks up the task
            if not _phase_processing_set and status == TASK_RUNNING:
                archive.update_job(job_id, phase='Processing')
                _phase_processing_set = True
                logger.info(f"[TAK] {job_id}: Phase → Processing")

            if status not in TERMINAL_STATES:
                continue

            if status == TASK_COMPLETED:
                archive.update_job(job_id, phase='Finalizing')
                logger.info(f"[TAK] {job_id}: WebODM task completed — Phase → Finalizing")
                break

            if status == TASK_CANCELLED:
                # Cancellation is not a failure. Two paths lead here:
                #   1. Operator hit Cancel in the plugin UI — cancel_view
                #      has already marked the archive record 'cancelled'.
                #      Do NOT raise: the generic except block would call
                #      mark_failed() and stomp the cancelled status (the
                #      operator would see their own cancel reported as a
                #      job failure).
                #   2. Someone cancelled the task directly in WebODM —
                #      archive still says 'running', so mark it cancelled
                #      here to keep the plugin UI consistent.
                # Either way: exit cleanly. The finally block still runs
                # (project cleanup + working dir removal).
                current = archive.get_job(job_id)
                if current and current.get('status') == 'running':
                    archive.mark_cancelled(job_id)
                    logger.info(
                        f"[TAK] {job_id}: Task cancelled from WebODM side — "
                        f"archive record updated"
                    )
                else:
                    logger.info(
                        f"[TAK] {job_id}: Task cancelled (operator-initiated) — "
                        f"pipeline exiting cleanly"
                    )
                return

            # FAILED
            last_error = getattr(task, 'last_error', None) or ''
            raise RuntimeError(
                f"WebODM task ended with status {status}. "
                f"{last_error or 'Check that all photos have GPS EXIF data and try again.'}"
            )

        # ------------------------------------------------------------------
        # Step 5 — Locate the orthophoto on disk
        # ------------------------------------------------------------------
        ortho_path = os.path.join(
            settings.MEDIA_ROOT,
            'project', str(project.id),
            'task',    str(task.id),
            'assets',  'odm_orthophoto', 'odm_orthophoto.tif',
        )

        if not os.path.exists(ortho_path):
            raise RuntimeError(
                f"Orthophoto not found at expected path: {ortho_path}\n"
                "The WebODM task reported success but produced no output — "
                "this may indicate too few overlap photos or GPS issues."
            )

        logger.info(f"[TAK] {job_id}: Orthophoto located at {ortho_path}")

        # ------------------------------------------------------------------
        # Steps 6–7 — GDAL pipeline
        #
        # Output GeoTIFF lands in the archive directory (NOT working_dir)
        # so it survives the cleanup step.
        # ------------------------------------------------------------------
        working_dir  = archive.get_working_dir(job_id)
        wgs84_tif    = os.path.join(working_dir, 'wgs84.tif')
        # geotiff_path was resolved at the top of the pipeline (v0.7.13)

        archive.update_job(job_id, phase='Reprojecting')
        logger.info(f"[TAK] {job_id}: Phase → Reprojecting")
        _reproject_to_wgs84(ortho_path, wgs84_tif)
        logger.info(f"[TAK] {job_id}: Reprojection complete → {wgs84_tif}")

        archive.update_job(job_id, phase='Exporting GeoTIFF')
        logger.info(f"[TAK] {job_id}: Phase → Exporting GeoTIFF")
        _export_rgb_geotiff(wgs84_tif, geotiff_path)
        logger.info(f"[TAK] {job_id}: GeoTIFF export complete → {geotiff_path}")

        # Sanity check — output must exist and be non-empty
        if not os.path.exists(geotiff_path) or os.path.getsize(geotiff_path) == 0:
            raise RuntimeError(
                "GeoTIFF file missing or empty after GDAL pipeline. "
                "Check disk space and GDAL logs above."
            )

        geotiff_mb = os.path.getsize(geotiff_path) / 1024 / 1024
        archive.mark_completed(job_id, geotiff_path)
        logger.info(
            f"[TAK] {job_id}: Pipeline complete — GeoTIFF {geotiff_mb:.1f} MB"
        )

    except subprocess.CalledProcessError as exc:
        # Subprocess failure — include stderr so logs are actionable
        stderr = (exc.stderr or '').strip()
        msg = f"GDAL command failed (exit {exc.returncode})"
        if stderr:
            msg += f": {stderr[:500]}"   # cap at 500 chars to avoid log spam
        logger.error(f"[TAK] {job_id}: {msg}", exc_info=True)
        archive.mark_failed(job_id, msg)
        # Remove any partially-written output (v0.7.13)
        try:
            if geotiff_path and os.path.exists(geotiff_path):
                os.remove(geotiff_path)
                logger.info(f"[TAK] {job_id}: Removed partial GeoTIFF after failure")
        except OSError:
            pass

    except Exception as exc:
        logger.error(f"[TAK] {job_id}: Pipeline failed — {exc}", exc_info=True)
        archive.mark_failed(job_id, str(exc))
        # Remove any partially-written output (v0.7.13)
        try:
            if geotiff_path and os.path.exists(geotiff_path):
                os.remove(geotiff_path)
                logger.info(f"[TAK] {job_id}: Removed partial GeoTIFF after failure")
        except OSError:
            pass

    finally:
        # Conditionally delete WebODM project — skip if operator requested retention.
        # Retained projects are cleaned up by purge_expired_jobs() at 72 hours.
        if retain_task and project is not None:
            logger.info(
                f"[TAK] {job_id}: WebODM project {project.id} retained "
                f"(auto-purge with job at 72h)"
            )
        else:
            _delete_webodm_project(project)
        archive.cleanup_working_dir(job_id)
