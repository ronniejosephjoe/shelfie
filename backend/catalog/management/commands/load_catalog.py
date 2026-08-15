from django.conf import settings
from django.core.management.base import BaseCommand

from catalog import catalog_store
from catalog.models import CatalogBook


class Command(BaseCommand):
    help = (
        "Load catalog.csv into the CatalogBook table. Upserts by "
        "catalog_id, so it's safe to re-run after editing the CSV. "
        "Rows whose catalog_id is no longer in the CSV are left in "
        "place (deliberately -- deleting rows out from under an "
        "existing library match would be a worse failure mode than a "
        "stale catalog row)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv-path",
            default=settings.CATALOG_CSV_PATH,
            help="Defaults to CATALOG_CSV_PATH / catalog.csv at the repo root.",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        entries = catalog_store.load_entries_from_csv(csv_path)

        created, updated = 0, 0
        for entry in entries:
            _, was_created = CatalogBook.objects.update_or_create(
                catalog_id=entry.catalog_id,
                defaults=dict(
                    title=entry.title,
                    alt_titles="|".join(entry.alt_titles),
                    author=entry.author,
                    author_alt="|".join(entry.author_alt),
                    year=entry.year,
                    format=entry.format,
                    series=entry.series,
                ),
            )
            created += was_created
            updated += not was_created

        catalog_store.clear_cache()
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {len(entries)} catalog rows from {csv_path} "
                f"({created} created, {updated} updated)."
            )
        )
