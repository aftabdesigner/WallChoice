"""Data model behind the wallpaper feeds.

Everything the API exposes is derived here rather than in the views, so a row
created from the admin, a shell or a future import all look identical.

Media: the admin uploads one source file (``file``). Every rendition the app
loads - thumbnail, preview, hd, uhd, the cleaned-up video - is generated from
it on upload, with its width, height and byte size kept in ``media_info`` so
the API never has to touch storage to describe a file.
"""

import os
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.text import slugify

from . import media
from .titles import TITLE_MAX_LENGTH, clean_title, shorten
from .titles import title_from_filename as _title_from_filename
from .validators import (
    validate_image_extension,
    validate_media_extension,
    validate_thumbnail_extension,
)


class Feature(models.TextChoices):
    WALLPAPER = "wallpaper", "Wallpaper"
    RINGTONE = "ringtone", "Ringtone"
    CHARGING = "charging", "Charging animation"


class WallpaperType(models.TextChoices):
    LIVE = "live", "Live"
    IMAGE = "image", "Image"
    HOURS = "hours", "24 Hours"
    PARALLAX = "parallax", "Parallax"
    FLUID = "fluid", "Fluid"


# Types that are browsed by category. Parallax and Fluid are one flat list.
CATEGORIZED_TYPES = (WallpaperType.LIVE, WallpaperType.IMAGE, WallpaperType.HOURS)
# Types whose cards come from child rows when no source file was uploaded.
COMPOSED_TYPES = (WallpaperType.HOURS, WallpaperType.PARALLAX)


class CategoryKind(models.TextChoices):
    TRENDING = "trending", "Trending (automatic)"
    POPULAR = "popular", "Popular (automatic)"
    STANDARD = "standard", "Standard"


AUTOMATIC_KINDS = (CategoryKind.TRENDING, CategoryKind.POPULAR)


class CategoryDisplay(models.TextChoices):
    ROW = "row", "Row (section with See all)"
    CHIP = "chip", "Chip (category bar)"


def title_from_filename(filename: str) -> str:
    """Kept here because migration 0006 imports it from this module."""
    return _title_from_filename(filename)


# --- upload paths ---------------------------------------------------------------


def _owner(instance):
    return instance if isinstance(instance, Wallpaper) else getattr(instance, "wallpaper", None)


def _media_path(instance, filename: str, folder: str) -> str:
    """wallpaper/<type>/<category-slug>/<folder>/<YYYY>/<MM>/<uuid4hex>.<ext>

    The stored name is random, so a re-upload never collides, the original
    name is kept separately, and every file can be cached forever.
    """
    extension = os.path.splitext(filename)[1].lower()
    owner = _owner(instance)
    wallpaper_type = getattr(owner, "type", None) or "misc"
    category = getattr(owner, "category", None)
    category_slug = getattr(category, "slug", None) or "all"
    now = timezone.now()
    return (
        f"wallpaper/{wallpaper_type}/{category_slug}/{folder}/"
        f"{now:%Y}/{now:%m}/{uuid.uuid4().hex}{extension}"
    )


def wallpaper_upload_path(instance, filename: str) -> str:
    return _media_path(instance, filename, "assets")


def thumbnail_upload_path(instance, filename: str) -> str:
    return _media_path(instance, filename, "thumbs")


def rendition_upload_path(instance, filename: str) -> str:
    return _media_path(instance, filename, "renditions")


def layer_upload_path(instance, filename: str) -> str:
    return _media_path(instance, filename, "layers")


def category_cover_path(instance, filename: str) -> str:
    extension = os.path.splitext(filename)[1].lower()
    now = timezone.now()
    return f"categories/{instance.feature}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}{extension}"


def _delete_after_commit(storage, name):
    """Remove a replaced file once the row pointing elsewhere is committed."""
    if name:
        transaction.on_commit(lambda: _safe_delete(storage, name))


def _safe_delete(storage, name):
    try:
        storage.delete(name)
    except Exception:  # noqa: BLE001 - a leftover file must never fail a request
        pass


def _replace_file(fieldfile, encoded: media.Encoded, stem: str):
    """Store ``encoded`` in ``fieldfile``, scheduling the old file's removal."""
    old_name = fieldfile.name
    fieldfile.save(f"{stem}.webp", encoded.as_file(f"{stem}.webp"), save=False)
    if old_name and old_name != fieldfile.name:
        _delete_after_commit(fieldfile.storage, old_name)


# --- content version (cache invalidation) ---------------------------------------


class ContentVersion(models.Model):
    """One row whose counter moves whenever published content changes.

    API responses are cached under the current value, so bumping it makes
    every worker serve fresh data on its next request without having to find
    and delete individual cache entries.
    """

    value = models.PositiveBigIntegerField(default=1)

    class Meta:
        verbose_name = "content version"


# --- categories ---------------------------------------------------------------------


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(
        max_length=140,
        unique=True,
        blank=True,
        help_text="Used in media paths. Left blank, it is built from the name and type.",
    )
    description = models.TextField(blank=True, null=True)
    feature = models.CharField(
        max_length=12, choices=Feature.choices, default=Feature.WALLPAPER, db_index=True
    )
    wallpaper_type = models.CharField(
        max_length=10,
        choices=[(t.value, t.label) for t in CATEGORIZED_TYPES],
        blank=True,
        null=True,
        help_text="Live, Image or 24 Hours. Empty for ringtone and charging categories.",
    )
    kind = models.CharField(
        max_length=10,
        choices=CategoryKind.choices,
        default=CategoryKind.STANDARD,
        help_text="Trending and Popular fill themselves from usage; standard ones are assigned by hand.",
    )
    display = models.CharField(
        max_length=4,
        choices=CategoryDisplay.choices,
        blank=True,
        help_text="Left blank: Trending and Popular show as rows, everything else as chips.",
    )
    cover = models.FileField(
        upload_to=category_cover_path,
        validators=[validate_thumbnail_extension],
        blank=True,
        null=True,
        help_text="Cropped to 600x375 automatically. Shown on Image Wallpapers category tiles.",
    )
    cover_info = models.JSONField(default=dict, blank=True, editable=False)
    position = models.PositiveIntegerField(
        default=0, db_index=True, help_text="Order on screen; lower comes first."
    )
    is_active = models.BooleanField(
        default=True, help_text="Unticked, the category disappears from the API."
    )
    auto_limit = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
        help_text="Trending and Popular only: how many top items the category holds.",
    )
    featured_ringtone = models.ForeignKey(
        "Ringtone",
        related_name="+",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Ringtone categories only: the TOP RINGTONE card. Left empty, the first item is used.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("feature", "wallpaper_type", "position", "id")
        verbose_name_plural = "categories"
        constraints = [
            models.UniqueConstraint(
                fields=["feature", "wallpaper_type", "name"], name="category_unique_name_per_type"
            ),
        ]
        indexes = [
            models.Index(
                fields=["feature", "wallpaper_type", "is_active", "position"],
                name="category_listing_idx",
            ),
        ]

    def __str__(self) -> str:
        if self.feature == Feature.WALLPAPER and self.wallpaper_type:
            return f"{self.name} ({WallpaperType(self.wallpaper_type).label})"
        return f"{self.name} ({Feature(self.feature).label})"

    @property
    def is_automatic(self) -> bool:
        return self.kind in AUTOMATIC_KINDS

    def clean(self):
        errors = {}
        if self.feature == Feature.WALLPAPER:
            if self.wallpaper_type not in CATEGORIZED_TYPES:
                errors["wallpaper_type"] = (
                    "Wallpaper categories need a type: Live, Image or 24 Hours. "
                    "Parallax and Fluid have no categories."
                )
        elif self.wallpaper_type:
            errors["wallpaper_type"] = "Only wallpaper categories have a wallpaper type."
        if self.feature != Feature.WALLPAPER and self.name:
            # The database constraint cannot catch this one: wallpaper_type is
            # NULL here, and NULLs never compare equal.
            if Category.objects.filter(feature=self.feature, name__iexact=self.name).exclude(pk=self.pk).exists():
                errors["name"] = f"There is already a {self.get_feature_display().lower()} category with this name."
        if self.featured_ringtone_id:
            if self.feature != Feature.RINGTONE:
                errors["featured_ringtone"] = "Only ringtone categories have a featured ringtone."
            elif not self.is_automatic and self.featured_ringtone.category_id != self.pk:
                errors["featured_ringtone"] = "Pick a ringtone from this category."
        if self.is_automatic:
            clash = Category.objects.filter(
                feature=self.feature, wallpaper_type=self.wallpaper_type, kind=self.kind
            ).exclude(pk=self.pk)
            if clash.exists():
                errors["kind"] = f"There is already a {self.get_kind_display()} category here."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        if not self.description:
            self.description = None
        if self.feature != Feature.WALLPAPER:
            # Ringtones and charging always show their categories as chips.
            self.wallpaper_type = None
            self.display = CategoryDisplay.CHIP
        elif not self.display:
            self.display = CategoryDisplay.ROW if self.is_automatic else CategoryDisplay.CHIP
        if self.cover and not self.cover._committed:
            encoded = media.build_cover(media.open_image(self.cover.file))
            self.cover.save("cover.webp", encoded.as_file("cover.webp"), save=False)
            self.cover_info = encoded.info
        elif not self.cover:
            self.cover_info = {}

        name_changed = False
        if self.pk:
            previous = Category.objects.filter(pk=self.pk).values_list("name", flat=True).first()
            name_changed = previous is not None and previous != self.name
        super().save(*args, **kwargs)
        if name_changed:
            # The name is part of every member's search text.
            for wallpaper in self.wallpapers.select_related("category").iterator():
                Wallpaper.objects.filter(pk=wallpaper.pk).update(
                    search_document=wallpaper.build_search_document()
                )

    def _unique_slug(self) -> str:
        parts = [self.name]
        if self.feature == Feature.WALLPAPER and self.wallpaper_type:
            parts.append(self.wallpaper_type)
        elif self.feature != Feature.WALLPAPER:
            parts.append(self.feature)
        base = (slugify(" ".join(parts)) or "category")[:130]
        slug, counter = base, 2
        while Category.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base}-{counter}"
            counter += 1
        return slug


class CategoryPin(models.Model):
    """An item the admin pins to the top of a Trending or Popular category."""

    category = models.ForeignKey(Category, related_name="pins", on_delete=models.CASCADE)
    # Exactly one of these is set, matching the category's feature.
    wallpaper = models.ForeignKey(
        "Wallpaper", related_name="pins", on_delete=models.CASCADE, null=True, blank=True
    )
    ringtone = models.ForeignKey(
        "Ringtone", related_name="pins", on_delete=models.CASCADE, null=True, blank=True
    )
    charging_animation = models.ForeignKey(
        "ChargingAnimation", related_name="pins", on_delete=models.CASCADE, null=True, blank=True
    )
    position = models.PositiveSmallIntegerField(default=0)

    ITEM_FIELDS = {
        Feature.WALLPAPER: "wallpaper",
        Feature.RINGTONE: "ringtone",
        Feature.CHARGING: "charging_animation",
    }

    class Meta:
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(fields=["category", "wallpaper"], name="pin_unique_per_category"),
            models.UniqueConstraint(fields=["category", "ringtone"], name="pin_unique_ringtone"),
            models.UniqueConstraint(
                fields=["category", "charging_animation"], name="pin_unique_charging"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(wallpaper__isnull=False, ringtone__isnull=True, charging_animation__isnull=True)
                    | models.Q(wallpaper__isnull=True, ringtone__isnull=False, charging_animation__isnull=True)
                    | models.Q(wallpaper__isnull=True, ringtone__isnull=True, charging_animation__isnull=False)
                ),
                name="pin_exactly_one_item",
            ),
        ]

    @property
    def item(self):
        return self.wallpaper or self.ringtone or self.charging_animation

    def __str__(self) -> str:
        return f"{self.item} pinned in {self.category}"

    def clean(self):
        category = getattr(self, "category", None)
        if category is None:
            return
        if not category.is_automatic:
            raise ValidationError("Only Trending and Popular categories can have pinned items.")
        expected = self.ITEM_FIELDS[category.feature]
        chosen = [f for f in self.ITEM_FIELDS.values() if getattr(self, f"{f}_id")]
        if chosen != [expected]:
            raise ValidationError({expected: f"Pick one {expected.replace('_', ' ')} to pin."})
        if expected == "wallpaper" and self.wallpaper.type != category.wallpaper_type:
            raise ValidationError(
                {"wallpaper": f"This category holds {category.wallpaper_type} wallpapers only."}
            )


# --- wallpapers -----------------------------------------------------------------------


class WallpaperQuerySet(models.QuerySet):
    def listed(self):
        """What the public API may show: published and with its media built."""
        return self.filter(is_published=True, media_ready=True)


STANDARD_ORDER = ("-priority", "-created_at", "-id")
RENDITION_FIELDS = ("thumbnail", "preview", "hd", "uhd")


class Wallpaper(models.Model):
    type = models.CharField(
        max_length=10, choices=WallpaperType.choices, default=WallpaperType.IMAGE, db_index=True
    )
    category = models.ForeignKey(
        Category,
        related_name="wallpapers",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Required for Live, Image and 24 Hours. Parallax and Fluid have none.",
    )
    title = models.CharField(
        max_length=TITLE_MAX_LENGTH,
        blank=True,
        default="",
        help_text="Max 40 characters. Left empty, a clean title is made from the file name.",
    )
    tags = models.JSONField(default=list, blank=True, help_text="Used by search.")
    file = models.FileField(
        upload_to=wallpaper_upload_path,
        validators=[validate_media_extension],
        blank=True,
        help_text=(
            "Live: mp4. Image: jpeg, png or webp. Fluid: an mp4 loop or a still image. "
            "24 Hours and Parallax: optional - the card is built from the phases/layers."
        ),
    )
    thumbnail = models.FileField(
        upload_to=thumbnail_upload_path,
        validators=[validate_thumbnail_extension],
        blank=True,
        null=True,
        help_text="Generated automatically. Upload one only to replace the generated image.",
    )
    preview = models.FileField(
        upload_to=rendition_upload_path,
        validators=[validate_thumbnail_extension],
        blank=True,
        null=True,
        help_text="Generated automatically (the first frame for videos). Upload to replace it.",
    )
    hd = models.FileField(upload_to=rendition_upload_path, blank=True, null=True, editable=False)
    uhd = models.FileField(upload_to=rendition_upload_path, blank=True, null=True, editable=False)
    media_info = models.JSONField(default=dict, blank=True, editable=False)
    media_ready = models.BooleanField(default=False, editable=False)
    dominant_color = models.CharField(max_length=7, blank=True, editable=False)
    content_hash = models.CharField(max_length=64, blank=True, null=True, editable=False)
    original_filename = models.CharField(max_length=255, blank=True, null=True, editable=False)

    fluid_hue_shift_deg = models.SmallIntegerField(
        default=45,
        validators=[MinValueValidator(-360), MaxValueValidator(360)],
        help_text="Fluid only: how far the colours shift during one cycle.",
    )
    fluid_cycle_ms = models.PositiveIntegerField(
        default=8000,
        validators=[MinValueValidator(1000), MaxValueValidator(120000)],
        help_text="Fluid only: length of one animation cycle in milliseconds.",
    )

    is_premium = models.BooleanField(default=False)
    priority = models.IntegerField(
        default=0, help_text="Higher sorts first inside standard categories, ahead of newer items."
    )
    is_published = models.BooleanField(
        default=True, help_text="Unticked, the item disappears from the API and every count."
    )

    # Filled from usage events (Phase 3). Kept on the row so ordering by them
    # is an index scan rather than an aggregate over the events table.
    downloads_count = models.PositiveBigIntegerField(default=0, editable=False)
    sets_count = models.PositiveBigIntegerField(default=0, editable=False)
    likes_count = models.PositiveBigIntegerField(default=0, editable=False)
    views_count = models.PositiveBigIntegerField(default=0, editable=False)
    trending_score = models.FloatField(default=0, editable=False)
    popular_score = models.FloatField(default=0, editable=False)

    search_document = models.TextField(blank=True, default="", editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WallpaperQuerySet.as_manager()

    class Meta:
        ordering = STANDARD_ORDER
        constraints = [
            models.UniqueConstraint(
                fields=["content_hash"],
                condition=models.Q(content_hash__isnull=False),
                name="wp_unique_content_hash",
            ),
        ]
        indexes = [
            models.Index(
                fields=["type", "is_published", "media_ready", "-priority", "-created_at", "-id"],
                name="wp_type_feed_idx",
            ),
            models.Index(
                fields=["category", "is_published", "media_ready", "-priority", "-created_at", "-id"],
                name="wp_category_feed_idx",
            ),
            models.Index(
                fields=["type", "is_published", "media_ready", "-trending_score"],
                name="wp_trending_idx",
            ),
            models.Index(
                fields=["type", "is_published", "media_ready", "-popular_score"],
                name="wp_popular_idx",
            ),
            models.Index(fields=["is_published", "-priority", "-created_at"], name="wp_feed_idx"),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._media_prepared = False

    def __str__(self) -> str:
        return self.title or self.original_filename or f"Wallpaper #{self.pk}"

    # --- validation -------------------------------------------------------------

    @property
    def has_fresh_upload(self) -> bool:
        return bool(self.file) and not self.file._committed

    def clean(self):
        errors = {}
        category = self.category if self.category_id else None
        if self.type in CATEGORIZED_TYPES:
            if category is None:
                errors["category"] = "Live, Image and 24 Hours wallpapers need a category."
            elif (
                category.feature != Feature.WALLPAPER
                or category.wallpaper_type != self.type
                or category.kind != CategoryKind.STANDARD
            ):
                errors["category"] = (
                    f"Pick a standard {WallpaperType(self.type).label} category. "
                    "Trending and Popular fill themselves."
                )
        elif category is not None:
            errors["category"] = "Parallax and Fluid wallpapers have no category - leave it empty."

        name = self.file.name if self.file else ""
        if self.type == WallpaperType.LIVE and (not name or not media.is_video_name(name)):
            errors["file"] = "A live wallpaper needs an mp4 video."
        elif self.type == WallpaperType.IMAGE and (not name or media.is_video_name(name)):
            errors["file"] = "An image wallpaper needs a jpeg, png or webp image."
        elif self.type == WallpaperType.FLUID and not name:
            errors["file"] = "A fluid wallpaper needs an mp4 loop or a still image."
        elif self.type in COMPOSED_TYPES and name and media.is_video_name(name):
            errors["file"] = "24 Hours and Parallax wallpapers take an image here, not a video."
        if errors:
            raise ValidationError(errors)

        # Heavy work runs here, during form validation, so a broken file comes
        # back as a form error rather than a crash on save.
        self.prepare_media()

    # --- saving -----------------------------------------------------------------

    def save(self, *args, **kwargs):
        if self.has_fresh_upload:
            self.prepare_media()
            self._commit_prepared_media()
        if self.title:
            self.title = shorten(self.title)
        else:
            self.title = clean_title(
                os.path.splitext(self.original_filename or "")[0], from_filename=True
            )
        if self.type not in CATEGORIZED_TYPES:
            self.category = None
        self.tags = normalise_tags(self.tags)
        self.search_document = self.build_search_document()
        self.media_ready = self.compute_media_ready()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {
                "title", "tags", "search_document", "media_ready", "category"
            }
        super().save(*args, **kwargs)

    def prepare_media(self):
        """Hash, de-duplicate and render a freshly uploaded source file.

        Runs once per upload: ``clean()`` calls it during form validation and
        ``save()`` reuses the result. Nothing is written to storage here.
        """
        if self._media_prepared or not self.has_fresh_upload:
            return
        upload = self.file.file
        filename = os.path.basename(self.file.name)
        is_video = media.is_video_name(filename)
        pending = {}
        with media.local_path(upload, suffix=os.path.splitext(filename)[1]) as path:
            digest = media.sha256_of_path(path)
            duplicate = (
                Wallpaper.objects.filter(content_hash=digest)
                .exclude(pk=self.pk)
                .values_list("pk", flat=True)
                .first()
            )
            if duplicate:
                raise ValidationError(
                    {"file": f"{filename}: this exact file is already uploaded as wallpaper #{duplicate}."}
                )
            if is_video:
                video = media.probe_video(path)
                optimised = media.optimise_video(path)
                if optimised is not None:
                    pending["video_bytes"] = optimised
                    video.size_bytes = len(optimised)
                pending["video_info"] = video.info
                poster = media.first_frame(path)
                pending["images"] = media.build_wallpaper_images(
                    poster, with_hd=self.type == WallpaperType.FLUID
                )
            else:
                pending["images"] = media.build_wallpaper_images(media.open_image(path))
        self.content_hash = digest
        self.original_filename = filename[:255]
        self._pending_media = pending
        self._media_prepared = True

    def _commit_prepared_media(self):
        pending = getattr(self, "_pending_media", None)
        if not pending:
            return
        stem = os.path.splitext(os.path.basename(self.file.name))[0] or "wallpaper"
        info = dict(self.media_info or {})
        video_bytes = pending.get("video_bytes")
        if video_bytes is not None:
            # Same extension, so the upload path still ends in .mp4.
            from django.core.files.base import ContentFile

            self.file = ContentFile(video_bytes, name=os.path.basename(self.file.name))
        if "video_info" in pending:
            info["video"] = pending["video_info"]
        else:
            info.pop("video", None)
        self._apply_image_set(pending["images"], info, stem)
        self.media_info = info
        self._pending_media = None

    def _apply_image_set(self, image_set, info, stem, *, respect_manual=True):
        manual = set(info.get("manual", []))
        for field in RENDITION_FIELDS:
            encoded = image_set["renditions"].get(field)
            fieldfile = getattr(self, field)
            if respect_manual and field in manual and fieldfile:
                continue
            if encoded is None:
                if fieldfile:
                    _delete_after_commit(fieldfile.storage, fieldfile.name)
                setattr(self, field, None)
                info.pop(field, None)
                continue
            _replace_file(fieldfile, encoded, f"{stem}_{field}")
            info[field] = encoded.info
        self.dominant_color = image_set["dominant_color"]

    def apply_manual_rendition(self, field: str):
        """Normalise a thumbnail/preview the admin uploaded by hand."""
        fieldfile = getattr(self, field)
        image = media.open_image(fieldfile.file)
        if field == "thumbnail":
            encoded = media.encode_under(
                media.fit_width(media.crop_to_ratio(image, 9, 16), media.THUMBNAIL_WIDTH),
                media.THUMBNAIL_MAX_BYTES,
            )
        else:
            encoded = media.encode_under(
                media.fit_width(image, media.PREVIEW_WIDTH), media.PREVIEW_MAX_BYTES
            )
        fieldfile.save(f"manual_{field}.webp", encoded.as_file(f"manual_{field}.webp"), save=False)
        info = dict(self.media_info or {})
        info[field] = encoded.info
        info["manual"] = sorted(set(info.get("manual", [])) | {field})
        if field == "preview":
            self.dominant_color = media.dominant_color(image)
        self.media_info = info

    def rebuild_composed_media(self):
        """Build the card images of a 24 Hours / Parallax item from its children.

        Only used when no source file was uploaded: 24 Hours takes the phase
        shown at midday, Parallax flattens its layers.
        """
        if self.file or self.type not in COMPOSED_TYPES or not self.pk:
            return False
        if self.type == WallpaperType.HOURS:
            phases = list(self.hours_phases.all())
            if not phases:
                return False
            chosen = next((p for p in phases if p.covers(12)), phases[0])
            with chosen.image.open("rb") as handle:
                image = media.open_image(handle)
        else:
            layers = list(self.parallax_layers.all())
            if len(layers) < 1:
                return False
            images = []
            for layer in layers:
                with layer.image.open("rb") as handle:
                    images.append(media.open_image(handle))
            image = media.composite_layers(images)
        info = dict(self.media_info or {})
        self._apply_image_set(media.build_wallpaper_images(image), info, f"{self.type}{self.pk}")
        self.media_info = info
        self.save(update_fields=[*RENDITION_FIELDS, "media_info", "dominant_color"])
        return True

    def rebuild_media_from_storage(self):
        """Regenerate every rendition from the already-stored source file.

        Used by the ``rebuild_media`` command and admin action for rows
        uploaded before renditions existed. Hand-picked thumbnails survive.
        """
        if not self.file:
            return self.rebuild_composed_media()
        filename = os.path.basename(self.file.name)
        is_video = media.is_video_name(filename)
        with self.file.open("rb") as handle:
            with media.local_path(handle, suffix=os.path.splitext(filename)[1]) as path:
                digest = media.sha256_of_path(path)
                info = dict(self.media_info or {})
                if is_video:
                    info["video"] = media.probe_video(path).info
                    image_set = media.build_wallpaper_images(
                        media.first_frame(path), with_hd=self.type == WallpaperType.FLUID
                    )
                else:
                    info.pop("video", None)
                    image_set = media.build_wallpaper_images(media.open_image(path))
        duplicate = (
            Wallpaper.objects.filter(content_hash=digest).exclude(pk=self.pk).values_list("pk", flat=True).first()
        )
        stem = os.path.splitext(filename)[0] or "wallpaper"
        self._apply_image_set(image_set, info, stem)
        self.media_info = info
        fields = [*RENDITION_FIELDS, "media_info", "dominant_color"]
        if duplicate:
            # The older upload keeps the file; this copy is hidden.
            self.is_published = False
            fields.append("is_published")
        else:
            self.content_hash = digest
            fields.append("content_hash")
        self.save(update_fields=fields)
        return duplicate

    # --- derived values -----------------------------------------------------------

    def build_search_document(self) -> str:
        category_name = self.category.name if self.category_id and self.category else ""
        return " ".join(
            part for part in [self.title, category_name, " ".join(self.tags or [])] if part
        ).lower()

    def compute_media_ready(self) -> bool:
        if not (self.thumbnail and self.preview):
            return False
        if self.type == WallpaperType.LIVE:
            return bool(self.file) and "video" in (self.media_info or {})
        if self.type == WallpaperType.FLUID:
            return bool(self.hd) or (bool(self.file) and "video" in (self.media_info or {}))
        if self.type == WallpaperType.HOURS:
            return bool(self.hd) and bool(self.pk) and self.hours_phases.exists()
        if self.type == WallpaperType.PARALLAX:
            return bool(self.hd) and bool(self.pk) and self.parallax_layers.count() >= 2
        return bool(self.hd)

    @property
    def video_file(self):
        """The served video: the source itself, once it has been processed."""
        if self.file and media.is_video_name(self.file.name) and "video" in (self.media_info or {}):
            return self.file
        return None


def normalise_tags(tags) -> list:
    if isinstance(tags, str):
        tags = tags.split(",")
    seen, result = set(), []
    for tag in tags or []:
        value = " ".join(str(tag).split()).lower()[:30]
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result[:20]


# --- type-specific children -------------------------------------------------------------


class HoursPhase(models.Model):
    """One time-of-day picture of a 24 Hours wallpaper."""

    wallpaper = models.ForeignKey(Wallpaper, related_name="hours_phases", on_delete=models.CASCADE)
    name = models.CharField(max_length=30, help_text="Dawn, Day, Dusk, Night…")
    from_hour = models.PositiveSmallIntegerField(validators=[MaxValueValidator(23)])
    to_hour = models.PositiveSmallIntegerField(
        validators=[MaxValueValidator(23)],
        help_text="Exclusive. A phase may wrap past midnight: 20 to 5 is Night.",
    )
    image = models.FileField(upload_to=rendition_upload_path, validators=[validate_image_extension])
    image_info = models.JSONField(default=dict, blank=True, editable=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "from_hour", "id")

    def __str__(self) -> str:
        return f"{self.name} {self.from_hour:02d}-{self.to_hour:02d}"

    def hours(self) -> list:
        if self.from_hour == self.to_hour:
            return list(range(24))
        end = self.to_hour if self.to_hour > self.from_hour else self.to_hour + 24
        return [hour % 24 for hour in range(self.from_hour, end)]

    def covers(self, hour: int) -> bool:
        return hour in self.hours()

    def save(self, *args, **kwargs):
        if self.image and not self.image._committed:
            encoded = media.build_phase_image(media.open_image(self.image.file))
            name = f"phase_{slugify(self.name) or 'image'}.webp"
            self.image.save(name, encoded.as_file(name), save=False)
            self.image_info = encoded.info
        super().save(*args, **kwargs)


class ParallaxRole(models.TextChoices):
    BACKGROUND = "background", "Background"
    MIDDLE = "middle", "Middle"
    FOREGROUND = "foreground", "Foreground"


class ParallaxLayer(models.Model):
    """One layer of a Parallax wallpaper, back to front by ``position``."""

    wallpaper = models.ForeignKey(Wallpaper, related_name="parallax_layers", on_delete=models.CASCADE)
    role = models.CharField(max_length=12, choices=ParallaxRole.choices)
    depth = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text="0 = does not move when the phone tilts, 1 = moves the most.",
    )
    image = models.FileField(
        upload_to=layer_upload_path,
        validators=[validate_image_extension],
        help_text="Layers above the background need a transparent background (PNG or WebP).",
    )
    image_info = models.JSONField(default=dict, blank=True, editable=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")

    def __str__(self) -> str:
        return f"{self.get_role_display()} ({self.depth})"

    def save(self, *args, **kwargs):
        if self.image and not self.image._committed:
            encoded = media.build_layer_image(media.open_image(self.image.file))
            name = f"layer_{self.role}.webp"
            self.image.save(name, encoded.as_file(name), save=False)
            self.image_info = encoded.info
        super().save(*args, **kwargs)


# Ringtones and charging animations live in their own module; importing them
# here registers them with this app.
from .feature_models import ChargingAnimation, Ringtone  # noqa: E402,F401
from .service_models import (  # noqa: E402,F401
    AppConfig,
    DailyInteraction,
    DownloadOption,
    ItemDailyStat,
    JobState,
    PaywallHero,
    PaywallPlan,
    WallpaperTab,
)
