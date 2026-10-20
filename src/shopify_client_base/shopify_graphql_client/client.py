import logging
import requests
from shopify_client_base.shopify_graphql_client.analytics import Analytics
from shopify_client_base.shopify_graphql_client.archive_and_remove_variant import (
    ArchiveAndRemoveVariant,
)
from shopify_client_base.shopify_graphql_client.article import Article
from shopify_client_base.shopify_graphql_client.collections import CollectionQueries
from shopify_client_base.shopify_graphql_client.customers import Customers
from shopify_client_base.shopify_graphql_client.inventory import Inventory
from shopify_client_base.shopify_graphql_client.medias import Medias
from shopify_client_base.shopify_graphql_client.merge_products_as_variants import (
    MergeProductsAsVariants,
)
from shopify_client_base.shopify_graphql_client.menus import Menus
from shopify_client_base.shopify_graphql_client.metafields import Metafields
from shopify_client_base.shopify_graphql_client.orders import Orders
from shopify_client_base.shopify_graphql_client.online_store import OnlineStore
from shopify_client_base.shopify_graphql_client.prices import Prices
from shopify_client_base.shopify_graphql_client.product_attributes import (
    ProductAttributes,
)
from shopify_client_base.shopify_graphql_client.product_create import ProductCreate
from shopify_client_base.shopify_graphql_client.product_queries import ProductQueries
from shopify_client_base.shopify_graphql_client.product_variants_to_products import (
    ProductVariantsToProducts,
)
from shopify_client_base.shopify_graphql_client.publication_catch_up import (
    PublicationCatchUp,
)
from shopify_client_base.shopify_graphql_client.publications import Publications
from shopify_client_base.shopify_graphql_client.shipping import Shipping
from shopify_client_base.shopify_graphql_client.theme_image_rotation import ThemeImageRotation
from shopify_client_base.shopify_graphql_client.variants import Variants

logger = logging.getLogger(__name__)


class ShopifyGraphqlClient(
    Analytics,
    ArchiveAndRemoveVariant,
    Article,
    CollectionQueries,
    Customers,
    Inventory,
    Medias,
    Menus,
    Orders,
    OnlineStore,
    Prices,
    ProductAttributes,
    ProductCreate,
    ProductQueries,
    ProductVariantsToProducts,
    Metafields,
    MergeProductsAsVariants,
    PublicationCatchUp,
    Publications,
    Shipping,
    ThemeImageRotation,
    Variants,
):
    def __init__(self, shop_name, access_token):
        self.shop_name = shop_name
        self.access_token = access_token
        self.base_url = (
            f"https://{shop_name}.myshopify.com/admin/api/2026-01/graphql.json"
        )

    def sanitize_id(self, identifier, prefix="Product"):
        if isinstance(identifier, int) or identifier.isnumeric():
            return f"gid://shopify/{prefix}/{identifier}"
        elif identifier.startswith("gid://"):
            assert (
                f"/{prefix}/" in identifier
            ), f"non-{prefix.lower()} gid was provided: {identifier}"
            return identifier
        else:
            raise ValueError(f"Invalid ID format: {identifier}")

    def run_query(self, query, variables=None, method="post"):
        headers = {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }
        data = {"query": query, "variables": variables}
        response = requests.post(self.base_url, headers=headers, json=data)
        res = response.json()
        if errors := res.get("errors"):
            raise RuntimeError(
                f"Error running the query: {errors}\n\n{query}\n\n{variables}"
            )
        if warnings := [
            r.get("warnings") for r in res.get("extensions", {}).get("search", [])
        ]:
            raise RuntimeError(
                f"Warning running the query: {warnings}\n\n{query}\n\n{variables}"
            )
        return res["data"]

    def run_paginated_query(self, query, variables, data_key):
        """
        query should accept `$first: Int!` argument. refer to ProductQueries.products_by_query.
        """
        all_items = []
        vars = variables.copy()
        vars["first"] = 200
        has_next_page = True
        while has_next_page:
            res = self.run_query(query, vars)
            data = res[data_key]
            all_items.extend(data["nodes"])
            page_info = data["pageInfo"]
            has_next_page = page_info["hasNextPage"]
            if has_next_page:
                vars["after"] = page_info["endCursor"]
        return all_items

    def option_value_by_key(self, selected_options: list, option_key="カラー"):
        for option in selected_options:
            if option["name"] == option_key:
                return option["value"]
