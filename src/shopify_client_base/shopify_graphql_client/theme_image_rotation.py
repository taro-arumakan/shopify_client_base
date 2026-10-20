"""Advance numbered theme images (tb_01 -> tb_02 -> ...) in a theme template.

The current index is read from the live template. The write is a text
substitution on the fetched file rather than a JSON round-trip, so Shopify's
header comment and the file's formatting survive.
"""

import logging
import re

logger = logging.getLogger(__name__)


def with_index(pattern, url, index, pad=0):
    """`url` with the digits matched by pattern's `index` group replaced."""
    m = pattern.search(url)
    return url[: m.start("index")] + f"{index:0{pad}d}" + url[m.end("index") :]


class ThemeImageRotation:

    def rotate_theme_images(
        self,
        pattern,
        section_type,
        block_type,
        *,
        wrap_to,
        setting_keys=("image",),
        theme_file="templates/index.json",
        pad=0,
        theme_name=None,
        dry_run=True,
    ):
        """Advance every image url matching `pattern` by one.

        `pattern` must capture the number as a group named `index`. A block
        rotates when its `setting_keys` (e.g. desktop and mobile crops) all
        match, and they move together; a block where only some match raises.
        When the next images are not all in Files, wrap back to `wrap_to`.

        Raises when nothing rotates - no block matches (an edited theme or a
        stale pattern), or a set has a single image - so a scheduled run
        cannot pass while the banner stands still. Returns [(old_url, new_url)].
        """
        theme = self.theme_by_name(theme_name) if theme_name else self.current_theme()
        content = self.theme_file_content(theme, theme_file)
        data = self.theme_json_to_dict(content)
        logger.info(f"{theme['name']} ({theme['id']}, role={theme['role']})")

        swaps = {}
        for section_key, block_key, settings in self.theme_json_blocks(
            data, section_type, block_type
        ):
            label = f"{section_key}/{block_key}"
            urls = [settings.get(k) or "" for k in setting_keys]
            matched = [bool(pattern.search(u)) for u in urls]
            if not any(matched):
                continue
            if not all(matched):
                raise RuntimeError(f"{label}: not all of {setting_keys} match: {urls}")
            indexes = {int(pattern.search(u)["index"]) for u in urls}
            if len(indexes) != 1:
                raise RuntimeError(f"{label}: images at different indexes: {urls}")
            current = indexes.pop()

            for index in (current + 1, wrap_to):
                new_urls = [with_index(pattern, u, index, pad) for u in urls]
                if all(self.image_file_exists(u) for u in new_urls):
                    break
            else:
                raise RuntimeError(
                    f"{label}: neither index {current + 1} nor {wrap_to} is in Files"
                )
            for old, new in zip(urls, new_urls):
                if old != new:
                    logger.info(f"  {label}: {old} -> {new}")
                    swaps[old] = new

        if not swaps:
            raise RuntimeError(
                f"nothing to rotate: no {block_type!r} block in a {section_type!r} "
                f"section of {theme_file} matches {pattern.pattern!r} with a next image"
            )
        if dry_run:
            logger.info("dry run - no changes written")
            return list(swaps.items())

        for old in swaps:
            if (found := content.count(old)) != 1:
                raise RuntimeError(f"expected 1 occurrence of {old!r}, found {found}")
        # One pass, so a new url that is another block's old url is not rewritten.
        new_content = re.sub(
            "|".join(map(re.escape, swaps)), lambda m: swaps[m[0]], content
        )
        self.upsert_theme_file(theme["id"], theme_file, new_content)

        live = self.theme_file_content(theme, theme_file)
        missing = [new for new in swaps.values() if new not in live]
        if missing:
            raise RuntimeError(f"{theme_file} not updated, missing: {missing}")
        logger.info(f"  upserted and verified {theme_file}")
        return list(swaps.items())
