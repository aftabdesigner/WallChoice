from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("wallpapers.urls")),
]

if settings.USE_LOCAL_MEDIA:
    # Only for the no-object-storage fallback; otherwise the bucket serves files.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# API paths answer in the JSON error shape; everything else keeps Django's pages.
handler404 = "wallpapers.exceptions.handler404"
handler500 = "wallpapers.exceptions.handler500"
