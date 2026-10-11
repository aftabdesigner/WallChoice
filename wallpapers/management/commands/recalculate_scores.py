"""Rebuild Trending scores from the last 7 days of usage.

This already runs on its own every SCORES_RECALC_MINUTES while events arrive.
Schedule it as a cron job too if traffic can stop for long periods:

    python manage.py recalculate_scores
"""

from django.core.management.base import BaseCommand

from wallpapers.events import recalculate


class Command(BaseCommand):
    help = "Recalculate Trending scores and prune old usage bookkeeping."

    def handle(self, *args, **options):
        changed = recalculate()
        summary = ", ".join(f"{feature}: {count}" for feature, count in changed.items())
        self.stdout.write(self.style.SUCCESS(f"Trending scores updated ({summary})."))
