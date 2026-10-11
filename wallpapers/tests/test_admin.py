"""The admin: batch uploads, 24 Hours / Parallax forms, overrides and actions."""

import os
import shutil

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from wallpapers.forms import MAX_FILES_PER_UPLOAD
from wallpapers.models import ContentVersion, HoursPhase, ParallaxLayer, Wallpaper

from .helpers import (
    TEMP_MEDIA,
    clear_media,
    image_bytes,
    image_upload,
    make_category,
    make_wallpaper,
    use_test_media,
    video_upload,
)


def _inline(prefix, rows):
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"{prefix}-{index}-{key}"] = value
    return data


@use_test_media
class AdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser("root", "root@example.com", "pw")
        cls.anime_image = make_category("Anime", "image")
        cls.anime_live = make_category("Anime", "live")
        cls.cities = make_category("Cities", "hours")
        cls.add_url = reverse("admin:wallpapers_wallpaper_add")
        cls.list_url = reverse("admin:wallpapers_wallpaper_changelist")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.client.force_login(self.staff)
        clear_media()

    def media_files(self):
        found = []
        for root, _, files in os.walk(TEMP_MEDIA):
            found.extend(files)
        return found

    def bulk(self, files, wallpaper_type="image", category=None, **extra):
        data = {"type": wallpaper_type, "files": files, "tags": "night, Neon", **extra}
        if category is not None:
            data["category"] = category.pk
        return self.client.post(self.add_url, data, follow=True)

    # --- pages load ----------------------------------------------------------------

    def test_every_admin_page_renders(self):
        image = make_wallpaper("image", self.anime_image)
        live = make_wallpaper("live", self.anime_live)
        pages = [
            self.list_url,
            self.add_url,
            self.add_url + "?type=hours",
            self.add_url + "?type=parallax",
            reverse("admin:wallpapers_wallpaper_change", args=[image.pk]),
            reverse("admin:wallpapers_wallpaper_change", args=[live.pk]),
            reverse("admin:wallpapers_category_changelist"),
            reverse("admin:wallpapers_category_add"),
            reverse("admin:wallpapers_category_change", args=[self.anime_image.pk]),
        ]
        trending = self.anime_image.__class__.objects.get(wallpaper_type="image", kind="trending")
        pages.append(reverse("admin:wallpapers_category_change", args=[trending.pk]))
        for url in pages:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    # --- batch upload ----------------------------------------------------------------

    def test_batch_of_images(self):
        response = self.bulk(
            [image_upload("beach_sunset.png"), image_upload("neon-city [a1b2c3d4].jpg")],
            category=self.anime_image,
        )
        self.assertEqual(response.status_code, 200)
        rows = list(Wallpaper.objects.order_by("id"))
        self.assertEqual([r.title for r in rows], ["Beach Sunset", "Neon City"])
        for row in rows:
            self.assertTrue(row.is_published)
            self.assertTrue(row.media_ready)
            self.assertEqual(row.tags, ["night", "neon"])
            self.assertEqual(row.type, "image")
        self.assertContains(response, "2 wallpapers added to Anime")

    def test_batch_of_live_videos(self):
        response = self.bulk([video_upload("a.mp4", 11), video_upload("b.mp4", 12)], "live", self.anime_live)
        self.assertEqual(response.status_code, 200)
        rows = Wallpaper.objects.filter(type="live")
        self.assertEqual(rows.count(), 2)
        self.assertTrue(all(r.media_ready and r.thumbnail for r in rows))

    def test_fluid_needs_no_category(self):
        self.bulk([image_upload("flow.png")], "fluid")
        self.assertEqual(Wallpaper.objects.get().type, "fluid")

    def test_wrong_category_type_is_rejected_and_nothing_is_written(self):
        response = self.bulk([image_upload()], "image", self.anime_live)
        self.assertContains(response, "Pick a standard Image category")
        self.assertFalse(Wallpaper.objects.exists())
        self.assertEqual(self.media_files(), [])

    def test_duplicates_are_rejected(self):
        data = image_bytes()
        response = self.bulk(
            [SimpleUploadedFile("a.png", data), SimpleUploadedFile("b.png", data)],
            category=self.anime_image,
        )
        self.assertContains(response, "same file as a.png")
        self.assertFalse(Wallpaper.objects.exists())

        existing = make_wallpaper("image", self.anime_image)
        with existing.file.open("rb") as handle:
            stored = handle.read()
        response = self.bulk([SimpleUploadedFile("again.png", stored)], category=self.anime_image)
        self.assertContains(response, f"already uploaded as wallpaper #{existing.pk}")

    def test_too_many_files(self):
        files = [image_upload(f"{i}.png", width=40, height=80) for i in range(MAX_FILES_PER_UPLOAD + 1)]
        response = self.bulk(files, category=self.anime_image)
        self.assertContains(response, f"Select at most {MAX_FILES_PER_UPLOAD} files")
        self.assertFalse(Wallpaper.objects.exists())

    def test_broken_video_is_a_form_error(self):
        broken = SimpleUploadedFile("broken.mp4", b"\x00\x00\x00\x18ftypmp42", content_type="video/mp4")
        response = self.bulk([broken], "live", self.anime_live)
        self.assertContains(response, "broken.mp4")
        self.assertFalse(Wallpaper.objects.exists())

    # --- 24 Hours and Parallax ---------------------------------------------------------

    def hours_post(self, phases):
        data = {
            "type": "hours", "category": self.cities.pk, "title": "City", "tags": "",
            "priority": "0", "is_published": "on",
        }
        data.update(_inline("hours_phases", phases))
        return self.client.post(self.add_url + "?type=hours", data, follow=True)

    def test_hours_wallpaper_with_full_day(self):
        response = self.hours_post([
            {"position": "0", "name": "Day", "from_hour": "6", "to_hour": "18", "image": image_upload("day.png")},
            {"position": "1", "name": "Night", "from_hour": "18", "to_hour": "6", "image": image_upload("night.png")},
        ])
        self.assertEqual(response.status_code, 200)
        wallpaper = Wallpaper.objects.get(type="hours")
        self.assertEqual(HoursPhase.objects.filter(wallpaper=wallpaper).count(), 2)
        self.assertTrue(wallpaper.media_ready)
        self.assertTrue(wallpaper.thumbnail)

    def test_hours_with_a_gap_is_rejected(self):
        response = self.hours_post([
            {"position": "0", "name": "Day", "from_hour": "6", "to_hour": "18", "image": image_upload("day.png")},
        ])
        self.assertContains(response, "must cover all 24 hours")
        self.assertFalse(Wallpaper.objects.exists())

    def test_hours_with_overlap_is_rejected(self):
        response = self.hours_post([
            {"position": "0", "name": "Day", "from_hour": "0", "to_hour": "0", "image": image_upload("a.png")},
            {"position": "1", "name": "Night", "from_hour": "20", "to_hour": "5", "image": image_upload("b.png")},
        ])
        self.assertContains(response, "is in two phases")

    def parallax_post(self, layers):
        data = {"type": "parallax", "title": "Peaks", "tags": "", "priority": "0", "is_published": "on"}
        data.update(_inline("parallax_layers", layers))
        return self.client.post(self.add_url + "?type=parallax", data, follow=True)

    def test_parallax_wallpaper(self):
        response = self.parallax_post([
            {"position": "0", "role": "background", "depth": "0.2", "image": image_upload("bg.png")},
            {"position": "1", "role": "foreground", "depth": "1", "image": image_upload("fg.png", alpha=True)},
        ])
        self.assertEqual(response.status_code, 200)
        wallpaper = Wallpaper.objects.get(type="parallax")
        self.assertEqual(ParallaxLayer.objects.filter(wallpaper=wallpaper).count(), 2)
        self.assertTrue(wallpaper.media_ready)
        self.assertIsNone(wallpaper.category)

    def test_parallax_layers_must_match_in_size(self):
        response = self.parallax_post([
            {"position": "0", "role": "background", "depth": "0.2", "image": image_upload("bg.png")},
            {"position": "1", "role": "foreground", "depth": "1",
             "image": image_upload("fg.png", width=300, height=300, alpha=True)},
        ])
        self.assertContains(response, "same size")
        self.assertFalse(Wallpaper.objects.exists())

    def test_parallax_needs_two_layers(self):
        response = self.parallax_post([
            {"position": "0", "role": "background", "depth": "0.2", "image": image_upload("bg.png")},
        ])
        self.assertContains(response, "2 to 4 layers")

    # --- change form --------------------------------------------------------------------

    def change_post(self, wallpaper, **extra):
        data = {
            "category": wallpaper.category_id or "",
            "title": wallpaper.title,
            "tags": ", ".join(wallpaper.tags),
            "priority": str(wallpaper.priority),
            "is_published": "on",
            **extra,
        }
        url = reverse("admin:wallpapers_wallpaper_change", args=[wallpaper.pk])
        return self.client.post(url, data, follow=True)

    def test_manual_thumbnail_then_back_to_automatic(self):
        wallpaper = make_wallpaper("image", self.anime_image)
        response = self.change_post(wallpaper, thumbnail=image_upload("mine.png", width=900, height=900))
        self.assertEqual(response.status_code, 200)
        wallpaper.refresh_from_db()
        self.assertEqual(wallpaper.media_info["manual"], ["thumbnail"])
        self.assertEqual(wallpaper.media_info["thumbnail"]["width"], 400)

        self.change_post(wallpaper, **{"thumbnail-clear": "on"})
        wallpaper.refresh_from_db()
        self.assertEqual(wallpaper.media_info.get("manual"), [])
        self.assertTrue(wallpaper.thumbnail)

    def test_title_and_tags_edit(self):
        wallpaper = make_wallpaper("image", self.anime_image)
        self.change_post(wallpaper, title="Ronin at Dusk", tags="Samurai, red, samurai")
        wallpaper.refresh_from_db()
        self.assertEqual(wallpaper.title, "Ronin at Dusk")
        self.assertEqual(wallpaper.tags, ["samurai", "red"])
        self.assertIn("samurai", wallpaper.search_document)

    # --- actions -------------------------------------------------------------------------

    def test_unpublish_action_hides_and_invalidates(self):
        wallpaper = make_wallpaper("image", self.anime_image)
        before = ContentVersion.objects.get(pk=1).value
        self.client.post(self.list_url, {"action": "unpublish", "_selected_action": [wallpaper.pk]})
        wallpaper.refresh_from_db()
        self.assertFalse(wallpaper.is_published)
        self.assertGreater(ContentVersion.objects.get(pk=1).value, before)

    def test_rebuild_action(self):
        wallpaper = make_wallpaper("image", self.anime_image)
        Wallpaper.objects.filter(pk=wallpaper.pk).update(thumbnail=None, media_ready=False)
        response = self.client.post(
            self.list_url, {"action": "rebuild_media", "_selected_action": [wallpaper.pk]}, follow=True
        )
        self.assertContains(response, "Rebuilt 1 wallpaper(s).")
        wallpaper.refresh_from_db()
        self.assertTrue(wallpaper.media_ready)
