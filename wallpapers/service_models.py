"""Phase 3 services: app config, usage events and the scores they feed.

* ``AppConfig`` is a single row (pk=1) holding everything ``GET /config``
  returns, with child rows for the lists (tabs, downloads, plans, hero
  wallpapers) so all of it is edited in the admin.
* ``DailyInteraction`` remembers what each install already did to an item
  today, which is what enforces "at most once per item per day".
* ``ItemDailyStat`` keeps the score points per item per day, so Trending can
  be recomputed exactly over the last 7 days without scanning raw events.
"""

import datetime as dt

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .models import Wallpaper, WallpaperType

# --- app config -------------------------------------------------------------------


class AdTrigger(models.TextChoices):
    SET_WALLPAPER = "set_wallpaper", "After setting a wallpaper"
    SET_RINGTONE = "set_ringtone", "After setting a ringtone"
    SET_CHARGING = "set_charging", "After setting a charging animation"
    DOWNLOAD = "download", "After a download"


class NativeAdSize(models.TextChoices):
    SMALL = "small", "Small"
    MEDIUM = "medium", "Medium"
    LARGE = "large", "Large"


class AppConfig(models.Model):
    """Everything the app would otherwise hard-code. Edit it in the admin."""

    # Versions
    min_version_code = models.PositiveIntegerField(
        default=1, help_text="Below this Android versionCode the app shows 'Update required'."
    )
    latest_version_code = models.PositiveIntegerField(
        default=1, help_text="Below this the app suggests updating."
    )
    # Feature switches
    feature_ringtones = models.BooleanField("Ringtones", default=True)
    feature_charging = models.BooleanField("Charging animations", default=True)
    feature_wallpaper_search = models.BooleanField("Wallpaper search", default=True)
    # Ads
    ads_enabled = models.BooleanField(default=True)
    interstitial_cooldown_sec = models.PositiveIntegerField(
        default=60, help_text="At most one full-screen ad per this many seconds."
    )
    interstitial_after = models.JSONField(
        default=list, blank=True, help_text="Actions after which a full-screen ad may show."
    )
    rewarded_download = models.BooleanField(default=True, help_text="Free users watch an ad to download.")
    rewarded_unlock = models.BooleanField(default=True, help_text="Free users watch an ad to unlock one Pro item.")
    native_size = models.CharField(max_length=6, choices=NativeAdSize.choices, default=NativeAdSize.MEDIUM)
    native_every_rows = models.PositiveSmallIntegerField(
        default=2, validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text="A native ad after every N rows of a list.",
    )
    # Paywall
    paywall_eyebrow = models.CharField("eyebrow", max_length=60, default="WALLCHOICE PRO", blank=True)
    paywall_title = models.CharField("title", max_length=80, default="Everything. No ads.")
    paywall_subtitle = models.CharField(
        "subtitle", max_length=160, blank=True,
        default="Every wallpaper, ringtone and charging animation.",
    )
    paywall_close_delay_sec = models.PositiveSmallIntegerField(
        "close button delay (seconds)", default=3, validators=[MaxValueValidator(30)]
    )
    # Onboarding
    welcome_title = models.CharField(max_length=80, default="Welcome to")
    welcome_subtitle = models.CharField(
        max_length=200, blank=True, default="Discover stunning wallpapers, live wallpapers and more."
    )
    onboarding_tips = models.TextField(
        blank=True, default="Tap a card to preview\nRingtones are here", help_text="One tip per line."
    )
    # Links
    terms_url = models.URLField(blank=True)
    privacy_url = models.URLField(blank=True)
    support_email = models.EmailField(blank=True)
    store_url = models.URLField(
        blank=True, default="https://play.google.com/store/apps/details?id=com.terafort.wallchoice"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "app config"
        verbose_name_plural = "app config"

    def __str__(self):
        return "App config"

    def save(self, *args, **kwargs):
        self.pk = 1  # there is only ever one
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def tips(self):
        return [line.strip() for line in self.onboarding_tips.splitlines() if line.strip()]


class WallpaperTab(models.Model):
    """One tab across the top of the Wallpapers screen."""

    config = models.ForeignKey(AppConfig, related_name="wallpaper_tabs", on_delete=models.CASCADE)
    key = models.CharField(max_length=10, choices=WallpaperType.choices)
    title = models.CharField(max_length=40)
    position = models.PositiveSmallIntegerField(default=0)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("position", "id")
        constraints = [models.UniqueConstraint(fields=["config", "key"], name="tab_unique_key")]

    def __str__(self):
        return self.title


class DownloadOption(models.Model):
    class Key(models.TextChoices):
        HD = "hd", "HD"
        UHD = "uhd", "4K"

    config = models.ForeignKey(AppConfig, related_name="download_options", on_delete=models.CASCADE)
    key = models.CharField(max_length=4, choices=Key.choices)
    label = models.CharField(max_length=20)
    pro_only = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")
        constraints = [models.UniqueConstraint(fields=["config", "key"], name="download_unique_key")]

    def __str__(self):
        return self.label


class PaywallPlan(models.Model):
    class Kind(models.TextChoices):
        SUBSCRIPTION = "subscription", "Subscription"
        ONE_TIME = "one_time", "One-time purchase"

    config = models.ForeignKey(AppConfig, related_name="paywall_plans", on_delete=models.CASCADE)
    product_id = models.CharField(
        max_length=100, help_text="Must match the product ID in Play Console exactly."
    )
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.SUBSCRIPTION)
    label = models.CharField(max_length=40)
    badge = models.CharField(max_length=40, blank=True, help_text="e.g. Save 84%")
    note = models.CharField(max_length=60, blank=True, help_text="e.g. 3 days free")
    is_default = models.BooleanField(default=False, help_text="Selected when the paywall opens.")
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")
        constraints = [models.UniqueConstraint(fields=["config", "product_id"], name="plan_unique_product")]

    def __str__(self):
        return f"{self.label} ({self.product_id})"


class PaywallHero(models.Model):
    """A wallpaper shown behind the paywall."""

    config = models.ForeignKey(AppConfig, related_name="paywall_heroes", on_delete=models.CASCADE)
    wallpaper = models.ForeignKey(Wallpaper, related_name="+", on_delete=models.CASCADE)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")
        constraints = [models.UniqueConstraint(fields=["config", "wallpaper"], name="hero_unique_wallpaper")]

    def __str__(self):
        return str(self.wallpaper)


# --- usage -----------------------------------------------------------------------------

# Likes are counted once per install per item for good: their dedupe rows use
# this fixed day and are never cleaned up.
FOREVER = dt.date(2000, 1, 1)


class DailyInteraction(models.Model):
    """What one install already did to one item on one day (for the caps)."""

    install = models.CharField(max_length=32)  # sha256 prefix of X-Install-Id
    feature = models.CharField(max_length=12)
    action = models.CharField(max_length=10)
    item_id = models.BigIntegerField()
    day = models.DateField()
    count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["install", "feature", "action", "item_id", "day"], name="interaction_unique"
            )
        ]
        indexes = [models.Index(fields=["day"], name="interaction_day_idx")]


class ItemDailyStat(models.Model):
    """Score points an item earned on a day; Trending sums the last 7 days."""

    feature = models.CharField(max_length=12)
    item_id = models.BigIntegerField()
    day = models.DateField()
    points = models.FloatField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["feature", "item_id", "day"], name="daily_stat_unique")
        ]
        indexes = [models.Index(fields=["day", "feature"], name="daily_stat_day_idx")]


class JobState(models.Model):
    """When a periodic job last ran; claimed atomically so it runs once."""

    name = models.CharField(max_length=40, unique=True)
    last_run = models.DateTimeField()

    def __str__(self):
        return self.name
