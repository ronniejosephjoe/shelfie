"""
Unit tests for the hosted VLM clients, with the network layer mocked --
these test parsing/classification logic (success, malformed JSON,
timeout, safety block, HTTP error), not real API behavior. See
docs/latency_cost_notes.md and the README for what was actually
measured against real providers vs. tested here in isolation.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from PIL import Image

from scanner.services.vlm_client import (
    GeminiVisionClient,
    MockVLMClient,
    OpenAIVisionClient,
    get_vlm_client,
)


def _tiny_image():
    return Image.new("RGB", (40, 120), (200, 200, 200))


class GeminiVisionClientTests(TestCase):
    def setUp(self):
        # min_call_interval=0 disables proactive throttling so these
        # tests run at full speed; throttling itself is covered by its
        # own test below with time.sleep mocked out.
        self.client = GeminiVisionClient(
            api_key="fake-key", model="gemini-3.1-flash-lite", timeout=5, min_call_interval=0
        )

    @patch("requests.post")
    def test_successful_read(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "candidates": [
                    {"content": {"parts": [{"text": '{"title": "Dune", "author": "Frank Herbert", "confidence": 0.9}'}]}}
                ],
                "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 20},
            },
        )
        result = self.client.read_spine(_tiny_image())
        self.assertTrue(result.ok)
        self.assertEqual(result.title, "Dune")
        self.assertEqual(result.author, "Frank Herbert")
        self.assertEqual(result.provider, "gemini")

    @patch("requests.post")
    def test_free_tier_cost_is_zero_even_with_usage_metadata(self, mock_post):
        # GEMINI_BILLING_ENABLED defaults to False -- cost must stay 0
        # so we never claim a paid-tier estimate against free traffic.
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "candidates": [{"content": {"parts": [{"text": '{"title": "Dune", "author": ""}'}]}}],
                "usageMetadata": {"promptTokenCount": 5000, "candidatesTokenCount": 200},
            },
        )
        result = self.client.read_spine(_tiny_image())
        self.assertEqual(result.estimated_cost_usd, 0.0)

    @patch("requests.post")
    def test_safety_blocked_response_is_unreadable_not_a_crash(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}},
        )
        result = self.client.read_spine(_tiny_image())
        self.assertEqual(result.error, "unreadable")

    @patch("requests.post")
    def test_malformed_json_in_text_part(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]},
        )
        result = self.client.read_spine(_tiny_image())
        self.assertEqual(result.error, "malformed_json")

    @patch("requests.post")
    def test_unexpected_response_shape_is_malformed_not_a_crash(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"candidates": [{"nothing": "useful"}]})
        result = self.client.read_spine(_tiny_image())
        self.assertEqual(result.error, "malformed_json")

    @patch("requests.post")
    def test_non_429_http_error_status(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, text="server error")
        result = self.client.read_spine(_tiny_image())
        self.assertEqual(result.error, "api_error")

    @patch("time.sleep")
    @patch("requests.post")
    def test_429_retries_then_reports_rate_limited_once_exhausted(self, mock_post, mock_sleep):
        # Free-tier quota exceeded and staying exceeded for the whole
        # retry budget: this must come back as a distinct, honest
        # "rate_limited" error -- not a generic api_error, and not a
        # crash -- after trying (1 initial + _MAX_RETRIES) times.
        mock_post.return_value = MagicMock(status_code=429, text="quota exceeded", headers={})
        result = self.client.read_spine(_tiny_image())
        self.assertEqual(result.error, "rate_limited")
        self.assertEqual(mock_post.call_count, 1 + GeminiVisionClient._MAX_RETRIES)
        self.assertEqual(mock_sleep.call_count, GeminiVisionClient._MAX_RETRIES)

    @patch("time.sleep")
    @patch("requests.post")
    def test_429_then_success_recovers_within_retry_budget(self, mock_post, mock_sleep):
        # A transient rate limit that clears on the next attempt should
        # end in a normal successful read, not an error -- retrying is
        # the whole point.
        rate_limited = MagicMock(status_code=429, text="quota exceeded", headers={})
        success = MagicMock(
            status_code=200,
            json=lambda: {
                "candidates": [
                    {"content": {"parts": [{"text": '{"title": "Dune", "author": "Frank Herbert"}'}]}}
                ],
                "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 20},
            },
        )
        mock_post.side_effect = [rate_limited, success]
        result = self.client.read_spine(_tiny_image())
        self.assertTrue(result.ok)
        self.assertEqual(result.title, "Dune")
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)

    @patch("time.sleep")
    @patch("requests.post")
    def test_429_honors_retry_after_header(self, mock_post, mock_sleep):
        mock_post.return_value = MagicMock(status_code=429, text="quota exceeded", headers={"Retry-After": "7"})
        self.client.read_spine(_tiny_image())
        mock_sleep.assert_called_with(7.0)

    @patch("time.sleep")
    def test_proactive_throttle_waits_between_calls(self, mock_sleep):
        import requests

        client = GeminiVisionClient(api_key="fake-key", model="gemini-3.1-flash-lite", timeout=5, min_call_interval=4.5)
        client._last_call_at = __import__("time").monotonic()  # pretend a call just happened

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"candidates": [{"content": {"parts": [{"text": '{"title": "Dune"}'}]}}]},
            )
            client.read_spine(_tiny_image())

        mock_sleep.assert_called_once()
        waited = mock_sleep.call_args[0][0]
        self.assertGreater(waited, 0)
        self.assertLessEqual(waited, 4.5)

    def test_min_call_interval_of_zero_never_throttles(self):
        # self.client is built with min_call_interval=0 in setUp --
        # calling _throttle() repeatedly must never sleep.
        with patch("time.sleep") as mock_sleep:
            self.client._throttle()
            self.client._throttle()
        mock_sleep.assert_not_called()

    @patch("requests.post")
    def test_timeout_is_classified_separately_from_other_errors(self, mock_post):
        import requests

        mock_post.side_effect = requests.exceptions.Timeout("took too long")
        result = self.client.read_spine(_tiny_image())
        self.assertEqual(result.error, "timeout")

    @patch("requests.post")
    def test_empty_title_is_unreadable(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"candidates": [{"content": {"parts": [{"text": '{"title": "", "author": ""}'}]}}]},
        )
        result = self.client.read_spine(_tiny_image())
        self.assertEqual(result.error, "unreadable")


class OpenAIVisionClientTests(TestCase):
    def setUp(self):
        self.client = OpenAIVisionClient(api_key="fake-key", model="gpt-4o-mini", timeout=5)

    def _mock_openai_response(self, content, prompt_tokens=500, completion_tokens=20):
        message = MagicMock(content=content)
        choice = MagicMock(message=message)
        usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return MagicMock(choices=[choice], usage=usage)

    @patch.object(OpenAIVisionClient, "_client")
    def test_successful_read_computes_cost_from_usage(self, mock_client_factory):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(
            '{"title": "1984", "author": "George Orwell", "confidence": 0.95}'
        )
        mock_client_factory.return_value = mock_client

        result = self.client.read_spine(_tiny_image())
        self.assertTrue(result.ok)
        self.assertEqual(result.title, "1984")
        self.assertGreater(result.estimated_cost_usd, 0)

    @patch.object(OpenAIVisionClient, "_client")
    def test_sdk_exception_does_not_propagate(self, mock_client_factory):
        mock_client_factory.side_effect = RuntimeError("connection reset")
        result = self.client.read_spine(_tiny_image())  # must not raise
        self.assertEqual(result.error, "api_error")

    @patch.object(OpenAIVisionClient, "_client")
    def test_timeout_named_exception_is_classified_as_timeout(self, mock_client_factory):
        class FakeTimeoutError(Exception):
            pass

        mock_client_factory.side_effect = FakeTimeoutError("Request timeout")
        result = self.client.read_spine(_tiny_image())
        self.assertEqual(result.error, "timeout")


class GetVlmClientProviderSelectionTests(TestCase):
    @override_settings(VLM_PROVIDER="gemini", GEMINI_API_KEY="a-key")
    def test_gemini_selected_when_configured(self):
        self.assertIsInstance(get_vlm_client(), GeminiVisionClient)

    @override_settings(VLM_PROVIDER="gemini", GEMINI_API_KEY="")
    def test_gemini_falls_back_to_mock_without_key(self):
        self.assertIsInstance(get_vlm_client(), MockVLMClient)

    @override_settings(VLM_PROVIDER="openai", OPENAI_API_KEY="a-key")
    def test_openai_selected_when_configured(self):
        self.assertIsInstance(get_vlm_client(), OpenAIVisionClient)

    @override_settings(VLM_PROVIDER="openai", OPENAI_API_KEY="")
    def test_openai_falls_back_to_mock_without_key(self):
        self.assertIsInstance(get_vlm_client(), MockVLMClient)

    @override_settings(VLM_PROVIDER="mock")
    def test_mock_is_the_default(self):
        self.assertIsInstance(get_vlm_client(), MockVLMClient)
