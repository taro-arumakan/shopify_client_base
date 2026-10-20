import difflib
import logging
import re
import textwrap

logger = logging.getLogger(__name__)


class ProductCreate:
    """
    A class to handle GraphQL queries related to products creation in Shopify, inherited by the ShopifyGraphqlClient class.
    """

    def product_create_default_variant(
        self,
        title,
        description_html,
        vendor,
        tags,
        price,
        sku,
        handle=None,
        status="DRAFT",
        template_suffix=None,
        metafields=None,
    ):
        query = """
        mutation productSet($input: ProductSetInput!) {
            productSet(synchronous: true, input: $input) {
                product {
                    id
                    title
                    handle
                    tags
                    vendor
                    status
                    templateSuffix
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {
            "input": {
                "title": title,
                "descriptionHtml": description_html,
                "vendor": vendor,
                "tags": tags,
                "status": status,
                "productOptions": [
                    {"name": "Title", "values": [{"name": "Default Title"}]}
                ],
                "variants": [
                    {
                        "price": price,
                        "compareAtPrice": price,
                        "sku": sku,
                        "taxable": True,
                        "position": 1,
                        "optionValues": [
                            {"optionName": "Title", "name": "Default Title"}
                        ],
                    }
                ],
            }
        }
        if handle:
            variables["input"]["handle"] = handle
        if template_suffix:
            variables["input"]["templateSuffix"] = template_suffix
        if metafields:
            variables["input"]["metafields"] = metafields

        res = self.run_query(query, variables)
        if errors := res["productSet"]["userErrors"]:
            raise RuntimeError(f"Product creation failed: {errors}")
        return res["productSet"]["product"]

    def product_create(
        self,
        title,
        description_html,
        vendor,
        tags,
        handle=None,
        status="DRAFT",
        template_suffix=None,
        metafields=None,
        option_dicts=None,
    ):
        query = """
        mutation productSet($input: ProductSetInput!) {
            productSet(synchronous: true, input: $input) {
                product {
                    id
                    title
                    handle
                    tags
                    vendor
                    status
                    templateSuffix
                    variants(first: 20) {
                        nodes {
                            id
                            title
                            sku
                        }
                    }
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {
            "input": {
                "title": title,
                "descriptionHtml": description_html,
                "vendor": vendor,
                "tags": tags,
                "status": status,
            }
        }
        if handle:
            variables["input"]["handle"] = handle
        if template_suffix:
            variables["input"]["templateSuffix"] = template_suffix
        if metafields:
            variables["input"]["metafields"] = metafields
        if option_dicts:
            variables["input"]["productOptions"] = self.populate_product_options(
                option_dicts
            )
            variables["input"]["variants"] = self.populate_variant_inputs(option_dicts)

        res = self.run_query(query, variables)
        if errors := res["productSet"]["userErrors"]:
            raise RuntimeError(f"Product creation failed: {errors}")
        return res["productSet"]["product"]

    def populate_product_options(self, option_dicts):
        product_options = {}
        for option in option_dicts:
            option_dict = option["option_values"]
            for k, v in option_dict.items():
                if v not in product_options.get(k, []):
                    product_options.setdefault(k, []).append(v)
        return [
            {"name": k, "position": i + 1, "values": [{"name": vv} for vv in v]}
            for i, (k, v) in enumerate(product_options.items())
        ]

    def populate_variant_inputs(self, option_dicts):
        """
        options_dicts shape:
        [{'option_values': {'カラー': 'PINK', 'サイズ': '2'}, 'price': 35200, 'sku': 'ALV-90154-PK-2', 'stock': 2},
         {'option_values': {'カラー': 'PINK', 'サイズ': '3'}, 'price': 35200, 'sku': 'ALV-90154-PK-3', 'stock': 2},
         {'option_values': {'カラー': 'PINK', 'サイズ': '4'}, 'price': 35200, 'sku': 'ALV-90154-PK-4', 'stock': 2},
         {'option_values': {'カラー': 'INK BLACK', 'サイズ': '2'}, 'price': 35200, 'sku': 'ALV-90154-BK-2', 'stock': 2},
         {'option_values': {'カラー': 'INK BLACK', 'サイズ': '3'}, 'price': 35200, 'sku': 'ALV-90154-BK-3', 'stock': 2},
         {'option_values': {'カラー': 'INK BLACK', 'サイズ': '4'}, 'price': 35200, 'sku': 'ALV-90154-BK-4', 'stock': 2}]
        """
        return [
            {
                "price": option_dict["price"],
                "compareAtPrice": option_dict["price"],
                "sku": option_dict["sku"],
                "taxable": True,
                "position": i + 1,
                "optionValues": [
                    {"optionName": option_name, "name": option_value}
                    for option_name, option_value in option_dict[
                        "option_values"
                    ].items()
                ],
            }
            for i, option_dict in enumerate(option_dicts)
        ]

    def escape_html(self, text):
        replace_map = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#039;",
            "\n": "<br>\n",
        }
        for k, v in replace_map.items():
            text = text.replace(k, v)
        return text

    header_value_expression = re.compile(r"([^\d/\-\~]+?)[\s/]*([\d\.\-\~]+)")
    trailing_colon_expression = re.compile(r"[\s\:]+$")

    def to_header_and_value(self, header_value_pair_text):

        if " " in header_value_pair_text:
            header, value = header_value_pair_text.rsplit(" ", 1)
        else:
            header_value_match = self.header_value_expression.match(
                header_value_pair_text
            )
            header, value = header_value_match.groups()
        return (
            self.trailing_colon_expression.sub(string=header.strip(), repl=""),
            value.strip(),
        )

    heading_size_expression = re.compile(r"\[(.+?)\][\s\:]+(.*)")

    def formatted_size_text_to_html_table(self, size_text):
        """
        [FREE]  LENGTH 69.7 / SHOULDER RAGLAN / CHEST 69.8 / SLEEVE 83.5 / HEM 64

        [S] : LENGTH 104 / WAIST 37 / HIP 52 / HEM 28 / FRONT RISE 26
        [M] : LENGTH 105 / WAIST 39 / HIP 54 / HEM 29 / FRONT RISE 27

        [0] 着丈 84 / 肩幅 xxx / 袖丈 yyy
        [1] 着丈 90 / 肩幅 xxx / 袖丈 yyy
        [2] 着丈 90 / 肩幅 xxx / 袖丈 yyy
        [3] 着丈 90 / 肩幅 xxx / 袖丈 yyy
        [4] 着丈 90 / 肩幅 xxx / 袖丈 yyy

        [S] Total104 / Waist35.5 / Hips48.5 / Hem20 / Rise26
        [M] Total105.5 / Waist35.5 / Hips48.5 / Hem20 / Rise26
        """
        rows = []
        headers = ["Size"]

        size_text = (
            size_text.replace("／", "/").replace("\r\n", "\n").replace("\r", "\n")
        )
        size_text = re.sub(
            r"\][^\s:]", lambda m: "] " + m.group(0)[1], size_text
        )  # ensure space after ]
        size_text = re.sub(r"\s*/\s*", " / ", size_text)  # canonicalize " / "

        for line in size_text.strip().split("\n"):
            match = self.heading_size_expression.match(line.strip())
            if not match:
                raise RuntimeError(f"Invalid size text format: {line}")
            values_by_header = {}
            header_value_pairs = [p.strip() for p in match.group(2).split(" / ")]

            for header_value_pair in header_value_pairs:
                header, value = self.to_header_and_value(header_value_pair)
                header = header.strip()
                if header in values_by_header:
                    raise RuntimeError(f"Duplicate size label {header!r}: {line}")
                if header not in headers:
                    headers.append(header)
                values_by_header[header] = value.strip()
            rows.append((match.group(1), values_by_header))
        self.check_size_labels(headers[1:], [v for _, v in rows])
        # Values are placed under their own label, not by position: a row that
        # lacks a measurement (e.g. the sleeveless half of a set) leaves that
        # cell blank instead of sliding the rest of the row left.
        return ProductCreate.generate_table_html(
            headers,
            [[size] + [v.get(h, "") for h in headers[1:]] for size, v in rows],
        )

    @staticmethod
    def check_size_labels(headers, rows_values):
        """Raise on two labels that look like one misspelled (AMRHOLE / ARMHOLE).

        The sheets are typed by hand, and a typo on one row used to shift every
        value after it. Two near-identical labels that never appear in the same
        row are almost always that typo.
        """
        for i, a in enumerate(headers):
            for b in headers[i + 1 :]:
                if any(a in v and b in v for v in rows_values):
                    continue
                if difflib.SequenceMatcher(None, a, b).ratio() >= 0.8:
                    raise RuntimeError(f"Size labels look misspelled: {a!r} / {b!r}")

    @staticmethod
    def get_size_table_html(size_text):
        kv_pairs = list(map(str.strip, re.split("[\n/]", size_text)))
        headers, values = zip(*[kv_pair.split(" ") for kv_pair in kv_pairs])
        return ProductCreate.generate_table_html(headers, values)

    @staticmethod
    def generate_table_html(headers, rows):
        spaces = "            "
        html = """
            <table border="1" style="border-collapse: collapse;" class="size-table">
              <thead>
                <tr>""".replace(
            spaces, ""
        )

        for header in headers:
            html += f"""
                  <th>{header}</th>""".replace(
                spaces, ""
            )

        html += """
                </tr>
              </thead>
              <tbody>""".replace(
            spaces, ""
        )

        for row in rows:
            html += """
                <tr>""".replace(
                spaces, ""
            )
            for v in row:
                html += f"""
                  <td>{v}</td>""".replace(
                    spaces, ""
                )
            html += """
                </tr>""".replace(
                spaces, ""
            )

        html += """
              </tbody>
            </table>""".replace(
            spaces, ""
        )

        return html

    @staticmethod
    def product_description_template():
        res = """
            <div id="cataldesignProduct">
                <h3>商品説明</h3>
                <p>${DESCRIPTION}</p>
                <h3>手入れ方法</h3>
                <p>${PRODUCTCARE}</p>
                <h3>サイズ・素材</h3>
                ${SIZE_TABLE}
                <br>
                <table width="100%">
                <tbody>
                    <tr>
                    <th>素材</th>
                    <td>${MATERIAL}</td>
                    </tr>
                    <tr>
                    <th>原産国</th>
                    <td>${MADEIN}</td>
                    </tr>
                </tbody>
                </table>
            </div>
            """
        return textwrap.dedent(res)

    def format_description_html(
        self,
        description,
        product_care,
        material,
        size_html,
        made_in,
    ):
        description = self.escape_html(description)
        product_care = self.escape_html(product_care)
        material = self.escape_html(material)
        made_in = self.escape_html(made_in)

        description_html = self.product_description_template()
        description_html = description_html.replace("${DESCRIPTION}", description)
        description_html = description_html.replace("${PRODUCTCARE}", product_care)
        description_html = description_html.replace("${SIZE_TABLE}", size_html)
        description_html = description_html.replace("${MATERIAL}", material)
        description_html = description_html.replace("${MADEIN}", made_in)

        return description_html
