from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("checks/run/", views.run_checks, name="run_checks"),

    # shipments
    path("shipments/", views.shipment_list, name="shipment_list"),
    path("shipments/new/", views.shipment_create, name="shipment_create"),
    path("shipments/<int:pk>/", views.shipment_detail, name="shipment_detail"),
    path("shipments/<int:pk>/edit/", views.shipment_edit, name="shipment_edit"),
    path("shipments/<int:pk>/archive/", views.shipment_archive, name="shipment_archive"),
    path("shipments/<int:pk>/restore/", views.shipment_restore, name="shipment_restore"),
    path("shipments/<int:pk>/events/add/", views.event_add, name="event_add"),
    path("shipments/<int:pk>/documents/upload/", views.document_upload, name="document_upload"),
    path("shipments/<int:pk>/versions/", views.shipment_versions, name="shipment_versions"),
    path("shipments/<int:pk>/versions/<int:number>/restore/", views.version_restore, name="version_restore"),
    path("shipments/<int:pk>/deliver/", views.delivery_confirm, name="delivery_confirm"),
    path("shipments/<int:pk>/pod/", views.pod_view, name="pod_view"),
    path("pod/<int:pk>/<str:kind>/", views.pod_file, name="pod_file"),

    # documents
    path("documents/<int:pk>/", views.document_download, name="document_download"),
    path("documents/<int:pk>/make-current/", views.document_current, name="document_current"),

    # duplicates
    path("duplicates/", views.duplicates, name="duplicates"),
    path("duplicates/scan/", views.scan_duplicates, name="scan_duplicates"),
    path("duplicates/<int:pk>/resolve/", views.duplicate_resolve, name="duplicate_resolve"),

    # notifications
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/read-all/", views.notifications_read_all, name="notifications_read_all"),
    path("notifications/<int:pk>/open/", views.notification_open, name="notification_open"),

    # reports & audit
    path("reports/", views.reports, name="reports"),
    path("reports/export.csv", views.report_csv, name="report_csv"),
    path("audit/", views.audit_log, name="audit_log"),

    # public tracking
    path("track/", views.track, name="track"),
    path("track/<str:number>/", views.track_result, name="track_result"),

    # JSON polling endpoints
    path("api/dashboard/", views.api_dashboard, name="api_dashboard"),
    path("api/notifications/", views.api_notifications, name="api_notifications"),
    path("api/shipments/<int:pk>/state/", views.api_shipment_state, name="api_shipment_state"),
    path("api/pulse/", views.api_pulse, name="api_pulse"),
]
