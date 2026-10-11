"""Generate thumbnails, previews and HD/4K versions for stored wallpapers.

Run once after deploying Phase 1, so rows uploaded before it get the media the
new API needs (until then they are hidden from the new endpoints, though the
old app still sees them):

    python manage.py rebuild_media            # only rows still missing media
    python manage.py rebuild_media --all      # every row
    python manage.py rebuild_media --ids 1 2  # specific rows

Duplicate uploads (same file bytes) are found on the way and unpublished,
keeping the oldest copy.
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from wallpapers.models import Wallpaper


class Command(BaseCommand):
    help = "Build the thumbnail, preview, HD and 4K versions of stored wallpapers."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Rebuild every wallpaper.")
        parser.add_argument("--ids", nargs="+", type=int, help="Rebuild only these ids.")

    def handle(self, *args, **options):
        queryset = Wallpaper.objects.order_by("id")
        if options["ids"]:
            queryset = queryset.filter(pk__in=options["ids"])
        elif not options["all"]:
            queryset = queryset.filter(media_ready=False)

        total = queryset.count()
        self.stdout.write(f"Rebuilding {total} wallpaper(s)…")
        done = duplicates = failed = 0
        for wallpaper in queryset.iterator(chunk_size=50):
            try:
                duplicate = wallpaper.rebuild_media_from_storage()
            except (ValidationError, OSError) as exc:
                failed += 1
                message = "; ".join(getattr(exc, "messages", [str(exc)]))
                self.stderr.write(f"  #{wallpaper.pk} failed: {message}")
                continue
            done += 1
            if duplicate:
                duplicates += 1
                self.stdout.write(f"  #{wallpaper.pk} is a duplicate of #{duplicate} - unpublished")
            else:
                state = "ready" if wallpaper.media_ready else "still incomplete"
                self.stdout.write(f"  #{wallpaper.pk} {wallpaper.title}: {state}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {done} rebuilt, {duplicates} duplicate(s) unpublished, {failed} failed."
            )
        )
