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


if __name__ == "__main__":
    unittest.main()
