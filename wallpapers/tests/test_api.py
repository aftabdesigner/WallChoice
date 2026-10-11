"""The Phase 1 wallpaper API, asserted against the spec's shapes."""

import json
import re
import shutil

from django.test import TestCase

from wallpapers.models import CategoryPin, HoursPhase, ParallaxLayer, Wallpaper

from .helpers import (
    HOST,
    TEMP_MEDIA,
    image_upload,
    make_category,
    make_wallpaper,
    use_test_media,
)

WALLPAPER_KEYS = [
    "id", "type", "title", "category", "tags", "is_premium", "dominant_color",
    "media", "hours", "parallax", "fluid", "stats", "created_at",
]
MEDIA_KEYS = ["thumbnail", "preview", "hd", "uhd", "video"]
IMAGE_KEYS = ["url", "width", "height", "size_bytes"]
VIDEO_KEYS = ["url", "width", "height", "duration_ms", "size_bytes"]
CATEGORY_KEYS = [
    "id", "name", "slug", "feature", "wallpaper_type", "kind", "display", "cover",
    "position", "item_count",
]
META_KEYS = ["page", "page_size", "total", "has_next"]
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ApiTestCase(TestCase):
    def get(self, path, **headers):
        response = self.client.get(path, HTTP_HOST=HOST, **headers)
        body = json.loads(response.content) if response.content else None
        return response, body

    def assertError(self, path, status, code):
        response, body = self.get(path)
        self.assertEqual(response.status_code, status, body)
        self.assertEqual(list(body), ["error"])
        self.assertEqual(list(body["error"]), ["code", "message"])
        self.assertEqual(body["error"]["code"], code)
        return body


@use_test_media
class WallpaperApiTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.trending_live = make_category("Trending", "live", kind="trending", position=0)
        cls.popular_live = make_category("Popular", "live", kind="popular", position=1)
        cls.anime_live = make_category("Anime", "live", position=2)
        cls.cars_live = make_category("Cars", "live", position=3)
        cls.empty_live = make_category("Empty", "live", position=4)
        cls.hidden_live = make_category("Hidden", "live", position=5, is_active=False)
        cls.anime_image = make_category("Anime", "image", position=2)

        cls.live_a = make_wallpaper("live", cls.anime_live, name="samurai_sunset.mp4", tags=["samurai", "Red"])
        cls.live_b = make_wallpaper("live", cls.anime_live, name="ronin.mp4")
        cls.live_c = make_wallpaper("live", cls.cars_live, name="drift.mp4")
        cls.live_hidden = make_wallpaper("live", cls.hidden_live, name="secret.mp4")
        cls.image_a = make_wallpaper("image", cls.anime_image, name="aurora.png", is_premium=True)
        cls.draft = make_wallpaper("image", cls.anime_image, name="draft.png", is_published=False)
        cls.fluid = make_wallpaper("fluid", name="flow.png", fluid_hue_shift_deg=30)

        Wallpaper.objects.filter(pk=cls.live_c.pk).update(trending_score=50, popular_score=1)
        Wallpaper.objects.filter(pk=cls.live_b.pk).update(trending_score=10, popular_score=99)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    # --- categories ---------------------------------------------------------------

    def test_categories_shape_and_order(self):
        response, body = self.get("/api/v1/wallpapers/categories?type=live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(body), ["data", "meta"])
        self.assertEqual(list(body["meta"]), META_KEYS)
        self.assertEqual(list(body["data"][0]), CATEGORY_KEYS)
        # Only categories with published items, sorted by position.
        self.assertEqual(
            [c["name"] for c in body["data"]], ["Trending", "Popular", "Anime", "Cars"]
        )
        first = body["data"][0]
        self.assertEqual(
            (first["feature"], first["wallpaper_type"], first["kind"], first["display"]),
            ("wallpaper", "live", "trending", "row"),
        )
        self.assertEqual(body["data"][2]["display"], "chip")
        self.assertIsNone(first["cover"])

    def test_item_counts_are_real(self):
        _, body = self.get("/api/v1/wallpapers/categories?type=live")
        counts = {c["name"]: c["item_count"] for c in body["data"]}
        # The hidden category's item is still a live wallpaper, so it counts
        # towards Trending/Popular, which hold every live item.
        self.assertEqual(counts, {"Trending": 4, "Popular": 4, "Anime": 2, "Cars": 1})

    def test_categories_are_separate_per_type(self):
        _, body = self.get("/api/v1/wallpapers/categories?type=image")
        self.assertEqual([c["name"] for c in body["data"]], ["Trending", "Popular", "Anime"])
        self.assertEqual(body["data"][2]["id"], self.anime_image.id)
        self.assertEqual(body["data"][2]["item_count"], 1)  # the draft is not counted
        self.assertNotEqual(self.anime_image.id, self.anime_live.id)

    def test_category_type_validation(self):
        self.assertError("/api/v1/wallpapers/categories?type=parallax", 400, "bad_request")
        self.assertError("/api/v1/wallpapers/categories?type=bogus", 400, "bad_request")

    # --- wallpaper object -----------------------------------------------------------

    def test_wallpaper_object_shape(self):
        response, body = self.get(f"/api/v1/wallpapers?type=live&category_id={self.anime_live.id}")
        self.assertEqual(response.status_code, 200)
        row = next(r for r in body["data"] if r["id"] == self.live_a.id)
        self.assertEqual(list(row), WALLPAPER_KEYS)
        self.assertEqual(list(row["media"]), MEDIA_KEYS)
        self.assertEqual(row["type"], "live")
        self.assertEqual(row["title"], "Samurai Sunset")
        self.assertEqual(row["category"], {"id": self.anime_live.id, "name": "Anime"})
        self.assertEqual(row["tags"], ["samurai", "red"])
        self.assertRegex(row["dominant_color"], r"^#[0-9A-F]{6}$")
        self.assertRegex(row["created_at"], ISO_Z)
        self.assertEqual(row["stats"], {"downloads": 0, "sets": 0, "likes": 0})
        for key in ("hours", "parallax", "fluid"):
            self.assertIsNone(row[key])

        media = row["media"]
        self.assertEqual(list(media["thumbnail"]), IMAGE_KEYS)
        self.assertEqual(media["thumbnail"]["width"], 216)  # never upscaled
        self.assertTrue(media["thumbnail"]["url"].startswith(f"http://{HOST}/media/"))
        self.assertEqual(list(media["video"]), VIDEO_KEYS)
        self.assertGreater(media["video"]["duration_ms"], 0)
        self.assertTrue(media["video"]["url"].endswith(".mp4"))
        self.assertIsNone(media["hd"])
        self.assertIsNone(media["uhd"])

    def test_image_wallpaper_has_hd_and_no_video(self):
        _, body = self.get("/api/v1/wallpapers?type=image")
        self.assertEqual([r["id"] for r in body["data"]], [self.image_a.id])
        row = body["data"][0]
        self.assertTrue(row["is_premium"])
        self.assertIsNotNone(row["media"]["hd"])
        self.assertIsNone(row["media"]["video"])

    def test_fluid_has_its_block_and_no_category(self):
        _, body = self.get("/api/v1/wallpapers?type=fluid")
        row = body["data"][0]
        self.assertIsNone(row["category"])
        self.assertEqual(row["fluid"], {"hue_shift_deg": 30, "cycle_ms": 8000})
        self.assertIsNotNone(row["media"]["hd"])

    # --- lists ---------------------------------------------------------------------------

    def test_standard_category_is_newest_first(self):
        _, body = self.get(f"/api/v1/wallpapers?type=live&category_id={self.anime_live.id}")
        self.assertEqual([r["id"] for r in body["data"]], [self.live_b.id, self.live_a.id])

    def test_priority_comes_before_newness(self):
        Wallpaper.objects.filter(pk=self.live_a.pk).update(priority=5)
        _, body = self.get(f"/api/v1/wallpapers?type=live&category_id={self.anime_live.id}")
        self.assertEqual([r["id"] for r in body["data"]], [self.live_a.id, self.live_b.id])

    def test_trending_is_ordered_by_score_with_pins_first(self):
        path = f"/api/v1/wallpapers?type=live&category_id={self.trending_live.id}"
        _, body = self.get(path)
        ids = [r["id"] for r in body["data"]]
        self.assertEqual(ids[:2], [self.live_c.id, self.live_b.id])
        CategoryPin.objects.create(category=self.trending_live, wallpaper=self.live_a, position=0)
        _, body = self.get(path)
        self.assertEqual([r["id"] for r in body["data"]][:3], [self.live_a.id, self.live_c.id, self.live_b.id])

    def test_popular_uses_its_own_score(self):
        _, body = self.get(f"/api/v1/wallpapers?type=live&category_id={self.popular_live.id}")
        self.assertEqual(body["data"][0]["id"], self.live_b.id)

    def test_auto_limit_caps_the_category(self):
        self.trending_live.auto_limit = 2
        self.trending_live.save()
        _, body = self.get(f"/api/v1/wallpapers?type=live&category_id={self.trending_live.id}")
        self.assertEqual(body["meta"]["total"], 2)

    def test_category_of_another_type_is_an_empty_list(self):
        response, body = self.get(f"/api/v1/wallpapers?type=image&category_id={self.anime_live.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["data"], [])
        _, body = self.get("/api/v1/wallpapers?type=live&category_id=999999")
        self.assertEqual(body["data"], [])

    def test_unpublished_and_unready_items_never_appear(self):
        Wallpaper.objects.filter(pk=self.live_b.pk).update(media_ready=False)
        _, body = self.get("/api/v1/wallpapers?type=live")
        ids = {r["id"] for r in body["data"]}
        self.assertNotIn(self.live_b.id, ids)
        _, body = self.get("/api/v1/wallpapers?type=image")
        self.assertNotIn(self.draft.id, {r["id"] for r in body["data"]})

    def test_ids_keep_the_requested_order(self):
        path = f"/api/v1/wallpapers?ids={self.image_a.id},{self.live_a.id},{self.draft.id},999"
        response, body = self.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["id"] for r in body["data"]], [self.image_a.id, self.live_a.id])
        self.assertEqual(body["meta"]["total"], 2)

    def test_bad_parameters_are_400s(self):
        for query in (
            "type=live&category_id=abc",
            "type=nope",
            "ids=1,two",
            "ids=" + ",".join(str(i) for i in range(101)),
            "type=live&page=0",
            "type=live&page_size=101",
        ):
            with self.subTest(query=query):
                self.assertError(f"/api/v1/wallpapers?{query}", 400, "bad_request")

    def test_paging(self):
        _, first = self.get("/api/v1/wallpapers?type=live&page_size=2")
        self.assertEqual(first["meta"], {"page": 1, "page_size": 2, "total": 4, "has_next": True})
        _, last = self.get("/api/v1/wallpapers?type=live&page=2&page_size=2")
        self.assertFalse(last["meta"]["has_next"])
        _, past = self.get("/api/v1/wallpapers?type=live&page=99")
        self.assertEqual(past["data"], [])

    # --- detail ------------------------------------------------------------------------------

    def test_detail(self):
        response, body = self.get(f"/api/v1/wallpapers/{self.image_a.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(body), ["data"])
        self.assertEqual(body["data"]["id"], self.image_a.id)

    def test_detail_404s(self):
        body = self.assertError("/api/v1/wallpapers/99999", 404, "not_found")
        self.assertEqual(body["error"]["message"], "Wallpaper 99999 does not exist.")
        self.assertError(f"/api/v1/wallpapers/{self.draft.id}", 404, "not_found")

    def test_unknown_api_path_is_a_json_404(self):
        self.assertError("/api/v1/nothing-here", 404, "not_found")

    # --- feed --------------------------------------------------------------------------------

    def test_feed_for_a_categorized_type(self):
        response, body = self.get("/api/v1/wallpapers/feed?type=live")
        self.assertEqual(response.status_code, 200)
        data = body["data"]
        self.assertEqual(list(data), ["type", "rows", "chips", "items", "has_more"])
        self.assertEqual([r["category"]["name"] for r in data["rows"]], ["Trending", "Popular"])
        self.assertEqual(data["rows"][0]["items"][0]["id"], self.live_c.id)
        self.assertFalse(data["rows"][0]["has_more"])
        chips = data["chips"]
        self.assertEqual([c["name"] for c in chips["categories"]], ["Anime", "Cars"])
        self.assertEqual(chips["selected_category_id"], self.anime_live.id)
        self.assertEqual([w["id"] for w in chips["items"]], [self.live_b.id, self.live_a.id])
        self.assertEqual(data["items"], [])

    def test_feed_for_a_flat_type(self):
        _, body = self.get("/api/v1/wallpapers/feed?type=fluid")
        data = body["data"]
        self.assertEqual(data["rows"], [])
        self.assertIsNone(data["chips"])
        self.assertEqual([w["id"] for w in data["items"]], [self.fluid.id])
        self.assertFalse(data["has_more"])

    def test_feed_needs_a_type(self):
        self.assertError("/api/v1/wallpapers/feed", 400, "bad_request")

    # --- search -----------------------------------------------------------------------------

    def test_search_matches_title_tags_and_category(self):
        for query, expected in (
            ("samurai", self.live_a.id),
            ("RED", self.live_a.id),
            ("aurora", self.image_a.id),
            ("drift", self.live_c.id),
        ):
            with self.subTest(query=query):
                _, body = self.get(f"/api/v1/wallpapers/search?q={query}")
                self.assertIn(expected, [r["id"] for r in body["data"]])
        _, body = self.get("/api/v1/wallpapers/search?q=anime&type=image")
        self.assertEqual([r["id"] for r in body["data"]], [self.image_a.id])

    def test_search_tolerates_small_typos(self):
        _, body = self.get("/api/v1/wallpapers/search?q=samurii")
        self.assertEqual(body["data"][0]["id"], self.live_a.id)

    def test_search_with_no_match_is_an_empty_200(self):
        response, body = self.get("/api/v1/wallpapers/search?q=zzzzqqq")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["data"], [])

    def test_search_needs_a_query(self):
        self.assertError("/api/v1/wallpapers/search?q=", 400, "bad_request")

    # --- scalability ------------------------------------------------------------------------

    def test_query_count_does_not_grow_with_the_catalogue(self):
        """No per-row queries: 1 item or 40, each endpoint costs the same."""
        paths = [
            "/api/v1/wallpapers?type=live&page_size=100",
            f"/api/v1/wallpapers?type=live&category_id={self.anime_live.id}&page_size=100",
            f"/api/v1/wallpapers?type=live&category_id={self.trending_live.id}&page_size=100",
            "/api/v1/wallpapers/feed?type=live",
            "/api/v1/wallpapers/categories?type=live",
            "/api/v1/wallpapers/search?q=clone",
        ]
        source = Wallpaper.objects.get(pk=self.live_a.pk)

        def clone(count):
            rows = []
            for n in range(count):
                row = Wallpaper.objects.get(pk=source.pk)
                row.pk = None
                row.content_hash = None
                row.title = f"Clone {n}"
                row.search_document = f"clone {n} anime"
                rows.append(row)
            Wallpaper.objects.bulk_create(rows)

        def counts():
            result = []
            for path in paths:
                from django.db import connection
                from django.test.utils import CaptureQueriesContext

                with CaptureQueriesContext(connection) as ctx:
                    self.assertEqual(self.get(path)[0].status_code, 200, path)
                result.append(len(ctx.captured_queries))
            return result

        clone(1)
        counts()  # warm-up: per-process one-offs such as the pg_trgm check
        small = counts()
        clone(40)
        self.assertEqual(counts(), small)


@use_test_media
class ComposedTypeApiTests(ApiTestCase):
    """24 Hours and Parallax carry their phases and layers."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def test_hours_phases_are_served(self):
        category = make_category("Cities", "hours")
        wallpaper = Wallpaper.objects.create(type="hours", category=category, title="City")
        for position, (name, start, end) in enumerate(
            [("Day", 6, 18), ("Night", 18, 6)]
        ):
            HoursPhase.objects.create(
                wallpaper=wallpaper, name=name, from_hour=start, to_hour=end,
                image=image_upload(f"{name}.png"), position=position,
            )
        self.assertTrue(wallpaper.rebuild_composed_media())
        wallpaper.refresh_from_db()
        self.assertTrue(wallpaper.media_ready)

        _, body = self.get(f"/api/v1/wallpapers/{wallpaper.id}")
        hours = body["data"]["hours"]
        self.assertEqual(
            [(p["name"], p["from_hour"], p["to_hour"]) for p in hours["phases"]],
            [("Day", 6, 18), ("Night", 18, 6)],
        )
        self.assertEqual(list(hours["phases"][0]["image"]), IMAGE_KEYS)
        self.assertIsNotNone(body["data"]["media"]["thumbnail"])
        _, categories = self.get("/api/v1/wallpapers/categories?type=hours")
        self.assertEqual(categories["data"][0]["item_count"], 1)

    def test_parallax_layers_are_served_back_to_front(self):
        wallpaper = Wallpaper.objects.create(type="parallax", title="Peaks")
        for position, (role, depth) in enumerate(
            [("background", "0.20"), ("foreground", "1.00")]
        ):
            ParallaxLayer.objects.create(
                wallpaper=wallpaper, role=role, depth=depth, position=position,
                image=image_upload(f"{role}.png", alpha=role != "background"),
            )
        wallpaper.rebuild_composed_media()
        wallpaper.refresh_from_db()
        self.assertTrue(wallpaper.media_ready)

        _, body = self.get("/api/v1/wallpapers/feed?type=parallax")
        row = body["data"]["items"][0]
        self.assertIsNone(row["category"])
        self.assertEqual(
            [(l["role"], l["depth"]) for l in row["parallax"]["layers"]],
            [("background", 0.2), ("foreground", 1.0)],
        )
        self.assertIsNone(row["hours"])

    def test_incomplete_composed_items_stay_hidden(self):
        Wallpaper.objects.create(type="parallax", title="No layers")
        _, body = self.get("/api/v1/wallpapers?type=parallax")
        self.assertEqual(body["data"], [])
