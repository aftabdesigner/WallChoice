"""The pre-Phase-1 response shape, for app builds already in users' hands.

Those builds call ``/wallpapers/categories`` and ``/wallpapers`` without a
``type`` parameter and parse the old flat rows (``artwork``,
``thumbnail_path``, ``artwork_count``...). New builds always send ``type``,
so its absence is what selects this shape. Only Live and Image items are
listed here - the old app cannot draw the other three types.
"""

import datetime as dt

from django.db.models import Count, Q
from django.utils import timezone

from .models import Category, CategoryKind, Feature, Wallpaper, WallpaperType

LEGACY_TYPES = (WallpaperType.LIVE, WallpaperType.IMAGE)


def legacy_categories():
    return (
        Category.objects.filter(
            is_active=True, feature=Feature.WALLPAPER, kind=CategoryKind.STANDARD
        )
        .annotate(
            artwork_count=Count(
                "wallpapers",
                filter=Q(wallpapers__is_published=True, wallpapers__type__in=LEGACY_TYPES),
            )
        )
        .order_by("-id")
    )


def legacy_wallpapers():
    return (
        Wallpaper.objects.filter(is_published=True, type__in=LEGACY_TYPES)
        .exclude(file="")
        .select_related("category")
    )


def legacy_category_row(category):
    return {
        "id": category.id,
        "name": category.name,
        "description": category.description or None,
        "position": category.position,
        "artwork_count": category.artwork_count,
    }


def _naive_utc(value):
    if timezone.is_aware(value):
        value = timezone.make_naive(value, dt.timezone.utc)
    return value.isoformat(timespec="microseconds")


def legacy_wallpaper_row(wallpaper, url):
    if wallpaper.type == WallpaperType.LIVE:
        media_type = "video"
    elif wallpaper.file.name.lower().endswith(".gif"):
        media_type = "gif"
    else:
        media_type = "image"
    return {
        "id": wallpaper.id,
        "category_id": wallpaper.category_id,
        "media_type": media_type,
        "is_premium": wallpaper.is_premium,
        "title": wallpaper.title,
        "artwork": url(wallpaper.file),
        "created_at": _naive_utc(wallpaper.created_at),
        "thumbnail_path": url(wallpaper.thumbnail),
    }
