"""Trending for ringtones and charging animations, first in the chip bar.

Hidden by the API until the feature has published items. Popular can be added
from the admin if wanted.
"""

from django.db import migrations
from django.utils.text import slugify


def forwards(apps, schema_editor):
    Category = apps.get_model("wallpapers", "Category")
    for feature in ("ringtone", "charging"):
        if Category.objects.filter(feature=feature, kind="trending").exists():
            continue
        if Category.objects.filter(feature=feature, name__iexact="Trending").exists():
            continue
        base = slugify(f"trending-{feature}")
        slug, counter = base, 2
        while Category.objects.filter(slug=slug).exists():
            slug = f"{base}-{counter}"
            counter += 1
        Category.objects.create(
            name="Trending",
            slug=slug,
            feature=feature,
            wallpaper_type=None,
            kind="trending",
            display="chip",
            position=0,
            is_active=True,
        )


class Migration(migrations.Migration):
    dependencies = [("wallpapers", "0012_phase2_ringtones_charging")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
