import logging

logger = logging.getLogger(__name__)


class Variants:
    def run_variants_bulk_update(self, variables, return_fields: list[str]):
        query = """
        mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                product {
                    id
                }
                productVariants {
                    id
                    %s
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """ % "\n".join(
            return_fields
        )
        res = self.run_query(query, variables)
        if errors := res["productVariantsBulkUpdate"]["userErrors"]:
            raise RuntimeError(f"Product variants update failed: {errors}")
        return res["productVariantsBulkUpdate"]

    def update_variant_attributes(
        self, product_id, variant_id, attribute_names, attribute_values, sku=None
    ):
        assert len(attribute_names) == len(
            attribute_values
        ), "attribute_names and attribute_values must have the same length"

        attribute_names
        variables = {
            "productId": self.sanitize_id(product_id),
            "variants": [
                {
                    "id": self.sanitize_id(variant_id, "ProductVariant"),
                    **{
                        attribute_name: attribute_value
                        for attribute_name, attribute_value in zip(
                            attribute_names, attribute_values
                        )
                    },
                }
            ],
        }
        if sku:
            variables["variants"][0].setdefault("inventoryItem", {})["sku"] = sku
        return self.run_variants_bulk_update(
            variables=variables, return_fields=attribute_names
        )

    def update_variant_sku_by_variant_id(self, product_id, variant_ids, skus):
        assert len(variant_ids) == len(
            skus
        ), "variant_ids and skus must have the same length"
        variables = {
            "productId": product_id,
            "variants": [
                {
                    "id": variant_id,
                    "inventoryItem": {
                        "sku": sku,
                    },
                }
                for variant_id, sku in zip(variant_ids, skus)
            ],
        }
        return self.run_variants_bulk_update(
            variables=variables, return_fields=["inventoryItem { sku }"]
        )

    def update_variant_inventory_track_by_variant_id(
        self, product_id, variant_ids, inventory_track_tfs
    ):
        assert len(variant_ids) == len(
            inventory_track_tfs
        ), "variant_ids and inventory_track_tfs must have the same length"
        variables = {
            "productId": product_id,
            "variants": [
                {
                    "id": variant_id,
                    "inventoryItem": {
                        "tracked": tf,
                    },
                }
                for variant_id, tf in zip(variant_ids, inventory_track_tfs)
            ],
        }
        return self.run_variants_bulk_update(
            variables=variables, return_fields=["inventoryItem { tracked }"]
        )

    def update_variant_barcode_by_sku(self, sku, barcode):
        variant = self.variant_by_sku(sku)
        return self.update_variant_attributes(
            variant["product"]["id"], variant["id"], ["barcode"], [barcode]
        )

    def update_variants_hs_code(self, product_id, hs_code, korean_hs_code):
        variants = self.product_variants_by_product_id(product_id)
        variables = {
            "productId": self.sanitize_id(product_id),
            "variants": [
                {
                    "id": v["id"],
                    "inventoryItem": {
                        "harmonizedSystemCode": hs_code,
                        "countryHarmonizedSystemCodes": [
                            {
                                "countryCode": "KR",
                                "harmonizedSystemCode": korean_hs_code,
                            }
                        ],
                    },
                }
                for v in variants
            ],
        }
        return_fields = [
            """
            inventoryItem {
                harmonizedSystemCode
                countryHarmonizedSystemCodes(first: 5) {
                    edges {
                        node {
                            countryCode
                            harmonizedSystemCode
                        }
                    }
                }
            }
            """
        ]
        return self.run_variants_bulk_update(
            variables=variables, return_fields=return_fields
        )

    def product_variants_bulk_create(self, product_id, variants):
        query = """
        mutation productVariantsBulkCreate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
            productVariantsBulkCreate(productId: $productId, variants: $variants) {
                productVariants {
                    id
                    title
                    selectedOptions {
                        name
                        value
                    }
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {"productId": product_id, "variants": variants}
        res = self.run_query(query, variables)
        if errors := res["productVariantsBulkCreate"]["userErrors"]:
            raise RuntimeError(f"Product variants creation failed: {errors}")
        return res["productVariantsBulkCreate"]["productVariants"]

    def variants_add(
        self,
        product_id,
        skus,
        media_ids,
        variant_media_ids,
        option_names,
        variant_option_valuess,
        prices,
        stocks,
        location_id,
        metafieldss=None,
    ):
        metafieldss = metafieldss or ([[]] * len(skus))
        assert (
            len(variant_option_valuess)
            == len(skus)
            == len(prices)
            == len(stocks)
            == len(metafieldss)
        ), "length of variant attributes do not match"
        for media_id in media_ids or []:
            logger.info(f"Assigning media ID {media_id} to product ID {product_id}")
            self.assign_existing_image_to_products_by_id(media_id, [product_id])
        variants = [
            {
                "optionValues": [
                    {"name": option_value, "optionName": option_name}
                    for option_name, option_value in zip(
                        option_names, variant_option_values
                    )
                ],
                "inventoryItem": {
                    "sku": sku,
                    "tracked": True,
                },
                "mediaId": variant_media_id,
                "price": price,
                "metafields": metafields,
                "inventoryQuantities": {
                    "availableQuantity": stock,
                    "locationId": location_id,
                },
            }
            for variant_option_values, sku, variant_media_id, price, stock, metafields in zip(
                variant_option_valuess,
                skus,
                variant_media_ids,
                prices,
                stocks,
                metafieldss,
            )
        ]
        return self.product_variants_bulk_create(product_id, variants)

    def remove_product_variants(self, product_id, variant_ids):
        query = """
        mutation bulkDeleteProductVariants($productId: ID!, $variantsIds: [ID!]!) {
            productVariantsBulkDelete(productId: $productId, variantsIds: $variantsIds) {
                product {
                    id
                    title
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {
            "productId": self.sanitize_id(product_id),
            "variantsIds": variant_ids,
        }
        res = self.run_query(query, variables)
        if res["productVariantsBulkDelete"]["userErrors"]:
            raise RuntimeError(
                f"Failed to remove variants: {res['productVariantsBulkDelete']['userErrors']}"
            )
        return res
