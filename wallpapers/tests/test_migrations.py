"""Existing production data survives the Phase 1 migrations."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("wallpapers", "0006_backfill_titles")]
AFTER = [("wallpapers", "0015_default_app_config")]


class Phase1MigrationTests(TransactionTestCase):
    def setUp(self):
        # Start from an empty table set: the rows migration 0009 created for
        # the test database would block stepping back to 0006.
        from wallpapers.models import Category, Wallpaper

        Wallpaper.objects.all().delete()
        Category.objects.all().delete()
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        old = executor.loader.project_state(BEFORE).apps
        Category = old.get_model("wallpapers", "Category")
        Wallpaper = old.get_model("wallpapers", "Wallpaper")
        anime = Category.objects.create(name="Anime", slug="anime", position=0)
        cars = Category.objects.create(name="Cars", slug="cars", position=1)
        self.ids = {}
        for key, category, title, media_type, filename in [
            ("video", anime, "Ronin at dusk LeniltonSL [9c0ae833]", "video", "ronin.mp4"),
            ("image", anime, "girl_sunset_4k", "image", "girl_sunset_4k.png"),
            ("car", cars, None, "image", "my-car (1).jpg"),
            ("long", cars, "A very long title that goes well beyond the forty character limit", "image", "x.jpg"),
        ]:
            row = Wallpaper.objects.create(
                category=category, title=title, media_type=media_type,
                original_filename=filename, file=f"wallpaper/x/assets/2026/09/{key}.bin",
            )
            self.ids[key] = row.pk
        self.anime_id, self.cars_id = anime.pk, cars.pk

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(AFTER)
        self.apps = executor.loader.project_state(AFTER).apps

    def test_data_is_carried_over(self):
        Category = self.apps.get_model("wallpapers", "Category")
        Wallpaper = self.apps.get_model("wallpapers", "Wallpaper")
        rows = {key: Wallpaper.objects.get(pk=pk) for key, pk in self.ids.items()}

        self.assertEqual(rows["video"].type, "live")
        self.assertEqual(rows["image"].type, "image")
        self.assertEqual(rows["video"].title, "Ronin at dusk LeniltonSL")
        self.assertEqual(rows["image"].title, "Girl Sunset")
        self.assertEqual(rows["car"].title, "My Car")
        self.assertLessEqual(len(rows["long"].title), 40)

        # Anime held a video and an image: it keeps its id for one type and a
        # same-named copy takes the other.
        anime = Category.objects.get(pk=self.anime_id)
        copy = Category.objects.exclude(pk=self.anime_id).get(name="Anime")
        self.assertEqual({anime.wallpaper_type, copy.wallpaper_type}, {"live", "image"})
        self.assertEqual(rows["video"].category.wallpaper_type, "live")
        self.assertEqual(rows["image"].category.wallpaper_type, "image")
        self.assertEqual(Category.objects.get(pk=self.cars_id).wallpaper_type, "image")

        for wallpaper_type in ("live", "image", "hours"):
            kinds = set(
                Category.objects.filter(wallpaper_type=wallpaper_type)
                .exclude(kind="standard").values_list("kind", flat=True)
            )
            self.assertEqual(kinds, {"trending", "popular"})
        for feature in ("ringtone", "charging"):
            trending = Category.objects.get(feature=feature, kind="trending")
            self.assertEqual((trending.name, trending.display), ("Trending", "chip"))
        config = self.apps.get_model("wallpapers", "AppConfig").objects.get(pk=1)
        self.assertEqual(config.wallpaper_tabs.count(), 5)
        self.assertEqual(config.paywall_plans.filter(is_default=True).count(), 1)
        self.assertIn("anime", rows["video"].search_document)
        self.assertTrue(self.apps.get_model("wallpapers", "ContentVersion").objects.filter(pk=1).exists())
