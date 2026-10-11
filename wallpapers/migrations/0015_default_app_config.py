"""The starting app config, with the values from the API spec.

Version codes start at 1 so no installed build is told to update; raise them
in the admin. The paywall product IDs must match Play Console - check them.
"""

from django.db import migrations

TABS = [
    ("live", "Live Wallpapers"),
    ("image", "Image Wallpapers"),
    ("hours", "24 Hours"),
    ("parallax", "Parallax Wallpapers"),
    ("fluid", "Fluid Wallpapers"),
]
PLANS = [
    ("wallchoice_pro_yearly", "subscription", "Yearly", "Save 84%", "3 days free", True),
    ("wallchoice_pro_weekly", "subscription", "Weekly", "", "Cancel anytime", False),
    ("wallchoice_pro_lifetime", "one_time", "Lifetime", "", "", False),
]


def forwards(apps, schema_editor):
    AppConfig = apps.get_model("wallpapers", "AppConfig")
    if AppConfig.objects.filter(pk=1).exists():
        return
    config = AppConfig.objects.create(pk=1, interstitial_after=["set_wallpaper", "set_ringtone"])
    WallpaperTab = apps.get_model("wallpapers", "WallpaperTab")
    WallpaperTab.objects.bulk_create(
        WallpaperTab(config=config, key=key, title=title, position=index)
        for index, (key, title) in enumerate(TABS)
    )
    DownloadOption = apps.get_model("wallpapers", "DownloadOption")
    DownloadOption.objects.bulk_create([
        DownloadOption(config=config, key="hd", label="HD", position=0),
        DownloadOption(config=config, key="uhd", label="4K", position=1),
    ])
    PaywallPlan = apps.get_model("wallpapers", "PaywallPlan")
    PaywallPlan.objects.bulk_create(
        PaywallPlan(config=config, product_id=product, kind=kind, label=label, badge=badge,
                    note=note, is_default=default, position=index)
        for index, (product, kind, label, badge, note, default) in enumerate(PLANS)
    )


class Migration(migrations.Migration):
    dependencies = [("wallpapers", "0014_phase3_config_events")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
