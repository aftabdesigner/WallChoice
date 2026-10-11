"""Response caching, ETags and invalidation."""

import shutil

from django.core.cache import cache
from django.test import TestCase, override_settings

from wallpapers import cache as api_cache

from .helpers import HOST, TEMP_MEDIA, make_category, make_wallpaper, use_test_media


# The outer decorator wins when both set a value, so caching is switched on last.
@override_settings(API_CACHE_SECONDS=300, API_CLIENT_MAX_AGE=300)
@use_test_media
class CacheTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anime = make_category("Anime", "image")
        cls.first = make_wallpaper("image", cls.anime, name="first.png")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        api_cache._memo.value = None

    def get(self, path, **headers):
        return self.client.get(path, HTTP_HOST=HOST, **headers)

    def test_second_request_is_served_from_cache(self):
        path = "/api/v1/wallpapers/feed?type=image"
        first = self.get(path)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first["Cache-Control"], "public, max-age=300")
        with self.assertNumQueries(0):
            second = self.get(path)
        self.assertEqual(second.content, first.content)

    def test_etag_gives_304(self):
        path = "/api/v1/wallpapers?type=image"
        first = self.get(path)
        etag = first["ETag"]
        again = self.get(path, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.content, b"")
        self.assertEqual(again["ETag"], etag)

    def test_a_content_change_invalidates_every_cached_list(self):
        path = "/api/v1/wallpapers?type=image"
        before = self.get(path).json()
        self.assertEqual(before["meta"]["total"], 1)
        with self.captureOnCommitCallbacks(execute=True):
            make_wallpaper("image", self.anime, name="second.png")
        api_cache._memo.value = None  # skip the 2-second per-process memo
        after = self.get(path).json()
        self.assertEqual(after["meta"]["total"], 2)

    def test_errors_are_not_cached(self):
        path = "/api/v1/wallpapers?type=nope"
        self.assertEqual(self.get(path).status_code, 400)
        self.assertEqual(self.get(path).status_code, 400)

    def test_query_order_does_not_split_the_cache(self):
        a = self.get("/api/v1/wallpapers?type=image&page=1")
        with self.assertNumQueries(0):
            b = self.get("/api/v1/wallpapers?page=1&type=image")
        self.assertEqual(a.content, b.content)
