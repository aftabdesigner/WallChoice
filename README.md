# WallChoice API

Django + DRF + Postgres backend for the WallChoice Android app. Media lives on
Cloudflare R2; Django admin is the content-management surface.

**Done:** Phase 1 (wallpapers), Phase 2 (ringtones, charging animations), and from
Phase 3 `/config` and `/events` (counters, Trending/Popular). **Still to do:** devices
and push, purchase check, Play Integrity and rate limits.

## Endpoints

All are public GETs under `/api/v1/`, JSON only.

| Endpoint | Notes |
|---|---|
| `GET /wallpapers/categories?type=live` | `type` = `live` \| `image` \| `hours`. Only categories with published items, by `position`. |
| `GET /wallpapers/feed?type=live` | One call fills a type's screen: `rows` (Trending, Popular…), `chips` with the first chip's items. For `parallax` / `fluid`: `rows: []`, `chips: null`, `items`. |
| `GET /wallpapers?type=live&category_id=1` | Paged list. `category_id` works for Trending and Popular too. |
| `GET /wallpapers?ids=12,4,31` | Specific items in the given order (favorites, paywall). Max 100. |
| `GET /wallpapers/{id}` | One item; `404` when missing or unpublished. |
| `GET /wallpapers/search?q=samurai&type=live` | Title, category name and tags, case-insensitive, tolerates small typos. Best match first. `type` optional. |

Lists use `{ "data": [...], "meta": { "page", "page_size", "total", "has_next" } }`;
single items `{ "data": {...} }`. `page` from 1, `page_size` default 20, max 100
(over the cap is a 400). Past the last page is an empty list.

Errors: `{ "error": { "code": "not_found", "message": "Wallpaper 99 does not exist." } }`
with codes `bad_request`, `not_found`, `server_error` (also for unknown `/api/` paths).

### Wallpaper object

`id, type, title, category, tags, is_premium, dominant_color, media, hours, parallax, fluid, stats, created_at`

- `media`: `thumbnail` (400 px wide, 9:16, ≤ 60 KB), `preview` (1080 px, ≤ 400 KB; the
  first frame for videos), `hd` (1080 px), `uhd` (2160 px or `null`), `video` (live,
  optional for fluid). Images are `{url, width, height, size_bytes}`; video adds
  `duration_ms`. Every URL is absolute.
- `hours` / `parallax` / `fluid` are `null` unless the item is that type.
- `category` is `{id, name}`; `null` for parallax and fluid.
- `created_at` is ISO 8601 UTC: `2026-10-11T06:14:51Z`.

### Category object

`id, name, slug, feature, wallpaper_type, kind, display, cover, position, item_count`

- `kind`: `trending` / `popular` fill themselves (pinned items first, then by score)
  up to `auto_limit` items; `standard` categories are assigned by hand.
- `display`: `row` (section with See all) or `chip` (category bar).
- Categories are separate per type: "Anime" in Live and in Image are two categories.

### Ringtones

| Endpoint | Notes |
|---|---|
| `GET /ringtones/categories` | Chips (Trending first). Same Category object, `feature: "ringtone"`. |
| `GET /ringtones?category_id=3` | `category_id` required. Trending: pinned first, then by score. |
| `GET /ringtones/featured?category_id=3` | The TOP RINGTONE card: the admin's pick, else the first item. `data` is `null` for an empty category. |
| `GET /ringtones/{id}` | |

Ringtone object: `id, title, artist, category, is_premium, artwork {thumbnail 200×200,
large 800×800}, audio {url, format, duration_ms, size_bytes}, waveform (44 values,
0–100), stats {plays, sets}, created_at`. Uploads are converted to MP3 (160 kbit/s,
about −14 LUFS) and trimmed to 40 s with a fade-out.

### Charging animations

| Endpoint | Notes |
|---|---|
| `GET /charging-animations/categories` | Chips. `feature: "charging"`. |
| `GET /charging-animations?category_id=2` | `category_id` required. |
| `GET /charging-animations/{id}` | |

Object: `id, title, category, is_premium, preview {thumbnail, video}, animation {format
lottie|mp4, url, size_bytes, loop}, battery_label {visible, position, color},
background_color, sound`. Lottie JSON is validated and minified (max 300 KB) and needs a
thumbnail or a preview video. An MP4 (max 5 MB) gets its audio removed, a thumbnail from
its first frame, and serves as its own preview video.

### App config: `GET /config`

Everything the app would otherwise hard-code: version codes ("Update required" /
"update available"), feature switches, the wallpaper tabs, ad rules, download options,
the paywall (texts, hero wallpapers, plans), onboarding texts and links. Shape as in the
spec. Edit it all in the admin under **App config**. It's a single page, and changes reach
the app within the 5-minute cache. Version codes start at 1, so no build is told to
update until you raise them. Prices are never sent; they come from Google Play per
`product_id`, so the plan IDs must match Play Console.

### Usage events: `POST /events`

Header `X-Install-Id` (required), body `{"events": [{"type", "item_id", "at", ...}]}`,
up to 500 per request, answer `204`. Types: `wallpaper_view`, `wallpaper_download`,
`wallpaper_set`, `ringtone_play`, `ringtone_set`, `charging_set`, `like` (with
`feature`). Unknown types and malformed rows are ignored; events older than 2 days, in
the future, or for unknown/unpublished items are dropped.

- **Caps per install:** a view or play counts once per item per day; a like once ever;
  downloads and sets at most 5 times per item per day.
- **Points:** set 3, download 2, play 1, like 1, view 0.1.
- **Popular** = all-time points, updated live with each batch.
- **Trending** = points from the last 7 days. Updated live, and rebuilt exactly every
  `SCORES_RECALC_MINUTES` (default 10) in the background while events arrive, so old
  points drop out. If traffic can stop for long, also schedule
  `python manage.py recalculate_scores` as a cron job.

### Old app builds

Requests **without** `type` (and without `ids`) get the pre-Phase-1 shape
(`artwork`, `thumbnail_path`, `artwork_count`…), listing Live and Image items, so
builds already installed keep working. New builds always send `type`.

## Admin

- **Wallpapers → Add**: Live, Image or Fluid. Pick the type and category, select up to
  10 files. Each file becomes a published wallpaper with a clean title, and its
  thumbnail, preview and HD/4K versions are made automatically. Videos get their
  audio track removed and are made to start playing before they finish downloading
  ("faststart"). Duplicate files are rejected.
- **24 Hours / Parallax**: use the links on the add page. Add phases (covering all 24
  hours) or 2–4 equally sized layers. The card image is built from them, or from a
  source image if you upload one.
- **Categories**: Trending and Popular exist for Live, Image and 24 Hours. Open one to
  pin items to the top. Upload a cover for Image category tiles (cropped to 600×375).
- An item appears in the app only when it is published **and** its media is complete
  (the *Media ready* column).
- To replace a generated thumbnail or preview, upload one on the change form. Clear it
  to go back to automatic.
- **Ringtones → Add**: pick a ringtone category, select up to 10 audio files and one
  square artwork used for all of them. Pick a category's TOP RINGTONE on the category page.
- **Charging animations → Add**: upload a Lottie JSON (plus a thumbnail or preview video)
  or an MP4, set the battery label, colours and an optional plug-in sound.
- Trending exists for ringtones and charging too. Pin items to the top from its
  category page.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows; use .venv/bin/pip elsewhere
cp .env.example .env                              # then edit it
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py createsuperuser
.venv/Scripts/python manage.py runserver 0.0.0.0:8000
```

ffmpeg comes with the `imageio-ffmpeg` package, so nothing else needs installing.

## Deploying Phase 1 (existing database)

1. Deploy. `migrate` runs on start and:
   - gives every wallpaper a `type` (video → Live, image → Image),
   - splits categories that held both videos and images into one per type,
   - cleans stored titles and caps them at 40 characters,
   - creates Trending and Popular for Live, Image and 24 Hours,
   - enables `pg_trgm` for typo-tolerant search (if the database refuses, the deploy
     still succeeds and search falls back to exact matching).
2. Build media for existing wallpapers. Until this runs they are hidden from the new
   endpoints (the old app still sees them):

   ```bash
   railway run python manage.py rebuild_media
   ```

   or select them in the admin and run **Rebuild thumbnails and versions**. Duplicates
   found on the way are unpublished (the oldest copy is kept).

## Deploying to Railway

The Dockerfile runs `migrate`, then gunicorn with `WEB_CONCURRENCY` workers ×
`GUNICORN_THREADS` threads (default 2 × 4). The health check can point at
`/api/v1/wallpapers/categories`.

| Variable | Value |
|---|---|
| `SECRET_KEY` | fresh random string |
| `DEBUG` | `False` |
| `DATABASE_URL` | `${{ Postgres.DATABASE_URL }}` |
| `USE_LOCAL_MEDIA` | `False` |
| `R2_ACCOUNT_ID` or `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_PUBLIC_BASE_URL` | Cloudflare R2 (public mode) |
| `REDIS_URL` | optional: `${{ Redis.REDIS_URL }}` to share the response cache between replicas |
| `R2_SIGNED_URLS` | optional: `True` for expiring links from a private bucket; then `R2_PUBLIC_BASE_URL` is not needed |
| `WEB_CONCURRENCY`, `GUNICORN_THREADS` | optional tuning |

`USE_LOCAL_MEDIA` must be `False` in production: Railway's disk is wiped on deploy.
For local development with R2, set `USE_LOCAL_MEDIA=False` and configure the R2
credentials and bucket. Public buckets also need `R2_PUBLIC_BASE_URL`; private
buckets can use `R2_SIGNED_URLS=True`.

## Scaling

- Every public response is cached (Redis when `REDIS_URL` is set, otherwise
  in-process) and invalidated automatically by any admin change through a single
  version counter, so reads rarely touch the database.
- Responses carry `ETag` and `Cache-Control: public, max-age=300`; a client sending
  `If-None-Match` gets an empty `304`.
- Each endpoint runs a fixed number of indexed queries whatever the catalogue size
  (asserted in the tests). Trending/Popular read straight from score indexes.
- Media files have random names and are stored with
  `Cache-Control: public, max-age=31536000, immutable`, so a CDN and phones keep them.
- Database connections are reused (`DB_CONN_MAX_AGE`).

## Tests

```bash
.venv/Scripts/python manage.py test
```

118 tests cover the API shapes, the old-app contract, media processing (renditions,
video, audio, waveform, Lottie, duplicates), the admin, caching, query counts, config,
events (caps, counters, Trending/Popular) and the data migrations. They run
on SQLite by default; set `DATABASE_URL` to a PostgreSQL URL to run them there.
