import logging

from django.shortcuts import get_object_or_404
from PIL import Image
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DetectedBook, LibraryBook, ScanSession
from .serializers import (
    DetectedBookSerializer,
    LibraryBookSerializer,
    ReviewDecisionSerializer,
    ScanSessionSerializer,
)
from .services.pipeline import run_pipeline

logger = logging.getLogger("shelfie")

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


class ScanListCreateView(APIView):
    """POST an image, get back the full pipeline result: detected spines,
    reads, matches, and confidence tiers. Runs synchronously (see
    pipeline.py docstring for why that's an acceptable scope cut here).
    """

    def post(self, request):
        image_file = request.FILES.get("image")
        if not image_file:
            return Response(
                {"detail": "No image file provided. Send multipart/form-data with an 'image' field."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if image_file.size > MAX_UPLOAD_BYTES:
            return Response(
                {"detail": f"Image too large ({image_file.size} bytes, max {MAX_UPLOAD_BYTES})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate it's actually a decodable image before we create any
        # DB rows for it -- a non-image upload should be a clean 400,
        # not a pipeline failure discovered mid-scan.
        try:
            image_file.seek(0)
            Image.open(image_file).verify()
            image_file.seek(0)
        except Exception:
            return Response(
                {"detail": "That file doesn't look like a valid image."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scan_session = ScanSession.objects.create(image=image_file)
        run_pipeline(scan_session)
        scan_session.refresh_from_db()

        serializer = ScanSessionSerializer(scan_session, context={"request": request})
        # The HTTP request itself always succeeded if we get here; a
        # failed *scan* (scan_session.status == "failed") is still a 200
        # with that status in the body, so the client can render a
        # specific "this scan failed, here's why" state instead of
        # special-casing a 5xx. See pipeline.py's module docstring.
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def get(self, request):
        sessions = ScanSession.objects.all()[:20]
        serializer = ScanSessionSerializer(sessions, many=True, context={"request": request})
        return Response(serializer.data)


class ScanDetailView(APIView):
    def get(self, request, pk):
        scan_session = get_object_or_404(ScanSession, pk=pk)
        serializer = ScanSessionSerializer(scan_session, context={"request": request})
        return Response(serializer.data)


class DetectedBookDecisionView(APIView):
    """The human-in-the-loop endpoint: confirm / correct / discard one
    detected book from the review screen.

    Anything not already auto_added lands here. confirm accepts the
    matcher's best guess (or the raw VLM read if unmatched) as-is;
    correct lets the user overwrite title/author entirely (typos, a
    completely wrong match, or a book the matcher didn't find at all);
    discard drops it -- explicitly, as a user action, never silently.
    """

    def post(self, request, pk):
        detected = get_object_or_404(DetectedBook, pk=pk)
        serializer = ReviewDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]

        if action == "discard":
            detected.review_status = "discarded"
            detected.save()
            return Response(DetectedBookSerializer(detected, context={"request": request}).data)

        if action == "confirm":
            title = detected.match_title or detected.read_title
            author = detected.match_author or detected.read_author
            if not title:
                return Response(
                    {"detail": "Nothing to confirm -- this book has no title (matched or read). Use 'correct' instead."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            detected.review_status = "confirmed"
        else:  # correct
            title = serializer.validated_data["title"].strip()
            author = serializer.validated_data.get("author", "").strip()
            detected.review_status = "corrected"

        detected.final_title = title
        detected.final_author = author
        detected.save()

        library_book = LibraryBook.objects.create(
            title=title,
            author=author,
            catalog_id=detected.match_catalog_id if action == "confirm" else "",
            source_detected_book=detected,
            was_auto_added=False,
            match_score_at_add=detected.match_score,
        )
        return Response(
            {
                "detected_book": DetectedBookSerializer(detected, context={"request": request}).data,
                "library_book": LibraryBookSerializer(library_book).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LibraryListView(APIView):
    def get(self, request):
        books = LibraryBook.objects.all()
        return Response(LibraryBookSerializer(books, many=True).data)
