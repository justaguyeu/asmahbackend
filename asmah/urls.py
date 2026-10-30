from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "ASK Company admin"
admin.site.site_title = "ASK Company"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("lending.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
