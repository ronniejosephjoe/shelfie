"""
API-level tests: request validation and the confirm/correct/discard
review endpoint. Pipeline internals are covered in test_pipeline.py;
here we go through actual HTTP requests via DRF's test client.
"""
import io
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APITestCase

from scanner.models import DetectedBook, LibraryBook, ScanSession
from scanner.services.spine_detector import SpineRegion
from scanner.services.vlm_client import VLMReadResult


def _jpeg_bytes(size=(300, 200), color=(180, 180, 180)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


class ScanUploadValidationTests(APITestCase):
    def test_missing_image_is_400(self):
        resp = self.client.post("/api/scans/", {})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No image file", resp.json()["detail"])

    def test_non_image_upload_is_400_and_creates_no_scan_session(self):
        upload = SimpleUploadedFile("notes.txt", b"just some text", content_type="text/plain")
        resp = self.client.post("/api/scans/", {"image": upload}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(ScanSession.objects.count(), 0)

    def test_oversized_upload_is_400(self):
        from scanner import views

        big = SimpleUploadedFile("shelf.jpg", _jpeg_bytes(), content_type="image/jpeg")
        with patch.object(views, "MAX_UPLOAD_BYTES", 10):
            resp = self.client.post("/api/scans/", {"image": big}, format="multipart")
        self.assertEqual(resp.status_code, 400)

    @patch("scanner.services.pipeline.get_vlm_client")
    @patch("scanner.services.pipeline.TesseractSpineDetector.detect")
    def test_valid_upload_runs_pipeline_and_returns_result(self, mock_detect, mock_get_vlm):
        mock_detect.return_value = [SpineRegion(x=0, y=0, width=50, height=100, confidence=0.7)]
        mock_get_vlm.return_value.read_spine.return_value = VLMReadResult(
            title="1984", author="George Orwell", model_confidence=0.9
        )
        upload = SimpleUploadedFile("shelf.jpg", _jpeg_bytes(), content_type="image/jpeg")

        resp = self.client.post("/api/scans/", {"image": upload}, format="multipart")

        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(len(body["detected_books"]), 1)
        self.assertEqual(body["detected_books"][0]["read_title"], "1984")


class ReviewDecisionTests(APITestCase):
    def setUp(self):
        self.scan = ScanSession.objects.create(
            image=SimpleUploadedFile("shelf.jpg", _jpeg_bytes(), content_type="image/jpeg")
        )
        self.detected = DetectedBook.objects.create(
            scan_session=self.scan,
            bbox_x=0, bbox_y=0, bbox_width=50, bbox_height=100,
            read_title="Dune", read_author="Frank Herbert",
            match_catalog_id="CAT0030", match_title="Dune", match_author="Frank Herbert",
            match_score=0.7, match_tier="review", review_status="pending_review",
        )

    def test_confirm_creates_library_book(self):
        resp = self.client.post(f"/api/detected-books/{self.detected.id}/decide/", {"action": "confirm"})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(LibraryBook.objects.count(), 1)
        self.detected.refresh_from_db()
        self.assertEqual(self.detected.review_status, "confirmed")

    def test_correct_overrides_title_and_author(self):
        resp = self.client.post(
            f"/api/detected-books/{self.detected.id}/decide/",
            {"action": "correct", "title": "Dune Messiah", "author": "Frank Herbert"},
        )
        self.assertEqual(resp.status_code, 201)
        book = LibraryBook.objects.get()
        self.assertEqual(book.title, "Dune Messiah")
        self.assertEqual(book.catalog_id, "")  # corrections don't inherit the (possibly wrong) match id

    def test_correct_without_title_is_rejected(self):
        resp = self.client.post(f"/api/detected-books/{self.detected.id}/decide/", {"action": "correct"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(LibraryBook.objects.count(), 0)

    def test_discard_creates_no_library_book(self):
        resp = self.client.post(f"/api/detected-books/{self.detected.id}/decide/", {"action": "discard"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(LibraryBook.objects.count(), 0)
        self.detected.refresh_from_db()
        self.assertEqual(self.detected.review_status, "discarded")

    def test_confirm_with_no_title_at_all_is_rejected_not_silently_added(self):
        blank = DetectedBook.objects.create(
            scan_session=self.scan, bbox_x=0, bbox_y=0, bbox_width=50, bbox_height=100,
            read_error="unreadable", review_status="pending_review",
        )
        resp = self.client.post(f"/api/detected-books/{blank.id}/decide/", {"action": "confirm"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(LibraryBook.objects.count(), 0)


class LibraryListTests(APITestCase):
    def test_library_list_reflects_confirmed_books(self):
        LibraryBook.objects.create(title="Circe", author="Madeline Miller")
        resp = self.client.get("/api/library/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["title"], "Circe")
