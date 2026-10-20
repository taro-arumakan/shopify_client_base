import datetime
import unittest
import zoneinfo
from unittest.mock import patch

from shopify_client_base.exceptions import NoProductsFoundException
from shopify_client_base.shopify_graphql_client.client import ShopifyGraphqlClient
from shopify_client_base.shopify_graphql_client.publications import PENDING_CHANNEL_PUBLISH

PUBLICATIONS = [
    {"id": "gid://shopify/Publication/1", "name": "Online Store"},
    {"id": "gid://shopify/Publication/2", "name": "Point of Sale"},
    {"id": "gid://shopify/Publication/3", "name": "Shop"},
]
PRODUCT_ID = "gid://shopify/Product/9311361892490"
SCHEDULED = datetime.datetime(2026, 10, 14, 12, tzinfo=zoneinfo.ZoneInfo("Asia/Tokyo"))


class PublicationsTestCase(unittest.TestCase):
    def setUp(self):
        self.client = ShopifyGraphqlClient(shop_name="example-shop", access_token="dummy")
        self.queries = []

    def recording_run_query(self, responses):
        """Answer run_query from `responses`, matched on a substring of the query."""

        def run_query(query, variables=None, method="post"):
            self.queries.append((query, variables))
            for marker, response in responses.items():
                if marker in query:
                    return response
            raise AssertionError(f"unexpected query: {query[:120]}")

        return run_query


class TestPublishSkipsOtherChannels(PublicationsTestCase):
    def publish_calls(self):
        return [
            variables
            for query, variables in self.queries
            if "publishablePublish" in query
        ]

    def tag_calls(self):
        return [variables for query, variables in self.queries if "tagsAdd" in query]

    def test_a_scheduled_publish_touches_the_online_store_only(self):
        with patch.object(
            self.client,
            "run_query",
            self.recording_run_query(
                {
                    "query publications": {"publications": {"nodes": PUBLICATIONS}},
                    "publishablePublish": {
                        "publishablePublish": {"publishable": {}, "userErrors": []}
                    },
                    "tagsAdd": {"tagsAdd": {"node": {}, "userErrors": []}},
                }
            ),
        ):
            with self.assertLogs(
                "shopify_client_base.shopify_graphql_client.publications", "INFO"
            ) as logs:
                self.client.publish_by_product_or_collection_id(
                    PRODUCT_ID, scheduled_time=SCHEDULED
                )

        calls = self.publish_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["input"]["publicationId"], PUBLICATIONS[0]["id"])
        self.assertEqual(calls[0]["input"]["publishDate"], SCHEDULED.isoformat())

        # The skip is logged, not silent - it is why a drop needs a catch-up.
        messages = [r.getMessage() for r in logs.records]
        self.assertTrue(
            any("not publishing to Point of Sale, Shop" in m for m in messages),
            messages,
        )

        # The product joins the sweeper's queue, so nobody has to remember to
        # schedule a catch-up for this drop.
        self.assertEqual(
            self.tag_calls(),
            [{"id": PRODUCT_ID, "tags": [PENDING_CHANNEL_PUBLISH]}],
        )

    def test_an_immediate_publish_is_not_queued_for_catch_up(self):
        with patch.object(
            self.client,
            "run_query",
            self.recording_run_query(
                {
                    "query publications": {"publications": {"nodes": PUBLICATIONS}},
                    "publishablePublish": {
                        "publishablePublish": {"publishable": {}, "userErrors": []}
                    },
                }
            ),
        ):
            self.client.publish_by_product_or_collection_id(PRODUCT_ID)

        # Nothing was left behind, so there is nothing to sweep up later.
        self.assertEqual(self.tag_calls(), [])

    def test_an_immediate_publish_still_reaches_every_channel(self):
        with patch.object(
            self.client,
            "run_query",
            self.recording_run_query(
                {
                    "query publications": {"publications": {"nodes": PUBLICATIONS}},
                    "publishablePublish": {
                        "publishablePublish": {"publishable": {}, "userErrors": []}
                    },
                }
            ),
        ):
            self.client.publish_by_product_or_collection_id(PRODUCT_ID)

        calls = self.publish_calls()
        self.assertEqual(
            [c["input"]["publicationId"] for c in calls],
            [p["id"] for p in PUBLICATIONS],
        )
        self.assertFalse(any("publishDate" in c["input"] for c in calls))


class TestUnpublish(PublicationsTestCase):
    def responses(self, user_errors=()):
        return {
            "query publications": {"publications": {"nodes": PUBLICATIONS}},
            "publishableUnpublish": {
                "publishableUnpublish": {
                    "publishable": {"id": PRODUCT_ID, "title": "JACKET"},
                    "userErrors": list(user_errors),
                }
            },
        }

    def unpublish_calls(self):
        return [
            variables
            for query, variables in self.queries
            if "publishableUnpublish" in query
        ]

    def test_unpublishes_the_named_publications_only(self):
        with patch.object(
            self.client, "run_query", self.recording_run_query(self.responses())
        ):
            res = self.client.unpublish_by_product_or_collection_id(
                PRODUCT_ID, ["Shop", "Point of Sale"]
            )

        self.assertEqual(
            [c["input"]["publicationId"] for c in self.unpublish_calls()],
            [PUBLICATIONS[1]["id"], PUBLICATIONS[2]["id"]],
        )
        self.assertEqual(len(res), 2)

    def test_an_unknown_publication_name_raises(self):
        with patch.object(
            self.client, "run_query", self.recording_run_query(self.responses())
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.client.unpublish_by_product_or_collection_id(
                    PRODUCT_ID, ["Shop", "TikTok"]
                )
        self.assertIn("no such publication(s): TikTok", str(ctx.exception))

    def test_a_user_error_raises(self):
        with patch.object(
            self.client,
            "run_query",
            self.recording_run_query(
                self.responses(user_errors=[{"field": None, "message": "nope"}])
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.client.unpublish_by_product_or_collection_id(PRODUCT_ID, ["Shop"])
        self.assertIn("Failed to unpublish product", str(ctx.exception))


class TestProductPublicationStates(PublicationsTestCase):
    NODES = [
        {
            "isPublished": False,
            "publishDate": "2026-10-14T03:00:00Z",
            "publication": PUBLICATIONS[0],
        },
        {"isPublished": False, "publishDate": None, "publication": PUBLICATIONS[2]},
    ]

    def test_returns_published_and_unpublished_channels(self):
        with patch.object(
            self.client,
            "run_query",
            self.recording_run_query(
                {
                    "query productPublications": {
                        "product": {
                            "id": PRODUCT_ID,
                            "title": "JACKET",
                            "resourcePublicationsV2": {"nodes": self.NODES},
                        }
                    }
                }
            ),
        ):
            states = self.client.product_publication_states(9311361892490)

        self.assertEqual(states, self.NODES)
        # A bare numeric id is turned into a gid.
        self.assertEqual(self.queries[0][1], {"id": PRODUCT_ID})

    def test_a_missing_product_raises(self):
        with patch.object(
            self.client,
            "run_query",
            self.recording_run_query({"query productPublications": {"product": None}}),
        ):
            with self.assertRaises(NoProductsFoundException):
                self.client.product_publication_states(PRODUCT_ID)


if __name__ == "__main__":
    unittest.main()
