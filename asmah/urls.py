from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Asmah Finance admin"
admin.site.site_title = "Asmah Finance"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("lending.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
