import datetime
import logging


logger = logging.getLogger(__name__)


class MergeProductsAsVariants:

    @staticmethod
    def _media_id_by_url(url, medias):
        for media in medias:
            if url == media["image"]["url"]:
                return media["id"]

    def add_product_to_product_as_variants(
        self,
        target_product_id,
        product_id_to_add,
        location_name,
        variant_option_names=None,
    ):
        right = self.product_by_id(product_id_to_add)

        skus = [v["sku"] for v in right["variants"]["nodes"]]
        media_ids = [v["id"] for v in right["media"]["nodes"]]
        variant_media_ids = [
            self._media_id_by_url(v["image"]["url"], right["media"]["nodes"])
            for v in right["variants"]["nodes"]
        ]
        prices = [v["price"] for v in right["variants"]["nodes"]]
        option_names = variant_option_names or [
            o["name"] for o in right["variants"]["nodes"][0]["selectedOptions"]
        ]
        metafieldss = [
            [m for m in v["metafields"]["nodes"] if m["namespace"] == "custom"]
            for v in right["variants"]["nodes"]
        ]
        option_valuess = []
        stocks = []
        for variant in right["variants"]["nodes"]:
            option_values = []
            for n in option_names:
                option_values.append(
                    next(
                        (
                            o["value"]
                            for o in variant["selectedOptions"]
                            if o["name"] == n
                        ),
                        None,
                    )
                )
            option_valuess.append(option_values)
            stocks.append(variant["inventoryQuantity"])

        location_id = self.location_id_by_name(location_name)
        self.variants_add(
            target_product_id,
            skus,
            media_ids,
            variant_media_ids,
            option_names,
            option_valuess,
            prices,
            stocks,
            location_id,
            metafieldss=metafieldss,
        )

    def archive_product_handle(self, product):
        self.update_product_attribute(
            product["id"],
            "handle",
            f"archived-{datetime.date.today():%Y%m%d}-{product['handle']}",
        )

    def archive_product(self, product, new_product_handle=None):
        logger.info(f"archiving {product['handle']}")
        self.update_product_status(product["id"], "ARCHIVED")
        for variant in product["variants"]["nodes"]:
            archived_sku = f"archived-{datetime.date.today():%Y%m%d}-{variant['sku']}"
            self.update_variant_attributes(
                product_id=product["id"],
                variant_id=variant["id"],
                attribute_names=[],
                attribute_values=[],
                sku=archived_sku,
            )
            self.disable_inventory_tracking_by_sku(archived_sku)
        self.archive_product_handle(product)
        if new_product_handle:
            logger.info(f"Redirecting {product['handle']} to {new_product_handle}")
            self.create_url_redirect(
                f"/products/{product['handle']}", f"/products/{new_product_handle}"
            )

    def merge_products_as_variants(self, products, new_product_title, location_names):
        """
        Total selling stock quantity gets added to the first in the list of location_names.
        Pass the primary location first e.g. ["Archivépke Warehouse", "Envycube Warehouse"].
        """
        assert len(products) >= 2, f"need at least 2 products to merge: {products}"
        products = list(reversed(products))
        logger.info(f"Merging products:\n{"\n".join(p['handle'] for p in products)}")
        merged = self.duplicate_product(
            products[0]["id"],
            new_product_title,
            include_images=True,
            new_status="DRAFT",
        )
        merged = merged["productDuplicate"]["newProduct"]
        new_product_id = merged["id"]
        new_product_handle = merged["handle"]
        variant_option_names = [
            o["name"] for o in merged["variants"]["nodes"][0]["selectedOptions"]
        ]
        logger.info(
            f"Merging into a new product {new_product_id}: {new_product_handle}"
        )
        rights = products[1:]
        for right in rights:
            self.add_product_to_product_as_variants(
                new_product_id, right["id"], location_names[0], variant_option_names
            )

        logger.info(f"Archiving and untracking the old products. Adding redirects")
        for product in products:
            self.archive_product(product, new_product_handle)

        logger.info(f"Activating the new product")
        self.enable_and_activate_inventory_by_product_id(new_product_id, location_names)
        self.activate_and_publish_by_product_id(new_product_id)

    def merge_products_as_variants_by_title(self, product_title, location_names):
        products = self.products_by_query(
            f"title:'{product_title}' AND status:'ACTIVE'", sort_key="CREATED_AT"
        )
        self.merge_products_as_variants(
            products=products,
            new_product_title=product_title,
            location_names=location_names,
        )
