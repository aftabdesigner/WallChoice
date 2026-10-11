"""Ringtones and charging animations (Phase 2).

Both share the Category model with wallpapers (``feature`` tells them apart)
and follow the same rules: the admin uploads source files, everything the app
loads is generated on upload, and an item is only listed once its media is
complete (``media_ready``).
"""

import os
import uuid

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from . import media
from .models import (
    Category,
    CategoryKind,
    Feature,
    _delete_after_commit,
)
from .titles import TITLE_MAX_LENGTH, clean_title, shorten
from .validators import (
    validate_animation_extension,
    validate_audio_extension,
    validate_thumbnail_extension,
)

hex_colour = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Use a colour like #FFFFFF.")


def _feature_path(prefix, instance, filename, folder):
    extension = os.path.splitext(filename)[1].lower()
    category = getattr(instance, "category", None) if getattr(instance, "category_id", None) else None
    slug = getattr(category, "slug", None) or "all"
    now = timezone.now()
    return f"{prefix}/{slug}/{folder}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}{extension}"


def ringtone_audio_path(instance, filename):
    return _feature_path("ringtones", instance, filename, "audio")


def ringtone_artwork_path(instance, filename):
    return _feature_path("ringtones", instance, filename, "artwork")


def charging_animation_path(instance, filename):
    return _feature_path("charging", instance, filename, "animations")


def charging_preview_path(instance, filename):
    return _feature_path("charging", instance, filename, "previews")


def charging_sound_path(instance, filename):
    return _feature_path("charging", instance, filename, "sounds")


def _store(fieldfile, data: bytes, name: str):
    """Write ``data`` into ``fieldfile``, removing the file it replaces."""
    old_name = fieldfile.name if getattr(fieldfile, "_committed", False) else None
    fieldfile.save(name, ContentFile(data, name=name), save=False)
    if old_name and old_name != fieldfile.name:
        _delete_after_commit(fieldfile.storage, old_name)


def _fresh(fieldfile) -> bool:
    return bool(fieldfile) and not fieldfile._committed


def _duplicate_of(model, digest, pk):
    return model.objects.filter(content_hash=digest).exclude(pk=pk).values_list("pk", flat=True).first()


def _check_category(category, feature, label):
    if category is None:
        return {"category": f"{label} need a category."}
    if category.feature != feature or category.kind != CategoryKind.STANDARD:
        return {"category": f"Pick a standard {label.lower()} category. Trending fills itself."}
    return {}


class ListedQuerySet(models.QuerySet):
    def listed(self):
        return self.filter(is_published=True, media_ready=True)


STANDARD_ORDER = ("-priority", "-created_at", "-id")


class Ringtone(models.Model):
    category = models.ForeignKey(
        Category, related_name="ringtones", on_delete=models.PROTECT,
        limit_choices_to={"feature": Feature.RINGTONE, "kind": CategoryKind.STANDARD},
    )
    title = models.CharField(
        max_length=TITLE_MAX_LENGTH, blank=True, default="",
        help_text="Max 40 characters. Left empty, a clean title is made from the file name.",
    )
    artist = models.CharField(max_length=80, blank=True, default="")
    audio = models.FileField(
        upload_to=ringtone_audio_path,
        validators=[validate_audio_extension],
        help_text="mp3, m4a, wav, ogg or flac. Converted to MP3 at an even volume; "
        "anything longer than 40 seconds is trimmed with a fade-out.",
    )
    audio_info = models.JSONField(default=dict, blank=True, editable=False)
    waveform = models.JSONField(default=list, blank=True, editable=False)
    artwork = models.FileField(
        upload_to=ringtone_artwork_path,
        validators=[validate_thumbnail_extension],
        help_text="Square image; cropped and resized to 800x800 (and a 200x200 thumbnail).",
    )
    artwork_thumb = models.FileField(upload_to=ringtone_artwork_path, blank=True, null=True, editable=False)
    artwork_info = models.JSONField(default=dict, blank=True, editable=False)
    content_hash = models.CharField(max_length=64, blank=True, null=True, editable=False)
    original_filename = models.CharField(max_length=255, blank=True, null=True, editable=False)

    is_premium = models.BooleanField(default=False)
    priority = models.IntegerField(default=0, help_text="Higher sorts first inside its category.")
    is_published = models.BooleanField(default=True)
    media_ready = models.BooleanField(default=False, editable=False)

    plays_count = models.PositiveBigIntegerField(default=0, editable=False)
    sets_count = models.PositiveBigIntegerField(default=0, editable=False)
    likes_count = models.PositiveBigIntegerField(default=0, editable=False)
    trending_score = models.FloatField(default=0, editable=False)
    popular_score = models.FloatField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ListedQuerySet.as_manager()

    class Meta:
        ordering = STANDARD_ORDER
        constraints = [
            models.UniqueConstraint(
                fields=["content_hash"], condition=models.Q(content_hash__isnull=False),
                name="ringtone_unique_content_hash",
            ),
        ]
        indexes = [
            models.Index(
                fields=["category", "is_published", "media_ready", "-priority", "-created_at", "-id"],
                name="ringtone_category_idx",
            ),
            models.Index(fields=["is_published", "media_ready", "-trending_score"], name="ringtone_trending_idx"),
            models.Index(fields=["is_published", "media_ready", "-popular_score"], name="ringtone_popular_idx"),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending = None

    def __str__(self):
        return self.title or self.original_filename or f"Ringtone #{self.pk}"

    def clean(self):
        errors = _check_category(self.category if self.category_id else None, Feature.RINGTONE, "Ringtones")
        if errors:
            raise ValidationError(errors)
        self.prepare_media()

    def prepare_media(self):
        if self._pending is not None:
            return
        pending = {}
        if _fresh(self.audio):
            filename = os.path.basename(self.audio.name)
            with media.local_path(self.audio.file, suffix=os.path.splitext(filename)[1]) as path:
                digest = media.sha256_of_path(path)
                duplicate = _duplicate_of(Ringtone, digest, self.pk)
                if duplicate:
                    raise ValidationError(
                        {"audio": f"{filename}: this exact file is already uploaded as ringtone #{duplicate}."}
                    )
                data, info = media.process_audio(path)
            with media.local_path(ContentFile(data), suffix=".mp3") as converted:
                pending["waveform"] = media.waveform(converted)
            pending["audio"] = (data, info.info, filename, digest)
        if _fresh(self.artwork):
            image = media.open_image(self.artwork.file)
            pending["artwork"] = (
                media.build_square(image, media.ARTWORK_SIZE, media.ARTWORK_MAX_BYTES),
                media.build_square(image, media.ARTWORK_THUMB_SIZE, media.ARTWORK_MAX_BYTES),
            )
        self._pending = pending

    def save(self, *args, **kwargs):
        if _fresh(self.audio) or _fresh(self.artwork):
            self.prepare_media()
        pending = self._pending or {}
        if "audio" in pending:
            data, info, filename, digest = pending["audio"]
            _store(self.audio, data, f"{os.path.splitext(filename)[0]}.mp3")
            self.audio_info = info
            self.waveform = pending["waveform"]
            self.content_hash = digest
            self.original_filename = filename[:255]
        if "artwork" in pending:
            large, thumb = pending["artwork"]
            _store(self.artwork, large.content, "artwork.webp")
            _store(self.artwork_thumb, thumb.content, "artwork_thumb.webp")
            self.artwork_info = {"large": large.info, "thumbnail": thumb.info}
        self._pending = None
        self.title = shorten(self.title) if self.title else clean_title(
            os.path.splitext(self.original_filename or "")[0], from_filename=True
        )
        self.artist = " ".join(self.artist.split())
        self.media_ready = bool(
            self.audio and self.audio_info and self.waveform and self.artwork and self.artwork_thumb
        )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"title", "artist", "media_ready"}
        super().save(*args, **kwargs)


class BatteryPosition(models.TextChoices):
    CENTER = "center", "Center"
    TOP = "top", "Top"
    BOTTOM = "bottom", "Bottom"


class ChargingAnimation(models.Model):
    category = models.ForeignKey(
        Category, related_name="charging_animations", on_delete=models.PROTECT,
        limit_choices_to={"feature": Feature.CHARGING, "kind": CategoryKind.STANDARD},
    )
    title = models.CharField(max_length=TITLE_MAX_LENGTH, blank=True, default="")
    animation = models.FileField(
        upload_to=charging_animation_path,
        validators=[validate_animation_extension],
        help_text="Lottie JSON (preferred, max 300 KB) or an mp4 loop (max 5 MB).",
    )
    animation_info = models.JSONField(default=dict, blank=True, editable=False)
    loop = models.BooleanField(default=True)
    thumbnail = models.FileField(
        upload_to=charging_preview_path, validators=[validate_thumbnail_extension], blank=True, null=True,
        help_text="9:16 grid card. Made automatically from an mp4 animation or the preview video; "
        "a Lottie animation needs this or a preview video.",
    )
    thumbnail_info = models.JSONField(default=dict, blank=True, editable=False)
    preview_video = models.FileField(
        upload_to=charging_preview_path, validators=[validate_animation_extension], blank=True, null=True,
        help_text="Optional short mp4 loop for the card and preview screen. "
        "An mp4 animation is used as its own preview.",
    )
    preview_video_info = models.JSONField(default=dict, blank=True, editable=False)
    battery_label_visible = models.BooleanField(default=True)
    battery_label_position = models.CharField(
        max_length=6, choices=BatteryPosition.choices, default=BatteryPosition.CENTER
    )
    battery_label_color = models.CharField(max_length=7, default="#FFFFFF", validators=[hex_colour])
    background_color = models.CharField(max_length=7, default="#0C0C0D", validators=[hex_colour])
    sound = models.FileField(
        upload_to=charging_sound_path, validators=[validate_audio_extension], blank=True, null=True,
        help_text="Optional sound played on plug-in (trimmed to 10 seconds).",
    )
    sound_info = models.JSONField(default=dict, blank=True, editable=False)
    content_hash = models.CharField(max_length=64, blank=True, null=True, editable=False)
    original_filename = models.CharField(max_length=255, blank=True, null=True, editable=False)

    is_premium = models.BooleanField(default=False)
    priority = models.IntegerField(default=0, help_text="Higher sorts first inside its category.")
    is_published = models.BooleanField(default=True)
    media_ready = models.BooleanField(default=False, editable=False)

    sets_count = models.PositiveBigIntegerField(default=0, editable=False)
    likes_count = models.PositiveBigIntegerField(default=0, editable=False)
    trending_score = models.FloatField(default=0, editable=False)
    popular_score = models.FloatField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ListedQuerySet.as_manager()

    class Meta:
        ordering = STANDARD_ORDER
        constraints = [
            models.UniqueConstraint(
                fields=["content_hash"], condition=models.Q(content_hash__isnull=False),
                name="charging_unique_content_hash",
            ),
        ]
        indexes = [
            models.Index(
                fields=["category", "is_published", "media_ready", "-priority", "-created_at", "-id"],
                name="charging_category_idx",
            ),
            models.Index(fields=["is_published", "media_ready", "-trending_score"], name="charging_trending_idx"),
            models.Index(fields=["is_published", "media_ready", "-popular_score"], name="charging_popular_idx"),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending = None

    def __str__(self):
        return self.title or self.original_filename or f"Charging animation #{self.pk}"

    @property
    def animation_format(self):
        return (self.animation_info or {}).get("format") or (
            "mp4" if media.is_video_name(self.animation.name) else "lottie"
        )

    def clean(self):
        errors = _check_category(
            self.category if self.category_id else None, Feature.CHARGING, "Charging animations"
        )
        if errors:
            raise ValidationError(errors)
        self.prepare_media()
        if (
            self.animation_format == "lottie"
            and not self.thumbnail
            and not self.preview_video
            and "thumbnail" not in (self._pending or {})
        ):
            raise ValidationError(
                {"thumbnail": "A Lottie animation needs a thumbnail image or a preview video."}
            )

    def prepare_media(self):
        if self._pending is not None:
            return
        pending = {}
        poster = None
        if _fresh(self.animation):
            filename = os.path.basename(self.animation.name)
            raw = self.animation.file
            with media.local_path(raw, suffix=os.path.splitext(filename)[1]) as path:
                digest = media.sha256_of_path(path)
                duplicate = _duplicate_of(ChargingAnimation, digest, self.pk)
                if duplicate:
                    raise ValidationError(
                        {"animation": f"{filename}: already uploaded as charging animation #{duplicate}."}
                    )
                if media.is_video_name(filename):
                    video = media.probe_video(path)
                    data = media.optimise_video(path)
                    if data is None:
                        with open(path, "rb") as handle:
                            data = handle.read()
                    if len(data) > media.ANIMATION_VIDEO_MAX_BYTES:
                        raise ValidationError({"animation": "MP4 animations must be at most 5 MB."})
                    video.size_bytes = len(data)
                    info = {"format": "mp4", **video.info}
                    poster = media.first_frame(path)
                else:
                    with open(path, "rb") as handle:
                        data, lottie_info = media.process_lottie(handle.read())
                    info = {"format": "lottie", **lottie_info}
            pending["animation"] = (data, info, filename, digest)
        if _fresh(self.preview_video):
            with media.local_path(self.preview_video.file, suffix=".mp4") as path:
                video = media.probe_video(path)
                data = media.optimise_video(path)
                if data is None:
                    with open(path, "rb") as handle:
                        data = handle.read()
                video.size_bytes = len(data)
                pending["preview_video"] = (data, video.info)
                if poster is None:
                    poster = media.first_frame(path)
        if _fresh(self.thumbnail):
            pending["thumbnail"] = (self._thumbnail(media.open_image(self.thumbnail.file)), True)
        elif poster is not None and not (self.thumbnail_info or {}).get("manual"):
            pending["thumbnail"] = (self._thumbnail(poster), False)
        if _fresh(self.sound):
            with media.local_path(self.sound.file, suffix=os.path.splitext(self.sound.name)[1]) as path:
                data, info = media.process_audio(path, max_seconds=media.SOUND_MAX_SECONDS)
            pending["sound"] = (data, info.info)
        self._pending = pending

    @staticmethod
    def _thumbnail(image):
        return media.encode_under(
            media.fit_width(media.crop_to_ratio(image, 9, 16), media.THUMBNAIL_WIDTH),
            media.THUMBNAIL_MAX_BYTES,
        )

    def save(self, *args, **kwargs):
        if any(_fresh(f) for f in (self.animation, self.preview_video, self.thumbnail, self.sound)):
            self.prepare_media()
        pending = self._pending or {}
        if "animation" in pending:
            data, info, filename, digest = pending["animation"]
            extension = ".mp4" if info["format"] == "mp4" else ".json"
            _store(self.animation, data, f"{os.path.splitext(filename)[0]}{extension}")
            self.animation_info = info
            self.content_hash = digest
            self.original_filename = filename[:255]
        if "preview_video" in pending:
            data, info = pending["preview_video"]
            _store(self.preview_video, data, "preview.mp4")
            self.preview_video_info = info
        if "thumbnail" in pending:
            encoded, manual = pending["thumbnail"]
            _store(self.thumbnail, encoded.content, "thumbnail.webp")
            self.thumbnail_info = {**encoded.info, "manual": manual}
        if "sound" in pending:
            data, info = pending["sound"]
            _store(self.sound, data, "sound.mp3")
            self.sound_info = info
        if not self.preview_video:
            self.preview_video_info = {}
        if not self.sound:
            self.sound_info = {}
        self._pending = None
        self.title = shorten(self.title) if self.title else clean_title(
            os.path.splitext(self.original_filename or "")[0], from_filename=True
        )
        self.battery_label_color = self.battery_label_color.upper()
        self.background_color = self.background_color.upper()
        self.media_ready = bool(self.animation and self.animation_info and self.thumbnail)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"title", "media_ready"}
        super().save(*args, **kwargs)
