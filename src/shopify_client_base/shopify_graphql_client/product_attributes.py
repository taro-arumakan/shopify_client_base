import logging

logger = logging.getLogger(__name__)


class ProductAttributes:
    """
    Product attributes management queries. Inherited by the ShopifyGraphqlClient class.
    """

    def product_description_by_product_id(self, product_id):
        product_id = self.sanitize_id(product_id)
        query = (
            """
        query {
            product(id: "%s") {
                id
                descriptionHtml
            }
        }
        """
            % product_id
        )
        res = self.run_query(query)
        return res["product"]["descriptionHtml"]

    def update_product_attribute(self, product_id, attribute_name, attribute_value):
        return self.update_product_attributes(
            product_id, [attribute_name], [attribute_value]
        )

    def update_product_attributes(self, product_id, attribute_names, attribute_values):
        query = """
        mutation productUpdate($input: ProductInput!) {
            productUpdate(input: $input) {
                product {
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
            attribute_names
        )
        variables = {
            "input": {
                "id": self.sanitize_id(product_id),
            }
        }
        variables["input"].update(
            {
                attribute_name: attribute_value
                for attribute_name, attribute_value in zip(
                    attribute_names, attribute_values
                )
            }
        )
        res = self.run_query(query, variables)
        if errors := res["productUpdate"]["userErrors"]:
            raise RuntimeError(f"Failed to update {attribute_names}: {errors}")
        return res

    def update_product_title(self, product_id, title):
        return self.update_product_attribute(product_id, "title", title)

    def update_product_tags(self, product_id, tags):
        if isinstance(tags, list):
            tags = ",".join(tags)
        return self.update_product_attribute(product_id, "tags", tags)

    def add_product_tags(self, product_id, tags):
        """Add tags without disturbing the others.

        Not update_product_tags: that goes through productUpdate, which replaces
        the whole tag list, so using it to add one would drop every season and
        category tag the product already carries. Shopify de-duplicates.
        """
        if isinstance(tags, str):
            tags = [tags]
        query = """
        mutation productTagsAdd($id: ID!, $tags: [String!]!) {
            tagsAdd(id: $id, tags: $tags) {
                node { id }
                userErrors { field message }
            }
        }
        """
        product_id = self.sanitize_id(product_id)
        res = self.run_query(query, {"id": product_id, "tags": tags})
        if errors := res["tagsAdd"]["userErrors"]:
            raise RuntimeError(f"add_product_tags failed for {product_id}: {errors}")
        return res["tagsAdd"]

    def remove_product_tags(self, product_id, tags):
        """Remove tags, leaving the rest of the product's tags alone."""
        if isinstance(tags, str):
            tags = [tags]
        query = """
        mutation productTagsRemove($id: ID!, $tags: [String!]!) {
            tagsRemove(id: $id, tags: $tags) {
                node { id }
                userErrors { field message }
            }
        }
        """
        product_id = self.sanitize_id(product_id)
        res = self.run_query(query, {"id": product_id, "tags": tags})
        if errors := res["tagsRemove"]["userErrors"]:
            raise RuntimeError(f"remove_product_tags failed for {product_id}: {errors}")
        return res["tagsRemove"]

    def update_product_description(self, product_id, desc):
        return self.update_product_attribute(product_id, "descriptionHtml", desc)

    def update_product_handle(self, product_id, handle):
        return self.update_product_attribute(product_id, "handle", handle)

    def update_product_status(self, product_id, status):
        assert status in [
            "ACTIVE",
            "DRAFT",
            "ARCHIVED",
            "UNLISTED",
        ], f"Invalid status: {status}"
        return self.update_product_attribute(product_id, "status", status)

    def update_product_theme_template(self, product_id, template_suffix):
        return self.update_product_attribute(
            product_id, "templateSuffix", template_suffix
        )

    def upload_and_assign_description_images_to_shopify(
        self, product_id, local_paths, dummy_product_id, shopify_url_prefix
    ):
        """
        upload images to shopify, generate HTML consists of the links of uploaded files and assign it to the product description.
        in order to keep the uploaded images, they are assigned to a dummy product.
        """
        local_paths = [
            local_path for local_path in local_paths if not local_path.endswith(".psd")
        ]
        mime_types = [
            f"image/{local_path.rsplit('.', 1)[-1].lower()}"
            for local_path in local_paths
        ]
        staged_targets = self.generate_staged_upload_targets(local_paths, mime_types)
        logger.info(f"generated staged upload targets: {len(staged_targets)}")
        self.upload_images_to_shopify_parallel(staged_targets, local_paths, mime_types)
        description = "\n".join(
            self.image_htmlfragment_in_description(
                local_path.rsplit("/", 1)[-1], i, shopify_url_prefix
            )
            for i, local_path in enumerate(local_paths)
        )
        self.assign_images_to_product(
            [target["resourceUrl"] for target in staged_targets],
            alts=[local_path.rsplit("/", 1)[-1] for local_path in local_paths],
            product_id=dummy_product_id,
        )
        return self.update_product_description(product_id, description)

    def sanitize_image_name(self, image_name):
        return (
            image_name.replace(" ", "_")
            .replace("[", "")
            .replace("]", "_")
            .replace("(", "")
            .replace(")", "")
        )

    def image_htmlfragment_in_description(
        self, image_name, sequence, shopify_url_prefix
    ):
        animation_classes = [
            "reveal_tran_bt",
            "reveal_tran_rl",
            "reveal_tran_lr",
            "reveal_tran_tb",
        ]
        animation_class = animation_classes[sequence % 4]
        return f'<p class="{animation_class}"><img src="{shopify_url_prefix}/files/{self.sanitize_image_name(image_name)}" alt=""></p>'
