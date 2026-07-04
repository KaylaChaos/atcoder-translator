import unittest

import index


class ProgressNotifyTests(unittest.TestCase):
    def test_disabled_does_not_call_sender(self):
        calls = []

        ok = index.maybe_send_progress(False, "hello", sender=calls.append)

        self.assertFalse(ok)
        self.assertEqual(calls, [])

    def test_enabled_calls_sender(self):
        calls = []

        ok = index.maybe_send_progress(True, "hello", sender=calls.append)

        self.assertTrue(ok)
        self.assertEqual(calls, ["hello"])

    def test_sender_error_is_ignored(self):
        def fail(_content):
            raise RuntimeError("network down")

        ok = index.maybe_send_progress(True, "hello", sender=fail)

        self.assertFalse(ok)


class OpenAITextExtractionTests(unittest.TestCase):
    def test_empty_responses_text_raises_for_auto_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "empty text"):
            index.ensure_openai_text("responses", "")

    def test_non_empty_responses_text_is_returned(self):
        self.assertEqual(index.ensure_openai_text("responses", "pong"), "pong")

    def test_json_object_parse_error_reports_preview(self):
        with self.assertRaisesRegex(ValueError, "preview"):
            index.parse_json_object("")


if __name__ == "__main__":
    unittest.main()
