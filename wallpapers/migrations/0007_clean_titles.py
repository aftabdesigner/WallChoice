"""Clean stored titles before the column shrinks to 40 characters.

Titles made from file names carried upload leftovers ("LeniltonSL [9c0ae833]").
Every title is cleaned the way new uploads now are, and capped at 40
characters on a word boundary, so the following AlterField cannot fail on
PostgreSQL with "value too long".
"""

import os

from django.db import migrations

from wallpapers.titles import clean_title, shorten


def clean(apps, schema_editor):
    Wallpaper = apps.get_model("wallpapers", "Wallpaper")
    for wallpaper in Wallpaper.objects.only("pk", "title", "original_filename").iterator():
        stem = os.path.splitext(os.path.basename(wallpaper.original_filename or ""))[0]
        title = (wallpaper.title or "").strip()
        if not title or title == stem:
            new_title = clean_title(stem, from_filename=True)
        else:
            new_title = shorten(clean_title(title))
        if new_title != wallpaper.title:
            Wallpaper.objects.filter(pk=wallpaper.pk).update(title=new_title)


class Migration(migrations.Migration):
    dependencies = [("wallpapers", "0006_backfill_titles")]

    operations = [migrations.RunPython(clean, migrations.RunPython.noop)]
