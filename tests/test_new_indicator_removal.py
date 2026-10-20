import json
import unittest
from unittest import mock

from shopify_client_base.brandclientbase import (
    NEW_INDICATOR_REMOVAL_PENDING_PREFIX,
    BrandClientBase,
)


class FakeBrandClient(BrandClientBase):
    SHOPNAME = "example-shop"
    VENDOR = "example"
    USES_NEW_BADGE_METAFIELD = True
    LOCATIONS = ["Shop location"]

    def __init__(self):
        self.products_season_tag = "2026_PF_DROP4"
        self.remove_existing_new_product_indicators = True
        self.shop_name = self.SHOPNAME


class NewIndicatorRemovalTests(unittest.TestCase):
    def test_pending_tag_encodes_season(self):
        self.assertEqual(
            BrandClientBase.new_indicator_removal_pending_tag("2026_PF_DROP4"),
            "pending-new-indicator-removal__2026_PF_DROP4",
        )
        self.assertEqual(
            BrandClientBase.season_tag_from_pending_tag(
                "pending-new-indicator-removal__2026_PF_DROP4"
            ),
            "2026_PF_DROP4",
        )
        self.assertIsNone(BrandClientBase.season_tag_from_pending_tag("New Arrival"))

    def test_schedule_skips_already_queued_and_same_season(self):
        client = FakeBrandClient()
        queued = {
            "id": "gid://shopify/Product/1",
            "title": "OLD",
            "tags": [
                "New Arrival",
                f"{NEW_INDICATOR_REMOVAL_PENDING_PREFIX}2026_PF_DROP3",
            ],
        }
        same_season = {
            "id": "gid://shopify/Product/2",
            "title": "DROP4 ALREADY",
            "tags": ["New Arrival", "2026_PF_DROP4"],
        }
        fresh = {
            "id": "gid://shopify/Product/3",
            "title": "FRESH",
            "tags": ["New Arrival"],
        }
        calls = []
        with mock.patch.object(
            client,
            "products_with_new_indicators",
            return_value=[queued, same_season, fresh],
        ), mock.patch.object(
            client, "add_product_tags", side_effect=lambda *a: calls.append(a)
        ):
            client.schedule_new_indicator_removal()
        self.assertEqual(
            calls,
            [
                (
                    "gid://shopify/Product/3",
                    "pending-new-indicator-removal__2026_PF_DROP4",
                )
            ],
        )

    def test_sweep_waits_until_season_tag_is_live(self):
        client = FakeBrandClient()
        old = {
            "id": "gid://shopify/Product/1",
            "title": "OLD",
            "tags": [
                "New Arrival",
                "pending-new-indicator-removal__2026_PF_DROP4",
            ],
        }
        calls = []
        with mock.patch.object(
            client, "products_pending_new_indicator_removal", return_value=[old]
        ), mock.patch.object(
            client, "season_tag_is_live", return_value=False
        ), mock.patch.object(
            client,
            "remove_product_tags",
            side_effect=lambda *a: calls.append(("tags", a)),
        ):
            self.assertEqual(client.sweep_new_indicator_removals(), [])
            self.assertEqual(calls, [])

        live_calls = []

        def season_tag_is_live(season_tag, now=None):
            live_calls.append(season_tag)
            return True

        with mock.patch.object(
            client, "products_pending_new_indicator_removal", return_value=[old, old]
        ), mock.patch.object(
            client, "season_tag_is_live", side_effect=season_tag_is_live
        ), mock.patch.object(
            client,
            "product_metafield_by_product_id",
            return_value={"value": json.dumps(["NEW", "SALE"])},
        ), mock.patch.object(
            client,
            "update_badges_metafield",
            side_effect=lambda *a: calls.append(("badges", a)),
        ), mock.patch.object(
            client,
            "remove_product_tags",
            side_effect=lambda *a: calls.append(("tags", a)),
        ):
            self.assertEqual(client.sweep_new_indicator_removals(), ["OLD", "OLD"])
        self.assertEqual(live_calls, ["2026_PF_DROP4"])
        self.assertEqual(
            calls,
            [
                ("badges", ("gid://shopify/Product/1", ["SALE"])),
                (
                    "tags",
                    (
                        "gid://shopify/Product/1",
                        [
                            "New Arrival",
                            "pending-new-indicator-removal__2026_PF_DROP4",
                        ],
                    ),
                ),
                ("badges", ("gid://shopify/Product/1", ["SALE"])),
                (
                    "tags",
                    (
                        "gid://shopify/Product/1",
                        [
                            "New Arrival",
                            "pending-new-indicator-removal__2026_PF_DROP4",
                        ],
                    ),
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
