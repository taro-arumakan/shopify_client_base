import json
import re
import unittest

from shopify_client_base.shopify_graphql_client.online_store import OnlineStore
from shopify_client_base.shopify_graphql_client.theme_image_rotation import ThemeImageRotation

THEME = {"id": "gid://shopify/OnlineStoreTheme/1", "name": "live", "role": "MAIN"}
TB = re.compile(r"shopify://shop_images/tb_(?P<index>\d\d)\.jpg$")
PAIR = re.compile(r"shopify://shop_images/tb_[a-z]+_(pc|m)_(?P<index>\d\d)\.jpg$")
PAIR_KEYS = ("image", "mobile_image")
VIEW_ALL = re.compile(r"shopify://shop_images/[a-z]+_view_all_(?P<index>\d+)\.jpg$")


def img(name):
    return f"shopify://shop_images/{name}"


def template(*blocks, section_type="slideshow", block_type="image"):
    keys = [f"b{i}" for i in range(len(blocks))]
    return {
        "order": ["s1"],
        "sections": {
            "s1": {
                "type": section_type,
                "block_order": keys,
                "blocks": {
                    k: {"type": block_type, "settings": settings}
                    for k, settings in zip(keys, blocks)
                },
            }
        },
    }


class FakeClient(ThemeImageRotation, OnlineStore):
    def __init__(self, data, files=()):
        self.content = "/* header */\n" + json.dumps(data, indent=2)
        self.files = set(files)
        self.written = []

    def current_theme(self):
        return THEME

    def theme_file_content(self, theme, file_name):
        return self.content

    def image_file_exists(self, name_or_url):
        return name_or_url.rsplit("/", 1)[-1] in self.files

    def upsert_theme_file(self, theme_id, file_name, contents):
        self.written.append(contents)
        self.content = contents


def rotate(client, pattern=TB, **kwargs):
    kwargs.setdefault("section_type", "slideshow")
    kwargs.setdefault("block_type", "image")
    return client.rotate_theme_images(pattern, dry_run=False, **kwargs)


class TestRotateThemeImages(unittest.TestCase):
    def test_advances_and_keeps_header(self):
        c = FakeClient(template({"image": img("tb_00.jpg")}), {"tb_01.jpg"})
        changes = rotate(c, pad=2, wrap_to=0)
        self.assertEqual(changes, [(img("tb_00.jpg"), img("tb_01.jpg"))])
        self.assertTrue(c.content.startswith("/* header */"))
        self.assertIn(img("tb_01.jpg"), c.content)

    def test_wraps_when_next_missing(self):
        c = FakeClient(template({"image": img("tb_03.jpg")}), {"tb_00.jpg"})
        self.assertEqual(rotate(c, pad=2, wrap_to=0)[0][1], img("tb_00.jpg"))

    def test_raises_when_neither_next_nor_wrap_exists(self):
        c = FakeClient(template({"image": img("tb_03.jpg")}))
        with self.assertRaises(RuntimeError):
            rotate(c, pad=2, wrap_to=0)

    def test_single_image_set_raises(self):
        c = FakeClient(template({"image": img("tb_00.jpg")}), {"tb_00.jpg"})
        with self.assertRaises(RuntimeError):
            rotate(c, pad=2, wrap_to=0)
        self.assertEqual(c.written, [])

    def test_wrap_to_is_required(self):
        c = FakeClient(template({"image": img("tb_00.jpg")}), {"tb_01.jpg"})
        with self.assertRaises(TypeError):
            rotate(c, pad=2)

    def test_dry_run_reports_without_writing(self):
        c = FakeClient(template({"image": img("tb_00.jpg")}), {"tb_01.jpg"})
        changes = c.rotate_theme_images(TB, "slideshow", "image", pad=2, wrap_to=0)
        self.assertEqual(len(changes), 1)
        self.assertEqual(c.written, [])

    def test_ignores_other_blocks(self):
        c = FakeClient(
            template({"image": img("sale.jpg")}, {"image": img("tb_00.jpg")}),
            {"tb_01.jpg"},
        )
        rotate(c, pad=2, wrap_to=0)
        self.assertIn(img("sale.jpg"), c.content)

    def test_pair_moves_together(self):
        block = {
            "image": img("tb_sepoct_pc_01.jpg"),
            "mobile_image": img("tb_sepoct_m_01.jpg"),
        }
        c = FakeClient(template(block), {"tb_sepoct_pc_02.jpg", "tb_sepoct_m_02.jpg"})
        rotate(c, PAIR, pad=2, wrap_to=1, setting_keys=PAIR_KEYS)
        self.assertIn(img("tb_sepoct_pc_02.jpg"), c.content)
        self.assertIn(img("tb_sepoct_m_02.jpg"), c.content)

    def test_half_uploaded_pair_wraps(self):
        block = {
            "image": img("tb_sepoct_pc_02.jpg"),
            "mobile_image": img("tb_sepoct_m_02.jpg"),
        }
        c = FakeClient(
            template(block),
            {"tb_sepoct_pc_03.jpg", "tb_sepoct_pc_01.jpg", "tb_sepoct_m_01.jpg"},
        )
        rotate(c, PAIR, pad=2, wrap_to=1, setting_keys=PAIR_KEYS)
        self.assertIn(img("tb_sepoct_m_01.jpg"), c.content)

    def test_pair_at_different_indexes_raises(self):
        block = {
            "image": img("tb_sepoct_pc_01.jpg"),
            "mobile_image": img("tb_sepoct_m_02.jpg"),
        }
        with self.assertRaises(RuntimeError):
            rotate(
                FakeClient(template(block)),
                PAIR,
                pad=2,
                wrap_to=1,
                setting_keys=PAIR_KEYS,
            )

    def test_half_matching_pair_raises(self):
        # Shopify renamed a re-upload: 26_fall_pc_1w_<uuid>.jpg.
        block = {
            "image": img("tb_sepoct_pc_01_5f30b7.jpg"),
            "mobile_image": img("tb_sepoct_m_01.jpg"),
        }
        c = FakeClient(template(block), {"tb_sepoct_m_02.jpg"})
        with self.assertRaises(RuntimeError):
            rotate(c, PAIR, pad=2, wrap_to=1, setting_keys=PAIR_KEYS)
        self.assertEqual(c.written, [])

    def test_unpadded_blocks_cycle_independently(self):
        c = FakeClient(
            template(
                {"image": img("bags_view_all_9.jpg")},
                {"image": img("clothes_view_all_4.jpg")},
                section_type="collection-list",
                block_type="collection",
            ),
            {"bags_view_all_10.jpg", "clothes_view_all_1.jpg"},
        )
        rotate(
            c,
            VIEW_ALL,
            wrap_to=1,
            section_type="collection-list",
            block_type="collection",
        )
        self.assertIn(img("bags_view_all_10.jpg"), c.content)
        self.assertIn(img("clothes_view_all_1.jpg"), c.content)

    def test_new_url_equal_to_another_blocks_old_url(self):
        # b0 1 -> 2 while b1 still shows 2 and wraps to 1.
        c = FakeClient(
            template({"image": img("tb_01.jpg")}, {"image": img("tb_02.jpg")}),
            {"tb_01.jpg", "tb_02.jpg"},
        )
        rotate(c, pad=2, wrap_to=1)
        data = c.theme_json_to_dict(c.content)
        blocks = data["sections"]["s1"]["blocks"]
        self.assertEqual(blocks["b0"]["settings"]["image"], img("tb_02.jpg"))
        self.assertEqual(blocks["b1"]["settings"]["image"], img("tb_01.jpg"))

    def test_no_matching_block_raises(self):
        c = FakeClient(template({"image": img("sale.jpg")}))
        with self.assertRaises(RuntimeError):
            rotate(c, pad=2, wrap_to=0)
        self.assertEqual(c.written, [])

    def test_wrong_section_type_raises(self):
        c = FakeClient(template({"image": img("tb_00.jpg")}), {"tb_01.jpg"})
        with self.assertRaises(RuntimeError):
            rotate(c, pad=2, wrap_to=0, section_type="image-banner")


if __name__ == "__main__":
    unittest.main()
