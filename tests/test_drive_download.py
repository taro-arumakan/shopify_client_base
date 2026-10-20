import os
import tempfile
import unittest
from unittest import mock

from googleapiclient.errors import HttpError

from shopify_client_base.google_api_interface.drive import GoogleDriveApiInterface


class FakeResponse(dict):
    def __init__(self, status, **headers):
        super().__init__(**headers)
        self.status = status
        self.reason = "stub"


class FakeHttp:
    """Serves bytes over ranged GETs the way Drive does.

    `fail_after` makes the given number of successful chunks, then raises
    `error` once per call until `recover_after` calls have failed.
    """

    def __init__(self, body, chunk_size=None, error=None, error_count=0):
        self.body = body
        self.chunk_size = chunk_size or len(body)
        self.error = error
        self.error_count = error_count
        self.calls = 0

    def request(self, uri, method, headers=None, **kwargs):
        self.calls += 1
        if self.error is not None and self.error_count > 0:
            self.error_count -= 1
            raise self.error
        start = int(headers["range"].split("=")[1].split("-")[0])
        total = len(self.body)
        if start >= total:
            # Range past the end - exactly what a finished download gets.
            return FakeResponse(416, **{"content-range": f"bytes */{total}"}), b""
        chunk = self.body[start : start + self.chunk_size]
        return (
            FakeResponse(
                206,
                **{"content-range": f"bytes {start}-{start + len(chunk) - 1}/{total}"},
            ),
            chunk,
        )


class FakeRequest:
    def __init__(self, http):
        self.http = http
        self.uri = "https://example.test/file"
        self.headers = {}


class FakeDriveService:
    def __init__(self, http):
        self._http = http

    def files(self):
        return self

    def get_media(self, fileId):
        return FakeRequest(self._http)


class Client(GoogleDriveApiInterface):
    def __init__(self, http):
        self.drive_service = FakeDriveService(http)


class TestDownloadFileFromDrive(unittest.TestCase):
    BODY = b"\xff\xd8" + b"x" * 4998

    def setUp(self):
        self.dest = os.path.join(tempfile.mkdtemp(), "img.jpg")

    def partial_path(self):
        return f"{self.dest}.part"

    def test_downloads_in_one_request(self):
        # Requesting a range past the end of a finished download returns 416,
        # so the loop must stop at the chunk that reported done.
        http = FakeHttp(self.BODY)
        Client(http).download_file_from_drive("FILEID", self.dest)
        with open(self.dest, "rb") as f:
            self.assertEqual(f.read(), self.BODY)
        self.assertEqual(http.calls, 1)

    def test_downloads_a_multi_chunk_file(self):
        http = FakeHttp(self.BODY, chunk_size=1000)
        Client(http).download_file_from_drive("FILEID", self.dest)
        with open(self.dest, "rb") as f:
            self.assertEqual(f.read(), self.BODY)
        self.assertEqual(http.calls, 5)

    def test_next_chunk_backoff_absorbs_transient_errors(self):
        # num_retries hands the first failures to googleapiclient's own
        # backoff, so they never reach the loop in download_file_from_drive.
        http = FakeHttp(self.BODY, error=TimeoutError("read timed out"), error_count=2)
        with mock.patch("time.sleep"):
            with self.assertLogs("googleapiclient.http", "WARNING") as logs:
                Client(http).download_file_from_drive("FILEID", self.dest)
        self.assertEqual(len(logs.records), 2)
        with open(self.dest, "rb") as f:
            self.assertEqual(f.read(), self.BODY)

    def test_the_loop_retries_once_that_backoff_is_exhausted(self):
        # max_retries=2 gives next_chunk 2 internal retries (3 requests); the
        # 4th failure propagates and the loop takes over.
        http = FakeHttp(self.BODY, error=TimeoutError("read timed out"), error_count=4)
        with mock.patch("time.sleep"):
            with self.assertLogs(
                "shopify_client_base.google_api_interface.drive", "WARNING"
            ) as logs:
                Client(http).download_file_from_drive(
                    "FILEID", self.dest, max_retries=2
                )
        self.assertIn("Drive download transient error", logs.records[0].getMessage())
        with open(self.dest, "rb") as f:
            self.assertEqual(f.read(), self.BODY)

    def test_a_failed_download_leaves_nothing_behind(self):
        # A leftover file at the destination would be taken for a finished
        # download by download_and_process_image on the next run.
        http = FakeHttp(self.BODY, error=RuntimeError("boom"), error_count=99)
        with self.assertRaises(RuntimeError):
            Client(http).download_file_from_drive("FILEID", self.dest)
        self.assertFalse(os.path.exists(self.dest))
        self.assertFalse(os.path.exists(self.partial_path()))

    def test_an_exhausted_retry_leaves_nothing_behind(self):
        http = FakeHttp(self.BODY, error=TimeoutError("read timed out"), error_count=99)
        with mock.patch("time.sleep"):
            with self.assertRaises(Exception) as cm:
                Client(http).download_file_from_drive(
                    "FILEID", self.dest, max_retries=2
                )
        self.assertIsInstance(cm.exception, (TimeoutError, HttpError))
        self.assertFalse(os.path.exists(self.dest))
        self.assertFalse(os.path.exists(self.partial_path()))

    def test_keyboard_interrupt_leaves_nothing_behind(self):
        http = FakeHttp(self.BODY, error=KeyboardInterrupt(), error_count=1)
        with self.assertRaises(KeyboardInterrupt):
            Client(http).download_file_from_drive("FILEID", self.dest)
        self.assertFalse(os.path.exists(self.dest))
        self.assertFalse(os.path.exists(self.partial_path()))


if __name__ == "__main__":
    unittest.main()
