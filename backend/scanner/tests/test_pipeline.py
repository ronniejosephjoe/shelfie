"""
Pipeline tests focused on the brief's "Four things we will specifically
check" #4 (graceful failure) and #3 (human in the loop), plus the happy
path. The local detector and VLM client are swapped for deterministic
fakes here -- real-model behavior is exercised separately against the
committed test photos (see README "measured latency/cost" and
scripts/bench_pipeline.py) where non-determinism is expected and fine;
these tests need to be deterministic and fast.
"""
from unittest.mock import patch

import numpy as np
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from catalog.catalog_store import load_entries_from_csv
from catalog.models import CatalogBook
from scanner.models import DetectedBook, LibraryBook, ScanSession
from scanner.services.pipeline import run_pipeline
from scanner.services.spine_detector import SpineRegion
from scanner.services.vlm_client import VLMReadResult


def _fake_jpeg_upload(name="shelf.jpg", size=(400, 300), color=(200, 200, 200)):
    img = Image.new("RGB", size, color)
    import io

    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/jpeg")


def _make_scan_session(**kwargs):
    return ScanSession.objects.create(image=_fake_jpeg_upload(), **kwargs)


class PipelineGracefulFailureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Load the real catalog so match results are meaningful.
        import django.conf

        entries = load_entries_from_csv(django.conf.settings.CATALOG_CSV_PATH)
        for e in entries:
            CatalogBook.objects.create(
                catalog_id=e.catalog_id, title=e.title, author=e.author,
                alt_titles="|".join(e.alt_titles), author_alt="|".join(e.author_alt),
                year=e.year, format=e.format, series=e.series,
            )

    @patch("scanner.services.pipeline.TesseractSpineDetector.detect")
    def test_zero_detections_is_a_successful_scan_not_a_failure(self, mock_detect):
        mock_detect.return_value = []
        scan = _make_scan_session()
        run_pipeline(scan)
        scan.refresh_from_db()
        self.assertEqual(scan.status, "done")
        self.assertEqual(scan.detected_books.count(), 0)
        self.assertEqual(scan.error_message, "")

    @patch("scanner.services.pipeline.get_vlm_client")
    @patch("scanner.services.pipeline.TesseractSpineDetector.detect")
    def test_vlm_timeout_produces_pending_review_row_not_a_crash(self, mock_detect, mock_get_vlm):
        mock_detect.return_value = [SpineRegion(x=0, y=0, width=100, height=200, confidence=0.8)]
        vlm = mock_get_vlm.return_value
        vlm.read_spine.return_value = VLMReadResult(error="timeout", latency_ms=20000)

        scan = _make_scan_session()
        run_pipeline(scan)
        scan.refresh_from_db()

        self.assertEqual(scan.status, "done")
        book = scan.detected_books.get()
        self.assertEqual(book.read_error, "timeout")
        self.assertEqual(book.review_status, "pending_review")
        self.assertEqual(LibraryBook.objects.count(), 0)

    @patch("scanner.services.pipeline.get_vlm_client")
    @patch("scanner.services.pipeline.TesseractSpineDetector.detect")
    def test_malformed_json_from_vlm_is_handled(self, mock_detect, mock_get_vlm):
        mock_detect.return_value = [SpineRegion(x=0, y=0, width=100, height=200, confidence=0.8)]
        vlm = mock_get_vlm.return_value
        vlm.read_spine.return_value = VLMReadResult(error="malformed_json")

        scan = _make_scan_session()
        run_pipeline(scan)

        book = scan.detected_books.get()
        self.assertEqual(book.read_error, "malformed_json")
        self.assertEqual(book.review_status, "pending_review")

    @patch("scanner.services.pipeline.get_vlm_client")
    @patch("scanner.services.pipeline.TesseractSpineDetector.detect")
    def test_unreadable_spine_does_not_invent_a_title(self, mock_detect, mock_get_vlm):
        mock_detect.return_value = [SpineRegion(x=0, y=0, width=100, height=200, confidence=0.8)]
        vlm = mock_get_vlm.return_value
        vlm.read_spine.return_value = VLMReadResult(error="unreadable")

        scan = _make_scan_session()
        run_pipeline(scan)

        book = scan.detected_books.get()
        self.assertEqual(book.read_title, "")
        self.assertEqual(book.read_error, "unreadable")

    @patch("scanner.services.pipeline.get_vlm_client")
    @patch("scanner.services.pipeline.TesseractSpineDetector.detect")
    def test_high_confidence_read_auto_adds_to_library(self, mock_detect, mock_get_vlm):
        mock_detect.return_value = [SpineRegion(x=0, y=0, width=100, height=200, confidence=0.9)]
        vlm = mock_get_vlm.return_value
        vlm.read_spine.return_value = VLMReadResult(
            title="Harry Potter and the Philosopher's Stone", author="J.K. Rowling", model_confidence=0.95,
        )

        scan = _make_scan_session()
        run_pipeline(scan)

        book = scan.detected_books.get()
        self.assertEqual(book.match_tier, "auto")
        self.assertEqual(book.review_status, "auto_added")
        self.assertEqual(LibraryBook.objects.count(), 1)
        self.assertTrue(LibraryBook.objects.get().was_auto_added)

    @patch("scanner.services.pipeline.get_vlm_client")
    @patch("scanner.services.pipeline.TesseractSpineDetector.detect")
    def test_ambiguous_read_goes_to_review_not_auto_add(self, mock_detect, mock_get_vlm):
        # "The Alchemist" with no author read is genuinely ambiguous in
        # this catalog (two different books share the title) -- must
        # not be silently auto-added.
        mock_detect.return_value = [SpineRegion(x=0, y=0, width=100, height=200, confidence=0.9)]
        vlm = mock_get_vlm.return_value
        vlm.read_spine.return_value = VLMReadResult(title="The Alchemist", author="", model_confidence=0.5)

        scan = _make_scan_session()
        run_pipeline(scan)

        book = scan.detected_books.get()
        self.assertNotEqual(book.review_status, "auto_added")
        self.assertEqual(book.review_status, "pending_review")
        top_two_ids = {a["catalog_id"] for a in book.match_alternates[:2]}
        self.assertEqual(top_two_ids, {"CAT0023", "CAT0024"})
        self.assertLess(abs(book.match_alternates[0]["score"] - book.match_alternates[1]["score"]), 0.05)
        self.assertEqual(LibraryBook.objects.count(), 0)

    @patch("scanner.services.pipeline.get_vlm_client")
    @patch("scanner.services.pipeline.TesseractSpineDetector.detect")
    def test_pipeline_crash_marks_scan_failed_instead_of_raising(self, mock_detect, mock_get_vlm):
        mock_detect.side_effect = RuntimeError("simulated unexpected failure")

        scan = _make_scan_session()
        run_pipeline(scan)  # must not raise
        scan.refresh_from_db()

        self.assertEqual(scan.status, "failed")
        self.assertIn("RuntimeError", scan.error_message)

    @patch("scanner.services.pipeline.TesseractSpineDetector.detect")
    def test_corrupt_image_fails_cleanly(self, mock_detect):
        scan = ScanSession.objects.create(
            image=SimpleUploadedFile("bad.jpg", b"not actually a jpeg", content_type="image/jpeg")
        )
        run_pipeline(scan)
        scan.refresh_from_db()
        self.assertEqual(scan.status, "failed")
        mock_detect.assert_not_called()
