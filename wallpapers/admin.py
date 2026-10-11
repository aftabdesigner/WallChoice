"""The whole content-management surface: categories and uploads live here."""

from django.contrib import admin, messages
from django.contrib.admin.models import ADDITION, LogEntry
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import ngettext

from .cache import bump_content_version
from .forms import (
    AppConfigForm,
    CategoryForm,
    PaywallPlanFormSet,
    ChargingAnimationForm,
    HoursPhaseFormSet,
    ParallaxLayerFormSet,
    RingtoneBulkAddForm,
    RingtoneForm,
    WallpaperBulkAddForm,
    WallpaperForm,
)
from .models import (
    COMPOSED_TYPES,
    Category,
    CategoryPin,
    ChargingAnimation,
    Feature,
    HoursPhase,
    ParallaxLayer,
    Ringtone,
    Wallpaper,
    WallpaperType,
)
from .service_models import (
    AppConfig,
    DownloadOption,
    PaywallHero,
    PaywallPlan,
    WallpaperTab,
)

REBUILD_BATCH_LIMIT = 25


def _img(url, width):
    return format_html(
        '<img src="{}" width="{}" style="border-radius:6px;object-fit:cover" />', url, width
    )


# --- categories ------------------------------------------------------------------


class CategoryPinInline(admin.TabularInline):
    model = CategoryPin
    extra = 0
    autocomplete_fields = ("wallpaper",)
    fields = ("wallpaper", "position")
    verbose_name = "pinned item"
    verbose_name_plural = "Pinned to the top (optional)"


class RingtonePinInline(CategoryPinInline):
    autocomplete_fields = ("ringtone",)
    fields = ("ringtone", "position")


class ChargingPinInline(CategoryPinInline):
    autocomplete_fields = ("charging_animation",)
    fields = ("charging_animation", "position")


_PIN_INLINES = {
    Feature.WALLPAPER: CategoryPinInline,
    Feature.RINGTONE: RingtonePinInline,
    Feature.CHARGING: ChargingPinInline,
}


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    form = CategoryForm
    list_display = (
        "name",
        "wallpaper_type",
        "kind",
        "display",
        "position",
        "is_active",
        "standard_count",
        "cover_thumb",
    )
    list_editable = ("position", "is_active")
    list_display_links = ("name",)
    list_filter = ("feature", "wallpaper_type", "kind", "is_active")
    search_fields = ("name", "description")
    ordering = ("feature", "wallpaper_type", "position", "name")
    readonly_fields = ("cover_thumb_large",)
    fieldsets = (
        (None, {"fields": ("name", "feature", "wallpaper_type", "kind", "display", "position", "is_active")}),
        ("Cover", {"fields": ("cover", "cover_thumb_large")}),
        (
            "Trending / Popular",
            {"fields": ("auto_limit",), "description": "Only used by automatic categories."},
        ),
        ("Advanced", {"classes": ("collapse",), "fields": ("slug", "description")}),
    )

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        if obj is not None and obj.feature == Feature.RINGTONE:
            fieldsets.insert(
                2,
                (
                    "Top ringtone card",
                    {"fields": ("featured_ringtone",), "description": "Optional. Empty = the first ringtone."},
                ),
            )
        return fieldsets

    def get_inlines(self, request, obj):
        return (_PIN_INLINES[obj.feature],) if obj is not None and obj.is_automatic else ()

    def get_queryset(self, request):
        listed = {"is_published": True, "media_ready": True}
        return (
            super()
            .get_queryset(request)
            .annotate(
                wallpaper_count=Count(
                    "wallpapers", filter=Q(**{f"wallpapers__{k}": v for k, v in listed.items()}),
                    distinct=True,
                ),
                ringtone_count=Count(
                    "ringtones", filter=Q(**{f"ringtones__{k}": v for k, v in listed.items()}),
                    distinct=True,
                ),
                charging_count=Count(
                    "charging_animations",
                    filter=Q(**{f"charging_animations__{k}": v for k, v in listed.items()}),
                    distinct=True,
                ),
            )
        )

    @admin.display(description="Items")
    def standard_count(self, obj):
        if obj.is_automatic:
            return "auto"
        return {
            Feature.WALLPAPER: obj.wallpaper_count,
            Feature.RINGTONE: obj.ringtone_count,
            Feature.CHARGING: obj.charging_count,
        }[obj.feature]

    @admin.display(description="Cover")
    def cover_thumb(self, obj):
        return _img(obj.cover.url, 80) if obj.cover else "-"

    @admin.display(description="Current cover")
    def cover_thumb_large(self, obj):
        return _img(obj.cover.url, 300) if obj.cover else "-"


# --- wallpapers ----------------------------------------------------------------------


class HoursPhaseInline(admin.TabularInline):
    model = HoursPhase
    formset = HoursPhaseFormSet
    extra = 0
    min_num = 1
    fields = ("position", "name", "from_hour", "to_hour", "image", "image_preview")
    readonly_fields = ("image_preview",)
    verbose_name_plural = "Phases (must cover all 24 hours; to_hour is exclusive and may wrap)"

    @admin.display(description="Preview")
    def image_preview(self, obj):
        return _img(obj.image.url, 60) if obj.pk and obj.image else "-"


class ParallaxLayerInline(admin.TabularInline):
    model = ParallaxLayer
    formset = ParallaxLayerFormSet
    extra = 0
    min_num = 2
    max_num = 4
    fields = ("position", "role", "depth", "image", "image_preview")
    readonly_fields = ("image_preview",)
    verbose_name_plural = "Layers (back to front, 2-4, all the same size)"

    @admin.display(description="Preview")
    def image_preview(self, obj):
        return _img(obj.image.url, 60) if obj.pk and obj.image else "-"


@admin.register(Wallpaper)
class WallpaperAdmin(admin.ModelAdmin):
    form = WallpaperForm
    list_display = (
        "card",
        "display_title",
        "type",
        "category",
        "is_premium",
        "is_published",
        "media_ready",
        "priority",
        "created_at",
    )
    list_display_links = ("card", "display_title")
    list_editable = ("is_premium", "is_published", "priority")
    list_filter = ("type", "media_ready", "is_premium", "is_published", "category")
    search_fields = ("title", "original_filename", "search_document")
    list_select_related = ("category",)
    list_per_page = 50
    date_hierarchy = "created_at"
    actions = ("publish", "unpublish", "mark_premium", "mark_free", "rebuild_media")

    change_readonly = (
        "type",
        "card_large",
        "media_ready",
        "media_summary",
        "dominant_color",
        "original_filename",
        "content_hash",
        "stats",
        "created_at",
    )

    # --- add form vs change form -------------------------------------------------

    def _add_type(self, request):
        requested = request.GET.get("type")
        return requested if requested in COMPOSED_TYPES else None

    def _is_bulk_add(self, request, obj):
        return obj is None and self._add_type(request) is None

    def get_fieldsets(self, request, obj=None):
        if self._is_bulk_add(request, obj):
            hours = reverse("admin:wallpapers_wallpaper_add") + "?type=hours"
            parallax = reverse("admin:wallpapers_wallpaper_add") + "?type=parallax"
            return (
                (
                    None,
                    {
                        "fields": ("type", "category", "files", "tags", "is_premium"),
                        "description": format_html(
                            "Live, Image and Fluid: each file becomes its own published wallpaper, "
                            "with its thumbnail, preview and HD/4K versions made automatically. "
                            'For other types: <a href="{}">add a 24 Hours wallpaper</a> · '
                            '<a href="{}">add a Parallax wallpaper</a>.',
                            hours,
                            parallax,
                        ),
                    },
                ),
            )
        wallpaper_type = obj.type if obj else self._add_type(request)
        main = ["type", "category", "title", "tags", "file"] if obj is None else [
            "type", "category", "title", "tags", "file", "card_large"
        ]
        if wallpaper_type == WallpaperType.PARALLAX:
            main.remove("category")
        sets = [(None, {"fields": main})]
        if wallpaper_type == WallpaperType.FLUID:
            sets.append(("Fluid motion", {"fields": ("fluid_hue_shift_deg", "fluid_cycle_ms")}))
        sets.append(("Placement", {"fields": ("is_premium", "is_published", "priority")}))
        if obj is not None:
            sets.append(
                (
                    "Replace generated images (optional)",
                    {
                        "classes": ("collapse",),
                        "fields": ("thumbnail", "preview"),
                        "description": "Upload only to replace the automatic image. Clear to go back to automatic.",
                    },
                )
            )
            sets.append(
                (
                    "Derived",
                    {
                        "fields": (
                            "media_ready",
                            "media_summary",
                            "dominant_color",
                            "original_filename",
                            "content_hash",
                            "stats",
                            "created_at",
                        ),
                    },
                )
            )
        return sets

    def get_readonly_fields(self, request, obj=None):
        return self.change_readonly if obj is not None else ()

    def get_form(self, request, obj=None, change=False, **kwargs):
        if self._is_bulk_add(request, obj):
            kwargs["form"] = WallpaperBulkAddForm
            return super().get_form(request, obj, change=change, **kwargs)
        form = super().get_form(request, obj, change=change, **kwargs)
        add_type = self._add_type(request) if obj is None else None
        if add_type is None:
            return form

        class TypedForm(form):
            def __init__(self, *args, **inner):
                inner["allowed_types"] = [add_type]
                super().__init__(*args, **inner)

        return TypedForm

    def get_inlines(self, request, obj):
        wallpaper_type = obj.type if obj is not None else self._add_type(request)
        if wallpaper_type == WallpaperType.HOURS:
            return (HoursPhaseInline,)
        if wallpaper_type == WallpaperType.PARALLAX:
            return (ParallaxLayerInline,)
        return ()

    # --- saving --------------------------------------------------------------------

    def save_model(self, request, obj, form, change):
        if isinstance(form, WallpaperBulkAddForm):
            for row in form.prepared:
                row.save()
            # changeform_view wraps this POST in transaction.atomic(), so a
            # failure part-way rolls every row back. ModelAdmin instances are
            # shared between threads, so the batch goes on the request.
            request._bulk_created = form.prepared
            return

        for field in ("thumbnail", "preview"):
            if field in form.changed_data:
                if getattr(obj, field):
                    obj.apply_manual_rendition(field)
                else:
                    info = dict(obj.media_info or {})
                    info["manual"] = [f for f in info.get("manual", []) if f != field]
                    obj.media_info = info
                    request._rebuild_after_save = True
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if isinstance(form, WallpaperBulkAddForm):
            return
        obj = form.instance
        if getattr(request, "_rebuild_after_save", False):
            obj.rebuild_media_from_storage()
        if obj.type in COMPOSED_TYPES and (
            not obj.thumbnail or any(formset.has_changed() for formset in formsets)
        ):
            obj.rebuild_composed_media()
        # Readiness depends on the phases/layers just saved.
        obj.save(update_fields=["media_ready"])
        if not obj.media_ready:
            self.message_user(
                request,
                "Saved, but not shown in the app yet: its media is incomplete.",
                messages.WARNING,
            )

    def log_addition(self, request, obj, message):
        created = getattr(request, "_bulk_created", None)
        if not created or len(created) == 1:
            return super().log_addition(request, obj, message)
        return LogEntry.objects.log_actions(
            user_id=request.user.pk,
            queryset=created,
            action_flag=ADDITION,
            change_message=message,
        )

    def response_add(self, request, obj, post_url_continue=None):
        created = getattr(request, "_bulk_created", None)
        if created and len(created) > 1:
            target = obj.category.name if obj.category_id else obj.get_type_display()
            request._bulk_message = ngettext(
                "%(count)d wallpaper added to %(target)s.",
                "%(count)d wallpapers added to %(target)s.",
                len(created),
            ) % {"count": len(created), "target": target}
        return super().response_add(request, obj, post_url_continue)

    def message_user(
        self, request, message, level=messages.INFO, extra_tags="", fail_silently=False
    ):
        replacement = getattr(request, "_bulk_message", None)
        if replacement and level == messages.SUCCESS:
            del request._bulk_message
            message = replacement
        return super().message_user(request, message, level, extra_tags, fail_silently)

    # --- display -----------------------------------------------------------------------

    @admin.display(description="Title", ordering="title")
    def display_title(self, obj):
        return obj.title or "(untitled)"

    @admin.display(description="Card")
    def card(self, obj):
        return _img(obj.thumbnail.url, 48) if obj.thumbnail else "-"

    @admin.display(description="Preview")
    def card_large(self, obj):
        if obj.preview:
            return _img(obj.preview.url, 260)
        return "-"

    @admin.display(description="Generated files")
    def media_summary(self, obj):
        info = obj.media_info or {}
        lines = []
        for key in ("thumbnail", "preview", "hd", "uhd", "video"):
            item = info.get(key)
            if item:
                size = item.get("size_bytes") or 0
                extra = f", {item['duration_ms'] / 1000:.1f}s" if item.get("duration_ms") else ""
                lines.append(f"{key}: {item.get('width')}x{item.get('height')}, {size / 1024:.0f} KB{extra}")
            else:
                lines.append(f"{key}: -")
        if info.get("manual"):
            lines.append("replaced by hand: " + ", ".join(info["manual"]))
        return format_html("<br>".join(["{}"] * len(lines)), *lines)

    @admin.display(description="Usage")
    def stats(self, obj):
        return (
            f"{obj.downloads_count} downloads · {obj.sets_count} sets · "
            f"{obj.likes_count} likes · {obj.views_count} views"
        )

    # --- actions -----------------------------------------------------------------------

    def _bulk_update(self, request, queryset, message, **values):
        count = queryset.update(**values)
        bump_content_version()
        self.message_user(request, message % count)

    @admin.action(description="Publish selected wallpapers")
    def publish(self, request, queryset):
        self._bulk_update(request, queryset, "%d published.", is_published=True)

    @admin.action(description="Unpublish selected wallpapers")
    def unpublish(self, request, queryset):
        self._bulk_update(request, queryset, "%d unpublished.", is_published=False)

    @admin.action(description="Mark selected as premium")
    def mark_premium(self, request, queryset):
        self._bulk_update(request, queryset, "%d marked premium.", is_premium=True)

    @admin.action(description="Mark selected as free")
    def mark_free(self, request, queryset):
        self._bulk_update(request, queryset, "%d marked free.", is_premium=False)

    @admin.action(description=f"Rebuild thumbnails and versions (max {REBUILD_BATCH_LIMIT})")
    def rebuild_media(self, request, queryset):
        rows = list(queryset[: REBUILD_BATCH_LIMIT + 1])
        if len(rows) > REBUILD_BATCH_LIMIT:
            self.message_user(
                request,
                f"Select at most {REBUILD_BATCH_LIMIT} at a time, or run "
                "`python manage.py rebuild_media` for everything.",
                messages.ERROR,
            )
            return
        done, failed = 0, []
        for wallpaper in rows:
            try:
                duplicate = wallpaper.rebuild_media_from_storage()
            except (ValidationError, OSError) as exc:
                failed.append(f"#{wallpaper.pk}: {exc}")
                continue
            done += 1
            if duplicate:
                failed.append(f"#{wallpaper.pk} duplicates #{duplicate} and was unpublished")
        self.message_user(request, f"Rebuilt {done} wallpaper(s).")
        for line in failed:
            self.message_user(request, line, messages.WARNING)


# --- ringtones and charging animations -------------------------------------------------


class PublishActionsMixin:
    actions = ("publish", "unpublish", "mark_premium", "mark_free")

    def _bulk_update(self, request, queryset, message, **values):
        count = queryset.update(**values)
        bump_content_version()
        self.message_user(request, message % count)

    @admin.action(description="Publish selected")
    def publish(self, request, queryset):
        self._bulk_update(request, queryset, "%d published.", is_published=True)

    @admin.action(description="Unpublish selected")
    def unpublish(self, request, queryset):
        self._bulk_update(request, queryset, "%d unpublished.", is_published=False)

    @admin.action(description="Mark selected as premium")
    def mark_premium(self, request, queryset):
        self._bulk_update(request, queryset, "%d marked premium.", is_premium=True)

    @admin.action(description="Mark selected as free")
    def mark_free(self, request, queryset):
        self._bulk_update(request, queryset, "%d marked free.", is_premium=False)

    def _warn_if_incomplete(self, request, obj):
        if not obj.media_ready:
            self.message_user(
                request, "Saved, but not shown in the app yet: its media is incomplete.", messages.WARNING
            )


@admin.register(Ringtone)
class RingtoneAdmin(PublishActionsMixin, admin.ModelAdmin):
    form = RingtoneForm
    list_display = (
        "cover", "title", "artist", "category", "duration", "is_premium", "is_published",
        "media_ready", "priority", "created_at",
    )
    list_display_links = ("cover", "title")
    list_editable = ("is_premium", "is_published", "priority")
    list_filter = ("category", "media_ready", "is_premium", "is_published")
    search_fields = ("title", "artist", "original_filename")
    list_select_related = ("category",)
    list_per_page = 50
    readonly_fields = ("cover_large", "player", "media_ready", "audio_summary", "original_filename", "stats", "created_at")

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (
                (
                    None,
                    {
                        "fields": ("category", "files", "artwork", "artist", "is_premium"),
                        "description": "Each audio file becomes its own published ringtone, titled after its file name.",
                    },
                ),
            )
        return (
            (None, {"fields": ("category", "title", "artist", "audio", "player", "artwork", "cover_large")}),
            ("Placement", {"fields": ("is_premium", "is_published", "priority")}),
            ("Derived", {"fields": ("media_ready", "audio_summary", "original_filename", "stats", "created_at")}),
        )

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields if obj is not None else ()

    def get_form(self, request, obj=None, change=False, **kwargs):
        if obj is None:
            kwargs["form"] = RingtoneBulkAddForm
        return super().get_form(request, obj, change=change, **kwargs)

    def save_model(self, request, obj, form, change):
        if isinstance(form, RingtoneBulkAddForm):
            for row in form.prepared:
                row.save()
            request._bulk_created = form.prepared
            return
        super().save_model(request, obj, form, change)
        self._warn_if_incomplete(request, obj)

    def log_addition(self, request, obj, message):
        created = getattr(request, "_bulk_created", None)
        if not created or len(created) == 1:
            return super().log_addition(request, obj, message)
        return LogEntry.objects.log_actions(
            user_id=request.user.pk, queryset=created, action_flag=ADDITION, change_message=message
        )

    def response_add(self, request, obj, post_url_continue=None):
        created = getattr(request, "_bulk_created", None)
        if created and len(created) > 1:
            request._bulk_message = f"{len(created)} ringtones added to {obj.category.name}."
        return super().response_add(request, obj, post_url_continue)

    def message_user(self, request, message, level=messages.INFO, extra_tags="", fail_silently=False):
        replacement = getattr(request, "_bulk_message", None)
        if replacement and level == messages.SUCCESS:
            del request._bulk_message
            message = replacement
        return super().message_user(request, message, level, extra_tags, fail_silently)

    @admin.display(description="Art")
    def cover(self, obj):
        return _img(obj.artwork_thumb.url, 40) if obj.artwork_thumb else "-"

    @admin.display(description="Artwork")
    def cover_large(self, obj):
        return _img(obj.artwork.url, 200) if obj.artwork else "-"

    @admin.display(description="Listen")
    def player(self, obj):
        if not obj.audio:
            return "-"
        return format_html('<audio controls preload="none" src="{}"></audio>', obj.audio.url)

    @admin.display(description="Length")
    def duration(self, obj):
        ms = (obj.audio_info or {}).get("duration_ms")
        return f"{ms // 60000}:{ms // 1000 % 60:02d}" if ms else "-"

    @admin.display(description="Audio")
    def audio_summary(self, obj):
        info = obj.audio_info or {}
        if not info:
            return "-"
        return f"{info.get('format')}, {info.get('duration_ms', 0) / 1000:.1f}s, {info.get('size_bytes', 0) / 1024:.0f} KB"

    @admin.display(description="Usage")
    def stats(self, obj):
        return f"{obj.plays_count} plays · {obj.sets_count} sets"


@admin.register(ChargingAnimation)
class ChargingAnimationAdmin(PublishActionsMixin, admin.ModelAdmin):
    form = ChargingAnimationForm
    list_display = (
        "card", "display_title", "category", "format", "is_premium", "is_published", "media_ready",
        "priority", "created_at",
    )
    list_display_links = ("card", "display_title")
    list_editable = ("is_premium", "is_published", "priority")
    list_filter = ("category", "media_ready", "is_premium", "is_published")
    search_fields = ("title", "original_filename")
    list_select_related = ("category",)
    readonly_fields = ("card_large", "media_ready", "file_summary", "original_filename", "created_at")
    fieldsets = (
        (None, {"fields": ("category", "title", "animation", "loop")}),
        ("Preview", {"fields": ("thumbnail", "preview_video", "card_large")}),
        (
            "Look",
            {"fields": ("battery_label_visible", "battery_label_position", "battery_label_color", "background_color")},
        ),
        ("Sound (optional)", {"fields": ("sound",)}),
        ("Placement", {"fields": ("is_premium", "is_published", "priority")}),
        ("Derived", {"fields": ("media_ready", "file_summary", "original_filename", "created_at")}),
    )

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return [(name, {**opts, "fields": [f for f in opts["fields"] if f not in self.readonly_fields]})
                    for name, opts in self.fieldsets if name != "Derived"]
        return self.fieldsets

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields if obj is not None else ()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self._warn_if_incomplete(request, obj)

    @admin.display(description="Title", ordering="title")
    def display_title(self, obj):
        return obj.title or "(untitled)"

    @admin.display(description="Format")
    def format(self, obj):
        return (obj.animation_info or {}).get("format", "-")

    @admin.display(description="Card")
    def card(self, obj):
        return _img(obj.thumbnail.url, 40) if obj.thumbnail else "-"

    @admin.display(description="Current card")
    def card_large(self, obj):
        return _img(obj.thumbnail.url, 180) if obj.thumbnail else "-"

    @admin.display(description="Files")
    def file_summary(self, obj):
        lines = []
        for label, info in (
            ("animation", obj.animation_info),
            ("preview video", obj.preview_video_info),
            ("sound", obj.sound_info),
        ):
            if info:
                parts = [str(info.get("format", ""))] if info.get("format") else []
                if info.get("width"):
                    parts.append(f"{info['width']}x{info['height']}")
                if info.get("duration_ms"):
                    parts.append(f"{info['duration_ms'] / 1000:.1f}s")
                parts.append(f"{info.get('size_bytes', 0) / 1024:.0f} KB")
                lines.append(f"{label}: {', '.join(parts)}")
        return format_html("<br>".join(["{}"] * len(lines)), *lines) if lines else "-"


# --- app config -----------------------------------------------------------------------------


class WallpaperTabInline(admin.TabularInline):
    model = WallpaperTab
    extra = 0
    max_num = len(WallpaperType.choices)
    fields = ("position", "key", "title", "is_enabled")
    verbose_name_plural = "Wallpaper tabs (top of the Wallpapers screen, in this order)"


class DownloadOptionInline(admin.TabularInline):
    model = DownloadOption
    extra = 0
    max_num = 2
    fields = ("position", "key", "label", "pro_only")
    verbose_name_plural = "Download options"


class PaywallPlanInline(admin.TabularInline):
    model = PaywallPlan
    formset = PaywallPlanFormSet
    extra = 0
    fields = ("position", "product_id", "kind", "label", "badge", "note", "is_default", "is_active")
    verbose_name_plural = "Paywall plans (prices always come from Google Play)"


class PaywallHeroInline(admin.TabularInline):
    model = PaywallHero
    extra = 0
    autocomplete_fields = ("wallpaper",)
    fields = ("position", "wallpaper")
    verbose_name_plural = "Paywall background wallpapers"


@admin.register(AppConfig)
class AppConfigAdmin(admin.ModelAdmin):
    """One settings page: the list view jumps straight to it."""

    form = AppConfigForm
    inlines = (WallpaperTabInline, DownloadOptionInline, PaywallPlanInline, PaywallHeroInline)
    readonly_fields = ("updated_at",)
    fieldsets = (
        ("App versions", {"fields": ("min_version_code", "latest_version_code")}),
        ("Features", {"fields": ("feature_ringtones", "feature_charging", "feature_wallpaper_search")}),
        (
            "Ads",
            {
                "fields": (
                    "ads_enabled", "interstitial_cooldown_sec", "interstitial_after",
                    "rewarded_download", "rewarded_unlock", "native_size", "native_every_rows",
                )
            },
        ),
        ("Paywall", {"fields": ("paywall_eyebrow", "paywall_title", "paywall_subtitle", "paywall_close_delay_sec")}),
        ("Onboarding", {"fields": ("welcome_title", "welcome_subtitle", "onboarding_tips")}),
        ("Links", {"fields": ("terms_url", "privacy_url", "support_email", "store_url")}),
        (None, {"fields": ("updated_at",)}),
    )

    def has_add_permission(self, request):
        return not AppConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        config = AppConfig.load()
        return redirect(reverse("admin:wallpapers_appconfig_change", args=[config.pk]))


admin.site.site_header = "WallChoice admin"
admin.site.site_title = "WallChoice"
admin.site.index_title = "Content"
