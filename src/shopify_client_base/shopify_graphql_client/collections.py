import json
import logging

logger = logging.getLogger(__name__)


class CollectionQueries:

    def products_by_collection_id(self, collection_id):
        query = """
        query ProductsByCollection ($id: ID!) {
            collection(id: $id) {
                handle
                products(first: 100) {
                    nodes {
                        title,
                        id,
                        status
                        variants (first:10) {
                            nodes {
                                id
                                title
                                displayName
                                sku
                                price
                                compareAtPrice
                                inventoryQuantity
                                selectedOptions {
                                    name
                                    value
                                }
                                image {
                                    id
                                    url
                                }
                            }
                        }                    }
                }
            }
        }
        """
        variables = {"id": self.sanitize_id(collection_id, "Collection")}
        res = self.run_query(query, variables)
        return res["collection"]["products"]["nodes"]

    def collections_by_query(self, query_string):
        query = """
            query collections($query_string: String!) {
                collections(first: 200, query: $query_string) {
                    nodes {
                        id
                        handle
                        title
                        sortOrder
                        templateSuffix
                        updatedAt
                        image {
                            id
                            url
                        }
                        products(first:200) {
                            nodes {
                                id
                                title
                            }
                        }
                        ruleSet {
                            rules {
                                column
                                relation
                                condition
                            }
                            appliedDisjunctively
                        }
                    }
                }
            }
            """
        variables = {"query_string": query_string}
        res = self.run_query(query, variables)
        return res["collections"]["nodes"]

    def collection_by_title(self, title):
        res = self.collections_by_query(f"title:'{title}'")
        if len(res) != 1:
            raise RuntimeError(
                f"{'Multiple' if res else 'No'} collections found for {title}: {res}"
            )
        return res[0]

    def collections_by_title_prefix(self, title_prefix):
        return self.collections_by_query(f"title:{title_prefix}*")

    def collection_id_by_title(self, title):
        collection = self.collection_by_title(title)
        return collection["id"]

    def collection_create_by_query_and_publish(self, query, variables):
        res = self.run_query(query, variables)
        if errors := res["collectionCreate"]["userErrors"]:
            raise RuntimeError(f"Collection creation failed: {errors}")
        res = res["collectionCreate"]["collection"]
        self.publish_by_product_or_collection_id(res["id"])
        return res

    def collection_create_by_product_ids(self, collection_title, product_ids):
        query = """
        mutation createCollection($input: CollectionInput!) {
            collectionCreate(input: $input) {
                collection {
                    id
                    products(first: 30) {
                        nodes {
                            id
                            title
                        }
                    }
                }
                userErrors {
                    message
                    field
                }
            }
        }
        """
        variables = {
            "input": {
                "title": collection_title,
                "products": [
                    self.sanitize_id(product_id) for product_id in product_ids
                ],
            }
        }
        return self.collection_create_by_query_and_publish(query, variables)

    def collection_add_products(self, collection_id, product_ids):
        query = """
        mutation collectionAddProducts($id: ID!, $productIds: [ID!]!) {
            collectionAddProducts(id: $id, productIds: $productIds) {
                collection {
                    id
                    title
                    products(first: 10) {
                        nodes {
                            id
                            title
                        }
                    }
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {
            "id": self.sanitize_id(collection_id, prefix="Collection"),
            "productIds": [self.sanitize_id(product_id) for product_id in product_ids],
        }
        res = self.run_query(query, variables)
        if errors := res["collectionAddProducts"]["userErrors"]:
            raise RuntimeError(f"Failed to add products to the collection: {errors}")
        return res

    def collection_create_by_rule_set(self, collection_title, rule_set: dict):
        """
        by tag:
        rule_set = {
            "appliedDisjunctively": False,
            "rules": [
                {
                    "column": "TAG",
                    "relation": "EQUALS",
                    "condition": "Summer2024"
                }
            ]

        }
        """
        query = """
        mutation createCollection($input: CollectionInput!) {
            collectionCreate(input: $input) {
                collection {
                    id
                    products(first: 30) {
                        nodes {
                            id
                            title
                        }
                    }
                }
                userErrors {
                    message
                    field
                }
            }
        }
        """
        variables = {
            "input": {
                "title": collection_title,
                "ruleSet": rule_set,
            }
        }
        return self.collection_create_by_query_and_publish(query, variables)

    def collection_create_by_tag(self, collection_title, tag):
        rule_set = {
            "appliedDisjunctively": False,
            "rules": [{"column": "TAG", "relation": "EQUALS", "condition": tag}],
        }
        return self.collection_create_by_rule_set(collection_title, rule_set)

    def collection_create_by_metafield_value(
        self, collection_title, namespace, key, value
    ):
        metafield_id = self.metafield_id_by_namespace_and_key(namespace, key)
        rule_set = {
            "appliedDisjunctively": False,
            "rules": [
                {
                    "column": "PRODUCT_METAFIELD_DEFINITION",
                    "conditionObjectId": metafield_id,
                    "relation": "EQUALS",
                    "condition": value,
                }
            ],
        }
        return self.collection_create_by_rule_set(collection_title, rule_set)

    def update_collection_image(self, collection_id, src, alt_text=None):
        """Set a collection's image from a URL. Shopify fetches and copies it."""
        query = """
        mutation updateCollectionImage($input: CollectionInput!) {
            collectionUpdate(input: $input) {
                collection {
                    id
                    title
                    handle
                    image { url altText width height }
                }
                userErrors {
                    message
                    field
                }
            }
        }
        """
        image = {"src": src}
        if alt_text:
            image["altText"] = alt_text
        variables = {
            "input": {
                "id": self.sanitize_id(collection_id, "Collection"),
                "image": image,
            }
        }
        res = self.run_query(query, variables)
        if errors := res["collectionUpdate"]["userErrors"]:
            raise RuntimeError(f"Collection image update failed: {errors}")
        return res["collectionUpdate"]["collection"]

    def collection_update_rule_set(self, collection_id, rule_set):
        query = """
        mutation updateCollection($input: CollectionInput!) {
            collectionUpdate(input: $input) {
                collection {
                    id
                    title
                    handle
                    ruleSet {
                        rules {
                            column
                            relation
                            condition
                        }
                        appliedDisjunctively
                    }
                }
                userErrors {
                    message
                    field
                }
            }
        }
        """
        variables = {
            "input": {
                "id": collection_id,
                "ruleSet": rule_set,
            }
        }
        res = self.run_query(query, variables)
        if errors := res["collectionUpdate"]["userErrors"]:
            raise RuntimeError(f"Collection update failed: {errors}")
        return res["collectionUpdate"]["collection"]
