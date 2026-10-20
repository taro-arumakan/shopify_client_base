import unittest
from unittest import mock

from googleapiclient.errors import HttpError

from shopify_client_base.google_api_interface.drive import GoogleDriveApiInterface


class FakeResponse(dict):
    def __init__(self, status):
        super().__init__()
        self.status = status
        self.reason = "stub"


def http_error(status):
    return HttpError(
        FakeResponse(status), b"Unknown Error.", uri="https://example.test"
    )


class FakeListRequest:
    """A files().list() request that raises the queued errors, then succeeds."""

    def __init__(self, errors, result):
        self.errors = list(errors)
        self.result = result
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.result


class FakeDriveService:
    def __init__(self, request):
        self.request = request

    def files(self):
        return self

    def list(self, **kwargs):
        return self.request


class Client(GoogleDriveApiInterface):
    def __init__(self, request):
        self.drive_service = FakeDriveService(request)


FOLDER = {"id": "FOLDERID", "name": "Instagram", "webViewLink": ""}


class TestMetadataRetry(unittest.TestCase):
    def test_a_backend_500_on_folder_lookup_is_retried(self):
        # An error seen in production on 2026-10-09.
        request = FakeListRequest([http_error(500)], {"files": [FOLDER]})
        with mock.patch("time.sleep"):
            with self.assertLogs(
                "shopify_client_base.google_api_interface.drive", "WARNING"
            ) as logs:
                folder_id = Client(request).find_folder_id_by_name(
                    "PARENTID", "Instagram"
                )
        self.assertEqual(folder_id, "FOLDERID")
        self.assertEqual(request.calls, 2)
        self.assertIn("Drive lookup in PARENTID", logs.records[0].getMessage())

    def test_network_errors_are_retried(self):
        request = FakeListRequest(
            [TimeoutError("read timed out"), ConnectionError("reset")],
            {"files": [{"id": "F1", "name": "a.csv"}]},
        )
        with mock.patch("time.sleep"):
            with self.assertLogs("shopify_client_base.google_api_interface.drive", "WARNING"):
                files = Client(request).list_files_in_folder("FOLDERID")
        self.assertEqual(files, [{"id": "F1", "name": "a.csv"}])
        self.assertEqual(request.calls, 3)

    def test_a_client_error_is_not_retried(self):
        # A 404 / 403 will not get better on retry, so it surfaces at once.
        request = FakeListRequest([http_error(404)], {"files": []})
        with mock.patch("time.sleep") as sleep:
            with self.assertRaises(HttpError):
                Client(request).list_folders("FOLDERID")
        self.assertEqual(request.calls, 1)
        sleep.assert_not_called()

    def test_gives_up_after_max_retries(self):
        request = FakeListRequest([http_error(503)] * 10, {"files": []})
        with mock.patch("time.sleep"):
            with self.assertLogs("shopify_client_base.google_api_interface.drive", "WARNING"):
                with self.assertRaises(HttpError):
                    Client(request).get_drive_image_details("FOLDERID")
        self.assertEqual(request.calls, 5)


if __name__ == "__main__":
    unittest.main()
