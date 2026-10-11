"""Usage events -> counters -> Trending and Popular scores.

Flow for one ``POST /events`` batch:

1. Parse. Unknown event types and malformed rows are skipped (the app may add
   new types later). An event's day comes from its ``at``; events older than
   two days or from the future are dropped.
2. Keep only items that exist and are listed (one query per feature).
3. Apply the per-install caps using ``DailyInteraction``: views, plays and
   likes count once per item per day (likes once ever); downloads and sets at
   most ``REPEAT_CAP`` times a day, so one install cannot push an item up.
4. Add the accepted counts to the item counters, add their points to
   ``popular_score`` (all-time, so always exact) and ``trending_score`` (live),
   and record the points per day in ``ItemDailyStat``.

``recalculate()`` then rebuilds ``trending_score`` exactly from the last
``TRENDING_DAYS`` days, so points older than the window drop out. It runs at
most every ``SCORES_RECALC_MINUTES`` (claimed through ``JobState``), started by
event traffic, and can also be run by cron: ``manage.py recalculate_scores``.
"""

import datetime as dt
import hashlib
import logging
import threading
from collections import Counter, defaultdict

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import ChargingAnimation, Feature, Ringtone, Wallpaper
from .service_models import FOREVER, DailyInteraction, ItemDailyStat, JobState

logger = logging.getLogger(__name__)

MAX_EVENTS_PER_BATCH = 500
TRENDING_DAYS = 7
REPEAT_CAP = 5
MAX_EVENT_AGE = dt.timedelta(days=2)
MAX_CLOCK_SKEW = dt.timedelta(hours=1)
INTERACTION_KEEP_DAYS = 3
DAILY_STAT_KEEP_DAYS = 35

FEATURE_MODELS = {
    Feature.WALLPAPER: Wallpaper,
    Feature.RINGTONE: Ringtone,
    Feature.CHARGING: ChargingAnimation,
}

# event type -> (feature, action, counter field)
EVENT_TYPES = {
    "wallpaper_view": (Feature.WALLPAPER, "view", "views_count"),
    "wallpaper_download": (Feature.WALLPAPER, "download", "downloads_count"),
    "wallpaper_set": (Feature.WALLPAPER, "set", "sets_count"),
    "ringtone_play": (Feature.RINGTONE, "play", "plays_count"),
    "ringtone_set": (Feature.RINGTONE, "set", "sets_count"),
    "charging_set": (Feature.CHARGING, "set", "sets_count"),
}
LIKE_FEATURES = {"wallpaper": Feature.WALLPAPER, "ringtone": Feature.RINGTONE, "charging": Feature.CHARGING}

# How much each action is worth in Trending/Popular. Sets and downloads are
# deliberate choices; a view is a glance.
POINTS = {"set": 3.0, "download": 2.0, "play": 1.0, "like": 1.0, "view": 0.1}
CAPS = {"view": 1, "play": 1, "like": 1, "download": REPEAT_CAP, "set": REPEAT_CAP}


def install_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _event_day(raw, now):
    if not isinstance(raw, str):
        return None
    moment = parse_datetime(raw)
    if moment is None:
        return None
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, dt.timezone.utc)
    if not (now - MAX_EVENT_AGE <= moment <= now + MAX_CLOCK_SKEW):
        return None
    return moment.astimezone(dt.timezone.utc).date()


def _parse(event, now):
    """``(feature, action, field, item_id, day)`` or None to skip."""
    if not isinstance(event, dict):
        return None
    kind = event.get("type")
    if kind == "like":
        feature = LIKE_FEATURES.get(event.get("feature"))
        if feature is None:
            return None
        spec = (feature, "like", "likes_count")
    else:
        spec = EVENT_TYPES.get(kind)
        if spec is None:
            return None
    item_id = event.get("item_id")
    if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id < 1:
        return None
    day = _event_day(event.get("at"), now)
    if day is None:
        return None
    return (*spec, item_id, day)


def ingest(raw_install_id: str, events: list, now=None) -> int:
    """Apply one batch; returns how many events were counted."""
    now = now or timezone.now()
    install = install_key(raw_install_id)
    parsed = [p for p in (_parse(e, now) for e in events[:MAX_EVENTS_PER_BATCH]) if p]
    if not parsed:
        return 0

    ids_by_feature = defaultdict(set)
    for feature, _, _, item_id, _ in parsed:
        ids_by_feature[feature].add(item_id)
    valid = {
        feature: set(FEATURE_MODELS[feature].objects.listed().filter(pk__in=ids).values_list("pk", flat=True))
        for feature, ids in ids_by_feature.items()
    }

    # (feature, action, item_id, dedupe_day) -> events in this batch
    wanted = Counter()
    fields = {}
    stat_days = {}
    for feature, action, field, item_id, day in parsed:
        if item_id not in valid[feature]:
            continue
        dedupe_day = FOREVER if action == "like" else day
        key = (feature, action, item_id, dedupe_day)
        wanted[key] += 1
        fields[key] = field
        stat_days.setdefault(key, day)
    if not wanted:
        return 0

    existing = {
        (row.feature, row.action, row.item_id, row.day): row.count
        for row in DailyInteraction.objects.filter(
            install=install,
            item_id__in={key[2] for key in wanted},
            day__in={key[3] for key in wanted},
        )
    }

    rows, counters, points = [], defaultdict(Counter), Counter()
    accepted = 0
    for key, n in wanted.items():
        feature, action, item_id, dedupe_day = key
        have = existing.get(key, 0)
        allowed = min(n, CAPS[action] - have)
        if allowed <= 0:
            continue
        accepted += allowed
        rows.append(DailyInteraction(
            install=install, feature=feature, action=action, item_id=item_id,
            day=dedupe_day, count=have + allowed,
        ))
        counters[(feature, item_id)][fields[key]] += allowed
        points[(feature, item_id, stat_days[key])] += allowed * POINTS[action]
    if not accepted:
        return 0

    with transaction.atomic():
        DailyInteraction.objects.bulk_create(
            rows,
            update_conflicts=True,
            unique_fields=["install", "feature", "action", "item_id", "day"],
            update_fields=["count"],
        )
        item_points = Counter()
        for (feature, item_id, _), value in points.items():
            item_points[(feature, item_id)] += value
        for (feature, item_id), increments in counters.items():
            gained = item_points[(feature, item_id)]
            updates = {field: F(field) + n for field, n in increments.items()}
            updates["popular_score"] = F("popular_score") + gained
            updates["trending_score"] = F("trending_score") + gained
            FEATURE_MODELS[feature].objects.filter(pk=item_id).update(**updates)
        for (feature, item_id, day), value in points.items():
            _add_daily_points(feature, item_id, day, value)
    return accepted


def _add_daily_points(feature, item_id, day, value):
    lookup = {"feature": feature, "item_id": item_id, "day": day}
    if ItemDailyStat.objects.filter(**lookup).update(points=F("points") + value):
        return
    try:
        with transaction.atomic():
            ItemDailyStat.objects.create(points=value, **lookup)
    except IntegrityError:
        # Another request created it in between.
        ItemDailyStat.objects.filter(**lookup).update(points=F("points") + value)


# --- recalculation ----------------------------------------------------------------------


def recalculate(now=None) -> dict:
    """Rebuild Trending from the last 7 days and prune old bookkeeping."""
    now = now or timezone.now()
    today = now.astimezone(dt.timezone.utc).date()
    since = today - dt.timedelta(days=TRENDING_DAYS - 1)
    changed = {}
    for feature, model in FEATURE_MODELS.items():
        fresh = {
            row["item_id"]: row["total"]
            for row in ItemDailyStat.objects.filter(feature=feature, day__gte=since)
            .values("item_id")
            .annotate(total=Sum("points"))
        }
        stale = list(
            model.objects.filter(trending_score__gt=0).exclude(pk__in=list(fresh)).values_list("pk", flat=True)
        )
        count = model.objects.filter(pk__in=stale).update(trending_score=0) if stale else 0
        current = model.objects.filter(pk__in=list(fresh)).only("pk", "trending_score")
        updates = []
        for item in current:
            score = round(fresh[item.pk], 4)
            if abs(item.trending_score - score) > 1e-6:
                item.trending_score = score
                updates.append(item)
        if updates:
            model.objects.bulk_update(updates, ["trending_score"], batch_size=500)
        changed[feature] = count + len(updates)

    DailyInteraction.objects.filter(day__lt=today - dt.timedelta(days=INTERACTION_KEEP_DAYS)).exclude(
        day=FOREVER
    ).delete()
    ItemDailyStat.objects.filter(day__lt=today - dt.timedelta(days=DAILY_STAT_KEEP_DAYS)).delete()

    if any(changed.values()):
        from .cache import bump_content_version

        bump_content_version()
    return changed


def claim_recalculation(now=None) -> bool:
    """True for exactly one caller per interval, across all workers."""
    now = now or timezone.now()
    cutoff = now - dt.timedelta(minutes=settings.SCORES_RECALC_MINUTES)
    job, created = JobState.objects.get_or_create(
        name="recalculate_scores", defaults={"last_run": now}
    )
    if created:
        return True
    return bool(JobState.objects.filter(pk=job.pk, last_run__lt=cutoff).update(last_run=now))


def maybe_recalculate():
    """Called after each event batch; cheap unless this caller wins the claim."""
    if not claim_recalculation():
        return
    if settings.SCORES_RECALC_ASYNC:
        threading.Thread(target=_recalculate_in_thread, daemon=True).start()
    else:
        recalculate()


def _recalculate_in_thread():
    close_old_connections()
    try:
        recalculate()
    except Exception:  # noqa: BLE001 - never let a background job crash a worker
        logger.exception("Score recalculation failed")
    finally:
        close_old_connections()
        from django.db import connection

        connection.close()
