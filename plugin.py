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

        @login_required
        def index(request):
            return render(request, self.template_path("app.html"), {
                'plugin_version': '0.7.2',
            })

        @login_required
        def ping(request):
            return JsonResponse({'status': 'ok', 'version': '0.7.2'})

        return [
            # ── UI ──────────────────────────────────────────────────
            MountPoint('$',                              index),
            MountPoint('ping/$',                         ping),

            # ── Job lifecycle ────────────────────────────────────────
            MountPoint('upload/$',                       api.upload_view),
            MountPoint('jobs/$',                         api.jobs_view),
            MountPoint('status/(?P<job_id>[^/]+)/$',     api.status_view),
            MountPoint('cancel/(?P<job_id>[^/]+)/$',     api.cancel_view),
            MountPoint('download/(?P<job_id>[^/]+)/$',          api.download_view),
            MountPoint('download-geotiff/(?P<job_id>[^/]+)/$',  api.download_geotiff_view),
            MountPoint('delete/(?P<job_id>[^/]+)/$',            api.delete_view),

            # ── Infrastructure status ────────────────────────────────
            MountPoint('node-status/$',                  api.node_status_view),
        ]
