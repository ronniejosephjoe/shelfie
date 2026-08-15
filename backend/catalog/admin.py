from django.contrib import admin

from .models import CatalogBook


@admin.register(CatalogBook)
class CatalogBookAdmin(admin.ModelAdmin):
    list_display = ("catalog_id", "title", "author", "year", "format", "series")
    search_fields = ("title", "alt_titles", "author", "author_alt")
    list_filter = ("format",)
