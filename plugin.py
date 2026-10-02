import json

from app.plugins import PluginBase, Menu, MountPoint
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.utils.translation import gettext as _


class Plugin(PluginBase):

    def main_menu(self):
        return [Menu(_("TAK Overlay"), self.public_url(""), "fa fa-crosshairs fa-fw")]

    def app_mount_points(self):
        from . import api
        from . import archive

        @login_required
        def index(request):
            # Pass current user's settings into the template so the first
            # render uses them without a round-trip (v0.8.1).
            #
            # user_settings MUST be JSON-serialized before reaching the
            # template. app.html does `var serverUserSettings =
            # {{ user_settings|safe }};` — |safe only stops Django from
            # HTML-escaping the value, it does NOT serialize it. Handed a
            # raw Python dict, Django's template engine falls back to
            # str(dict), i.e. Python repr syntax: single-quoted strings and
            # capitalized True/False/None. That's valid Python, not valid
            # JavaScript — the browser hits "ReferenceError: False is not
            # defined" on that line, which kills the entire rest of the
            # inline <script> before the DOMContentLoaded handler even
            # registers. The page loads but nothing on it ever works: no
            # job list, no node status, no settings (confirmed against a
            # HAR capture from the P310 — only the initial page + font
            # requests fire, no XHR/fetch calls at all afterward).
            # json.dumps() produces valid JS (lowercase true/false/null).
            user_settings = archive.get_user_settings(request.user.username)
            return render(request, self.template_path("app.html"), {
                'plugin_version': '0.8.5',
                'user_settings': json.dumps(user_settings),
                'resize_target_standard': archive.RESIZE_TARGET_STANDARD,
                'resize_target_high_res': archive.RESIZE_TARGET_HIGH_RES,
            })

        @login_required
        def ping(request):
            return JsonResponse({'status': 'ok', 'version': '0.8.5'})

        return [
            # ── UI ──────────────────────────────────────────────────
            MountPoint('$',                              index),
            MountPoint('ping/$',                         ping),

            # ── Job lifecycle ────────────────────────────────────────
            MountPoint('upload/$',                       api.upload_view),
            MountPoint('jobs/$',                         api.jobs_view),
            MountPoint('status/(?P<job_id>[^/]+)/$',     api.status_view),
            MountPoint('cancel/(?P<job_id>[^/]+)/$',     api.cancel_view),
            MountPoint('download-geotiff/(?P<job_id>[^/]+)/$',  api.download_geotiff_view),
            MountPoint('delete/(?P<job_id>[^/]+)/$',            api.delete_view),

            # ── Settings (v0.8.1) ────────────────────────────────────
            MountPoint('settings/$',                     api.settings_view),

            # ── Infrastructure status ────────────────────────────────
            MountPoint('node-status/$',                  api.node_status_view),
        ]
