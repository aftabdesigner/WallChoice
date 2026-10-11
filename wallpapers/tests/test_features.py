"""Phase 2: ringtones and charging animations - media, API and admin."""

import io
import json
import re
import shutil

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from wallpapers import media
from wallpapers.models import Category, CategoryPin, ChargingAnimation, Ringtone

from .helpers import (
    HOST,
    TEMP_MEDIA,
    audio_bytes,
    audio_upload,
    feature_category,
    image_upload,
    lottie_bytes,
    lottie_upload,
    make_category,
    make_charging,
    make_ringtone,
    use_test_media,
    video_bytes,
    video_upload,
)

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
RINGTONE_KEYS = [
    "id", "title", "artist", "category", "is_premium", "artwork", "audio", "waveform", "stats",
    "created_at",
]
CHARGING_KEYS = [
    "id", "title", "category", "is_premium", "preview", "animation", "battery_label",
    "background_color", "sound",
]
AUDIO_KEYS = ["url", "format", "duration_ms", "size_bytes"]
IMAGE_KEYS = ["url", "width", "height", "size_bytes"]


class AudioAndLottieTests(SimpleTestCase):
    def test_long_audio_is_trimmed_converted_and_fits(self):
        with media.local_path(io.BytesIO(audio_bytes(50, 440, "wav")), suffix=".wav") as path:
            data, info = media.process_audio(path)
        self.assertEqual(info.format, "mp3")
        self.assertAlmostEqual(info.duration_ms, 40000, delta=300)
        self.assertLessEqual(len(data), media.AUDIO_MAX_BYTES)
        self.assertEqual(info.size_bytes, len(data))

    def test_too_short_and_garbage_audio_are_rejected(self):
        with media.local_path(io.BytesIO(audio_bytes(1, 500)), suffix=".mp3") as path:
            with self.assertRaises(ValidationError):
                media.process_audio(path)
        with media.local_path(io.BytesIO(b"not audio at all"), suffix=".mp3") as path:
            with self.assertRaises(ValidationError):
                media.process_audio(path)

    def test_waveform_has_44_values_from_0_to_100(self):
        with media.local_path(io.BytesIO(audio_bytes(5, 600)), suffix=".mp3") as path:
            bars = media.waveform(path)
        self.assertEqual(len(bars), 44)
        self.assertTrue(all(isinstance(v, int) and 0 <= v <= 100 for v in bars))
        self.assertEqual(max(bars), 100)

    def test_lottie_is_validated_and_minified(self):
        raw = lottie_bytes(frames=90)
        data, info = media.process_lottie(raw)
        self.assertLess(len(data), len(raw))
        self.assertEqual(info, {"width": 360, "height": 640, "duration_ms": 3000, "size_bytes": len(data)})

    def test_bad_lottie_is_rejected(self):
        for raw in (b"{not json", b'{"hello": 1}', lottie_bytes(extra_layers=6000)):
            with self.subTest(size=len(raw)):
                with self.assertRaises(ValidationError):
                    media.process_lottie(raw)


class FeatureTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def get(self, path):
        response = self.client.get(path, HTTP_HOST=HOST)
        return response, (json.loads(response.content) if response.content else None)

    def assertError(self, path, status, code):
        response, body = self.get(path)
        self.assertEqual(response.status_code, status, body)
        self.assertEqual(body["error"]["code"], code)
        return body


@use_test_media
class RingtoneTests(FeatureTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.trending = feature_category("Trending", "ringtone", kind="trending")
        cls.calm = feature_category("Calm", "ringtone", position=1)
        cls.electronic = feature_category("Electronic", "ringtone", position=2)
        cls.empty = feature_category("Empty", "ringtone", position=3)
        cls.a = make_ringtone(cls.calm, "morning_glass.mp3", artist="Aria")
        cls.b = make_ringtone(cls.calm, "soft rain.mp3")
        cls.c = make_ringtone(cls.electronic, "pulse.mp3", is_premium=True)
        cls.draft = make_ringtone(cls.electronic, "draft.mp3", is_published=False)
        Ringtone.objects.filter(pk=cls.c.pk).update(trending_score=90)
        Ringtone.objects.filter(pk=cls.a.pk).update(trending_score=10)

    def test_upload_is_processed(self):
        ringtone = Ringtone.objects.get(pk=self.a.pk)
        self.assertTrue(ringtone.media_ready)
        self.assertEqual(ringtone.title, "Morning Glass")
        self.assertTrue(ringtone.audio.name.endswith(".mp3"))
        self.assertEqual(ringtone.audio_info["format"], "mp3")
        self.assertEqual(len(ringtone.waveform), 44)
        self.assertEqual(ringtone.artwork_info["large"]["width"], 800)
        self.assertEqual(ringtone.artwork_info["large"]["height"], 800)
        self.assertEqual(ringtone.artwork_info["thumbnail"]["width"], 200)
        self.assertLessEqual(ringtone.artwork_info["large"]["size_bytes"], media.ARTWORK_MAX_BYTES)
        self.assertRegex(ringtone.audio.name, r"^ringtones/calm-ringtone/audio/\d{4}/\d{2}/[0-9a-f]{32}\.mp3$")

    def test_duplicate_and_category_rules(self):
        data = audio_bytes(4, 1234)
        Ringtone(category=self.calm, audio=SimpleUploadedFile("x.mp3", data),
                 artwork=image_upload("a.png")).save()
        duplicate = Ringtone(category=self.calm, audio=SimpleUploadedFile("y.mp3", data),
                             artwork=image_upload("b.png"))
        with self.assertRaises(ValidationError) as caught:
            duplicate.full_clean()
        self.assertIn("already uploaded", str(caught.exception))

        wallpaper_category = make_category("Anime", "image")
        for category in (wallpaper_category, self.trending):
            wrong = Ringtone(category=category, audio=audio_upload(), artwork=image_upload())
            with self.assertRaises(ValidationError) as caught:
                wrong.full_clean()
            self.assertIn("category", caught.exception.message_dict)

    def test_categories(self):
        response, body = self.get("/api/v1/ringtones/categories")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c["name"] for c in body["data"]], ["Trending", "Calm", "Electronic"])
        first = body["data"][0]
        self.assertEqual(
            (first["feature"], first["wallpaper_type"], first["kind"], first["display"]),
            ("ringtone", None, "trending", "chip"),
        )
        self.assertEqual({c["name"]: c["item_count"] for c in body["data"]},
                         {"Trending": 3, "Calm": 2, "Electronic": 1})

    def test_ringtone_object_shape(self):
        _, body = self.get(f"/api/v1/ringtones?category_id={self.calm.id}")
        row = next(r for r in body["data"] if r["id"] == self.a.id)
        self.assertEqual(list(row), RINGTONE_KEYS)
        self.assertEqual(row["artist"], "Aria")
        self.assertEqual(row["category"], {"id": self.calm.id, "name": "Calm"})
        self.assertEqual(list(row["artwork"]), ["thumbnail", "large"])
        self.assertEqual(list(row["artwork"]["large"]), IMAGE_KEYS)
        self.assertEqual(list(row["audio"]), AUDIO_KEYS)
        self.assertEqual(row["audio"]["format"], "mp3")
        self.assertAlmostEqual(row["audio"]["duration_ms"], 5000, delta=300)
        self.assertTrue(row["audio"]["url"].startswith(f"http://{HOST}/media/"))
        self.assertEqual(len(row["waveform"]), 44)
        self.assertEqual(row["stats"], {"plays": 0, "sets": 0})
        self.assertRegex(row["created_at"], ISO_Z)
        other = next(r for r in body["data"] if r["id"] == self.b.id)
        self.assertIsNone(other["artist"])

    def test_lists(self):
        _, body = self.get(f"/api/v1/ringtones?category_id={self.calm.id}")
        self.assertEqual([r["id"] for r in body["data"]], [self.b.id, self.a.id])  # newest first
        _, body = self.get(f"/api/v1/ringtones?category_id={self.trending.id}")
        self.assertEqual([r["id"] for r in body["data"]][:2], [self.c.id, self.a.id])
        CategoryPin.objects.create(category=self.trending, ringtone=self.b, position=0)
        _, body = self.get(f"/api/v1/ringtones?category_id={self.trending.id}")
        self.assertEqual([r["id"] for r in body["data"]], [self.b.id, self.c.id, self.a.id])
        _, body = self.get("/api/v1/ringtones?category_id=99999")
        self.assertEqual(body["data"], [])

    def test_list_needs_a_category(self):
        self.assertError("/api/v1/ringtones", 400, "bad_request")
        self.assertError("/api/v1/ringtones?category_id=x", 400, "bad_request")

    def test_featured(self):
        _, body = self.get(f"/api/v1/ringtones/featured?category_id={self.calm.id}")
        self.assertEqual(list(body), ["data"])
        self.assertEqual(body["data"]["id"], self.b.id)
        self.calm.featured_ringtone = self.a
        self.calm.full_clean()
        self.calm.save()
        _, body = self.get(f"/api/v1/ringtones/featured?category_id={self.calm.id}")
        self.assertEqual(body["data"]["id"], self.a.id)
        _, body = self.get(f"/api/v1/ringtones/featured?category_id={self.trending.id}")
        self.assertEqual(body["data"]["id"], self.c.id)
        _, body = self.get(f"/api/v1/ringtones/featured?category_id={self.empty.id}")
        self.assertIsNone(body["data"])
        self.assertError("/api/v1/ringtones/featured", 400, "bad_request")

    def test_featured_must_come_from_the_category(self):
        self.calm.featured_ringtone = self.c
        with self.assertRaises(ValidationError):
            self.calm.full_clean()

    def test_detail(self):
        response, body = self.get(f"/api/v1/ringtones/{self.c.id}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["data"]["is_premium"])
        error = self.assertError("/api/v1/ringtones/99999", 404, "not_found")
        self.assertEqual(error["error"]["message"], "Ringtone 99999 does not exist.")
        self.assertError(f"/api/v1/ringtones/{self.draft.id}", 404, "not_found")

    def test_category_names_are_unique_per_feature(self):
        duplicate = Category(name="calm", feature="ringtone")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        # The same name is fine in another feature.
        Category(name="Calm", feature="charging").full_clean()

    def test_query_count_does_not_grow(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.get(f"/api/v1/ringtones?category_id={self.calm.id}&page_size=100")
                self.get(f"/api/v1/ringtones?category_id={self.trending.id}&page_size=100")
            return len(ctx.captured_queries)

        before = count()
        rows = []
        for n in range(30):
            row = Ringtone.objects.get(pk=self.a.pk)
            row.pk, row.content_hash, row.title = None, None, f"Copy {n}"
            rows.append(row)
        Ringtone.objects.bulk_create(rows)
        self.assertEqual(count(), before)


@use_test_media
class ChargingTests(FeatureTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.neon = feature_category("Neon", "charging", position=1)
        cls.halo = make_charging(cls.neon, title="Halo", battery_label_color="#ff00aa")
        cls.tide = make_charging(
            cls.neon, animation=video_upload("tide.mp4", variant=21), title="Tide",
            sound=audio_upload("plug.mp3", seconds=3),
        )

    def test_lottie_item(self):
        self.assertTrue(self.halo.media_ready)
        self.assertEqual(self.halo.animation_info["format"], "lottie")
        self.assertTrue(self.halo.animation.name.endswith(".json"))
        self.assertEqual(self.halo.battery_label_color, "#FF00AA")
        self.assertTrue(self.halo.thumbnail_info["manual"])

    def test_mp4_item_gets_a_thumbnail_and_sound(self):
        self.assertTrue(self.tide.media_ready)
        self.assertEqual(self.tide.animation_info["format"], "mp4")
        self.assertFalse(self.tide.thumbnail_info["manual"])
        self.assertEqual(self.tide.sound_info["format"], "mp3")
        with self.tide.animation.open("rb") as handle:
            data = handle.read()
        self.assertLess(data.find(b"moov"), data.find(b"mdat"))

    def test_lottie_needs_a_thumbnail_or_preview(self):
        item = ChargingAnimation(category=self.neon, animation=lottie_upload(label="bare"))
        with self.assertRaises(ValidationError) as caught:
            item.full_clean()
        self.assertIn("thumbnail", caught.exception.message_dict)
        with_preview = make_charging(
            self.neon, animation=lottie_upload(label="previewed"),
            preview_video=video_upload("p.mp4", variant=22),
        )
        self.assertTrue(with_preview.media_ready)
        self.assertFalse(with_preview.thumbnail_info["manual"])

    def test_api(self):
        _, categories = self.get("/api/v1/charging-animations/categories")
        self.assertEqual([c["name"] for c in categories["data"]], ["Trending", "Neon"])
        self.assertEqual(categories["data"][0]["feature"], "charging")

        _, body = self.get(f"/api/v1/charging-animations?category_id={self.neon.id}")
        rows = {r["id"]: r for r in body["data"]}
        halo, tide = rows[self.halo.id], rows[self.tide.id]
        self.assertEqual(list(halo), CHARGING_KEYS)
        self.assertEqual(list(halo["preview"]), ["thumbnail", "video"])
        self.assertIsNone(halo["preview"]["video"])
        self.assertEqual(list(halo["animation"]), ["format", "url", "size_bytes", "loop"])
        self.assertEqual(halo["animation"]["format"], "lottie")
        self.assertTrue(halo["animation"]["loop"])
        self.assertEqual(halo["battery_label"], {"visible": True, "position": "center", "color": "#FF00AA"})
        self.assertEqual(halo["background_color"], "#0C0C0D")
        self.assertIsNone(halo["sound"])

        self.assertEqual(tide["animation"]["format"], "mp4")
        self.assertEqual(tide["preview"]["video"]["url"], tide["animation"]["url"])
        self.assertEqual(list(tide["sound"]), AUDIO_KEYS)

    def test_detail_and_errors(self):
        response, body = self.get(f"/api/v1/charging-animations/{self.halo.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["data"]["title"], "Halo")
        error = self.assertError("/api/v1/charging-animations/99999", 404, "not_found")
        self.assertEqual(error["error"]["message"], "Charging animation 99999 does not exist.")
        self.assertError("/api/v1/charging-animations", 400, "bad_request")


@use_test_media
class FeatureAdminTests(FeatureTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser("root", "root@example.com", "pw")
        cls.calm = feature_category("Calm", "ringtone")
        cls.neon = feature_category("Neon", "charging")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_pages_render(self):
        ringtone = make_ringtone(self.calm)
        charging = make_charging(self.neon)
        trending = feature_category("Trending", "ringtone", kind="trending")
        for url in (
            reverse("admin:wallpapers_ringtone_changelist"),
            reverse("admin:wallpapers_ringtone_add"),
            reverse("admin:wallpapers_ringtone_change", args=[ringtone.pk]),
            reverse("admin:wallpapers_charginganimation_changelist"),
            reverse("admin:wallpapers_charginganimation_add"),
            reverse("admin:wallpapers_charginganimation_change", args=[charging.pk]),
            reverse("admin:wallpapers_category_change", args=[self.calm.pk]),
            reverse("admin:wallpapers_category_change", args=[trending.pk]),
            reverse("admin:wallpapers_category_changelist"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_bulk_ringtones(self):
        response = self.client.post(
            reverse("admin:wallpapers_ringtone_add"),
            {
                "category": self.calm.pk,
                "files": [audio_upload("ocean_waves.mp3"), audio_upload("night-bells.wav")],
                "artwork": image_upload("art.png", width=800, height=800),
                "artist": "Studio",
            },
            follow=True,
        )
        self.assertContains(response, "2 ringtones added to Calm")
        rows = list(Ringtone.objects.order_by("id"))
        self.assertEqual([r.title for r in rows], ["Ocean Waves", "Night Bells"])
        self.assertTrue(all(r.media_ready and r.artist == "Studio" for r in rows))
        self.assertEqual(rows[1].audio_info["format"], "mp3")

    def test_bulk_ringtones_reject_wrong_category_and_duplicates(self):
        url = reverse("admin:wallpapers_ringtone_add")
        response = self.client.post(url, {"category": self.neon.pk, "files": [audio_upload()],
                                          "artwork": image_upload()}, follow=True)
        self.assertContains(response, "Select a valid choice")
        data = audio_bytes(4, 2222)
        response = self.client.post(
            url,
            {"category": self.calm.pk,
             "files": [SimpleUploadedFile("a.mp3", data), SimpleUploadedFile("b.mp3", data)],
             "artwork": image_upload()},
            follow=True,
        )
        self.assertContains(response, "same file as a.mp3")
        self.assertFalse(Ringtone.objects.exists())

    def test_charging_add(self):
        url = reverse("admin:wallpapers_charginganimation_add")
        base = {
            "category": self.neon.pk, "title": "Orbit", "loop": "on", "battery_label_visible": "on",
            "battery_label_position": "bottom", "battery_label_color": "#ffffff",
            "background_color": "#000000", "priority": "0", "is_published": "on",
        }
        response = self.client.post(url, {**base, "animation": lottie_upload(label="orbit")}, follow=True)
        self.assertContains(response, "needs a thumbnail image or a preview video")
        self.assertFalse(ChargingAnimation.objects.exists())

        response = self.client.post(
            url, {**base, "animation": lottie_upload(label="orbit"), "thumbnail": image_upload("o.png")},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        item = ChargingAnimation.objects.get()
        self.assertTrue(item.media_ready)
        self.assertEqual(item.battery_label_position, "bottom")

    def test_unpublish_action(self):
        ringtone = make_ringtone(self.calm)
        self.client.post(
            reverse("admin:wallpapers_ringtone_changelist"),
            {"action": "unpublish", "_selected_action": [ringtone.pk]},
        )
        ringtone.refresh_from_db()
        self.assertFalse(ringtone.is_published)
