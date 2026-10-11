"""Phase 3: GET /config and POST /events (counters, caps, Trending/Popular)."""

import datetime as dt
import io
import json
import shutil

from django.contrib.auth.models import User
from django.core.management import call_command
from django.forms import inlineformset_factory
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from wallpapers import events
from wallpapers.forms import AppConfigForm, PaywallPlanFormSet
from wallpapers.models import (
    AppConfig,
    ContentVersion,
    DailyInteraction,
    ItemDailyStat,
    JobState,
    PaywallHero,
    PaywallPlan,
    Ringtone,
    Wallpaper,
)

from .helpers import (
    HOST,
    TEMP_MEDIA,
    feature_category,
    make_category,
    make_charging,
    make_ringtone,
    make_wallpaper,
    use_test_media,
)

CONFIG_KEYS = [
    "min_version_code", "latest_version_code", "features", "wallpaper_types", "ads",
    "downloads", "paywall", "onboarding", "links",
]


def iso(moment):
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ServiceTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def get(self, path):
        response = self.client.get(path, HTTP_HOST=HOST)
        return response, json.loads(response.content)


@use_test_media
class ConfigTests(ServiceTestCase):
    def test_default_config_matches_the_spec(self):
        response, body = self.get("/api/v1/config")
        self.assertEqual(response.status_code, 200)
        data = body["data"]
        self.assertEqual(list(data), CONFIG_KEYS)
        self.assertEqual((data["min_version_code"], data["latest_version_code"]), (1, 1))
        self.assertEqual(data["features"], {"ringtones": True, "charging": True, "wallpaper_search": True})
        self.assertEqual(
            data["wallpaper_types"][:2],
            [
                {"key": "live", "title": "Live Wallpapers", "has_categories": True},
                {"key": "image", "title": "Image Wallpapers", "has_categories": True},
            ],
        )
        self.assertEqual(data["wallpaper_types"][3], {"key": "parallax", "title": "Parallax Wallpapers", "has_categories": False})
        self.assertEqual(
            data["ads"],
            {
                "enabled": True, "interstitial_cooldown_sec": 60,
                "interstitial_after": ["set_wallpaper", "set_ringtone"],
                "rewarded_download": True, "rewarded_unlock": True,
                "native_size": "medium", "native_every_rows": 2,
            },
        )
        self.assertEqual(data["downloads"], [
            {"key": "hd", "label": "HD", "pro_only": False},
            {"key": "uhd", "label": "4K", "pro_only": False},
        ])
        paywall = data["paywall"]
        self.assertEqual(list(paywall), ["eyebrow", "title", "subtitle", "hero_wallpaper_ids", "close_delay_sec", "plans"])
        self.assertEqual(paywall["hero_wallpaper_ids"], [])
        self.assertEqual(paywall["plans"][0], {
            "product_id": "wallchoice_pro_yearly", "kind": "subscription", "label": "Yearly",
            "badge": "Save 84%", "note": "3 days free", "is_default": True,
        })
        self.assertIsNone(paywall["plans"][1]["badge"])
        self.assertEqual(paywall["plans"][2]["kind"], "one_time")
        self.assertEqual(data["onboarding"]["tips"], ["Tap a card to preview", "Ringtones are here"])
        self.assertIsNone(data["links"]["terms_url"])
        self.assertTrue(data["links"]["store_url"].endswith("com.terafort.wallchoice"))

    def test_admin_edits_show_up(self):
        config = AppConfig.load()
        config.paywall_title = "Go Pro"
        config.min_version_code, config.latest_version_code = 10, 12
        config.feature_charging = False
        config.save()
        config.wallpaper_tabs.filter(key="fluid").update(is_enabled=False)
        config.wallpaper_tabs.filter(key="hours").update(title="Day & Night")
        PaywallPlan.objects.filter(product_id="wallchoice_pro_weekly").update(is_active=False)
        anime = make_category("Anime", "image")
        shown = make_wallpaper("image", anime)
        hidden = make_wallpaper("image", anime, is_published=False)
        PaywallHero.objects.create(config=config, wallpaper=shown, position=1)
        PaywallHero.objects.create(config=config, wallpaper=hidden, position=0)

        _, body = self.get("/api/v1/config")
        data = body["data"]
        self.assertEqual(data["paywall"]["title"], "Go Pro")
        self.assertEqual((data["min_version_code"], data["latest_version_code"]), (10, 12))
        self.assertFalse(data["features"]["charging"])
        self.assertNotIn("fluid", [t["key"] for t in data["wallpaper_types"]])
        self.assertIn({"key": "hours", "title": "Day & Night", "has_categories": True}, data["wallpaper_types"])
        self.assertEqual([p["product_id"] for p in data["paywall"]["plans"]],
                         ["wallchoice_pro_yearly", "wallchoice_pro_lifetime"])
        self.assertEqual(data["paywall"]["hero_wallpaper_ids"], [shown.id])  # unpublished left out

    def test_config_change_invalidates_the_cache(self):
        before = ContentVersion.objects.get(pk=1).value
        with self.captureOnCommitCallbacks(execute=True):
            config = AppConfig.load()
            config.paywall_title = "New"
            config.save()
        self.assertGreater(ContentVersion.objects.get(pk=1).value, before)

    def test_admin(self):
        staff = User.objects.create_superuser("root", "root@example.com", "pw")
        self.client.force_login(staff)
        response = self.client.get(reverse("admin:wallpapers_appconfig_changelist"))
        self.assertRedirects(response, reverse("admin:wallpapers_appconfig_change", args=[1]))
        self.assertEqual(self.client.get(reverse("admin:wallpapers_appconfig_change", args=[1])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:wallpapers_appconfig_add")).status_code, 403)

    def test_form_rules(self):
        config = AppConfig.load()
        form = AppConfigForm(instance=config, data={
            "min_version_code": 12, "latest_version_code": 10, "interstitial_cooldown_sec": 60,
            "native_size": "medium", "native_every_rows": 2, "paywall_title": "x",
            "paywall_close_delay_sec": 3, "welcome_title": "Hi", "interstitial_after": ["download"],
        })
        self.assertFalse(form.is_valid())
        self.assertIn("latest_version_code", form.errors)

        PlanFormSet = inlineformset_factory(
            AppConfig, PaywallPlan, formset=PaywallPlanFormSet,
            fields=("product_id", "kind", "label", "is_default", "is_active", "position"), extra=0,
        )
        plans = list(config.paywall_plans.all())
        data = {
            "paywall_plans-TOTAL_FORMS": str(len(plans)), "paywall_plans-INITIAL_FORMS": str(len(plans)),
            "paywall_plans-MIN_NUM_FORMS": "0", "paywall_plans-MAX_NUM_FORMS": "1000",
        }
        for index, plan in enumerate(plans):
            data.update({
                f"paywall_plans-{index}-id": str(plan.id),
                f"paywall_plans-{index}-product_id": plan.product_id,
                f"paywall_plans-{index}-kind": plan.kind,
                f"paywall_plans-{index}-label": plan.label,
                f"paywall_plans-{index}-is_active": "on",
                f"paywall_plans-{index}-position": str(index),
                f"paywall_plans-{index}-is_default": "on",  # every plan default: invalid
            })
        formset = PlanFormSet(data, instance=config)
        self.assertFalse(formset.is_valid())
        self.assertIn("Exactly one active plan", str(formset.non_form_errors()))


@use_test_media
class EventTests(ServiceTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.live = make_category("Anime", "live")
        cls.a = make_wallpaper("live", cls.live, name="a.mp4")
        cls.b = make_wallpaper("live", cls.live, name="b.mp4")
        cls.draft = make_wallpaper("live", cls.live, name="draft.mp4", is_published=False)
        cls.calm = feature_category("Calm", "ringtone")
        cls.tone = make_ringtone(cls.calm)
        cls.neon = feature_category("Neon", "charging")
        cls.halo = make_charging(cls.neon)

    def setUp(self):
        self.now = timezone.now()

    def post(self, batch, install="install-0001", **extra):
        headers = {"HTTP_X_INSTALL_ID": install} if install else {}
        return self.client.post(
            "/api/v1/events", data=json.dumps({"events": batch}), content_type="application/json",
            HTTP_HOST=HOST, **headers, **extra,
        )

    def event(self, kind, item, at=None, **extra):
        return {"type": kind, "item_id": item.id, "at": iso(at or self.now), **extra}

    def test_counters_and_scores(self):
        response = self.post([
            self.event("wallpaper_download", self.a, quality="uhd"),
            self.event("wallpaper_set", self.a, target="both"),
            self.event("wallpaper_view", self.a),
            self.event("ringtone_play", self.tone),
            self.event("ringtone_set", self.tone, use="alarm"),
            self.event("charging_set", self.halo),
            self.event("like", self.a, feature="wallpaper"),
        ])
        self.assertEqual(response.status_code, 204)
        a = Wallpaper.objects.get(pk=self.a.pk)
        self.assertEqual((a.downloads_count, a.sets_count, a.views_count, a.likes_count), (1, 1, 1, 1))
        self.assertAlmostEqual(a.popular_score, 2 + 3 + 0.1 + 1)
        self.assertAlmostEqual(a.trending_score, a.popular_score)
        tone = Ringtone.objects.get(pk=self.tone.pk)
        self.assertEqual((tone.plays_count, tone.sets_count), (1, 1))
        self.halo.refresh_from_db()
        self.assertEqual(self.halo.sets_count, 1)
        stat = ItemDailyStat.objects.get(feature="wallpaper", item_id=self.a.id)
        self.assertAlmostEqual(stat.points, 6.1)

        _, body = self.get(f"/api/v1/wallpapers/{self.a.id}")
        self.assertEqual(body["data"]["stats"], {"downloads": 1, "sets": 1, "likes": 1})
        _, body = self.get(f"/api/v1/ringtones/{self.tone.id}")
        self.assertEqual(body["data"]["stats"], {"plays": 1, "sets": 1})

    def test_views_and_plays_count_once_per_install_per_day(self):
        yesterday = self.now - dt.timedelta(days=1)
        self.post([self.event("wallpaper_view", self.a)] * 3 + [self.event("ringtone_play", self.tone)] * 2)
        self.post([self.event("wallpaper_view", self.a)])  # same day, later batch
        self.post([self.event("wallpaper_view", self.a, at=yesterday)])  # another day
        self.post([self.event("wallpaper_view", self.a)], install="install-0002")  # another install
        a = Wallpaper.objects.get(pk=self.a.pk)
        self.assertEqual(a.views_count, 3)
        self.assertEqual(Ringtone.objects.get(pk=self.tone.pk).plays_count, 1)

    def test_downloads_and_sets_are_capped_per_day(self):
        self.post([self.event("wallpaper_download", self.a)] * 4)
        self.post([self.event("wallpaper_download", self.a)] * 4)
        self.assertEqual(Wallpaper.objects.get(pk=self.a.pk).downloads_count, events.REPEAT_CAP)

    def test_likes_count_once_ever(self):
        self.post([self.event("like", self.a, feature="wallpaper")])
        self.post([self.event("like", self.a, feature="wallpaper", at=self.now - dt.timedelta(days=1))])
        self.assertEqual(Wallpaper.objects.get(pk=self.a.pk).likes_count, 1)

    def test_bad_events_are_ignored(self):
        response = self.post([
            {"type": "something_new", "item_id": self.a.id, "at": iso(self.now)},
            {"type": "wallpaper_view", "item_id": "12", "at": iso(self.now)},
            {"type": "wallpaper_view", "item_id": True, "at": iso(self.now)},
            {"type": "wallpaper_view", "item_id": self.a.id, "at": "yesterday"},
            {"type": "wallpaper_view", "item_id": self.a.id},
            self.event("wallpaper_view", self.a, at=self.now - dt.timedelta(days=3)),
            self.event("wallpaper_view", self.a, at=self.now + dt.timedelta(hours=3)),
            self.event("wallpaper_view", self.draft),
            {"type": "wallpaper_view", "item_id": 999999, "at": iso(self.now)},
            {"type": "like", "feature": "nope", "item_id": self.a.id, "at": iso(self.now)},
            {"type": "ringtone_play", "item_id": self.a.id + 100000, "at": iso(self.now)},
            "not even an object",
        ])
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Wallpaper.objects.get(pk=self.a.pk).views_count, 0)
        self.assertEqual(Wallpaper.objects.get(pk=self.draft.pk).views_count, 0)
        self.assertFalse(DailyInteraction.objects.exists())

    def test_request_validation(self):
        for response in (
            self.post([self.event("wallpaper_view", self.a)], install=None),
            self.post([self.event("wallpaper_view", self.a)], install="short"),
            self.client.post("/api/v1/events", data=json.dumps({"events": "x"}),
                             content_type="application/json", HTTP_X_INSTALL_ID="install-0001"),
            self.post([self.event("wallpaper_view", self.a)] * 501),
        ):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "bad_request")

    def test_events_reorder_trending(self):
        trending = make_category("Trending", "live", kind="trending")
        path = f"/api/v1/wallpapers?type=live&category_id={trending.id}"
        _, body = self.get(path)
        self.assertEqual(body["data"][0]["id"], self.b.id)  # newest first while no usage
        self.post([self.event("wallpaper_set", self.a)])
        _, body = self.get(path)
        self.assertEqual(body["data"][0]["id"], self.a.id)

    def test_recalculation_keeps_only_the_last_seven_days(self):
        today = self.now.date()
        ItemDailyStat.objects.create(feature="wallpaper", item_id=self.a.id, day=today - dt.timedelta(days=8), points=50)
        ItemDailyStat.objects.create(feature="wallpaper", item_id=self.b.id, day=today - dt.timedelta(days=6), points=4)
        ItemDailyStat.objects.create(feature="wallpaper", item_id=self.b.id, day=today, points=1)
        ItemDailyStat.objects.create(feature="ringtone", item_id=self.tone.id, day=today, points=2)
        Wallpaper.objects.filter(pk=self.a.pk).update(trending_score=50, popular_score=50)
        DailyInteraction.objects.create(install="x", feature="wallpaper", action="view", item_id=1,
                                        day=today - dt.timedelta(days=10), count=1)
        DailyInteraction.objects.create(install="x", feature="wallpaper", action="like", item_id=1,
                                        day=events.FOREVER, count=1)

        changed = events.recalculate(self.now)
        a, b = Wallpaper.objects.get(pk=self.a.pk), Wallpaper.objects.get(pk=self.b.pk)
        self.assertEqual(a.trending_score, 0)  # its points fell out of the window
        self.assertEqual(a.popular_score, 50)  # all-time stays
        self.assertEqual(b.trending_score, 5)
        self.assertEqual(Ringtone.objects.get(pk=self.tone.pk).trending_score, 2)
        self.assertEqual(changed["wallpaper"], 2)
        # Old dedupe rows are pruned; like rows are kept forever.
        self.assertEqual(list(DailyInteraction.objects.values_list("action", flat=True)), ["like"])

    @override_settings(SCORES_RECALC_MINUTES=10)
    def test_recalculation_runs_once_per_interval(self):
        self.assertTrue(events.claim_recalculation(self.now))
        self.assertFalse(events.claim_recalculation(self.now + dt.timedelta(minutes=5)))
        self.assertTrue(events.claim_recalculation(self.now + dt.timedelta(minutes=11)))

    def test_events_trigger_recalculation(self):
        self.post([self.event("wallpaper_view", self.a)])
        self.assertTrue(JobState.objects.filter(name="recalculate_scores").exists())

    def test_command(self):
        out = io.StringIO()
        call_command("recalculate_scores", stdout=out)
        self.assertIn("Trending scores updated", out.getvalue())
