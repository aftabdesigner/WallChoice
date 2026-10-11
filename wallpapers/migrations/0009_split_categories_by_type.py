"""Move existing content into the Phase 1 structure.

* Every wallpaper gets a ``type`` from its old ``media_type``: videos become
  Live, images (and gifs) become Image.
* Categories used to be shared by every kind of item. Each now belongs to one
  wallpaper type, so a category holding both videos and images keeps its id
  for the type it holds most of, and a copy (same name) is made for the
  other type, with those items moved to it.
* Trending and Popular are created for Live, Image and 24 Hours. They stay
  hidden until their type has published items.
* The cache version row is created.
"""

from collections import Counter

from django.db import migrations
from django.utils.text import slugify

AUTOMATIC = (("trending", "Trending", 0), ("popular", "Popular", 1))
CATEGORIZED = ("live", "image", "hours")


def _unique_slug(Category, base):
    base = (slugify(base) or "category")[:130]
    slug, counter = base, 2
    while Category.objects.filter(slug=slug).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def forwards(apps, schema_editor):
    Category = apps.get_model("wallpapers", "Category")
    Wallpaper = apps.get_model("wallpapers", "Wallpaper")
    ContentVersion = apps.get_model("wallpapers", "ContentVersion")

    Wallpaper.objects.filter(media_type="video").update(type="live")
    Wallpaper.objects.exclude(media_type="video").update(type="image")

    for category in Category.objects.filter(wallpaper_type__isnull=True).order_by("id"):
        types = Counter(
            Wallpaper.objects.filter(category=category).values_list("type", flat=True)
        )
        primary = types.most_common(1)[0][0] if types else "image"
        category.feature = "wallpaper"
        category.wallpaper_type = primary
        category.kind = "standard"
        category.display = "chip"
        category.save(update_fields=["feature", "wallpaper_type", "kind", "display"])

        for other_type in [t for t in types if t != primary]:
            clone = Category.objects.create(
                name=category.name,
                slug=_unique_slug(Category, f"{category.slug}-{other_type}"),
                description=category.description,
                feature="wallpaper",
                wallpaper_type=other_type,
                kind="standard",
                display="chip",
                position=category.position,
                is_active=category.is_active,
            )
            Wallpaper.objects.filter(category=category, type=other_type).update(category=clone)

    for wallpaper_type in CATEGORIZED:
        for kind, name, position in AUTOMATIC:
            if Category.objects.filter(
                feature="wallpaper", wallpaper_type=wallpaper_type, kind=kind
            ).exists():
                continue
            if Category.objects.filter(
                feature="wallpaper", wallpaper_type=wallpaper_type, name=name
            ).exists():
                # A hand-made category already uses the name; leave it alone.
                continue
            Category.objects.create(
                name=name,
                slug=_unique_slug(Category, f"{name}-{wallpaper_type}"),
                feature="wallpaper",
                wallpaper_type=wallpaper_type,
                kind=kind,
                display="row",
                position=position,
                is_active=True,
            )

    for wallpaper in Wallpaper.objects.select_related("category").iterator():
        category_name = wallpaper.category.name if wallpaper.category_id else ""
        document = " ".join(p for p in [wallpaper.title, category_name] if p).lower()
        Wallpaper.objects.filter(pk=wallpaper.pk).update(search_document=document)

    ContentVersion.objects.get_or_create(pk=1, defaults={"value": 1})


class Migration(migrations.Migration):
    dependencies = [("wallpapers", "0008_phase1_schema")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
