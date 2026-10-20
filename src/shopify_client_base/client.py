import datetime
import logging
import os
import pathlib
import smtplib
from email.message import EmailMessage
from shopify_client_base.shopify_graphql_client import ShopifyGraphqlClient
from shopify_client_base.google_api_interface.interface import GoogleApiInterface

logger = logging.getLogger(__name__)


def send_smtp_email(subject: str, body: str, to_addrs: list[str], subtype="plain"):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    from_addr = os.getenv("SMTP_FROM_ADDR")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body, subtype=subtype)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)


class Client(ShopifyGraphqlClient, GoogleApiInterface):
    def __init__(
        self,
        shop_name,
        access_token,
        google_credential_path,
        sheet_id=None,
    ):
        ShopifyGraphqlClient.__init__(
            self, shop_name=shop_name, access_token=access_token
        )
        GoogleApiInterface.__init__(
            self, google_credential_path=google_credential_path, sheet_id=sheet_id
        )

    def add_product_images(
        self, product_id, drive_id, local_dir, filename_prefix, remove_existings=False
    ):
        local_paths = self.drive_images_to_local(
            drive_id, local_dir, filename_prefix=filename_prefix
        )
        res = self.upload_and_assign_images_to_product(
            product_id, local_paths, remove_existings
        )
        return res

    def process_product_images(
        self, product_input, local_dir=None, filename_prefix=None
    ):
        if handle := product_input.get("handle"):
            product_id = self.product_id_by_handle(handle)
        else:
            product_id = self.product_id_by_title(product_input["title"])

        local_dir = (
            local_dir
            or f"{pathlib.Path.home()}/Downloads/{self.shop_name}_{datetime.date.today():%Y%m%d}/"
        )
        filename_prefix = filename_prefix or f"upload_{datetime.date.today():%Y%m%d}"

        drive_links, skuss = self.populate_drive_ids_and_skuss(product_input)
        ress = []
        for index, (drive_id, skus) in enumerate(zip(drive_links, skuss)):
            if drive_id == "no image":
                ress.append([])
            else:
                res = self.add_product_images(
                    product_id,
                    drive_id,
                    local_dir,
                    filename_prefix=f"{filename_prefix}_{skus[0]}",
                    remove_existings=index == 0,
                )
                ress.append(res)
                if res[-1]["productCreateMedia"]["media"]:
                    variant_media_id = res[-1]["productCreateMedia"]["media"][0]["id"]
                    logger.info(f"assigning media {variant_media_id} to {skus}")
                    ress.append(
                        self.assign_image_to_skus(product_id, variant_media_id, skus)
                    )
                else:
                    logger.error(f"\n\n!!! missing images: {skus} !!!\n\n")
        return ress

    def create_product_by_product_input(
        self, product_input, vendor, description_html, tags
    ):
        logger.info(f'creating {product_input["title"]}')
        options = self.populate_option_dicts(product_input)
        if any(o["option_values"] for o in options):
            res = self.product_create(
                title=product_input["title"],
                handle=product_input.get("handle"),
                description_html=description_html,
                vendor=vendor,
                tags=tags,
                option_dicts=options,
            )
        else:
            res = self.product_create_default_variant(
                title=product_input["title"],
                description_html=description_html,
                handle=product_input.get("handle"),
                vendor=vendor,
                tags=tags,
                price=product_input["price"],
                sku=product_input["sku"],
            )
        return res

    def create_product_and_activate_inventory(
        self, product_input, vendor, description_html, tags, location_names
    ):
        res = self.create_product_by_product_input(
            product_input, vendor, description_html, tags
        )
        res2 = self.enable_and_activate_inventory_by_product_input(
            product_input, location_names
        )
        return (res, res2)

    def product_input_to_skus(self, product_input):
        options = self.populate_option_dicts(product_input)
        return [o["sku"] for o in options]

    def enable_and_activate_inventory_by_product_input(
        self, product_input, location_names
    ):
        skus = self.product_input_to_skus(product_input)
        return self.enable_and_activate_inventories_by_skus(skus, location_names)

    def get_sku_stocks_map(self, product_input):
        variants_info = self.get_variants_level_info(product_input)
        return {variant["sku"]: variant.get("stock", 0) for variant in variants_info}

    def update_stocks(self, product_inputs, location_name):
        logger.info("updating inventory")
        location_id = self.location_id_by_name(location_name)
        sku_stock_map = {}
        for product_input in product_inputs:
            sku_stock_map.update(self.get_sku_stocks_map(product_input))
        return [
            self.set_inventory_quantity_by_sku_and_location_id(sku, location_id, stock)
            for sku, stock in sku_stock_map.items()
        ]

    def update_stock(self, product_input, location_name):
        location_id = self.location_id_by_name(location_name)
        for sku, stock in self.get_sku_stocks_map(product_input).items():
            logger.info(f"updating inventory of {sku} to {stock} in {location_name}")
            self.set_inventory_quantity_by_sku_and_location_id(sku, location_id, stock)

    def product_id_by_product_input(self, product_input):
        if "handle" in product_input:
            func = self.product_id_by_handle
            param = product_input["handle"]
        else:
            func = self.product_id_by_title
            param = product_input["title"]
        return func(param)

    def publish_products(self, product_inputs, scheduled_time=None):
        for pi in product_inputs:
            product_id = self.product_id_by_product_input(pi)
            self.activate_and_publish_by_product_id(
                product_id, scheduled_time=scheduled_time
            )

    def replace_images_by_skus(
        self, skus, folder_id, local_dir=None, filename_prefix=None
    ):
        medias = self.medias_by_sku(skus[0])
        existing_ids = [m["id"] for m in medias]
        logger.info(f"going to replace images of {skus} with {folder_id}")

        local_dir = (
            local_dir
            or f"{pathlib.Path.home()}/Downloads/{self.shop_name}_{datetime.date.today():%Y%m%d}/"
        )
        filename_prefix = (
            filename_prefix or f"upload_{datetime.date.today():%Y%m%d}_{skus[0]}"
        )

        local_paths = self.drive_images_to_local(
            folder_id,
            local_dir,
            filename_prefix=filename_prefix,
        )
        file_names = [path.rsplit("/", 1)[-1] for path in local_paths]
        mime_types = [f"image/{path.rsplit('.', 1)[-1]}" for path in local_paths]
        staged_targets = self.generate_staged_upload_targets(file_names, mime_types)
        logger.info(f"generated staged upload targets: {len(staged_targets)}")
        self.upload_images_to_shopify_parallel(staged_targets, local_paths, mime_types)

        product_id = self.product_id_by_sku(skus[0])
        logger.info(
            f"Images uploaded for {product_id}, going to remove existing and assign."
        )
        if medias:
            logger.info(f"removing media ids: {existing_ids}")
            self.remove_product_media_by_product_id(product_id, existing_ids)
        logger.info(f"adding medias to {product_id}")
        self.assign_images_to_product(
            [target["resourceUrl"] for target in staged_targets],
            alts=file_names,
            product_id=product_id,
        )
        logger.info(f"adding a media to {skus}")
        uploaded_variant_media = self.media_by_product_id_by_file_name(
            product_id, file_names[0]
        )
        self.assign_image_to_skus(product_id, uploaded_variant_media["id"], skus)

    def segment_options_list_by_key_option(self, option_dicts):
        """
        group the flat options list to list of lists by the same first option i.e. color

        [{'option_values': {'カラー': 'PINK', 'サイズ': '2'}, 'price': 35200, 'sku': 'ALV-90154-PK-2', 'stock': 2},
         {'option_values': {'カラー': 'PINK', 'サイズ': '3'}, 'price': 35200, 'sku': 'ALV-90154-PK-3', 'stock': 2},
         {'option_values': {'カラー': 'PINK', 'サイズ': '4'}, 'price': 35200, 'sku': 'ALV-90154-PK-4', 'stock': 2},
         {'option_values': {'カラー': 'INK BLACK', 'サイズ': '2'}, 'price': 35200, 'sku': 'ALV-90154-BK-2', 'stock': 2},
         {'option_values': {'カラー': 'INK BLACK', 'サイズ': '3'}, 'price': 35200, 'sku': 'ALV-90154-BK-3', 'stock': 2},
         {'option_values': {'カラー': 'INK BLACK', 'サイズ': '4'}, 'price': 35200, 'sku': 'ALV-90154-BK-4', 'stock': 2}]
         becomes

        [[{'option_values': {'カラー': 'PINK', 'サイズ': '2'}, 'price': 35200, 'sku': 'ALV-90154-PK-2', 'stock': 2},
          {'option_values': {'カラー': 'PINK', 'サイズ': '3'}, 'price': 35200, 'sku': 'ALV-90154-PK-3', 'stock': 2},
          {'option_values': {'カラー': 'PINK', 'サイズ': '4'}, 'price': 35200, 'sku': 'ALV-90154-PK-4', 'stock': 2}],
         [{'option_values': {'カラー': 'INK BLACK', 'サイズ': '2'}, 'price': 35200, 'sku': 'ALV-90154-BK-2', 'stock': 2},
          {'option_values': {'カラー': 'INK BLACK', 'サイズ': '3'}, 'price': 35200, 'sku': 'ALV-90154-BK-3', 'stock': 2},
          {'option_values': {'カラー': 'INK BLACK', 'サイズ': '4'}, 'price': 35200, 'sku': 'ALV-90154-BK-4', 'stock': 2}]]
        """
        res = {}
        key_attr = list(option_dicts[0]["option_values"].keys())[0]
        for option in option_dicts:
            res.setdefault(option["option_values"][key_attr], []).append(option)
        return list(res.values())

    def add_variants_from_product_input(self, product_input, location_names):
        optionss = self.segment_options_list_by_key_option(
            self.populate_option_dicts(product_input)
        )
        drive_links, skuss = self.populate_drive_ids_and_skuss(product_input)
        product_id = self.product_id_by_title(product_input["title"])
        for drive_link, skus, options in zip(drive_links, skuss, optionss):
            logger.info(f"  processing sku: {skus} - {drive_link}")
            res = self.add_product_images(
                product_id,
                drive_link,
                f"{pathlib.Path.home()}/Downloads/{self.shop_name}_{datetime.date.today():%Y%m%d}/",
                f"upload_{datetime.date.today():%Y%m%d}_{skus[0]}_",
            )
            new_media_ids = [m["id"] for m in res[-1]["productCreateMedia"]["media"]]
            self.variants_add(
                product_id=product_id,
                skus=skus,
                media_ids=[],
                variant_media_ids=[new_media_ids[0]] * len(options),
                option_names=options[0]["option_values"].keys(),
                variant_option_valuess=[
                    option["option_values"].values() for option in options
                ],
                prices=[option["price"] for option in options],
                stocks=[option["stock"] for option in options],
                location_id=self.location_id_by_name(location_names[0]),
            )
        self.enable_and_activate_inventory_by_product_id(
            product_id, location_names=location_names
        )

    def send_email(self, subject: str, body: str, to_addrs: list[str], subtype="plain"):
        send_smtp_email(subject, body, to_addrs, subtype=subtype)

    def _nl_to_br(self, s):
        return s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
