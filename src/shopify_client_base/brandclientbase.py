import json
import logging
import pathlib
import pandas as pd
from shopify_client_base.sanity_checks import SanityChecks
from shopify_client_base.client import Client
from shopify_client_base.credentials import credentials

logger = logging.getLogger(__name__)

NEW_INDICATOR_REMOVAL_PENDING_PREFIX = "pending-new-indicator-removal__"


class BrandClientBase(Client, SanityChecks):
    SHOPNAME = ""
    VENDOR = ""
    LOCATIONS = []
    NEW_PRODUCT_TAG = "New Arrival"
    # Stores whose theme shows a "NEW" badge from the custom.badges metafield
    # in addition to the NEW_PRODUCT_TAG tag.
    USES_NEW_BADGE_METAFIELD = False

    def __init__(
        self,
        product_sheet_start_row=None,
        remove_existing_new_product_indicators=None,
        products_season_tag=None,
    ):
        assert self.SHOPNAME, "SHOPNAME must be set in subclass"
        assert self.VENDOR, "VENDOR must be set in subclass"
        assert self.LOCATIONS, "LOCATIONS must be set in subclass"
        self.product_sheet_start_row = product_sheet_start_row
        self.remove_existing_new_product_indicators = (
            remove_existing_new_product_indicators
        )
        if products_season_tag:
            assert " " not in products_season_tag, "Prefer no whitespaces in a tag"
        self.products_season_tag = products_season_tag
        self.shop_credentials = self.load_credentials(self.SHOPNAME)
        super().__init__(
            shop_name=self.shop_credentials.shop_name,
            access_token=self.shop_credentials.access_token,
            google_credential_path=self.shop_credentials.google_credential_path,
            sheet_id=self.shop_credentials.google_sheet_id,
        )

    def load_credentials(self, shop_name):
        """Override to read credentials from somewhere other than .env."""
        return credentials(shop_name)

    def product_attr_column_map(self):
        raise NotImplementedError

    def option1_attr_column_map(self):
        raise NotImplementedError

    def option2_attr_column_map(self):
        raise NotImplementedError

    def product_inputs_by_sheet_name(self, sheet_name, handle_suffix=None):
        self.drive_link_cache = {}  # repopulate drive link cache
        return self.to_product_inputs(
            self.sheet_id,
            sheet_name,
            self.product_sheet_start_row,
            product_attr_column_map=self.product_attr_column_map(),
            option1_attr_column_map=self.option1_attr_column_map(),
            option2_attr_column_map=self.option2_attr_column_map(),
            handle_suffix=handle_suffix,
        )

    def get_tags_from_product_input(self, product_input):
        tags = product_input.get("tags") or []
        if isinstance(tags, str):
            return [t.strip() for t in tags.split(",") if t.strip()]
        return list(tags)

    def get_tags(self, product_input, additional_tags):
        return ",".join(
            [self.products_season_tag, self.NEW_PRODUCT_TAG]
            + self.get_tags_from_product_input(product_input)
            + (additional_tags or [])
        )

    def get_size_field(self, product_input):
        raise NotImplementedError

    def get_description_html(self, product_input):
        raise NotImplementedError

    def process_product_input(self, product_input, additional_tags=None):
        logger.info(f'processing {product_input.get("handle", product_input["title"])}')
        res = {}
        res["create_product"] = self.create_product_by_product_input(
            product_input,
            self.VENDOR,
            description_html=self.get_description_html(product_input),
            tags=self.get_tags(product_input, additional_tags),
        )
        res["enable_and_activate_inventory"] = (
            self.enable_and_activate_inventory_by_product_input(
                product_input, self.LOCATIONS
            )
        )
        res["process_product_images"] = self.process_product_images(product_input)
        res["update_stock"] = self.update_stock(product_input)
        return res

    def post_process_product_input(self, process_product_input_res, product_input):
        pass

    def add_variants_from_product_input(self, product_input):
        return super().add_variants_from_product_input(
            product_input, location_names=self.LOCATIONS
        )

    def update_stocks(self, product_inputs):
        return super().update_stocks(product_inputs, self.LOCATIONS[0])

    def update_stock(self, product_input):
        return super().update_stock(product_input, self.LOCATIONS[0])

    def merge_products_as_variants_by_title(self, product_title):
        return super().merge_products_as_variants_by_title(
            product_title, location_names=self.LOCATIONS
        )

    def merge_products_as_variants(
        self, products, new_product_title, location_names=None
    ):
        return super().merge_products_as_variants(
            products=products,
            new_product_title=new_product_title,
            location_names=location_names or self.LOCATIONS,
        )

    def has_open_orders(self, product_title):
        products = self.products_by_title(product_title)
        for product in products:
            for variant in product["variants"]["nodes"]:
                orders = self.orders_by_sku(sku=variant["sku"], open_only=True)
                if orders:
                    return True

    def merge_existing_products_as_variants_by_title(self):
        product_titles = self.product_titles_with_multiple_products()
        for product_title in product_titles:
            if self.has_open_orders(product_title):
                logger.info(
                    f"skipping merging products with title {product_title} having open orders"
                )
            else:
                logger.info(f"merging products with title {product_title} as variants")
                self.merge_products_as_variants_by_title(product_title)

    def remove_existing_new_proudct_tags(self):
        products = self.products_by_tag(self.NEW_PRODUCT_TAG)
        for product in products:
            logger.info(f"removing {self.NEW_PRODUCT_TAG} tag from {product['title']}")
            tags = [t for t in product["tags"] if t != self.NEW_PRODUCT_TAG]
            self.update_product_tags(product_id=product["id"], tags=",".join(tags))

    def remove_existing_new_badges(self):
        if self.USES_NEW_BADGE_METAFIELD:
            products = self.products_by_metafield("custom", "badges", "NEW")
            for product in products:
                logger.info(
                    f"Removing 'NEW' badge from {product['title']} (id: {product['id']})"
                )
                badges = [
                    m for m in product["metafields"]["nodes"] if m["key"] == "badges"
                ][0]["value"]
                badges = [b for b in json.loads(badges) if b != "NEW"]
                self.update_badges_metafield(product["id"], badges)

    def products_with_new_indicators(self):
        products = {p["id"]: p for p in self.products_by_tag(self.NEW_PRODUCT_TAG)}
        if self.USES_NEW_BADGE_METAFIELD:
            for p in self.products_by_metafield("custom", "badges", "NEW"):
                products.setdefault(p["id"], p)
        return list(products.values())

    @staticmethod
    def new_indicator_removal_pending_tag(season_tag):
        return f"{NEW_INDICATOR_REMOVAL_PENDING_PREFIX}{season_tag}"

    @staticmethod
    def season_tag_from_pending_tag(tag):
        if not tag.startswith(NEW_INDICATOR_REMOVAL_PENDING_PREFIX):
            return None
        return tag[len(NEW_INDICATOR_REMOVAL_PENDING_PREFIX) :]

    def pending_new_indicator_tags(self, product):
        return [
            t
            for t in product["tags"]
            if t.startswith(NEW_INDICATOR_REMOVAL_PENDING_PREFIX)
        ]

    def products_pending_new_indicator_removal(self):
        return self.products_by_tag(f"{NEW_INDICATOR_REMOVAL_PENDING_PREFIX}*")

    def schedule_new_indicator_removal(self):
        assert self.products_season_tag, "products_season_tag must be set"
        pending_tag = self.new_indicator_removal_pending_tag(self.products_season_tag)
        for product in self.products_with_new_indicators():
            if (
                self.products_season_tag in product["tags"]
                or self.pending_new_indicator_tags(product)
            ):
                continue
            logger.info(
                f"queueing {product['title']} to lose its new indicators "
                f"when {self.products_season_tag} goes live ({pending_tag})"
            )
            self.add_product_tags(product["id"], pending_tag)

    def season_tag_is_live(self, season_tag, now=None):
        products = self.products_by_tag(season_tag)
        if not products:
            logger.warning(f"{self.shop_name}: no products tagged {season_tag!r}")
            return False
        return any(
            self.online_store_is_live(
                self.product_publication_states(p["id"]),
                launch_datetime=self.product_launch_datetime(p["id"]),
                now=now,
            )[0]
            for p in products
        )

    def sweep_new_indicator_removals(self, dry_run=False, now=None):
        live_by_season_tag = {}
        removed = []
        for product in self.products_pending_new_indicator_removal():
            pending_tags = self.pending_new_indicator_tags(product)
            season_tags = [
                season_tag
                for season_tag in (
                    self.season_tag_from_pending_tag(t) for t in pending_tags
                )
                if season_tag
            ]
            if not season_tags:
                continue
            due_season_tags = []
            for season_tag in season_tags:
                if season_tag not in live_by_season_tag:
                    live_by_season_tag[season_tag] = self.season_tag_is_live(
                        season_tag, now=now
                    )
                if live_by_season_tag[season_tag]:
                    due_season_tags.append(season_tag)
            if not due_season_tags:
                continue
            logger.info(
                f"{self.shop_name}: {'would remove' if dry_run else 'removing'} "
                f"new indicators from {product['title']} "
                f"({due_season_tags[0]} is live)"
            )
            removed.append(product["title"])
            if dry_run:
                continue
            if self.USES_NEW_BADGE_METAFIELD:
                badges = self.product_metafield_by_product_id(
                    product["id"], "custom", "badges"
                )
                if badges and "NEW" in json.loads(badges["value"]):
                    self.update_badges_metafield(
                        product["id"],
                        [b for b in json.loads(badges["value"]) if b != "NEW"],
                    )
            self.remove_product_tags(
                product["id"], [self.NEW_PRODUCT_TAG, *pending_tags]
            )
        return removed

    def pre_process_product_inputs(self, product_inputs, scheduled_time=None):
        assert (
            self.remove_existing_new_product_indicators is not None
        ), f"remove_existing_new_product_indicators must be set explicitly."
        if self.remove_existing_new_product_indicators:
            if scheduled_time:
                self.schedule_new_indicator_removal()
            else:
                self.remove_existing_new_proudct_tags()
                self.remove_existing_new_badges()

    def process_product_inputs(
        self, product_inputs, additional_tags, scheduled_time=None
    ):
        for product_input in product_inputs:
            res = self.process_product_input(
                product_input, additional_tags=additional_tags
            )
            self.post_process_product_input(res, product_input)
            self.activate_and_publish_by_product_id(
                product_id=res["create_product"]["id"], scheduled_time=scheduled_time
            )

    def colors_from_product_inputs(self, product_inputs):
        if "options" in product_inputs[0]:
            for option in ["カラー", "Color"]:
                if option in product_inputs[0]["options"][0].keys():
                    break
            else:
                return False
            return set([o[option] for pi in product_inputs for o in pi["options"]])

    def post_process_product_inputs(self, product_inputs):
        if colors := self.colors_from_product_inputs(product_inputs):
            if current_color_swatch_config := self.current_color_swatch_config():
                configured_colors = [
                    l.split(": ")[0] for l in current_color_swatch_config.split("\n")
                ]
                missings = [color for color in colors if color not in configured_colors]
                if missings:
                    logger.warning(f"add these colors to color swatch config:")
                    for color in missings:
                        print(color)
            else:
                logger.warning("no color swatch config found")

    def process_sheet_to_products(
        self,
        sheet_name,
        additional_tags=None,
        handle_suffix=None,
        restart_at_product_title=None,
        scheduled_time=None,
        product_inputs_filter_func=None,
    ):
        assert (
            self.products_season_tag is not None
        ), "products_season_tag must be set explicitly"
        product_inputs = self.product_inputs_by_sheet_name(sheet_name, handle_suffix)
        if not restart_at_product_title:
            i = 0
        else:
            self.remove_existing_new_product_indicators = False
            if restart_at_product_title == "DO NOT CREATE":
                i = len(product_inputs)
            else:
                for i, pi in enumerate(product_inputs):
                    if pi["title"] == restart_at_product_title:
                        break
                else:
                    raise RuntimeError(
                        f"Product with name {restart_at_product_title} not found in sheet {sheet_name}"
                    )
        product_inputs = product_inputs[i:]

        if product_inputs_filter_func:
            product_inputs = list(filter(product_inputs_filter_func, product_inputs))

        self.pre_process_product_inputs(
            product_inputs, scheduled_time=scheduled_time
        )
        self.process_product_inputs(
            product_inputs,
            additional_tags=additional_tags,
            scheduled_time=scheduled_time,
        )
        self.post_process_product_inputs(product_inputs)

    def get_skus_from_excel(
        self,
        file_path,
        header_row=None,
        sku_column=1,
        discount_column=None,
    ):
        """
        ExcelファイルからSKUを取得する（オプションで割引率も取得可能）

        Args:
            file_path: Excelファイルのパス（文字列またはpathlib.Path）
            sku_column: SKUが含まれる列のインデックス（0ベース、デフォルト: 1 = B列）
            discount_column: 割引率が含まれる列のインデックス（0ベース、Noneの場合は取得しない）
            header_row: ヘッダー行のインデックス（0ベース、Noneの場合はheader=Noneで読み込む）

        Returns:
            discount_columnがNoneの場合: SKUのリスト
            discount_columnが指定されている場合: (SKU, 割引%)のタプルのリスト
        """
        # pathlib.Pathオブジェクトの場合は文字列に変換
        if isinstance(file_path, pathlib.Path):
            file_path = str(file_path)

        df = pd.read_excel(file_path, header=header_row)

        # 必要な列を取得
        columns_to_get = [sku_column]
        if discount_column is not None:
            columns_to_get.append(discount_column)
        column_names = [df.columns[i] for i in columns_to_get]
        df = df[column_names]
        df = df[df[column_names[0]].notna()]  # SKU列がNaNでない行のみ
        if len(column_names) == 1:
            return df[column_names[0]].tolist()
        return df.values.tolist()
