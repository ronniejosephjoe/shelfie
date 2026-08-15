from django.db import models


class CatalogBook(models.Model):
    """
    One row of catalog.csv, persisted so it can be browsed in
    /admin and queried without re-parsing the CSV on every request.

    catalog.csv (repo root) is the source of truth. Run
    `manage.py load_catalog` after editing it -- see that command's
    docstring for the (intentionally simple) upsert-by-catalog_id
    behavior.
    """

    catalog_id = models.CharField(max_length=16, unique=True, db_index=True)
    title = models.CharField(max_length=500)
    alt_titles = models.TextField(blank=True, help_text="Pipe-separated")
    author = models.CharField(max_length=300)
    author_alt = models.TextField(blank=True, help_text="Pipe-separated")
    year = models.IntegerField(null=True, blank=True)
    format = models.CharField(max_length=100, blank=True)
    series = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["catalog_id"]

    def __str__(self):
        return f"{self.catalog_id}: {self.title} ({self.author})"

    def alt_titles_list(self):
        return [t.strip() for t in self.alt_titles.split("|") if t.strip()]

    def author_alt_list(self):
        return [a.strip() for a in self.author_alt.split("|") if a.strip()]
