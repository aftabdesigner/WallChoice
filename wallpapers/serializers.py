"""Response objects, built as plain dicts.

DRF serializers cost a lot per field at list sizes of 100; these functions do
the same job with no per-field machinery. Key order matches the API spec.
Fields that do not apply are ``None``; lists are ``[]``, never ``None``.
"""

import datetime as dt

from django.conf import settings
from django.utils import timezone

from .models import CATEGORIZED_TYPES, WallpaperType


class UrlBuilder:
    """Absolute URLs for stored files.

    Object storage already returns absolute public URLs; the local-media
    fallback returns ``/media/...``, which is absolutised against the request.
    """

    def __init__(self, request):
        self.request = request
        self.local = settings.USE_LOCAL_MEDIA

    def __call__(self, fieldfile):
        if not fieldfile:
            return None
        url = fieldfile.url
        if self.local and self.request is not None and not url.startswith("http"):
            return self.request.build_absolute_uri(url)
        return url


def iso_utc(value):
    """``2026-10-11T06:14:51Z``"""
    if value is None:
        return None
    if timezone.is_aware(value):
        value = value.astimezone(dt.timezone.utc)
    return value.replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def image_object(fieldfile, info, url):
    if not fieldfile:
        return None
    info = info or {}
    return {
        "url": url(fieldfile),
        "width": info.get("width"),
        "height": info.get("height"),
        "size_bytes": info.get("size_bytes"),
    }


def video_object(fieldfile, info, url):
    if not fieldfile or not info:
        return None
    return {
        "url": url(fieldfile),
        "width": info.get("width"),
        "height": info.get("height"),
        "duration_ms": info.get("duration_ms"),
        "size_bytes": info.get("size_bytes"),
    }


def category_object(category, item_count, url):
    return {
        "id": category.id,
        "name": category.name,
        "slug": category.slug,
        "feature": category.feature,
        "wallpaper_type": category.wallpaper_type,
        "kind": category.kind,
        "display": category.display,
        "cover": image_object(category.cover, category.cover_info, url),
        "position": category.position,
        "item_count": item_count,
    }


def wallpaper_object(wallpaper, url):
    info = wallpaper.media_info or {}
    category = None
    if wallpaper.type in CATEGORIZED_TYPES and wallpaper.category_id:
        category = {"id": wallpaper.category_id, "name": wallpaper.category.name}

    hours = parallax = fluid = None
    if wallpaper.type == WallpaperType.HOURS:
        hours = {
            "phases": [
                {
                    "name": phase.name,
                    "from_hour": phase.from_hour,
                    "to_hour": phase.to_hour,
                    "image": image_object(phase.image, phase.image_info, url),
                }
                for phase in wallpaper.hours_phases.all()
            ]
        }
    elif wallpaper.type == WallpaperType.PARALLAX:
        parallax = {
            "layers": [
                {
                    "role": layer.role,
                    "depth": float(layer.depth),
                    "image": image_object(layer.image, layer.image_info, url),
                }
                for layer in wallpaper.parallax_layers.all()
            ]
        }
    elif wallpaper.type == WallpaperType.FLUID:
        fluid = {
            "hue_shift_deg": wallpaper.fluid_hue_shift_deg,
            "cycle_ms": wallpaper.fluid_cycle_ms,
        }

    return {
        "id": wallpaper.id,
        "type": wallpaper.type,
        "title": wallpaper.title,
        "category": category,
        "tags": list(wallpaper.tags or []),
        "is_premium": wallpaper.is_premium,
        "dominant_color": wallpaper.dominant_color or None,
        "media": {
            "thumbnail": image_object(wallpaper.thumbnail, info.get("thumbnail"), url),
            "preview": image_object(wallpaper.preview, info.get("preview"), url),
            "hd": image_object(wallpaper.hd, info.get("hd"), url),
            "uhd": image_object(wallpaper.uhd, info.get("uhd"), url),
            "video": video_object(wallpaper.video_file, info.get("video"), url),
        },
        "hours": hours,
        "parallax": parallax,
        "fluid": fluid,
        "stats": {
            "downloads": wallpaper.downloads_count,
            "sets": wallpaper.sets_count,
            "likes": wallpaper.likes_count,
        },
        "created_at": iso_utc(wallpaper.created_at),
    }


def audio_object(fieldfile, info, url):
    if not fieldfile or not info:
        return None
    return {
        "url": url(fieldfile),
        "format": info.get("format"),
        "duration_ms": info.get("duration_ms"),
        "size_bytes": info.get("size_bytes"),
    }


def item_category(item):
    return {"id": item.category_id, "name": item.category.name} if item.category_id else None


def ringtone_object(ringtone, url):
    artwork = ringtone.artwork_info or {}
    return {
        "id": ringtone.id,
        "title": ringtone.title,
        "artist": ringtone.artist or None,
        "category": item_category(ringtone),
        "is_premium": ringtone.is_premium,
        "artwork": {
            "thumbnail": image_object(ringtone.artwork_thumb, artwork.get("thumbnail"), url),
            "large": image_object(ringtone.artwork, artwork.get("large"), url),
        },
        "audio": audio_object(ringtone.audio, ringtone.audio_info, url),
        "waveform": list(ringtone.waveform or []),
        "stats": {"plays": ringtone.plays_count, "sets": ringtone.sets_count},
        "created_at": iso_utc(ringtone.created_at),
    }


def charging_object(item, url):
    info = item.animation_info or {}
    if item.preview_video:
        preview_video = video_object(item.preview_video, item.preview_video_info, url)
    elif info.get("format") == "mp4":
        # An mp4 animation is its own preview loop.
        preview_video = video_object(item.animation, info, url)
    else:
        preview_video = None
    return {
        "id": item.id,
        "title": item.title,
        "category": item_category(item),
        "is_premium": item.is_premium,
        "preview": {
            "thumbnail": image_object(item.thumbnail, item.thumbnail_info, url),
            "video": preview_video,
        },
        "animation": {
            "format": info.get("format"),
            "url": url(item.animation),
            "size_bytes": info.get("size_bytes"),
            "loop": item.loop,
        },
        "battery_label": {
            "visible": item.battery_label_visible,
            "position": item.battery_label_position,
            "color": item.battery_label_color,
        },
        "background_color": item.background_color,
        "sound": audio_object(item.sound, item.sound_info, url),
    }

def _blank_to_none(value):
    return value or None


def config_object(config, hero_ids):
    return {
        "min_version_code": config.min_version_code,
        "latest_version_code": config.latest_version_code,
        "features": {
            "ringtones": config.feature_ringtones,
            "charging": config.feature_charging,
            "wallpaper_search": config.feature_wallpaper_search,
        },
        "wallpaper_types": [
            {"key": tab.key, "title": tab.title, "has_categories": tab.key in CATEGORIZED_TYPES}
            for tab in config.wallpaper_tabs.all()
            if tab.is_enabled
        ],
        "ads": {
            "enabled": config.ads_enabled,
            "interstitial_cooldown_sec": config.interstitial_cooldown_sec,
            "interstitial_after": list(config.interstitial_after or []),
            "rewarded_download": config.rewarded_download,
            "rewarded_unlock": config.rewarded_unlock,
            "native_size": config.native_size,
            "native_every_rows": config.native_every_rows,
        },
        "downloads": [
            {"key": option.key, "label": option.label, "pro_only": option.pro_only}
            for option in config.download_options.all()
        ],
        "paywall": {
            "eyebrow": _blank_to_none(config.paywall_eyebrow),
            "title": config.paywall_title,
            "subtitle": _blank_to_none(config.paywall_subtitle),
            "hero_wallpaper_ids": hero_ids,
            "close_delay_sec": config.paywall_close_delay_sec,
            "plans": [
                {
                    "product_id": plan.product_id,
                    "kind": plan.kind,
                    "label": plan.label,
                    "badge": _blank_to_none(plan.badge),
                    "note": _blank_to_none(plan.note),
                    "is_default": plan.is_default,
                }
                for plan in config.paywall_plans.all()
                if plan.is_active
            ],
        },
        "onboarding": {
            "welcome_title": config.welcome_title,
            "welcome_subtitle": _blank_to_none(config.welcome_subtitle),
            "tips": config.tips,
        },
        "links": {
            "terms_url": _blank_to_none(config.terms_url),
            "privacy_url": _blank_to_none(config.privacy_url),
            "support_email": _blank_to_none(config.support_email),
            "store_url": _blank_to_none(config.store_url),
        },
    }