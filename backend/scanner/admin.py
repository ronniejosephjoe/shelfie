from django.contrib import admin

from .models import DetectedBook, LibraryBook, ScanSession


class DetectedBookInline(admin.TabularInline):
    model = DetectedBook
    extra = 0
    readonly_fields = (
        "read_title", "read_author", "read_error",
        "match_catalog_id", "match_score", "match_tier", "review_status",
    )


@admin.register(ScanSession)
class ScanSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "created_at", "local_model_ms", "vlm_total_ms", "vlm_call_count", "estimated_cost_usd")
    inlines = [DetectedBookInline]


@admin.register(LibraryBook)
class LibraryBookAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "catalog_id", "was_auto_added", "match_score_at_add", "added_at")
    search_fields = ("title", "author")
