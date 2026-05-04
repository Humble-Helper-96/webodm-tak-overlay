"""
pipeline.py — TAK Incident Overlay plugin (v0.7.2)
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
  - Delete WebODM project on both success and failure (disk space matters)

v0.7.2 changes vs v0.7.1:
  - Phase tracking: archive.update_job(phase=...) called at each pipeline
    transition so the frontend can display discrete state labels rather than
    a static Standby message. Phases: Queued → Processing → Finalizing →
    Reprojecting → Exporting GeoTIFF → Building MBTiles → Building Overviews.

v0.7.1 changes vs v0.6.0:
  - Zoom range widened from 15–21
      * gdal_translate -outsize 65%  → base zoom 21
      * gdaladdo factor list adds 256  → reaches zoom 13
  - New _export_rgb_geotiff() step produces a 4-band RGBA GeoTIFF in EPSG:4326
    alongside the MBTiles. Useful for QGIS/ArcGIS/TAK server tile workflows
    that prefer plain GeoTIFF. LZW + TILED
    keeps the file workable.

Pre-stage path note (Session 7):
  WebODM's Task.process() scans the task ROOT directory, not an images/
  subdirectory:

      app/models/task.py  images_path = self.task_path()   # no arg = root
                          images = [os.path.join(images_path, i)
                                      for i in self.scan_images()]

  Fix: stage images at the task root directly. Output assets land at
  <task_root>/assets/odm_orthophoto/odm_orthophoto.tif — no collision.

TASK_OPTIONS rationale (v0.6.0 — simplified from v0.5.1):
  Investigation on a reference WebODM instance showed that the native WebODM UI run used only:
      auto-boundary:true   — crop output to actual flight area, not bounding box
      fast-orthophoto:true — skip MVS densification (~80% time reduction)
  with all other settings left at NodeODX defaults. That 37-image run completed
  in under 3 minutes with good quality.

  Our v0.5.1 preset (skip-3dmodel, skip-report, orthophoto-resolution:5,
  feature-quality:ultra, min-num-features:20000) was over-specified and caused
  validation errors in Sessions 5/6. Those options are removed.

WebODM resize mechanism (v0.6.0 — new):
  The WebODM GUI "Resize images" option is NOT an ODM task option — it never
  appears in Task.options. It is server-side pre-processing:
    1. Task created with resize_to=2048 field + pending_action=RESIZE
    2. Worker calls task.resize_images() — Pillow LANCZOS resize, EXIF preserved
       inline (no exiftool needed for JPEGs; Pillow carries GPS EXIF through)
    3. Worker clears pending_action, THEN assigns processing node and starts ODM

  This means resize acts as a natural gate between image staging and ODM
  dispatch — it fully resolves the Session 6 race condition (process_task
  firing before images are ready) as a side effect.

  resize_to=2048 targets the longest side of each image. DJI Mini 2 photos
  are 4000x3000; this halves pixel count to ~2000x1500. Measured effect:
  similar processing time to non-resized, similar output quality for the
  orthophoto use case (features are still detectable at 2048px).
"""

import logging

# Module-level logger is fine for start() — it runs in webapp, not worker.
# _run_pipeline recreates its own logger inside the function body.
logger = logging.getLogger('app.plugins.tak_incident_overlay')


# ---------------------------------------------------------------------------
# Public entry point — called by api.py from the webapp container
# ---------------------------------------------------------------------------

def start(job_id, saved_paths):
    """
    Kick off the async pipeline.  Returns immediately — all real work happens
    in _run_pipeline() via Celery in the worker container.

    Args:
        job_id      (str): UUID from archive.create_job()
        saved_paths (list[str]): Absolute paths to uploaded JPEG images on disk,
                                  inside archive.get_images_dir(job_id).
    """
    from app.plugins.worker import run_function_async
    logger.info(
        f"[TAK] {job_id}: Queuing pipeline with {len(saved_paths)} images"
    )
    run_function_async(_run_pipeline, job_id, saved_paths)


# ---------------------------------------------------------------------------
# Async worker function — runs in the Celery worker container.
# Everything _run_pipeline needs MUST be defined inside its own body.
# See the module-level docstring for the rationale.
# ---------------------------------------------------------------------------

def _run_pipeline(job_id, saved_paths, progress_callback=None):
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
      3. Create a WebODM task with pk=task_uuid, resize_to=2048,
         pending_action=RESIZE — worker resizes images before ODM dispatch
      4. Poll until task reaches a terminal state
      5. Locate the orthophoto produced by WebODM/ODM
      6. gdalwarp  — reproject to EPSG:4326 (produces wgs84.tif)
      7. gdal_translate — 4-band RGBA GeoTIFF
      8. gdal_translate -of MBTiles  — convert to raster tiles
      9. gdaladdo  — build zoom pyramid (required for non-blank client display)
     10. Mark job completed / failed in archive
     11. Delete WebODM project + task (always — disk space)
     12. Delete working directory (always)
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

    # =====================================================================
    # TASK_OPTIONS — minimal proven set (v0.6.0)
    #
    # Validated against native WebODM UI run on reference hardware:
    #   37 images, auto-boundary:true + fast-orthophoto:true only
    #   Completed in <3 min, good quality output
    #
    # Everything else left to NodeODX defaults.
    # =====================================================================
    TASK_OPTIONS = [
        {'name': 'auto-boundary',   'value': True},
        {'name': 'fast-orthophoto', 'value': True},
    ]

    # Target longest side in pixels for pre-processing resize.
    # WebODM's server-side resize_image() uses Pillow LANCZOS and preserves
    # EXIF (including GPS) inline. resize_to=-1 disables resize.
    RESIZE_TO = 2048

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
        Export the WGS84 reprojected raster as a 4-band RGBA GeoTIFF.

        gdalwarp -dstalpha produces a 4-band output where band 4 is already
        typed as alpha in the TIFF metadata. Passing no -b selectors copies
        all 4 bands intact — the alpha boundary is preserved automatically.
        No -co ALPHA=YES needed (not a valid GeoTIFF creation option).
        """
        result = subprocess.run(
            [
                'gdal_translate',
                '-co', 'COMPRESS=LZW',
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

    def _convert_to_mbtiles(wgs84_tif, output_mbtiles):
        """
        Convert WGS84 GeoTIFF to MBTiles raster format.

        Key flags:
          -of MBTiles               GDAL native MBTiles raster driver
          -co TILE_FORMAT=PNG       Preserves alpha channel for irregular flight boundaries
          -co ZOOM_LEVEL_STRATEGY=UPPER
                                    Selects zoom at or above native resolution
          -outsize 65% 65%          Downsamples,
                                    landing the base tile layer at zoom 21.
                                    Combined with the factor-64 overview, this gives
                                    the file a 13–21 zoom range. Workaround for the
                                    ZOOM_LEVEL creation option not being supported in
                                    GDAL 3.4.1.

        NOTE: Do NOT add -co ZOOM_LEVEL=N here — not supported in GDAL 3.4.1 and
        will produce a Warning 6 while silently ignoring the option.
        """
        result = subprocess.run(
            [
                'gdal_translate',
                '-of', 'MBTiles',
                '-co', 'TILE_FORMAT=PNG',
                '-co', 'ZOOM_LEVEL_STRATEGY=UPPER',
                '-outsize', '65%', '65%',
                wgs84_tif,
                output_mbtiles,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stderr:
            logger.debug(f"[TAK] gdal_translate stderr: {result.stderr.strip()}")

    def _build_overviews(mbtiles_path):
        """
        Build zoom pyramid for the MBTiles file.

        Required — without overview levels, TAK clients display blank tiles when
        the operator zooms out past the base zoom level. With v0.7.1's 65% outsize,
        the base zoom is 21, and overview factors 2 4 8 16 32 64 128 256 cover zoom
        levels 13–19. Final coverage: zooms 13–20.

        Field tile range comparison:
          v0.6 base 21, factors 2..32   → zooms 16..21
          v0.7.1 base 21, factors 2..256   → zooms 13..21
        """
        result = subprocess.run(
            [
                'gdaladdo',
                '-r',    'average',
                mbtiles_path,
                '2', '4', '8', '16', '32', '64',  '128', '256',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stderr:
            logger.debug(f"[TAK] gdaladdo stderr: {result.stderr.strip()}")

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
            name=f"TAK {display_name}",
            owner=user,
        )
        archive.update_job(job_id, webodm_project_id=project.id)
        logger.info(f"[TAK] {job_id}: Created WebODM project {project.id} — '{project.name}'")

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
            name=display_name,
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

        while True:
            time.sleep(POLL_INTERVAL)
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

            # FAILED or CANCELLED
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
        # Steps 6–9 — GDAL pipeline
        #
        # v0.7.0 adds a parallel RGB GeoTIFF deliverable derived from the
        # same WGS84 reprojection. Both outputs land in the archive
        # directory (NOT working_dir) so they survive the cleanup step.
        # ------------------------------------------------------------------
        working_dir  = archive.get_working_dir(job_id)
        wgs84_tif    = os.path.join(working_dir, 'wgs84.tif')
        mbtiles_path = archive.get_mbtiles_path(job)
        geotiff_path = archive.get_geotiff_path(job)

        archive.update_job(job_id, phase='Reprojecting')
        logger.info(f"[TAK] {job_id}: Phase → Reprojecting")
        _reproject_to_wgs84(ortho_path, wgs84_tif)
        logger.info(f"[TAK] {job_id}: Reprojection complete → {wgs84_tif}")

        archive.update_job(job_id, phase='Exporting GeoTIFF')
        logger.info(f"[TAK] {job_id}: Phase → Exporting GeoTIFF")
        _export_rgb_geotiff(wgs84_tif, geotiff_path)
        logger.info(f"[TAK] {job_id}: GeoTIFF export complete → {geotiff_path}")

        archive.update_job(job_id, phase='Building MBTiles')
        logger.info(f"[TAK] {job_id}: Phase → Building MBTiles")
        _convert_to_mbtiles(wgs84_tif, mbtiles_path)
        logger.info(f"[TAK] {job_id}: MBTiles conversion complete → {mbtiles_path}")

        archive.update_job(job_id, phase='Building Overviews')
        logger.info(f"[TAK] {job_id}: Phase → Building Overviews")
        _build_overviews(mbtiles_path)
        logger.info(f"[TAK] {job_id}: Zoom pyramid built")

        # Sanity check — both outputs must exist and be non-empty
        if not os.path.exists(mbtiles_path) or os.path.getsize(mbtiles_path) == 0:
            raise RuntimeError(
                "MBTiles file missing or empty after GDAL pipeline. "
                "Check disk space and GDAL logs above."
            )
        if not os.path.exists(geotiff_path) or os.path.getsize(geotiff_path) == 0:
            raise RuntimeError(
                "GeoTIFF file missing or empty after GDAL pipeline. "
                "Check disk space and GDAL logs above."
            )

        mbtiles_mb = os.path.getsize(mbtiles_path) / 1024 / 1024
        geotiff_mb = os.path.getsize(geotiff_path) / 1024 / 1024
        archive.mark_completed(job_id, mbtiles_path, geotiff_path)
        logger.info(
            f"[TAK] {job_id}: Pipeline complete — MBTiles {mbtiles_mb:.1f} MB, "
            f"GeoTIFF {geotiff_mb:.1f} MB"
        )

    except subprocess.CalledProcessError as exc:
        # Subprocess failure — include stderr so logs are actionable
        stderr = (exc.stderr or '').strip()
        msg = f"GDAL command failed (exit {exc.returncode})"
        if stderr:
            msg += f": {stderr[:500]}"   # cap at 500 chars to avoid log spam
        logger.error(f"[TAK] {job_id}: {msg}", exc_info=True)
        archive.mark_failed(job_id, msg)

    except Exception as exc:
        logger.error(f"[TAK] {job_id}: Pipeline failed — {exc}", exc_info=True)
        archive.mark_failed(job_id, str(exc))

    finally:
        # Always clean up — disk space is precious on-scene
        _delete_webodm_project(project)
        archive.cleanup_working_dir(job_id)
