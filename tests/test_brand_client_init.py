import unittest
from unittest import mock

from shopify_client_base.brandclientbase import BrandClientBase
from shopify_client_base.credentials import Credentials


class ExampleClient(BrandClientBase):
    SHOPNAME = "example-shop"
    VENDOR = "example"
    LOCATIONS = ["Shop location"]

    def load_credentials(self, shop_name):
        return Credentials(
            shop_name=shop_name,
            access_token="token",
            google_credential_path="/path/to/service_account.json",
            google_sheet_id="sheet",
        )


class BrandClientInitTests(unittest.TestCase):
    @mock.patch("shopify_client_base.google_api_interface.interface.build")
    @mock.patch("shopify_client_base.google_api_interface.interface.gspread")
    @mock.patch(
        "shopify_client_base.google_api_interface.interface.Credentials.from_service_account_file"
    )
    def test_shop_credentials_survive_google_init(self, from_file, _gspread, _build):
        google_credentials = object()
        from_file.return_value = google_credentials

        client = ExampleClient()

        # GoogleApiInterface keeps the service-account credentials on
        # self.credentials; the store's own credentials must not be overwritten.
        self.assertIs(client.credentials, google_credentials)
        self.assertEqual(client.shop_credentials.access_token, "token")
        self.assertEqual(client.access_token, "token")
        self.assertEqual(client.sheet_id, "sheet")


if __name__ == "__main__":
    unittest.main()
