import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fetch_demo_data import DemoFeed, FetchError, fetch_demo_data


FEED_CONTENT = b"test GRID feed contents"


def _feed(content=FEED_CONTENT, filename="events_test_grid.jsonl.zst"):
    blob = hashlib.sha1(f"blob {len(content)}\0".encode("ascii") + content).hexdigest()
    return DemoFeed(filename, len(content), blob, "https://example.test/feed")


class FetchDemoDataTests(unittest.TestCase):
    def test_valid_local_copy_is_reused_without_network(self):
        feed = _feed()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / feed.filename
            destination.write_bytes(FEED_CONTENT)
            with patch("fetch_demo_data.FEEDS", (feed,)), patch("fetch_demo_data.urlopen") as urlopen:
                fetch_demo_data(Path(temporary_directory))

        urlopen.assert_not_called()

    def test_corrupt_local_copy_is_replaced_only_after_validation(self):
        feed = _feed()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / feed.filename
            destination.write_bytes(b"corrupt")
            with patch("fetch_demo_data.FEEDS", (feed,)), patch(
                "fetch_demo_data.urlopen", return_value=io.BytesIO(FEED_CONTENT)
            ):
                fetch_demo_data(Path(temporary_directory))

            self.assertEqual(FEED_CONTENT, destination.read_bytes())
            self.assertEqual([feed.filename], [path.name for path in Path(temporary_directory).iterdir()])

    def test_network_failure_preserves_existing_file_and_cleans_temporary_file(self):
        feed = _feed()
        original = b"keep the existing copy"
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / feed.filename
            destination.write_bytes(original)
            with patch("fetch_demo_data.FEEDS", (feed,)), patch(
                "fetch_demo_data.urlopen", side_effect=OSError("network unavailable")
            ):
                with self.assertRaises(OSError):
                    fetch_demo_data(Path(temporary_directory))

            self.assertEqual(original, destination.read_bytes())
            self.assertEqual([feed.filename], [path.name for path in Path(temporary_directory).iterdir()])

    def test_invalid_download_does_not_replace_existing_file(self):
        feed = _feed()
        original = b"keep the existing copy"
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / feed.filename
            destination.write_bytes(original)
            with patch("fetch_demo_data.FEEDS", (feed,)), patch(
                "fetch_demo_data.urlopen", return_value=io.BytesIO(b"wrong contents")
            ):
                with self.assertRaises(FetchError):
                    fetch_demo_data(Path(temporary_directory))

            self.assertEqual(original, destination.read_bytes())
            self.assertEqual([feed.filename], [path.name for path in Path(temporary_directory).iterdir()])


if __name__ == "__main__":
    unittest.main()