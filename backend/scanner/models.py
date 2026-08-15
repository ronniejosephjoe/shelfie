import uuid

from django.db import models


def scan_image_upload_path(instance, filename):
    return f"scans/{instance.id}/{filename}"


def crop_upload_path(instance, filename):
    return f"scans/{instance.scan_session_id}/spines/{instance.id}_{filename}"


class ScanSession(models.Model):
    """One 'user took/picked a photo' event, and everything that came of it.

    Status machine: pending -> processing -> done, or -> failed at any
    point along the way. See scanner/services/pipeline.py for what moves
    it between states and how each failure mode (timeout, zero
    detections, malformed VLM JSON, ...) is handled without crashing the
    request.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    image = models.ImageField(upload_to=scan_image_upload_path)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Measured, not estimated -- see README "Local vs. hosted routing".
    local_model_ms = models.FloatField(null=True, blank=True)
    vlm_total_ms = models.FloatField(null=True, blank=True)
    vlm_call_count = models.IntegerField(default=0)
    estimated_cost_usd = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ScanSession({self.id}, {self.status})"


class DetectedBook(models.Model):
    """One candidate book spine found in a scan, at every stage of the pipeline.

    A row exists for a detected region even if the VLM read fails, or the
    match fails -- 'zero books detected' still produces a ScanSession
    with zero DetectedBook rows and a clear status, and 'detected but
    unreadable' produces a row the review screen can show as
    unreadable rather than silently dropping it.
    """

    REVIEW_STATUS_CHOICES = [
        ("pending_review", "Pending review"),
        ("auto_added", "Auto-added (high confidence)"),
        ("confirmed", "Confirmed by user"),
        ("corrected", "Corrected by user"),
        ("discarded", "Discarded by user"),
    ]

    MATCH_TIER_CHOICES = [
        ("auto", "Auto"),
        ("review", "Review"),
        ("unmatched", "Unmatched"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scan_session = models.ForeignKey(ScanSession, related_name="detected_books", on_delete=models.CASCADE)

    # From the local spine-region detector.
    bbox_x = models.FloatField()
    bbox_y = models.FloatField()
    bbox_width = models.FloatField()
    bbox_height = models.FloatField()
    detector_confidence = models.FloatField(null=True, blank=True)
    crop_image = models.ImageField(upload_to=crop_upload_path, null=True, blank=True)

    # From the hosted VLM read. Blank/null means the read failed --
    # see read_error, which is always populated in that case.
    read_title = models.CharField(max_length=500, blank=True)
    read_author = models.CharField(max_length=300, blank=True)
    read_error = models.CharField(max_length=100, blank=True)

    # From the matching engine.
    match_catalog_id = models.CharField(max_length=16, blank=True)
    match_title = models.CharField(max_length=500, blank=True)
    match_author = models.CharField(max_length=300, blank=True)
    match_score = models.FloatField(null=True, blank=True)
    match_tier = models.CharField(max_length=20, choices=MATCH_TIER_CHOICES, blank=True)
    match_alternates = models.JSONField(default=list, blank=True)

    # Human-in-the-loop state.
    review_status = models.CharField(max_length=20, choices=REVIEW_STATUS_CHOICES, default="pending_review")
    final_title = models.CharField(max_length=500, blank=True)
    final_author = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["bbox_x"]

    def __str__(self):
        return f"DetectedBook({self.read_title or 'unread'!r}, {self.review_status})"


class LibraryBook(models.Model):
    """A confirmed entry in the user's library. What screen 7 of the flow shows."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=500)
    author = models.CharField(max_length=300)
    catalog_id = models.CharField(max_length=16, blank=True)
    series = models.CharField(max_length=300, blank=True)
    year = models.IntegerField(null=True, blank=True)

    source_detected_book = models.ForeignKey(
        DetectedBook, null=True, blank=True, on_delete=models.SET_NULL, related_name="library_entries"
    )
    was_auto_added = models.BooleanField(default=False)
    match_score_at_add = models.FloatField(null=True, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-added_at"]

    def __str__(self):
        return f"{self.title} - {self.author}"
