"""Response caching for the public read endpoints.

Every list the app loads is the same for every user, so the JSON is built once
and served from the cache until content changes. Invalidation is a single
counter (``ContentVersion``) that is part of every cache key: any admin change
bumps it, and every worker picks up the new value within
``VERSION_MEMO_SECONDS``. Nothing has to find or delete individual entries.

Responses also carry an ``ETag``; a client that sends it back in
``If-None-Match`` gets an empty ``304`` instead of the body.
"""

import hashlib
import json
import threading
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, HttpResponseNotModified

VERSION_MEMO_SECONDS = 2

_memo = threading.local()


def content_version() -> int:
    now = time.monotonic()
    cached = getattr(_memo, "value", None)
    if cached is not None and now < getattr(_memo, "expires", 0):
        return cached
    from .models import ContentVersion

    value = ContentVersion.objects.filter(pk=1).values_list("value", flat=True).first()
    if value is None:
        value = ContentVersion.objects.get_or_create(pk=1)[0].value
    _memo.value, _memo.expires = value, now + VERSION_MEMO_SECONDS
    return value


def bump_content_version() -> None:
    from .models import ContentVersion

    updated = ContentVersion.objects.filter(pk=1).update(value=F("value") + 1)
    if not updated:
        ContentVersion.objects.get_or_create(pk=1, defaults={"value": 2})
    _memo.value = None


def bump_on_commit(*args, **kwargs) -> None:
    """Signal receiver: invalidate once the change is actually committed."""
    transaction.on_commit(bump_content_version)


def _cache_key(request) -> str:
    params = sorted(request.GET.lists())
    query = urlencode([(key, value) for key, values in params for value in values])
    raw = f"{request.get_host()}|{request.path}|{query}"
    digest = hashlib.sha1(raw.encode()).hexdigest()
    return f"api:{content_version()}:{digest}"


def _encode(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()


def cached_json(request, build) -> HttpResponse:
    """Serve ``build()``'s data from the cache, building it on a miss.

    ``build`` may raise DRF exceptions; those propagate (and are never
    cached), so error responses keep their normal shape.
    """
    ttl = settings.API_CACHE_SECONDS
    key = _cache_key(request) if ttl else None
    entry = cache.get(key) if key else None
    if entry is None:
        body = _encode(build())
        etag = f'"{hashlib.md5(body).hexdigest()}"'
        entry = (body, etag)
        if key:
            cache.set(key, entry, ttl)
    body, etag = entry

    max_age = settings.API_CLIENT_MAX_AGE
    if etag in _if_none_match(request):
        response = HttpResponseNotModified()
    else:
        response = HttpResponse(body, content_type="application/json")
    response["ETag"] = etag
    response["Cache-Control"] = f"public, max-age={max_age}"
    return response


def _if_none_match(request) -> set:
    header = request.META.get("HTTP_IF_NONE_MATCH", "")
    return {part.strip().removeprefix("W/") for part in header.split(",") if part.strip()}
