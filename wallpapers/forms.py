"""Admin forms.

* ``WallpaperBulkAddForm`` - Live, Image and Fluid: a batch of files into one
  category, one wallpaper per file. Every file is fully processed during
  validation, so a broken or duplicate file is reported by name before
  anything is saved.
* ``WallpaperForm`` - the single-item form (change form, and the add form for
  24 Hours and Parallax, which need phases/layers alongside).
* Inline formsets that check a 24 Hours set covers the whole day and a
  Parallax set has 2-4 equally sized layers.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.forms.models import BaseInlineFormSet
from django.utils.translation import ngettext
from PIL import Image

from . import media
from .models import (
    CATEGORIZED_TYPES,
    Category,
    CategoryKind,
    ChargingAnimation,
    Feature,
    ParallaxRole,
    Ringtone,
    Wallpaper,
    WallpaperType,
    normalise_tags,
)
from .service_models import AdTrigger, AppConfig
from .validators import validate_audio_extension, validate_media_extension

MAX_FILES_PER_UPLOAD = 10
BULK_TYPES = (WallpaperType.LIVE, WallpaperType.IMAGE, WallpaperType.FLUID)


class MultiFileInput(forms.ClearableFileInput):
    # Django 5 refuses attrs={"multiple": True} on a stock file widget; this
    # flag is the supported opt-in, and it also switches value_from_datadict
    # over to files.getlist().
    allow_multiple_selected = True


class MultiFileField(forms.FileField):
    """A FileField whose cleaned value is a *list* of uploaded files.

    Each file goes through the single-file ``clean()``, so ``validators`` run
    per file instead of once against the list.
    """

    widget = MultiFileInput

    def __init__(self, *args, max_files=None, **kwargs):
        self.max_files = max_files
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if data in (None, False):
            data = []
        elif not isinstance(data, (list, tuple)):
            data = [data]

        if not data:
            if self.required:
                raise ValidationError(self.error_messages["required"], code="required")
            return []

        if self.max_files is not None and len(data) > self.max_files:
            raise ValidationError(
                ngettext(
                    "Select at most %(max)d file at a time - you selected %(count)d.",
                    "Select at most %(max)d files at a time - you selected %(count)d.",
                    self.max_files,
                ),
                code="too_many_files",
                params={"max": self.max_files, "count": len(data)},
            )

        cleaned = []
        errors = []
        clean_one = super().clean
        for upload in data:
            try:
                cleaned.append(clean_one(upload, initial))
            except ValidationError as exc:
                for message in exc.messages:
                    errors.append(
                        ValidationError(
                            "%(name)s: %(message)s",
                            code="invalid",
                            params={"name": getattr(upload, "name", "?"), "message": message},
                        )
                    )
        if errors:
            raise ValidationError(errors)
        return cleaned


class TagsField(forms.CharField):
    """Comma-separated in the form, a list on the model."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("help_text", "Comma separated, e.g. samurai, red, night. Used by search.")
        kwargs.setdefault("widget", forms.TextInput(attrs={"size": 60}))
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        if isinstance(value, (list, tuple)):
            return ", ".join(value)
        return value

    def clean(self, value):
        return normalise_tags(super().clean(value) or "")


def wallpaper_category_queryset(types=CATEGORIZED_TYPES):
    return Category.objects.filter(
        feature=Feature.WALLPAPER, kind=CategoryKind.STANDARD, wallpaper_type__in=types
    ).order_by("wallpaper_type", "position", "name")


def _error_text(exc: ValidationError) -> list:
    if hasattr(exc, "message_dict"):
        return [message for messages in exc.message_dict.values() for message in messages]
    return exc.messages


class WallpaperBulkAddForm(forms.ModelForm):
    """Add form for Live, Image and Fluid."""

    type = forms.ChoiceField(
        choices=[(t.value, t.label) for t in BULK_TYPES],
        initial=WallpaperType.IMAGE,
        help_text="For 24 Hours or Parallax use the links above - they need phases or layers.",
    )
    files = MultiFileField(
        label="Files",
        max_files=MAX_FILES_PER_UPLOAD,
        validators=[validate_media_extension],
        help_text=(
            f"Up to {MAX_FILES_PER_UPLOAD} files at once. Live: mp4. Image: jpeg, png or webp. "
            "Fluid: mp4 loops or still images. Each file becomes its own published wallpaper."
        ),
    )
    tags = TagsField()

    class Meta:
        model = Wallpaper
        fields = ("type", "category", "is_premium")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = wallpaper_category_queryset(
            (WallpaperType.LIVE, WallpaperType.IMAGE)
        )
        self.fields["category"].help_text = "Required for Live and Image. Leave empty for Fluid."
        self.prepared = []

    def _post_clean(self):
        # Model validation runs per file below instead of once for the form.
        if self.errors:
            return
        data = self.cleaned_data
        uploads = data.get("files") or []
        rows, errors, hashes = [], [], {}
        for index, upload in enumerate(uploads):
            row = self.instance if index == 0 else Wallpaper()
            row.type = data["type"]
            row.category = data.get("category")
            row.is_premium = data.get("is_premium", False)
            row.tags = data.get("tags", [])
            row.file = upload
            try:
                row.full_clean(exclude=["title"])
            except ValidationError as exc:
                for message in _error_text(exc):
                    prefix = "" if message.startswith(upload.name) else f"{upload.name}: "
                    errors.append(f"{prefix}{message}")
                continue
            if row.content_hash in hashes:
                errors.append(f"{upload.name}: same file as {hashes[row.content_hash]} in this upload.")
                continue
            hashes[row.content_hash] = upload.name
            rows.append(row)
        if errors:
            # Category/type mistakes repeat once per file; show each once.
            unique = list(dict.fromkeys(errors))
            self.add_error("files" if uploads else None, unique)
            return
        self.prepared = rows

    def save(self, commit=True):
        # The admin saves the batch itself (WallpaperAdmin.save_model).
        self.save_m2m = lambda: None
        return self.instance


class WallpaperForm(forms.ModelForm):
    tags = TagsField()

    class Meta:
        model = Wallpaper
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        allowed_types = kwargs.pop("allowed_types", None)
        super().__init__(*args, **kwargs)
        if "category" in self.fields:
            self.fields["category"].queryset = wallpaper_category_queryset()
        if allowed_types and "type" in self.fields:
            self.fields["type"].choices = [
                (t.value, t.label) for t in WallpaperType if t.value in allowed_types
            ]
            self.fields["type"].initial = allowed_types[0]


class HoursPhaseFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        covered = {}
        phases = 0
        for form in self.forms:
            data = getattr(form, "cleaned_data", None)
            if not data or data.get("DELETE"):
                continue
            phases += 1
            start, end = data.get("from_hour"), data.get("to_hour")
            if start is None or end is None:
                continue
            span = list(range(24)) if start == end else [
                h % 24 for h in range(start, end if end > start else end + 24)
            ]
            for hour in span:
                if hour in covered:
                    raise ValidationError(
                        f"Hour {hour:02d}:00 is in two phases ({covered[hour]} and {data.get('name')})."
                    )
                covered[hour] = data.get("name")
        if not phases:
            raise ValidationError("A 24 Hours wallpaper needs at least one phase.")
        missing = [h for h in range(24) if h not in covered]
        if missing:
            listed = ", ".join(f"{h:02d}:00" for h in missing[:6])
            raise ValidationError(
                f"The phases must cover all 24 hours with no gaps. Not covered: {listed}"
                + ("…" if len(missing) > 6 else "")
            )


class ParallaxLayerFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        layers = []
        for form in self.forms:
            data = getattr(form, "cleaned_data", None)
            if not data or data.get("DELETE"):
                continue
            layers.append((form, data))
        if not 2 <= len(layers) <= 4:
            raise ValidationError("A Parallax wallpaper needs 2 to 4 layers.")
        backgrounds = [d for _, d in layers if d.get("role") == ParallaxRole.BACKGROUND]
        if len(backgrounds) != 1:
            raise ValidationError("Exactly one layer must be the background.")

        sizes = set()
        for form, data in layers:
            upload = data.get("image")
            if isinstance(upload, UploadedFile):
                try:
                    upload.seek(0)
                    with Image.open(upload) as image:
                        width, height = image.size
                    upload.seek(0)
                except (OSError, ValueError) as exc:
                    raise ValidationError(f"{upload.name} could not be read as an image.") from exc
                if width > media.LAYER_WIDTH:
                    height, width = round(height * media.LAYER_WIDTH / width), media.LAYER_WIDTH
                sizes.add((width, height))
            elif form.instance.pk and form.instance.image_info:
                info = form.instance.image_info
                sizes.add((info.get("width"), info.get("height")))
        if len(sizes) > 1:
            raise ValidationError("All layers must be the same size (for example 1080 x 2400).")


# --- ringtones (Phase 2) ---------------------------------------------------------------


def feature_category_queryset(feature):
    return Category.objects.filter(feature=feature, kind=CategoryKind.STANDARD).order_by("position", "name")


class RingtoneBulkAddForm(forms.ModelForm):
    """Several audio files into one category, sharing one artwork image."""

    files = MultiFileField(
        label="Audio files",
        max_files=MAX_FILES_PER_UPLOAD,
        validators=[validate_audio_extension],
        help_text=(
            f"Up to {MAX_FILES_PER_UPLOAD} files. Each becomes its own published ringtone, "
            "converted to MP3 at an even volume and trimmed to 40 seconds."
        ),
    )

    class Meta:
        model = Ringtone
        fields = ("category", "artwork", "artist", "is_premium")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = feature_category_queryset(Feature.RINGTONE)
        self.fields["artwork"].help_text = "One square image used for every ringtone in this upload."
        self.prepared = []

    def _post_clean(self):
        if self.errors:
            return
        data = self.cleaned_data
        rows, errors, hashes = [], [], {}
        for index, upload in enumerate(data.get("files") or []):
            row = self.instance if index == 0 else Ringtone()
            row.category = data["category"]
            row.artwork = data["artwork"]
            row.artist = data.get("artist", "")
            row.is_premium = data.get("is_premium", False)
            row.audio = upload
            try:
                row.full_clean(exclude=["title"])
            except ValidationError as exc:
                for message in _error_text(exc):
                    prefix = "" if message.startswith(upload.name) else f"{upload.name}: "
                    errors.append(f"{prefix}{message}")
                continue
            digest = row._pending["audio"][3]
            if digest in hashes:
                errors.append(f"{upload.name}: same file as {hashes[digest]} in this upload.")
                continue
            hashes[digest] = upload.name
            rows.append(row)
        if errors:
            self.add_error("files", list(dict.fromkeys(errors)))
            return
        self.prepared = rows

    def save(self, commit=True):
        self.save_m2m = lambda: None
        return self.instance


class RingtoneForm(forms.ModelForm):
    class Meta:
        model = Ringtone
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "category" in self.fields:
            self.fields["category"].queryset = feature_category_queryset(Feature.RINGTONE)


class ChargingAnimationForm(forms.ModelForm):
    class Meta:
        model = ChargingAnimation
        fields = "__all__"
        widgets = {
            "battery_label_color": forms.TextInput(attrs={"type": "color"}),
            "background_color": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "category" in self.fields:
            self.fields["category"].queryset = feature_category_queryset(Feature.CHARGING)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("featured_ringtone")
        if field is not None:
            if self.instance.pk and self.instance.feature == Feature.RINGTONE and not self.instance.is_automatic:
                field.queryset = Ringtone.objects.filter(category=self.instance)
            elif self.instance.pk and self.instance.feature == Feature.RINGTONE:
                field.queryset = Ringtone.objects.all()
            else:
                field.queryset = Ringtone.objects.none()

# --- app config (Phase 3) -------------------------------------------------------------


class AppConfigForm(forms.ModelForm):
    interstitial_after = forms.MultipleChoiceField(
        choices=AdTrigger.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Actions after which a full-screen ad may show.",
    )

    class Meta:
        model = AppConfig
        fields = "__all__"
        widgets = {"onboarding_tips": forms.Textarea(attrs={"rows": 4})}

    def clean(self):
        data = super().clean()
        low, high = data.get("min_version_code"), data.get("latest_version_code")
        if low is not None and high is not None and high < low:
            self.add_error("latest_version_code", "Must be at least the minimum version code.")
        return data


class PaywallPlanFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        active = [
            form.cleaned_data
            for form in self.forms
            if getattr(form, "cleaned_data", None)
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("is_active")
        ]
        defaults = [plan for plan in active if plan.get("is_default")]
        if active and len(defaults) != 1:
            raise ValidationError("Exactly one active plan must be the default.")