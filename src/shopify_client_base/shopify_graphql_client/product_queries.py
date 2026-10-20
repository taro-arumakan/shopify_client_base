import collections
import logging
import string
from shopify_client_base.exceptions import (
    MultipleProductsFoundException,
    NoProductsFoundException,
    MultipleVariantsFoundException,
    NoVariantsFoundException,
)

logger = logging.getLogger(__name__)

additional_punctuation_chars = "‘’“” "
punctuation_chrs = (
    "".join([s for s in string.punctuation if s not in ["-"]])
    + additional_punctuation_chars
)
punctuation_translator = str.maketrans("", "", punctuation_chrs)


class ProductQueries:
    """
    A class to handle GraphQL queries related to products in Shopify, inherited by the ShopifyGraphqlClient class.
    """

    def remove_punctuations(self, s):
        parts = s.lower().split(" ")
        parts = [part.translate(punctuation_translator) for part in parts]
        return "-".join(parts)

    def product_title_to_handle(self, title, handle_suffix=None):
        if handle_suffix:
            title += f" {handle_suffix}"
        return self.remove_punctuations(title)

    def products_by_query(
        self,
        query_string="",
        additional_fields=None,
        sort_key="TITLE",
        reverse=False,
    ):
        additional_fields_str = (
            f"\n{'\n'.join(additional_fields)}" if additional_fields else ""
        )

        query = f"""
        query productsByQueryPaginated($query_string: String!, $after: String, $sortKey: ProductSortKeys, $reverse: Boolean, $first: Int!) {{
          products(first: $first, query: $query_string, sortKey: $sortKey, reverse: $reverse, after: $after) {{
            pageInfo {{
              hasNextPage
              endCursor
            }}
            nodes {{
              id
              title
              handle
              status
              tags{additional_fields_str}
              metafields(first: 10) {{
                nodes {{
                  id
                  namespace
                  key
                  value
                }}
              }}
              variants(first: 30) {{
                nodes {{
                  id
                  title
                  displayName
                  sku
                  price
                  compareAtPrice
                  inventoryQuantity
                  selectedOptions {{
                    name
                    value
                  }}
                  image {{
                    id
                    url
                  }}
                  metafields(first: 10) {{
                    nodes {{
                      id
                      namespace
                      key
                      value
                    }}
                  }}
                  product {{
                    id
                  }}
                }}
              }}
              media(first: 100) {{
                nodes {{
                  ... on MediaImage {{
                    id
                    image {{
                      id
                      url
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
        """

        variables = {
            "query_string": query_string,
            "sortKey": sort_key,
            "reverse": reverse,
        }
        all_products = self.run_paginated_query(
            query=query, variables=variables, data_key="products"
        )
        return all_products

    def products_by_title(self, title, *args, **kwargs):
        products = self.products_by_query(
            f"title:'{title.replace("'", "\\'")}'", *args, **kwargs
        )
        if len(products) == 0:
            raise NoProductsFoundException(f"No products found for {title}")
        return products

    def product_ids_by_title(self, title):
        res = self.products_by_title(title)
        return [r["id"] for r in res]

    def products_by_tag(self, tag, *args, **kwargs):
        return self.products_by_query(f"tag:'{tag}'", *args, **kwargs)

    def products_by_metafield(
        self, metafield_namespace, metafield_key, metafield_value, *args, **kwargs
    ):
        if type(metafield_value) is str:
            metafield_value = f'"{metafield_value}"'
        return self.products_by_query(
            f"metafields.{metafield_namespace}.{metafield_key}:{metafield_value}",
            *args,
            **kwargs,
        )

    def product_by_query(self, query_string, filter_archived=True, *args, **kwargs):
        products = self.products_by_query(query_string, *args, **kwargs)
        if filter_archived:
            products = [p for p in products if p["status"] != "ARCHIVED"]
        if len(products) != 1:
            raise (
                MultipleProductsFoundException if products else NoProductsFoundException
            )(
                f"{'Multiple' if products else 'No'} products found for {query_string}: {products}"
            )
        return products[0]

    def product_by_id(self, identifier, *args, **kwargs):
        return self.products_by_query(
            f"id:'{identifier.rsplit('/', 1)[-1]}'", *args, **kwargs
        )[0]

    def product_by_title(self, title, *args, **kwargs):
        return self.product_by_query(
            f'title:"{title.replace('"', '\\"')}"', *args, **kwargs
        )

    def product_id_by_title(self, title):
        return self.product_by_title(title)["id"]

    def product_by_handle(self, handle, *args, **kwargs):
        return self.product_by_query(f"handle:'{handle}'", *args, **kwargs)

    def product_id_by_handle(self, handle):
        return self.product_by_handle(handle)["id"]

    def product_variants_by_query(self, query_string, filter_archived=True):
        query = f"""
        query productVariantsByQueryPaginated($query_string: String!, $after: String, $first: Int!) {{
          productVariants(first: $first, query: $query_string, after: $after) {{
            pageInfo {{
              hasNextPage
              endCursor
            }}
            nodes {{
              id
              title
              displayName
              sku
              barcode
              price
              compareAtPrice
              inventoryQuantity
              media (first:5){{
                nodes{{
                  id
                  ... on MediaImage {{
                    image{{
                      url
                    }}
                  }}
                }}
              }}
              selectedOptions {{
                name
                value
              }}
              product {{
                id
                title
                status
              }}
            }}
          }}
        }}
        """
        res = self.run_paginated_query(
            query=query,
            variables={"query_string": query_string},
            data_key="productVariants",
        )
        if filter_archived:
            # Shopify API ignores product_status filter on productVariants query
            logger.debug("Filtering ARCHIVED products' variants")
            res = [r for r in res if r["product"]["status"] != "ARCHIVED"]
        return res

    def product_variants_by_product_id(self, product_id):
        product_id = self.sanitize_id(product_id)
        product_id = product_id.rsplit("/", 1)[-1]
        return self.product_variants_by_query(
            f"product_id:{product_id}", filter_archived=False
        )

    def product_variants_by_tag(self, tag, **kwargs):
        return self.product_variants_by_query(f"tag:'{tag}'", **kwargs)

    def variant_by_variant_id(self, variant_id):
        variant_id = self.sanitize_id(variant_id, "ProductVariant").rsplit("/", 1)[-1]
        if (
            len(
                res := self.product_variants_by_query(
                    f"id:{variant_id}", filter_archived=False
                )
            )
            != 1
        ):
            raise (MultipleVariantsFoundException if res else NoVariantsFoundException)(
                f"{'Multiple' if res else 'No'} variants found for {variant_id}: {res}"
            )
        return res[0]

    def variants_by_skus(self, skus, active_only=True):
        skus = list(skus)
        if not skus:
            return []
        query = " OR ".join(f"sku:'{sku}'" for sku in skus)
        res = self.product_variants_by_query(query, filter_archived=active_only)
        missing = sorted(set(skus) - {v["sku"] for v in res})
        if missing:
            raise NoVariantsFoundException(
                f"No variants found for {len(missing)} SKU(s): {missing}"
            )
        return res

    def variants_by_barcodes(self, barcodes, active_only=True):
        barcodes = list(barcodes)
        if not barcodes:
            return []
        query = " OR ".join(f"barcode:'{barcode}'" for barcode in barcodes)
        res = self.product_variants_by_query(query, filter_archived=active_only)
        missing = sorted(set(barcodes) - {v["barcode"] for v in res})
        if missing:
            raise NoVariantsFoundException(
                f"No variants found for {len(missing)} barcode(s): {missing}"
            )
        return res

    def variant_by_barcode(self, barcode, active_only=True):
        res = self.variants_by_barcodes([barcode], active_only=active_only)
        if len(res) != 1:
            if res and active_only:
                logger.info("Filtering non-active products' variants")
                res = [r for r in res if r["product"]["status"] == "ACTIVE"]
            if len(res) != 1:
                raise (
                    MultipleVariantsFoundException if res else NoVariantsFoundException
                )(f"{'Multiple' if res else 'No'} variants found for {barcode}: {res}")
        return res[0]

    def variant_by_sku(self, sku, active_only=True):
        if len(res := self.variants_by_skus([sku], active_only=active_only)) != 1:
            if res and active_only:
                logger.info("Filtering non-active products' variants")
                res = [r for r in res if r["product"]["status"] == "ACTIVE"]
            if len(res) != 1:
                raise (
                    MultipleVariantsFoundException if res else NoVariantsFoundException
                )(f"{'Multiple' if res else 'No'} variants found for {sku}: {res}")
        return res[0]

    def variant_id_by_sku(self, sku, active_only=True):
        return self.variant_by_sku(sku, active_only=active_only)["id"]

    def product_id_by_variant_id(self, variant_id):
        variant = self.variant_by_variant_id(variant_id)
        return variant["product"]["id"]

    def product_id_by_sku(self, sku, active_only=True):
        variant = self.variant_by_sku(sku, active_only=active_only)
        return variant["product"]["id"]

    def products_by_collection_handle(self, collection_handle):
        query = """
            query getCollectionByHandle($collection_handle: String!) {
                collectionByHandle(handle: $collection_handle) {
                    id
                    title
                    products(first: 100) {
                        nodes {
                            id
                            title
                            tags
                        }
                    }
                }
            }
        """
        variables = {"collection_handle": collection_handle}
        res = self.run_query(query, variables)
        res = res["collectionByHandle"]["products"]["nodes"]
        assert (
            len(res) < 100
        ), f"Too many products found for {collection_handle}: {len(res)}"
        return res

    def product_titles_with_multiple_products(self):
        """
        Product titles with multiple products i.e. merge candidates.
        Titles are returned in lower.
        """
        products = self.products_by_query("status:'ACTIVE'")
        counts_by_title = collections.Counter(p["title"].lower() for p in products)
        return [title for title, count in counts_by_title.items() if count > 1]
