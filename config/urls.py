from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "CBSRMS Administration"
admin.site.site_title = "CBSRMS Admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("", include("shipments.urls")),
]
