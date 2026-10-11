"""Media processing: titles, image renditions, video handling, duplicates."""

import io
import shutil
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from PIL import Image

from wallpapers import media
from wallpapers.models import Wallpaper
from wallpapers.titles import clean_title, title_from_filename

from .helpers import (
    TEMP_MEDIA,
    image_bytes,
    image_upload,
    make_category,
    make_wallpaper,
    use_test_media,
    video_bytes,
)


class TitleTests(SimpleTestCase):
    def test_upload_leftovers_are_removed(self):
        cases = {
            "Ronin at dusk LeniltonSL [9c0ae833].mp4": "Ronin at dusk LeniltonSL",
            "beach sunset.png": "Beach Sunset",
            "girl_sunset_4k.png": "Girl Sunset",
            "my-car (1).jpg": "My Car",
            "neon_city_1080x2400_final.webp": "Neon City",
            "a1b2c3d4e5f6.png": "Wallpaper",
            "": "Wallpaper",
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(title_from_filename(filename), expected)

    def test_titles_fit_forty_characters_on_a_word_boundary(self):
        title = title_from_filename(
            "an extremely long wallpaper file name that keeps on going forever.png"
        )
        self.assertLessEqual(len(title), 40)
        self.assertFalse(title.endswith(" "))
        self.assertEqual(title, "An Extremely Long Wallpaper File Name")

    def test_hand_written_titles_keep_their_case(self):
        self.assertEqual(clean_title("Ronin at Dusk"), "Ronin at Dusk")


class ImageRenditionTests(SimpleTestCase):
    def open(self, encoded):
        return Image.open(BytesIO(encoded.content))

    def test_every_rendition_meets_its_spec(self):
        source = media.open_image(BytesIO(image_bytes(2400, 5000)))
        result = media.build_wallpaper_images(source)
        renditions = result["renditions"]

        thumbnail = renditions["thumbnail"]
        self.assertEqual(thumbnail.width, 400)
        self.assertAlmostEqual(thumbnail.height / thumbnail.width, 16 / 9, places=1)
        self.assertLessEqual(len(thumbnail.content), media.THUMBNAIL_MAX_BYTES)
        self.assertEqual(self.open(thumbnail).format, "WEBP")

        self.assertEqual(renditions["preview"].width, 1080)
        self.assertLessEqual(len(renditions["preview"].content), media.PREVIEW_MAX_BYTES)
        self.assertEqual(renditions["hd"].width, 1080)
        self.assertLessEqual(len(renditions["hd"].content), media.HD_MAX_BYTES)
        self.assertEqual(renditions["uhd"].width, 2160)
        self.assertLessEqual(len(renditions["uhd"].content), media.UHD_MAX_BYTES)
        self.assertRegex(result["dominant_color"], r"^#[0-9A-F]{6}$")

    def test_no_4k_from_a_small_source_and_no_upscaling(self):
        source = media.open_image(BytesIO(image_bytes(720, 1600)))
        renditions = media.build_wallpaper_images(source)["renditions"]
        self.assertIsNone(renditions["uhd"])
        self.assertEqual(renditions["hd"].width, 720)

    def test_noise_still_fits_the_byte_budget(self):
        import random

        random.seed(3)
        noisy = Image.frombytes("RGB", (1200, 2400), random.randbytes(1200 * 2400 * 3))
        renditions = media.build_wallpaper_images(noisy)["renditions"]
        self.assertLessEqual(len(renditions["thumbnail"].content), media.THUMBNAIL_MAX_BYTES)
        self.assertLessEqual(len(renditions["preview"].content), media.PREVIEW_MAX_BYTES)

    def test_dominant_colour_is_the_main_colour(self):
        image = Image.new("RGB", (100, 100), (200, 20, 20))
        self.assertEqual(media.dominant_color(image)[:3], "#C8")

    def test_unreadable_image_is_a_validation_error(self):
        with self.assertRaises(ValidationError):
            media.open_image(BytesIO(b"not an image"))


class VideoTests(SimpleTestCase):
    def test_probe_and_optimise(self):
        data = video_bytes(0)
        with media.local_path(io.BytesIO(data), suffix=".mp4") as path:
            info = media.probe_video(path)
            self.assertEqual((info.width, info.height), (216, 384))
            self.assertAlmostEqual(info.duration_ms, 1000, delta=150)
            optimised = media.optimise_video(path)
            frame = media.first_frame(path)
        self.assertEqual(frame.size, (216, 384))
        # faststart: the index (moov) now comes before the media data (mdat).
        self.assertLess(optimised.find(b"moov"), optimised.find(b"mdat"))
        with media.local_path(io.BytesIO(optimised), suffix=".mp4") as path:
            text = media._run_ffmpeg(["-i", path]).stderr.decode()
        self.assertIn("Video:", text)
        self.assertNotIn("Audio:", text)

    def test_garbage_is_rejected(self):
        with media.local_path(io.BytesIO(b"\x00\x00\x00\x18ftypmp42"), suffix=".mp4") as path:
            with self.assertRaises(ValidationError):
                media.probe_video(path)


@use_test_media
class WallpaperMediaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anime_live = make_category("Anime", "live")
        cls.anime_image = make_category("Anime", "image")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def test_image_upload_gets_every_rendition(self):
        wallpaper = make_wallpaper("image", self.anime_image, name="girl_sunset_4k.png")
        self.assertTrue(wallpaper.media_ready)
        self.assertEqual(wallpaper.title, "Girl Sunset")
        self.assertEqual(wallpaper.original_filename, "girl_sunset_4k.png")
        for field in ("thumbnail", "preview", "hd"):
            self.assertTrue(getattr(wallpaper, field), field)
            self.assertIn(field, wallpaper.media_info)
        self.assertEqual(len(wallpaper.content_hash), 64)
        self.assertRegex(wallpaper.file.name, r"^wallpaper/image/anime-image/assets/\d{4}/\d{2}/[0-9a-f]{32}\.png$")

    def test_live_upload_gets_a_poster_thumbnail_and_clean_video(self):
        wallpaper = make_wallpaper("live", self.anime_live)
        self.assertTrue(wallpaper.media_ready)
        self.assertTrue(wallpaper.thumbnail)
        self.assertTrue(wallpaper.preview)
        self.assertFalse(wallpaper.hd)
        video = wallpaper.media_info["video"]
        self.assertEqual((video["width"], video["height"]), (216, 384))
        with wallpaper.file.open("rb") as handle:
            stored = handle.read()
        self.assertLess(stored.find(b"moov"), stored.find(b"mdat"))
        self.assertEqual(video["size_bytes"], len(stored))

    def test_duplicate_upload_is_rejected(self):
        data = image_bytes()
        Wallpaper(type="image", category=self.anime_image,
                  file=SimpleUploadedFile("a.png", data)).save()
        duplicate = Wallpaper(type="image", category=self.anime_image,
                              file=SimpleUploadedFile("b.png", data))
        with self.assertRaises(ValidationError) as caught:
            duplicate.full_clean()
        self.assertIn("already uploaded", str(caught.exception))

    def test_type_and_category_rules(self):
        cases = [
            (dict(type="image", category=None), "category"),
            (dict(type="image", category=self.anime_live), "category"),
            (dict(type="parallax", category=self.anime_image), "category"),
        ]
        for values, field in cases:
            with self.subTest(values=values):
                wallpaper = Wallpaper(file=image_upload(), **values)
                with self.assertRaises(ValidationError) as caught:
                    wallpaper.full_clean()
                self.assertIn(field, caught.exception.message_dict)

    def test_live_needs_a_video_and_image_needs_an_image(self):
        live = Wallpaper(type="live", category=self.anime_live, file=image_upload())
        with self.assertRaises(ValidationError):
            live.full_clean()
        image = Wallpaper(type="image", category=self.anime_image,
                          file=SimpleUploadedFile("x.mp4", video_bytes(1)))
        with self.assertRaises(ValidationError):
            image.full_clean()

    def test_manual_thumbnail_survives_a_rebuild(self):
        wallpaper = make_wallpaper("image", self.anime_image)
        wallpaper.thumbnail = image_upload("hand.png", width=800, height=800)
        wallpaper.apply_manual_rendition("thumbnail")
        wallpaper.save()
        manual_name = wallpaper.thumbnail.name
        wallpaper.rebuild_media_from_storage()
        wallpaper.refresh_from_db()
        self.assertEqual(wallpaper.thumbnail.name, manual_name)
        self.assertEqual(wallpaper.media_info["manual"], ["thumbnail"])

    def test_rebuild_command_backfills_old_rows_and_unpublishes_duplicates(self):
        first = make_wallpaper("image", self.anime_image)
        second = make_wallpaper("image", self.anime_image)
        # Make the second an old-style row: same bytes as the first, no media.
        with first.file.open("rb") as handle:
            data = handle.read()
        with open(second.file.path, "wb") as handle:
            handle.write(data)
        Wallpaper.objects.filter(pk__in=[first.pk, second.pk]).update(
            thumbnail=None, preview=None, hd=None, media_info={}, media_ready=False, content_hash=None
        )
        out = io.StringIO()
        call_command("rebuild_media", stdout=out, stderr=io.StringIO())
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.media_ready)
        self.assertTrue(first.is_published)
        self.assertFalse(second.is_published)
        self.assertIn(f"#{second.pk} is a duplicate of #{first.pk}", out.getvalue())
