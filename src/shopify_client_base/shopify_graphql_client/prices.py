import copy
import logging

logger = logging.getLogger(__name__)


class Prices:
    def update_variant_prices_by_variant_ids(
        self, product_id, variant_ids, prices, compare_at_prices
    ):
        assert (
            len(variant_ids) == len(prices) == len(compare_at_prices)
        ), "target variants and prices must have the same length"

        variables = {
            "productId": product_id,
            "variants": [
                {"id": variant_id, "price": price, "compareAtPrice": compare_at_price}
                for variant_id, price, compare_at_price in zip(
                    variant_ids, prices, compare_at_prices
                )
            ],
        }
        return self.run_variants_bulk_update(
            variables=variables, return_fields=["price", "compareAtPrice"]
        )

    def update_variant_prices_by_skus(
        self, product_id, skus, prices, compare_at_prices
    ):
        variant_ids = [self.variant_id_by_sku(sku) for sku in skus]
        return self.update_variant_prices_by_variant_ids(
            product_id=product_id,
            variant_ids=variant_ids,
            prices=prices,
            compare_at_prices=compare_at_prices,
        )

    def update_variants_by_product_id_prices_by_dict(
        self, variants_by_product_id, new_prices_by_variant_id, testrun=True
    ):
        if testrun:
            logger.info("\n\nTest run mode - no prices will be updated\n\n")
        for product_id, variants in variants_by_product_id.items():
            variant_ids, new_prices, compare_at_prices = [], [], []
            for v in variants:
                variant_ids.append(v["id"])
                new_prices.append(new_prices_by_variant_id[v["id"]])
                compare_at_prices.append(v["compareAtPrice"] or v["price"])
                logger.info(
                    f"Updating price of {v['displayName']} from {int(v["price"])} to {new_prices[-1]}"
                )
            if not testrun:
                self.update_variant_prices_by_variant_ids(
                    product_id=product_id,
                    variant_ids=variant_ids,
                    prices=new_prices,
                    compare_at_prices=compare_at_prices,
                )

    def update_variant_prices_by_dict(
        self, variants, new_prices_by_variant_id, testrun=True
    ):
        variants_by_product_id = {}
        for v in variants:
            variants_by_product_id.setdefault(v["product"]["id"], []).append(v)
        return self.update_variants_by_product_id_prices_by_dict(
            variants_by_product_id,
            new_prices_by_variant_id=new_prices_by_variant_id,
            testrun=testrun,
        )

    def update_product_prices_by_dict(
        self, products, new_prices_by_variant_id, testrun=True
    ):
        variants_by_product_id = {p["id"]: p["variants"]["nodes"] for p in products}
        return self.update_variants_by_product_id_prices_by_dict(
            variants_by_product_id,
            new_prices_by_variant_id=new_prices_by_variant_id,
            testrun=testrun,
        )

    def revert_variant_prices(self, variants, testrun=True):
        new_prices_by_variant_id = {v["id"]: int(v["compareAtPrice"]) for v in variants}
        return self.update_variant_prices_by_dict(
            variants, new_prices_by_variant_id=new_prices_by_variant_id, testrun=testrun
        )

    def revert_product_prices(self, products, testrun=True):
        new_prices_by_variant_id = {
            v["id"]: int(v["compareAtPrice"])
            for p in products
            for v in p["variants"]["nodes"]
        }
        return self.update_product_prices_by_dict(
            products, new_prices_by_variant_id=new_prices_by_variant_id, testrun=testrun
        )
