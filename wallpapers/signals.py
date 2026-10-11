"""Keep caches and storage in step with the database."""

from django.db.models.signals import post_delete, post_save

from .cache import bump_on_commit
from .models import (
    Category,
    CategoryPin,
    ChargingAnimation,
    HoursPhase,
    ParallaxLayer,
    Ringtone,
    Wallpaper,
    _delete_after_commit,
)
from .service_models import AppConfig, DownloadOption, PaywallHero, PaywallPlan, WallpaperTab

CONTENT_MODELS = (
    Wallpaper, Category, CategoryPin, HoursPhase, ParallaxLayer, Ringtone, ChargingAnimation,
    AppConfig, WallpaperTab, DownloadOption, PaywallPlan, PaywallHero,
)

_FILE_FIELDS = {
    Wallpaper: ("file", "thumbnail", "preview", "hd", "uhd"),
    Category: ("cover",),
    HoursPhase: ("image",),
    ParallaxLayer: ("image",),
    Ringtone: ("audio", "artwork", "artwork_thumb"),
    ChargingAnimation: ("animation", "thumbnail", "preview_video", "sound"),
}


def delete_files(sender, instance, **kwargs):
    """A deleted row takes its files with it, once the delete is committed."""
    for field in _FILE_FIELDS.get(sender, ()):
        fieldfile = getattr(instance, field)
        if fieldfile:
            _delete_after_commit(fieldfile.storage, fieldfile.name)


def connect():
    for model in CONTENT_MODELS:
        post_save.connect(bump_on_commit, sender=model, dispatch_uid=f"bump-save-{model.__name__}")
        post_delete.connect(bump_on_commit, sender=model, dispatch_uid=f"bump-del-{model.__name__}")
    for model in _FILE_FIELDS:
        post_delete.connect(delete_files, sender=model, dispatch_uid=f"files-del-{model.__name__}")
