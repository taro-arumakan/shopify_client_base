import datetime
import logging
import re
import string
import gspread

logger = logging.getLogger(__name__)


class GoogleSheetsApiInterface:

    def __init__(self):
        self.drive_link_cache = {}

    def to_product_inputs(
        self,
        sheet_id,
        sheet_title,
        start_row,
        product_attr_column_map,
        option1_attr_column_map=None,
        option2_attr_column_map=None,
        handle_suffix=None,
        row_filter_func=None,
    ):
        def update_list(target_list, column_map, row, row_num):
            for i, (k, ci) in enumerate((column_map or {}).items()):
                if (
                    value := self.get_cell_value(
                        row, ci, k, row_num, sheet_id, sheet_title
                    )
                ) not in [
                    None,
                    "",
                ]:  # allow int zero as such.
                    if i == 0:
                        if not target_list or target_list[-1].get(k) != value:
                            target_list.append({k: value})
                    else:
                        target_list[-1][k] = value

        rows = self.worksheet_rows(sheet_id, sheet_title)
        res = []
        for index, row in enumerate(rows[start_row:]):
            if row_filter_func and not row_filter_func(row):
                continue
            sheet_row_num = index + start_row + 1
            update_list(res, product_attr_column_map, row, sheet_row_num)
            if handle_suffix is not None and "handle" not in res[-1]:
                res[-1]["handle"] = self.product_title_to_handle(
                    title=res[-1]["title"], handle_suffix=handle_suffix
                )
            if option1_attr_column_map:
                update_list(
                    res[-1].setdefault("options", []),
                    option1_attr_column_map,
                    row,
                    sheet_row_num,
                )
            if option2_attr_column_map:
                update_list(
                    res[-1]["options"][-1].setdefault("options", []),
                    option2_attr_column_map,
                    row,
                    sheet_row_num,
                )
        return res

    def get_cell_value(
        self, row, column_index, column_name, row_num, sheet_id, sheet_title
    ):
        v = row[column_index]
        if v or v == 0:
            if column_name in ["release_date"] and isinstance(v, int):
                v = str(datetime.date(1899, 12, 30) + datetime.timedelta(days=v))
            elif column_name in ["price", "stock", "compare_at_price"]:
                if isinstance(v, str):
                    v = (
                        v.replace("¥", "")
                        .replace(",", "")
                        .replace(" ", "")
                        .replace("￥", "")
                    )
                assert (
                    isinstance(v, (int, float)) or v.isnumeric()
                ), f"expected int for {column_name}, got {type(v)}: {v}"
                v = int(v)
            elif column_name in ["sku", "product_number", "barcode"]:
                v = str(v).strip()
            elif column_name == "drive_link":
                v = str(v).strip()
                if v == "なし":
                    v = "no image"
                if all([v, v != "no image", not v.startswith("http")]):
                    v = self.get_richtext_link(sheet_title, row_num, column_index)
            elif column_name in ["weight"]:
                assert isinstance(
                    v, (int, float)
                ), f"expected int or float for {column_name}, got {type(v)}: {v}"
            elif column_name in ["Size", "size", "サイズ"]:
                assert isinstance(
                    v, (str, int)
                ), f"expected str or int for {column_name}, got {type(v)}: {v}"
                v = str(v).strip()
            else:
                assert isinstance(
                    v, str
                ), f"expected str for {column_name}, got {type(v)}: {v}"
                v = v.replace("\u200b", "")
                if self.should_remove_empty_characters(column_name):
                    v = " ".join(v.strip().split())
                else:
                    v = v.strip()
        return v

    def should_remove_empty_characters(self, column_name):
        return all(
            s not in column_name
            for s in [
                "description",
                "material",
                "product_care",
                "size_text",
                "image",
                "product_remarks",
            ]
        )

    def get_sheet_index_by_title(self, sheet_id, sheet_title):
        worksheet = self.gspread_client.open_by_key(sheet_id)
        for meta in worksheet.fetch_sheet_metadata()["sheets"]:
            if meta["properties"]["title"] == sheet_title:
                return meta["properties"]["index"]
        raise RuntimeError(f"Did not find a sheet named {sheet_title}")

    def get_link(self, spreadsheet_id, sheet_title, row, row_num, column_num):
        link = row[column_num]
        if all([link, link != "no image", not link.startswith("http")]):
            link = self.get_richtext_link(sheet_title, row_num, column_num)
        return link

    def populate_drive_link_cache(self, sheet_title, column_index):
        # Use the Google Sheets API directly for rich text data. Cache range to avoid API call quota.
        range_notation = f"{sheet_title}!{string.ascii_uppercase[column_index]}1:{string.ascii_uppercase[column_index]}"
        response = (
            self.sheets_service.spreadsheets()
            .get(
                spreadsheetId=self.sheet_id,
                ranges=range_notation,
                fields="sheets(data(rowData(values)))",
            )
            .execute()
        )
        row_data = response["sheets"][0]["data"][0]["rowData"]
        for row_index, row in enumerate(row_data):
            if values := row.get("values"):
                values = values[0]
                if "chipRuns" in values:
                    hyperlink = values["chipRuns"][0]["chip"]["richLinkProperties"][
                        "uri"
                    ]
                else:
                    hyperlink = values.get("hyperlink")
                if hyperlink:
                    self.drive_link_cache[row_index + 1] = hyperlink
        if not self.drive_link_cache:
            raise RuntimeError(
                f"No drive links populated parsing range {range_notation}. Check if the column index is correct."
            )

    def get_richtext_link(self, sheet_title, row_num, column_index):
        if not self.drive_link_cache:
            self.populate_drive_link_cache(sheet_title, column_index)
        return self.drive_link_cache.get(row_num)

    def drive_link_to_id(self, link):
        res = (
            link.strip()
            .rsplit("/", 1)[-1]
            .replace("open?id=", "")
            .replace("?usp=drive_link", "")
            .replace("&usp=drive_link", "")
            .replace("?usp=sharing", "")
            .replace("&usp=sharing", "")
            .replace("?usp=drive_fs", "")
            .replace("&usp=drive_fs", "")
            .replace("?usp=drive_copy", "")
            .replace("&usp=drive_copy", "")
            .replace("?dmr=1&ec=wgc-drive-globalnav-goto", "")
        )
        return re.sub(r"\?role=.*$", "", res)

    def worksheet_rows(self, sheet_id, sheet_title):
        sheet_index = self.get_sheet_index_by_title(sheet_id, sheet_title)
        worksheet = self.gspread_client.open_by_key(sheet_id).get_worksheet(sheet_index)
        return worksheet.get_all_values(
            value_render_option=gspread.utils.ValueRenderOption.unformatted
        )

    def get_variants_level_info(self, product_input, key="sku"):
        if key in product_input:
            variants_info = [product_input]
        elif (o1 := product_input["options"]) and any(key in o for o in o1):
            variants_info = product_input["options"]
        elif (o2 := product_input["options"][0]["options"]) and any(
            key in o for o in o2
        ):
            variants_info = [
                options2
                for options1 in product_input["options"]
                for options2 in options1["options"]
            ]
        else:
            raise ValueError(
                f"No variant {key} found in product input: {str(product_input)[:50]}..."
            )
        return variants_info

    def _get_stock(self, op):
        return op["stock"]

    def populate_option_dicts(self, product_input):
        """
        Returns options in a list of dicts
        [{'option_values': {'カラー': 'PINK', 'サイズ': '2'}, 'price': 35200, 'sku': 'ALV-90154-PK-2', 'stock': 2},
         {'option_values': {'カラー': 'PINK', 'サイズ': '3'}, 'price': 35200, 'sku': 'ALV-90154-PK-3', 'stock': 2},
         {'option_values': {'カラー': 'PINK', 'サイズ': '4'}, 'price': 35200, 'sku': 'ALV-90154-PK-4', 'stock': 2},
         {'option_values': {'カラー': 'INK BLACK', 'サイズ': '2'}, 'price': 35200, 'sku': 'ALV-90154-BK-2', 'stock': 2},
         {'option_values': {'カラー': 'INK BLACK', 'サイズ': '3'}, 'price': 35200, 'sku': 'ALV-90154-BK-3', 'stock': 2},
         {'option_values': {'カラー': 'INK BLACK', 'サイズ': '4'}, 'price': 35200, 'sku': 'ALV-90154-BK-4', 'stock': 2}]
        """
        option1_key, option2_key = None, None
        if option1s := product_input.get("options"):
            option1_key = list(option1s[0].keys())[0]
            if option2s := option1s[0].get("options"):
                option2_key = list(option2s[0].keys())[0]
        if option2_key:
            return [
                dict(
                    option_values={
                        option1_key: option1[option1_key],
                        option2_key: option2[option2_key],
                    },
                    price=option2.get("price")
                    or option1.get("price")
                    or product_input["price"],
                    sku=option2["sku"],
                    stock=self._get_stock(option2),
                )
                for option1 in product_input["options"]
                for option2 in option1["options"]
            ]
        if option1_key:
            return [
                dict(
                    option_values={option1_key: option1[option1_key]},
                    price=option1.get("price") or product_input["price"],
                    sku=option1["sku"],
                    stock=self._get_stock(option1),
                )
                for option1 in product_input["options"]
            ]
        return [
            dict(
                option_values={},
                price=product_input["price"],
                sku=product_input["sku"],
                stock=product_input["stock"],
            )
        ]

    def get_child_variants_attribute(self, variant_info, attribute_name):
        if attribute_name in variant_info:
            return [variant_info[attribute_name]]
        if (o1 := variant_info["options"]) and attribute_name in o1[0]:
            return [option1[attribute_name] for option1 in variant_info["options"]]
        if (o2 := variant_info["options"][0]["options"]) and attribute_name in o2[0]:
            return [
                option2[attribute_name]
                for option1 in variant_info["options"]
                for option2 in option1["options"]
            ]
        raise ValueError(f"No sku found in variant info: {variant_info}")

    def populate_drive_ids_and_skuss(self, product_input):
        skuss = []
        drive_ids = []
        image_level_variant = self.get_variants_level_info(
            product_input, key="drive_link"
        )
        for variant in image_level_variant:
            assert variant[
                "drive_link"
            ], f"no drive link for {product_input['title'], {variant}}"
            drive_ids.append(self.drive_link_to_id(variant["drive_link"]))
            skuss.append(self.get_child_variants_attribute(variant, "sku"))
        return drive_ids, skuss
