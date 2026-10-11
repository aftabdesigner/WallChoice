"""Give already-stored rows the title the model now derives on save.

New uploads get one in ``Wallpaper.save()``, but rows written before that
would keep serving ``title: null`` until something happened to re-save them.
"""

from django.db import migrations
from django.db.models import Q

from wallpapers.models import title_from_filename


def fill_titles(apps, schema_editor):
    Wallpaper = apps.get_model("wallpapers", "Wallpaper")
    untitled = Wallpaper.objects.filter(Q(title__isnull=True) | Q(title="")).exclude(
        Q(original_filename__isnull=True) | Q(original_filename="")
    )
    for wallpaper in untitled.iterator():
        # .update() rather than .save(): the historical model has no save()
        # override, and nothing else about these rows should change.
        Wallpaper.objects.filter(pk=wallpaper.pk).update(
            title=title_from_filename(wallpaper.original_filename)
        )


def unfill(apps, schema_editor):
    # Irreversible in substance - a title set by hand is indistinguishable
    # from a derived one - so reversing is a no-op rather than a guess.
    pass


class Migration(migrations.Migration):
    dependencies = [("wallpapers", "0005_alter_wallpaper_title")]

    operations = [migrations.RunPython(fill_titles, unfill)]
