import re
import unittest

from shopify_client_base.shopify_graphql_client.client import (
    API_VERSION,
    ShopifyGraphqlClient,
)

SOURCE_ID = "gid://shopify/CollectionConditionsSource/1"
TAG_CONDITION_ID = "gid://shopify/CollectionSourceInclusionConditionProductTag/9"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = ShopifyGraphqlClient(shop_name="example-shop", access_token="dummy")
        self.queries = []

    def answer(self, response):
        def run_query(query, variables=None, method="post"):
            self.queries.append((query, variables))
            return response

        self.client.run_query = run_query


class TestApiVersion(ApiTestCase):
    def test_requests_go_to_the_pinned_version(self):
        self.assertEqual(
            self.client.base_url,
            f"https://example-shop.myshopify.com/admin/api/{API_VERSION}/graphql.json",
        )


class TestInventoryMutationsAreIdempotent(ApiTestCase):
    """API 2026-04 rejects these mutations without an @idempotent key."""

    def idempotency_keys(self):
        return [
            re.search(rf'@idempotent\(key: "({UUID})"\)', query).group(1)
            for query, _ in self.queries
        ]

    def test_activate_sends_a_fresh_key_per_call(self):
        self.answer({"inventoryActivate": {"userErrors": [], "inventoryLevel": {}}})
        self.client.activate_inventory_item("gid://x", "gid://y")
        self.client.activate_inventory_item("gid://x", "gid://y", available=3)
        first, second = self.idempotency_keys()
        self.assertNotEqual(first, second)
        self.assertIn("available: $available", self.queries[1][0])

    def test_set_quantities_sends_a_key(self):
        self.answer(
            {
                "inventorySetQuantities": {
                    "userErrors": [],
                    "inventoryAdjustmentGroup": {"id": "1"},
                }
            }
        )
        self.client.inventory_item_id_by_sku = lambda sku: "gid://item"
        self.client.set_inventory_quantity_by_sku_and_location_id("SKU", "gid://y", 5)
        self.assertEqual(len(self.idempotency_keys()), 1)


class TestCollectionConditions(ApiTestCase):
    def test_conditions_source_is_the_single_conditions_source(self):
        collection = {
            "title": "OUTER",
            "sources": [
                {"__typename": "CollectionSubCollectionsSource"},
                {"__typename": "CollectionConditionsSource", "id": SOURCE_ID},
            ],
        }
        self.assertEqual(
            self.client.collection_conditions_source(collection)["id"], SOURCE_ID
        )

    def test_conditions_source_raises_without_one(self):
        with self.assertRaises(RuntimeError):
            self.client.collection_conditions_source({"title": "KNIT", "sources": []})

    def test_update_conditions_swaps_conditions_on_the_source(self):
        self.answer({"collectionUpdate": {"userErrors": [], "collection": {}}})
        new_tag = {
            "productTag": {
                "relation": "TAGGED_WITH",
                "values": ["26PF", "26FW"],
                "matchType": "ANY",
            }
        }
        self.client.collection_update_conditions(
            "123",
            SOURCE_ID,
            conditions_to_create=[new_tag],
            condition_ids_to_delete=[TAG_CONDITION_ID],
            match_type="ALL",
        )
        query, variables = self.queries[0]
        self.assertIn("collectionUpdate(collection: $collection)", query)
        self.assertEqual(
            variables["collection"],
            {
                "id": "gid://shopify/Collection/123",
                "sourcesToUpdate": [
                    {
                        "condition": {
                            "id": SOURCE_ID,
                            "inclusion": {
                                "conditionsToCreate": [new_tag],
                                "conditionsToDelete": [TAG_CONDITION_ID],
                                "matchType": "ALL",
                            },
                        }
                    }
                ],
            },
        )

    def test_update_conditions_raises_on_user_errors(self):
        self.answer(
            {"collectionUpdate": {"userErrors": [{"message": "bad"}], "collection": None}}
        )
        with self.assertRaises(RuntimeError):
            self.client.collection_update_conditions("123", SOURCE_ID)


class TestCollectionsReadSources(ApiTestCase):
    def test_collections_query_reads_sources_not_rule_set(self):
        self.answer({"collections": {"nodes": []}})
        self.client.collections_by_query("title:'KNIT'")
        query = self.queries[0][0]
        self.assertIn("sources {", query)
        self.assertNotIn("ruleSet", query)

    def test_collection_by_handle_returns_the_single_match(self):
        self.answer({"collections": {"nodes": [{"id": "1", "handle": "jacket"}]}})
        self.assertEqual(self.client.collection_by_handle("jacket")["id"], "1")
        self.assertEqual(self.queries[0][1], {"query_string": "handle:'jacket'"})

    def test_collection_by_handle_raises_unless_one_match(self):
        self.answer({"collections": {"nodes": []}})
        with self.assertRaises(RuntimeError):
            self.client.collection_by_handle("jacket")


if __name__ == "__main__":
    unittest.main()
