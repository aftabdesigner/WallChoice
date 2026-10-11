"""App builds already installed call without ``type`` and must keep working."""

import json
import shutil

from django.test import TestCase

from wallpapers.models import Wallpaper

from .helpers import HOST, TEMP_MEDIA, make_category, make_wallpaper, use_test_media

LEGACY_CATEGORY_KEYS = ["id", "name", "description", "position", "artwork_count"]
LEGACY_WALLPAPER_KEYS = [
    "id", "category_id", "media_type", "is_premium", "title", "artwork", "created_at",
    "thumbnail_path",
]


@use_test_media
class LegacyContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anime_live = make_category("Anime", "live", position=0)
        cls.anime_image = make_category("Anime", "image", position=0)
        cls.empty = make_category("Empty", "image", position=1)
        cls.video = make_wallpaper("live", cls.anime_live, name="clip.mp4")
        cls.image = make_wallpaper("image", cls.anime_image, name="my shot.png", is_premium=True)
        cls.draft = make_wallpaper("image", cls.empty, name="draft.png", is_published=False)
        cls.fluid = make_wallpaper("fluid", name="flow.png")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def get(self, path):
        response = self.client.get(path, HTTP_HOST=HOST)
        return response, json.loads(response.content)

    def test_categories_keep_the_old_shape(self):
        response, body = self.get("/api/v1/wallpapers/categories?page=1&page_size=100")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(body), ["data", "meta"])
        self.assertEqual(list(body["data"][0]), LEGACY_CATEGORY_KEYS)
        rows = {c["id"]: c for c in body["data"]}
        # Standard categories only; empty ones still listed, as before.
        self.assertEqual(set(rows), {self.anime_live.id, self.anime_image.id, self.empty.id})
        self.assertEqual(rows[self.anime_live.id]["artwork_count"], 1)
        self.assertEqual(rows[self.empty.id]["artwork_count"], 0)
        self.assertIsNone(rows[self.empty.id]["description"])

    def test_wallpapers_keep_the_old_shape(self):
        response, body = self.get("/api/v1/wallpapers?page=1&page_size=100")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(body["data"][0]), LEGACY_WALLPAPER_KEYS)
        rows = {r["id"]: r for r in body["data"]}
        # Live and Image only - the old app cannot draw the other types.
        self.assertEqual(set(rows), {self.video.id, self.image.id})
        self.assertEqual(rows[self.video.id]["media_type"], "video")
        self.assertEqual(rows[self.image.id]["media_type"], "image")
        self.assertTrue(rows[self.video.id]["artwork"].endswith(".mp4"))
        self.assertTrue(rows[self.video.id]["artwork"].startswith(f"http://{HOST}/media/"))
        # Videos now come with a thumbnail, which the old app already reads.
        self.assertIsNotNone(rows[self.video.id]["thumbnail_path"])
        self.assertRegex(
            rows[self.image.id]["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}$"
        )

    def test_category_filter_still_works(self):
        _, body = self.get(f"/api/v1/wallpapers?category_id={self.anime_image.id}")
        self.assertEqual([r["id"] for r in body["data"]], [self.image.id])

    def test_rows_without_new_media_are_still_served_to_the_old_app(self):
        Wallpaper.objects.filter(pk=self.image.pk).update(media_ready=False, thumbnail=None)
        _, body = self.get("/api/v1/wallpapers")
        row = next(r for r in body["data"] if r["id"] == self.image.id)
        self.assertIsNone(row["thumbnail_path"])
