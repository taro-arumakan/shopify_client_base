import collections
import logging
from shopify_client_base.exceptions import (
    NoProductsFoundException,
    MultipleProductsFoundException,
)

logger = logging.getLogger(__name__)


class SanityChecks:
    # Sheet fields a client reads unconditionally while creating a product. A
    # blank cell drops the key from the product input, so a missing one only
    # surfaces once the product already exists — the run then stops partway
    # through it. Declared per client; fails the sanity check up front.
    REQUIRED_PRODUCT_INPUT_FIELDS = ()

    # Same idea, for fields the creation path skips over when they are blank:
    # a missing one is usually an oversight, but it can also be a product that
    # legitimately has nothing to say, so it only gets a warning.
    EXPECTED_PRODUCT_INPUT_FIELDS = ()

    def blank_fields(self, product_inputs, fields):
        res = []
        for product_input in product_inputs:
            for field in fields:
                value = product_input.get(field)
                if value is None or not str(value).strip():
                    res.append(f"Blank {field} for {product_input['title']}")
        return res

    def check_required_fields(self, product_inputs):
        return self.blank_fields(product_inputs, self.REQUIRED_PRODUCT_INPUT_FIELDS)

    def check_expected_fields(self, product_inputs):
        res = self.blank_fields(product_inputs, self.EXPECTED_PRODUCT_INPUT_FIELDS)
        for r in res:
            logger.warning(r)
        return res

    def check_size_field(self, product_inputs, raise_on_error=True):
        res = []
        for product_input in product_inputs:
            try:
                size_text = self.get_size_field(product_input)
            except Exception as e:
                m = f"Error formatting size text for {product_input['title']}: {e}"
                if raise_on_error:
                    logger.error(m)
                    raise
                else:
                    res.append(m)
            else:
                if raise_on_error:
                    print(size_text)
        return res

    def check_description(self, product_inputs, raise_on_error=True):
        res = []
        for product_input in product_inputs:
            try:
                description_html = self.get_description_html(product_input)
            except Exception as e:
                m = f"Error formatting description for {product_input['title']}: {e}"
                if raise_on_error:
                    logger.error(m)
                    raise
                else:
                    res.append(m)
            else:
                if raise_on_error:
                    print(description_html)
        return res

    def check_metafields(self, product_inputs):
        return []

    def check_images_link(self, product_inputs):
        res = []
        for product_input in product_inputs:
            try:
                drive_ids, skuss = self.populate_drive_ids_and_skuss(product_input)
                for drive_id, skus in zip(drive_ids, skuss):
                    if drive_id != "no image":
                        image_details = self.get_drive_image_details(drive_id)
                        if not image_details:
                            res.append(
                                f"Missing or inaccessible drive image: {product_input['title']} {skus} - {drive_id}"
                            )
                    else:
                        logger.warning(f"going to have no image: {skus}")
            except Exception as e:
                res.append(
                    f"Error checking drive images for {product_input['title']}: {e}"
                )
        return res

    def check_sku_duplicates(self, product_inputs):
        skus = sum([self.product_input_to_skus(pi) for pi in product_inputs], [])
        counts_by_sku = collections.Counter(skus)
        counts_by_sku = {
            sku: count for sku, count in counts_by_sku.items() if count > 1
        }
        if counts_by_sku:
            m = "\n".join(
                ": ".join(map(str, [sku, count]))
                for sku, count in counts_by_sku.items()
            )
            raise RuntimeError(f"Duplicate SKUs found:\n{m}")

    def check_existing_skus(self, product_inputs):
        res = []
        skus = sum((self.product_input_to_skus(pi) for pi in product_inputs), [])
        query = query = " OR ".join(f"sku:'{sku}'" for sku in skus)
        exists = self.product_variants_by_query(query)
        for r in exists:
            res.append(f"Existing SKU found: {r['product']['title']} - {r['sku']}")
        return res

    def check_existing_products(self, product_inputs):
        res = []
        for pi in product_inputs:
            try:
                checking = "handle" if "handle" in pi else "title"
                func = getattr(self, f"product_by_{checking}")
                param = pi[checking]
                func(param)
            except NoProductsFoundException:
                pass
            except MultipleProductsFoundException:
                res.append(f"Existing product found by {checking}: {param}")
            else:
                res.append(f"Existing product found by {checking}: {param}")
        return res

    def sanity_check_product_inputs(self, product_inputs, pre_rewrite=False):
        assert (
            self.remove_existing_new_product_indicators is not None
        ), f"remove_existing_new_product_indicators must be set explicitly."
        assert (
            self.products_season_tag is not None
        ), "products_season_tag must be set explicitly"
        res = []
        try:
            self.check_sku_duplicates(product_inputs)
        except RuntimeError as e1:
            logger.error(e1)
            res.append(e1)
        res += self.check_existing_skus(product_inputs)
        res += self.check_existing_products(product_inputs)
        res += self.check_images_link(product_inputs)
        res += self.check_required_fields(product_inputs)
        # Warning only: the check logs these, they never reach res.
        self.check_expected_fields(product_inputs)
        res += self.check_size_field(product_inputs, raise_on_error=False)
        res += self.check_metafields(product_inputs)
        if not pre_rewrite:
            res += self.check_description(product_inputs, raise_on_error=False)
        for r in res:
            logger.error(r)

        if res:
            raise RuntimeError("Failed sanity check")

    def sanity_check_sheet(
        self,
        sheet_name,
        handle_suffix=None,
        product_inputs_filter_func=None,
        pre_rewrite=False,
    ):
        product_inputs = self.product_inputs_by_sheet_name(
            sheet_name, handle_suffix=handle_suffix
        )
        if product_inputs_filter_func:
            product_inputs = list(filter(product_inputs_filter_func, product_inputs))

        logger.info(
            f"Sanity checking {len(product_inputs)} products from sheet {sheet_name}"
        )
        return self.sanity_check_product_inputs(product_inputs, pre_rewrite=pre_rewrite)
