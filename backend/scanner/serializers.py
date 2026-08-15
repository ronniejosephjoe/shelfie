from rest_framework import serializers

from .models import DetectedBook, LibraryBook, ScanSession


class DetectedBookSerializer(serializers.ModelSerializer):
    bbox = serializers.SerializerMethodField()
    crop_url = serializers.SerializerMethodField()

    class Meta:
        model = DetectedBook
        fields = [
            "id", "bbox", "detector_confidence", "crop_url",
            "read_title", "read_author", "read_error",
            "match_catalog_id", "match_title", "match_author", "match_score", "match_tier",
            "match_alternates", "review_status", "final_title", "final_author",
        ]

    def get_bbox(self, obj):
        return {"x": obj.bbox_x, "y": obj.bbox_y, "width": obj.bbox_width, "height": obj.bbox_height}

    def get_crop_url(self, obj):
        request = self.context.get("request")
        if not obj.crop_image:
            return None
        url = obj.crop_image.url
        return request.build_absolute_uri(url) if request else url


class ScanSessionSerializer(serializers.ModelSerializer):
    detected_books = DetectedBookSerializer(many=True, read_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ScanSession
        fields = [
            "id", "status", "error_message", "created_at", "completed_at",
            "local_model_ms", "vlm_total_ms", "vlm_call_count", "estimated_cost_usd",
            "image_url", "detected_books",
        ]

    def get_image_url(self, obj):
        request = self.context.get("request")
        if not obj.image:
            return None
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url


class LibraryBookSerializer(serializers.ModelSerializer):
    class Meta:
        model = LibraryBook
        fields = ["id", "title", "author", "catalog_id", "series", "year", "was_auto_added", "match_score_at_add", "added_at"]


class ReviewDecisionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["confirm", "correct", "discard"])
    title = serializers.CharField(required=False, allow_blank=True)
    author = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["action"] == "correct" and not attrs.get("title", "").strip():
            raise serializers.ValidationError("title is required when action is 'correct'.")
        return attrs
