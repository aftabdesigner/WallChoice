"""Queries behind the endpoints, kept apart from HTTP concerns.

Every list is built so its cost does not grow with the size of the catalogue:
pages are fetched by index range, counts are one grouped query, and the
automatic categories (Trending, Popular) are resolved to an id list of at most
``auto_limit`` rows straight from a score index.

The category logic is shared by all three features: wallpapers (scoped by
wallpaper type), ringtones and charging animations.
"""

import difflib

from django.db import connection
from django.db.models import Case, Count, IntegerField, Q, Value, When

from .models import (
    CATEGORIZED_TYPES,
    STANDARD_ORDER,
    Category,
    CategoryKind,
    CategoryPin,
    ChargingAnimation,
    Feature,
    Ringtone,
    Wallpaper,
    WallpaperType,
)

ROW_PREVIEW_SIZE = 12
SEARCH_RESULT_LIMIT = 500
_FALLBACK_SCAN_LIMIT = 5000

FEATURE_MODELS = {
    Feature.WALLPAPER: Wallpaper,
    Feature.RINGTONE: Ringtone,
    Feature.CHARGING: ChargingAnimation,
}

_trigram_ready = None


def listed(model=Wallpaper):
    return model.objects.listed()


def with_relations(queryset, wallpaper_type=None):
    queryset = queryset.select_related("category")
    if queryset.model is not Wallpaper:
        return queryset
    if wallpaper_type in (None, WallpaperType.HOURS):
        queryset = queryset.prefetch_related("hours_phases")
    if wallpaper_type in (None, WallpaperType.PARALLAX):
        queryset = queryset.prefetch_related("parallax_layers")
    return queryset


def fetch_in_order(ids, wallpaper_type=None, model=Wallpaper):
    """Load full rows for ``ids`` and return them in that order."""
    if not ids:
        return []
    rows = {w.pk: w for w in with_relations(model.objects.filter(pk__in=ids), wallpaper_type)}
    return [rows[pk] for pk in ids if pk in rows]


# --- categories -------------------------------------------------------------------


def feature_categories(feature, wallpaper_type=None):
    return list(
        Category.objects.filter(
            is_active=True, feature=feature, wallpaper_type=wallpaper_type
        ).order_by("position", "id")
    )


def wallpaper_categories(wallpaper_type):
    return feature_categories(Feature.WALLPAPER, wallpaper_type)


def _scope(feature, wallpaper_type):
    """Every listed item a category of this feature/type can hold."""
    queryset = listed(FEATURE_MODELS[feature])
    if feature == Feature.WALLPAPER:
        queryset = queryset.filter(type=wallpaper_type)
    return queryset


def item_counts(categories, wallpaper_type=None, feature=Feature.WALLPAPER):
    """``{category_id: count}`` in at most two queries, whatever the size."""
    standard_ids = [c.id for c in categories if not c.is_automatic]
    counts = {}
    if standard_ids:
        counts.update(
            _scope(feature, wallpaper_type)
            .filter(category_id__in=standard_ids)
            .order_by()
            .values("category_id")
            .annotate(n=Count("id"))
            .values_list("category_id", "n")
        )
    automatic = [c for c in categories if c.is_automatic]
    if automatic:
        total = _scope(feature, wallpaper_type).count()
        for category in automatic:
            counts[category.id] = min(total, category.auto_limit)
    return {c.id: counts.get(c.id, 0) for c in categories}


def automatic_ids(category):
    """Pinned items first, then the best scores, capped at ``auto_limit``."""
    field = CategoryPin.ITEM_FIELDS[category.feature]
    pin_filter = {
        "category": category,
        f"{field}__is_published": True,
        f"{field}__media_ready": True,
    }
    if category.feature == Feature.WALLPAPER:
        pin_filter["wallpaper__type"] = category.wallpaper_type
    pinned = list(
        CategoryPin.objects.filter(**pin_filter)
        .order_by("position", "id")
        .values_list(f"{field}_id", flat=True)
    )[: category.auto_limit]
    score = "-trending_score" if category.kind == CategoryKind.TRENDING else "-popular_score"
    remaining = category.auto_limit - len(pinned)
    top = []
    if remaining > 0:
        top = list(
            _scope(category.feature, category.wallpaper_type)
            .exclude(pk__in=pinned)
            .order_by(score, "-created_at", "-id")
            .values_list("pk", flat=True)[:remaining]
        )
    return pinned + top


def standard_queryset(category):
    return with_relations(
        _scope(category.feature, category.wallpaper_type).filter(category=category),
        category.wallpaper_type,
    ).order_by(*STANDARD_ORDER)


def category_page(category, paginate, paginate_list, request):
    """One page of any category: ``(items, meta)``."""
    model = FEATURE_MODELS[category.feature]
    if category.is_automatic:
        page_ids, meta = paginate_list(automatic_ids(category), request)
        return fetch_in_order(page_ids, category.wallpaper_type, model), meta
    return paginate(standard_queryset(category), request)


def first_items(category, limit):
    """The first ``limit`` items of a category and whether more follow."""
    if category.is_automatic:
        ids = automatic_ids(category)
        model = FEATURE_MODELS[category.feature]
        return fetch_in_order(ids[:limit], category.wallpaper_type, model), len(ids) > limit
    rows = list(standard_queryset(category)[: limit + 1])
    return rows[:limit], len(rows) > limit


def type_queryset(wallpaper_type):
    return with_relations(listed().filter(type=wallpaper_type), wallpaper_type).order_by(
        *STANDARD_ORDER
    )


# --- search ---------------------------------------------------------------------------


def normalise_query(raw):
    return " ".join((raw or "").lower().split())[:100]


def trigram_ready():
    """True on PostgreSQL with pg_trgm installed (checked once per process)."""
    global _trigram_ready
    if connection.vendor != "postgresql":
        return False
    if _trigram_ready is None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            _trigram_ready = cursor.fetchone() is not None
    return _trigram_ready


def search_ids(query, wallpaper_type=None):
    """Best matches first, as ids, at most ``SEARCH_RESULT_LIMIT``.

    PostgreSQL: trigram word similarity over title + category + tags, served by
    a GIN index, so "samurii" still finds "samurai". Elsewhere (local SQLite):
    substring match, then a bounded fuzzy pass when that finds nothing.
    """
    base = listed()
    if wallpaper_type:
        base = base.filter(type=wallpaper_type)

    if trigram_ready():
        from django.contrib.postgres.search import TrigramWordSimilarity

        return list(
            base.filter(
                Q(search_document__icontains=query)
                | Q(search_document__trigram_word_similar=query)
            )
            .annotate(
                exact=Case(
                    When(title__iexact=query, then=Value(2)),
                    When(title__istartswith=query, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
                rank=TrigramWordSimilarity(query, "search_document"),
            )
            .order_by("-exact", "-rank", *STANDARD_ORDER)
            .values_list("pk", flat=True)[:SEARCH_RESULT_LIMIT]
        )

    terms = query.split()
    matches = base
    for term in terms:
        matches = matches.filter(search_document__icontains=term)
    ids = list(
        matches.annotate(
            exact=Case(
                When(title__iexact=query, then=Value(2)),
                When(title__istartswith=query, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by("-exact", *STANDARD_ORDER)
        .values_list("pk", flat=True)[:SEARCH_RESULT_LIMIT]
    )
    if ids:
        return ids
    scored = []
    for pk, document in base.order_by(*STANDARD_ORDER).values_list("pk", "search_document")[
        :_FALLBACK_SCAN_LIMIT
    ]:
        words = document.split()
        score = min(
            (
                max((difflib.SequenceMatcher(None, term, word).ratio() for word in words), default=0)
                for term in terms
            ),
            default=0,
        )
        if score >= 0.75:
            scored.append((score, pk))
    scored.sort(key=lambda pair: -pair[0])
    return [pk for _, pk in scored[:SEARCH_RESULT_LIMIT]]


def is_categorized(wallpaper_type):
    return wallpaper_type in CATEGORIZED_TYPES
