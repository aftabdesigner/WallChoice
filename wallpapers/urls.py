from django.urls import re_path

from .views import (
    CategoryListView,
    ChargingCategoryListView,
    ChargingDetailView,
    ChargingListView,
    ConfigView,
    EventsView,
    RingtoneCategoryListView,
    RingtoneDetailView,
    RingtoneFeaturedView,
    RingtoneListView,
    WallpaperDetailView,
    WallpaperFeedView,
    WallpaperListView,
    WallpaperSearchView,
)

# The app calls these without a trailing slash; `/?` accepts either so a stray
# slash from a client is not a 404. Literal paths come before the id routes.
urlpatterns = [
    re_path(r"^wallpapers/categories/?$", CategoryListView.as_view(), name="wallpaper-categories"),
    re_path(r"^wallpapers/feed/?$", WallpaperFeedView.as_view(), name="wallpaper-feed"),
    re_path(r"^wallpapers/search/?$", WallpaperSearchView.as_view(), name="wallpaper-search"),
    re_path(r"^wallpapers/(?P<pk>\d+)/?$", WallpaperDetailView.as_view(), name="wallpaper-detail"),
    re_path(r"^wallpapers/?$", WallpaperListView.as_view(), name="wallpaper-list"),
    re_path(r"^ringtones/categories/?$", RingtoneCategoryListView.as_view(), name="ringtone-categories"),
    re_path(r"^ringtones/featured/?$", RingtoneFeaturedView.as_view(), name="ringtone-featured"),
    re_path(r"^ringtones/(?P<pk>\d+)/?$", RingtoneDetailView.as_view(), name="ringtone-detail"),
    re_path(r"^ringtones/?$", RingtoneListView.as_view(), name="ringtone-list"),
    re_path(
        r"^charging-animations/categories/?$",
        ChargingCategoryListView.as_view(),
        name="charging-categories",
    ),
    re_path(r"^charging-animations/(?P<pk>\d+)/?$", ChargingDetailView.as_view(), name="charging-detail"),
    re_path(r"^charging-animations/?$", ChargingListView.as_view(), name="charging-list"),
    re_path(r"^config/?$", ConfigView.as_view(), name="config"),
    re_path(r"^events/?$", EventsView.as_view(), name="events"),
]
