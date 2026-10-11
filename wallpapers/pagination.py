"""Paging for the list envelope.

DRF's PageNumberPagination is the wrong shape here on three counts: it emits
count/next/previous rather than our ``meta``, it silently clamps an oversized
page_size where the contract wants a 400, and it 404s past the last page where
the contract wants an empty list. Doing the slicing by hand is shorter than
overriding all three.
"""

from rest_framework.exceptions import ValidationError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def positive_int(raw, name, default):
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be an integer.")
    if value < 1:
        raise ValidationError(f"{name} must be 1 or greater.")
    return value


def page_params(request):
    params = request.query_params
    page = positive_int(params.get("page"), "page", 1)
    page_size = positive_int(params.get("page_size"), "page_size", DEFAULT_PAGE_SIZE)
    if page_size > MAX_PAGE_SIZE:
        raise ValidationError(f"page_size may not exceed {MAX_PAGE_SIZE}.")
    return page, page_size


def _meta(page, page_size, total, returned, start):
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": start + returned < total,
    }


def paginate(queryset, request):
    """Return ``(items, meta)`` for a queryset (one COUNT, one page query)."""
    page, page_size = page_params(request)
    total = queryset.count()
    start = (page - 1) * page_size
    # Past the last page this slices to [], which is what the contract asks for.
    items = list(queryset[start : start + page_size]) if start < total else []
    return items, _meta(page, page_size, total, len(items), start)


def paginate_list(sequence, request):
    """Same as ``paginate`` for an in-memory list (ids, small category sets)."""
    page, page_size = page_params(request)
    total = len(sequence)
    start = (page - 1) * page_size
    items = list(sequence[start : start + page_size])
    return items, _meta(page, page_size, total, len(items), start)
