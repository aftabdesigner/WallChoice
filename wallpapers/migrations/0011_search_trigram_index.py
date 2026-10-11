"""Typo-tolerant search on PostgreSQL: pg_trgm plus a GIN trigram index.

Skipped on SQLite. If the database user may not create extensions, the
migration logs a warning instead of failing the deploy; search then falls back
to plain substring matching (catalog.trigram_ready() checks at runtime).
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SAVEPOINT wp_trgm")
        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        except Exception as exc:  # noqa: BLE001
            cursor.execute("ROLLBACK TO SAVEPOINT wp_trgm")
            logger.warning("pg_trgm unavailable, search will not tolerate typos: %s", exc)
            return
        cursor.execute("RELEASE SAVEPOINT wp_trgm")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS wp_search_trgm_idx "
            "ON wallpapers_wallpaper USING gin (search_document gin_trgm_ops)"
        )


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS wp_search_trgm_idx")


class Migration(migrations.Migration):
    dependencies = [("wallpapers", "0010_remove_wallpaper_media_type")]

    operations = [migrations.RunPython(forwards, backwards)]
