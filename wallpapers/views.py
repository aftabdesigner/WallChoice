"""The public wallpaper endpoints.

Every response is served through ``cached_json``: built once, cached until
content changes, and answered with ``304`` when the client already has it.
"""

import re

from django.http import Http404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import catalog, legacy
from . import events as events_module
from .cache import cached_json
from .models import (
    CATEGORIZED_TYPES,
    Category,
    ChargingAnimation,
    Feature,
    Ringtone,
    WallpaperType,
)
from .pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, paginate, paginate_list
from .serializers import (
    UrlBuilder,
    category_object,
    charging_object,
    config_object,
    ringtone_object,
    wallpaper_object,
)

_TYPE_VALUES = [t.value for t in WallpaperType]
_CATEGORIZED_VALUES = [t.value for t in CATEGORIZED_TYPES]


class CachedView(APIView):
    def get(self, request, *args, **kwargs):
        return cached_json(request, lambda: self.build(request, *args, **kwargs))

    def build(self, request, *args, **kwargs):
        raise NotImplementedError


def _type_param(request, *, required, allowed=_TYPE_VALUES):
    raw = request.query_params.get("type")
    if raw in (None, ""):
        if required:
            raise ValidationError(f"type is required: one of {', '.join(allowed)}.")
        return None
    if raw not in allowed:
        if raw in _TYPE_VALUES:
            raise ValidationError(f"{raw} wallpapers have no categories.")
        raise ValidationError(f"type must be one of {', '.join(allowed)}.")
    return raw


def _int_param(request, name):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be an integer.")


def _ids_param(request):
    raw = request.query_params.get("ids")
    if raw in (None, ""):
        return None
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            raise ValidationError("ids must be a comma-separated list of integers.")
        if value not in ids:
            ids.append(value)
    if len(ids) > MAX_PAGE_SIZE:
        raise ValidationError(f"ids may hold at most {MAX_PAGE_SIZE} values.")
    return ids


def _envelope(items, meta, request):
    url = UrlBuilder(request)
    return {"data": [wallpaper_object(w, url) for w in items], "meta": meta}


class CategoryListView(CachedView):
    """GET /wallpapers/categories?type=live|image|hours"""

    def build(self, request):
        wallpaper_type = _type_param(request, required=False, allowed=_CATEGORIZED_VALUES)
        if wallpaper_type is None:
            # Builds that predate Phase 1 never send type.
            items, meta = paginate(legacy.legacy_categories(), request)
            return {"data": [legacy.legacy_category_row(c) for c in items], "meta": meta}

        categories = catalog.wallpaper_categories(wallpaper_type)
        counts = catalog.item_counts(categories, wallpaper_type)
        visible = [c for c in categories if counts[c.id] > 0]
        items, meta = paginate_list(visible, request)
        url = UrlBuilder(request)
        return {"data": [category_object(c, counts[c.id], url) for c in items], "meta": meta}


class WallpaperListView(CachedView):
    """GET /wallpapers?type=…&category_id=…&ids=…"""

    def build(self, request):
        ids = _ids_param(request)
        wallpaper_type = _type_param(request, required=False)
        category_id = _int_param(request, "category_id")

        if ids is not None:
            found = catalog.listed().filter(pk__in=ids)
            if wallpaper_type:
                found = found.filter(type=wallpaper_type)
            found_ids = set(found.values_list("pk", flat=True))
            ordered = [pk for pk in ids if pk in found_ids]
            page_ids, meta = paginate_list(ordered, request)
            return _envelope(catalog.fetch_in_order(page_ids), meta, request)

        if wallpaper_type is None:
            queryset = legacy.legacy_wallpapers()
            if category_id is not None:
                queryset = queryset.filter(category_id=category_id)
            items, meta = paginate(queryset, request)
            url = UrlBuilder(request)
            return {"data": [legacy.legacy_wallpaper_row(w, url) for w in items], "meta": meta}

        if category_id is None:
            items, meta = paginate(catalog.type_queryset(wallpaper_type), request)
            return _envelope(items, meta, request)

        category = Category.objects.filter(
            pk=category_id, is_active=True, feature=Feature.WALLPAPER, wallpaper_type=wallpaper_type
        ).first()
        if category is None:
            # Unknown, inactive or of another type: an empty list, not an error.
            items, meta = paginate_list([], request)
            return _envelope(items, meta, request)
        if category.is_automatic:
            page_ids, meta = paginate_list(catalog.automatic_ids(category), request)
            return _envelope(catalog.fetch_in_order(page_ids, wallpaper_type), meta, request)
        items, meta = paginate(catalog.standard_queryset(category), request)
        return _envelope(items, meta, request)


class WallpaperDetailView(CachedView):
    """GET /wallpapers/{id}"""

    def build(self, request, pk):
        wallpaper = catalog.with_relations(catalog.listed().filter(pk=pk)).first()
        if wallpaper is None:
            raise Http404(f"Wallpaper {pk} does not exist.")
        return {"data": wallpaper_object(wallpaper, UrlBuilder(request))}


class WallpaperFeedView(CachedView):
    """GET /wallpapers/feed?type=… - one call fills a type's screen."""

    def build(self, request):
        wallpaper_type = _type_param(request, required=True)
        url = UrlBuilder(request)

        if wallpaper_type not in _CATEGORIZED_VALUES:
            rows = list(catalog.type_queryset(wallpaper_type)[: DEFAULT_PAGE_SIZE + 1])
            return {
                "data": {
                    "type": wallpaper_type,
                    "rows": [],
                    "chips": None,
                    "items": [wallpaper_object(w, url) for w in rows[:DEFAULT_PAGE_SIZE]],
                    "has_more": len(rows) > DEFAULT_PAGE_SIZE,
                }
            }

        categories = catalog.wallpaper_categories(wallpaper_type)
        counts = catalog.item_counts(categories, wallpaper_type)
        visible = [c for c in categories if counts[c.id] > 0]

        rows = []
        for category in (c for c in visible if c.display == "row"):
            items, has_more = catalog.first_items(category, catalog.ROW_PREVIEW_SIZE)
            rows.append(
                {
                    "category": category_object(category, counts[category.id], url),
                    "items": [wallpaper_object(w, url) for w in items],
                    "has_more": has_more,
                }
            )

        chip_categories = [c for c in visible if c.display == "chip"]
        chips = {"categories": [], "selected_category_id": None, "items": [], "has_more": False}
        if chip_categories:
            selected = chip_categories[0]
            items, has_more = catalog.first_items(selected, DEFAULT_PAGE_SIZE)
            chips = {
                "categories": [category_object(c, counts[c.id], url) for c in chip_categories],
                "selected_category_id": selected.id,
                "items": [wallpaper_object(w, url) for w in items],
                "has_more": has_more,
            }

        return {
            "data": {
                "type": wallpaper_type,
                "rows": rows,
                "chips": chips,
                "items": [],
                "has_more": False,
            }
        }


class WallpaperSearchView(CachedView):
    """GET /wallpapers/search?q=…&type=…"""

    def build(self, request):
        query = catalog.normalise_query(request.query_params.get("q"))
        if not query:
            raise ValidationError("q is required.")
        wallpaper_type = _type_param(request, required=False)
        page_ids, meta = paginate_list(catalog.search_ids(query, wallpaper_type), request)
        return _envelope(catalog.fetch_in_order(page_ids), meta, request)


# --- ringtones and charging animations (Phase 2) ------------------------------------


class FeatureCategoryListView(CachedView):
    """GET /ringtones/categories and /charging-animations/categories"""

    feature = None

    def build(self, request):
        categories = catalog.feature_categories(self.feature)
        counts = catalog.item_counts(categories, feature=self.feature)
        visible = [c for c in categories if counts[c.id] > 0]
        items, meta = paginate_list(visible, request)
        url = UrlBuilder(request)
        return {"data": [category_object(c, counts[c.id], url) for c in items], "meta": meta}


def _feature_category(request, feature):
    """The category named by the required ``category_id``, or None if unknown."""
    category_id = _int_param(request, "category_id")
    if category_id is None:
        raise ValidationError("category_id is required.")
    return Category.objects.filter(pk=category_id, is_active=True, feature=feature).first()


class FeatureListView(CachedView):
    """GET /ringtones?category_id=… and /charging-animations?category_id=…"""

    feature = None
    serialize = None

    def build(self, request):
        category = _feature_category(request, self.feature)
        if category is None:
            items, meta = paginate_list([], request)
        else:
            items, meta = catalog.category_page(category, paginate, paginate_list, request)
        url = UrlBuilder(request)
        serialize = type(self).serialize
        return {"data": [serialize(item, url) for item in items], "meta": meta}


class FeatureDetailView(CachedView):
    """GET /ringtones/{id} and /charging-animations/{id}"""

    model = None
    label = None
    serialize = None

    def build(self, request, pk):
        item = catalog.listed(self.model).select_related("category").filter(pk=pk).first()
        if item is None:
            raise Http404(f"{self.label} {pk} does not exist.")
        return {"data": type(self).serialize(item, UrlBuilder(request))}


class RingtoneFeaturedView(CachedView):
    """GET /ringtones/featured?category_id=… - the TOP RINGTONE card."""

    def build(self, request):
        category = _feature_category(request, Feature.RINGTONE)
        featured = None
        if category is not None:
            if category.featured_ringtone_id:
                featured = (
                    catalog.listed(Ringtone)
                    .select_related("category")
                    .filter(pk=category.featured_ringtone_id)
                    .first()
                )
            if featured is None:
                items, _ = catalog.first_items(category, 1)
                featured = items[0] if items else None
        url = UrlBuilder(request)
        return {"data": ringtone_object(featured, url) if featured else None}


class RingtoneCategoryListView(FeatureCategoryListView):
    feature = Feature.RINGTONE


class RingtoneListView(FeatureListView):
    feature = Feature.RINGTONE
    serialize = ringtone_object


class RingtoneDetailView(FeatureDetailView):
    model = Ringtone
    label = "Ringtone"
    serialize = ringtone_object


class ChargingCategoryListView(FeatureCategoryListView):
    feature = Feature.CHARGING


class ChargingListView(FeatureListView):
    feature = Feature.CHARGING
    serialize = charging_object


class ChargingDetailView(FeatureDetailView):
    model = ChargingAnimation
    label = "Charging animation"
    serialize = charging_object

# --- config and events (Phase 3) ------------------------------------------------------

_INSTALL_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


class ConfigView(CachedView):
    """GET /config - called on every app start."""

    def build(self, request):
        from .models import AppConfig, PaywallHero

        config = AppConfig.objects.prefetch_related(
            "wallpaper_tabs", "download_options", "paywall_plans"
        ).filter(pk=1).first() or AppConfig.load()
        hero_ids = list(
            PaywallHero.objects.filter(
                config=config, wallpaper__is_published=True, wallpaper__media_ready=True
            ).order_by("position", "id").values_list("wallpaper_id", flat=True)
        )
        return {"data": config_object(config, hero_ids)}


class EventsView(APIView):
    """POST /events - batched usage events; answers 204."""

    def post(self, request):
        install_id = request.headers.get("X-Install-Id", "")
        if not _INSTALL_ID.match(install_id):
            raise ValidationError("The X-Install-Id header is required.")
        events = request.data.get("events") if isinstance(request.data, dict) else None
        if not isinstance(events, list):
            raise ValidationError("Send {\"events\": [...]}.")
        if len(events) > events_module.MAX_EVENTS_PER_BATCH:
            raise ValidationError(f"At most {events_module.MAX_EVENTS_PER_BATCH} events per request.")
        events_module.ingest(install_id, events)
        events_module.maybe_recalculate()
        return Response(status=204)