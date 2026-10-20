import logging

logger = logging.getLogger(__name__)


class ProductVariantsToProducts:
    """
    Defines workflow and required queries to convert product variants to standalone products. Inherited by the ShopifyGraphqlClient class.
    """

    def product_variants_to_products(
        self, product_title, option_name="カラー", new_status="DRAFT"
    ):
        product = self.product_by_title(product_title)
        product_id = product["id"]
        product_handle = product["handle"]
        variants = self.product_variants_by_product_id(product_id)
        color_options = set(
            [
                so["value"]
                for v in variants
                for so in v["selectedOptions"]
                if so["name"] == option_name
            ]
        )

        new_product_ids = []
        for color_option in color_options:
            res = self.duplicate_product(product_id, product_title, True, new_status)
            new_product = res["productDuplicate"]["newProduct"]
            new_product_id = new_product["id"]
            logger.info(f"Duplicated product ID: {new_product_id}")
            new_product_ids.append(new_product_id)
            new_product_handle = "-".join(
                [product_handle, "-".join(color_option.lower().split(" "))]
            )
            color_option_id = [
                o["id"] for o in new_product["options"] if o["name"] == option_name
            ]
            assert (
                len(color_option_id) == 1
            ), f"{'Multiple' if color_option_id else 'No'} option {option_name} for {new_product_id}"
            color_option_id = color_option_id[0]
            new_variants = new_product["variants"]["nodes"]
            variant_ids_to_keep = [
                v["id"]
                for v in new_variants
                if any(
                    so["name"] == option_name and so["value"] == color_option
                    for so in v["selectedOptions"]
                )
            ]
            variant_ids_to_remove = [
                v["id"] for v in new_variants if v["id"] not in variant_ids_to_keep
            ]

            self.remove_unrelated_medias_by_variant_ids_to_keep(
                new_product_id, variant_ids_to_keep
            )
            self.remove_product_variants(new_product_id, variant_ids_to_remove)
            self.delete_product_options(new_product_id, [color_option_id])
            self.update_product_handle(new_product_id, new_product_handle)
            self.update_variation_value_metafield(new_product_id, color_option)
            self.update_product_theme_template(new_product_id, "variants-as-products")

        for new_product_id in new_product_ids:
            self.update_variation_products_metafield(new_product_id, new_product_ids)

    def duplicate_product(
        self, product_id, new_title, include_images=False, new_status="DRAFT"
    ):
        query = """
        mutation DuplicateProduct($productId: ID!, $newTitle: String!, $includeImages: Boolean, $newStatus: ProductStatus) {
            productDuplicate(productId: $productId, newTitle: $newTitle, includeImages: $includeImages, newStatus: $newStatus) {
                newProduct {
                    id
                    handle
                    title
                    vendor
                    productType
                    variants(first: 30) {
                        nodes {
                            id
                            title
                            selectedOptions {
                                name
                                value
                            }
                        }
                    }
                    options {
                        id
                        name
                        values
                    }
                }
                imageJob {
                    id
                    done
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        if product_id.isnumeric():
            product_id = f"gid://shopify/Product/{product_id}"
        variables = {
            "productId": product_id,
            "newTitle": new_title,
            "includeImages": include_images,
            "newStatus": new_status,
        }
        res = self.run_query(query, variables)
        if res["productDuplicate"]["userErrors"]:
            raise RuntimeError(
                f"Failed to duplicate the product: {res['productDuplicate']['userErrors']}"
            )
        return res

    def delete_product_options(self, product_id, option_ids):
        query = """
        mutation deleteOptions($productId: ID!, $options: [ID!]!) {
            productOptionsDelete(productId: $productId, options: $options, strategy: DEFAULT) {
                userErrors {
                    field
                    message
                    code
                }
                deletedOptionsIds
                product {
                    id
                    options {
                        id
                        name
                        values
                        position
                        optionValues {
                            id
                            name
                            hasVariants
                        }
                    }
                }
            }
        }
        """
        variables = {"productId": self.sanitize_id(product_id), "options": option_ids}
        res = self.run_query(query, variables)
        if res["productOptionsDelete"]["userErrors"]:
            raise RuntimeError(
                f"Failed to update the tags: {res['productOptionsDelete']['userErrors']}"
            )
        return res
