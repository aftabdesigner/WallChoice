"""``type`` replaces ``media_type`` (the data was carried over in 0009)."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("wallpapers", "0009_split_categories_by_type")]

    operations = [
        migrations.RemoveField(model_name="wallpaper", name="media_type"),
    ]
