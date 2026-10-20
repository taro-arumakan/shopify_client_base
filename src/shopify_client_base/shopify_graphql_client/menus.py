import logging

logger = logging.getLogger(__name__)


class Menus:
    """
    Online Store > Navigation (admin: Content > Menus). Inherited by the ShopifyGraphqlClient class.

    Two Shopify behaviours drive the shape of this class:

    1. `menuUpdate` REPLACES the whole item tree -- there is no per-item mutation. Anything
       omitted is deleted, so every write here is a read-modify-write of the entire menu.
       Build item lists with the transform helpers rather than by hand.
    2. Menu items are typed. A page link is `type: PAGE` + `resourceId: gid://shopify/Page/x`,
       and its `url` is derived by Shopify, not stored. So repointing a page link at an
       article means changing type and resourceId; rewriting `url` alone does nothing.
       Note the `url` a query returns is locale-prefixed (e.g. "/en/pages/x") and is not
       necessarily a routable storefront path -- never treat it as one.
    """

    # menus nest up to three levels in Shopify
    MENU_ITEM_FIELDS = "id title type url resourceId tags"
    MENU_FIELDS = """
        id
        handle
        title
        isDefault
        items {
            %(f)s
            items {
                %(f)s
                items { %(f)s }
            }
        }
    """ % {
        "f": MENU_ITEM_FIELDS
    }

    def menus_by_query(self, query_string=None, first=50):
        query = (
            """
        query menusByQuery($query_string: String, $first: Int!) {
            menus(first: $first, query: $query_string) {
                nodes { %s }
            }
        }
        """
            % self.MENU_FIELDS
        )
        res = self.run_query(query, {"query_string": query_string, "first": first})
        return res["menus"]["nodes"]

    def menu_by_handle(self, handle):
        # the menus query rejects `handle:` as a search field, so filter client side
        menus = [m for m in self.menus_by_query() if m["handle"] == handle]
        assert (
            len(menus) == 1
        ), f'{"Multiple" if menus else "No"} menus found for {handle}: {menus}'
        return menus[0]

    def menu_id_by_handle(self, handle):
        return self.menu_by_handle(handle)["id"]

    """ item tree helpers """

    @staticmethod
    def walk_menu_items(items):
        """Yield every item in the tree, depth first, parents before children."""
        for item in items or []:
            yield item
            yield from Menus.walk_menu_items(item.get("items"))

    @staticmethod
    def menu_item_to_input(item):
        """A queried item -> MenuItemUpdateInput. Keeps `id` so existing items are updated
        rather than recreated (recreating would break any deep link to the item)."""
        res = {"title": item["title"], "type": item["type"]}
        if item.get("id"):
            res["id"] = item["id"]
        # a typed item is addressed by resourceId; only HTTP items carry a real url
        if item["type"] == "HTTP":
            res["url"] = item.get("url")
        elif item.get("resourceId"):
            res["resourceId"] = item["resourceId"]
        if item.get("tags"):
            res["tags"] = item["tags"]
        if item.get("items"):
            res["items"] = [Menus.menu_item_to_input(c) for c in item["items"]]
        return res

    def menu_update_items(self, handle, items):
        """Low-level full replace of a menu's items. `items` are queried-shape dicts."""
        menu = self.menu_by_handle(handle)
        query = (
            """
        mutation menuUpdate($id: ID!, $title: String!, $handle: String!, $items: [MenuItemUpdateInput!]!) {
            menuUpdate(id: $id, title: $title, handle: $handle, items: $items) {
                menu { %s }
                userErrors { field message }
            }
        }
        """
            % self.MENU_FIELDS
        )
        variables = {
            "id": menu["id"],
            "title": menu["title"],
            "handle": menu["handle"],
            "items": [self.menu_item_to_input(i) for i in items],
        }
        res = self.run_query(query, variables)
        if errors := res["menuUpdate"]["userErrors"]:
            raise RuntimeError(f"Error updating menu {handle}: {errors}")
        return res["menuUpdate"]["menu"]

    def menu_transform_items(self, handle, fn, dry_run=False):
        """Read-modify-write the whole menu: `fn(item)` is called for every item at every
        depth and returns the item (mutated or not), or None to drop it and its children.
        Returns (updated_menu_or_None, changes) where changes lists human readable diffs.
        """
        menu = self.menu_by_handle(handle)
        changes = []

        def apply(items):
            out = []
            for item in items or []:
                before = (
                    item["title"],
                    item["type"],
                    item.get("resourceId"),
                    item.get("url"),
                )
                result = fn(dict(item))
                if result is None:
                    changes.append(f"{handle}: DROP {before[0]!r} ({before[1]})")
                    continue
                result["items"] = apply(item.get("items"))
                after = (
                    result["title"],
                    result["type"],
                    result.get("resourceId"),
                    result.get("url"),
                )
                if before != after:
                    changes.append(
                        f"{handle}: {before[0]!r} {before[1]}:{before[2] or before[3]} -> {after[1]}:{after[2] or after[3]}"
                    )
                out.append(result)
            return out

        items = apply(menu["items"])
        if not changes:
            logger.info(f"{handle}: no changes")
            return None, changes
        for c in changes:
            logger.info(("DRY RUN: " if dry_run else "") + c)
        if dry_run:
            return None, changes
        return self.menu_update_items(handle, items), changes

    def menu_repoint_items(self, handle, mapping, dry_run=False):
        """Repoint menu items at new resources. `mapping` maps an existing resourceId (GID)
        to either a new resourceId GID of the same type, or a (type, resourceId) tuple when
        the type changes too -- e.g. moving a PAGE link onto an ARTICLE."""

        def fn(item):
            target = mapping.get(item.get("resourceId"))
            if target:
                new_type, new_id = (
                    target
                    if isinstance(target, (tuple, list))
                    else (item["type"], target)
                )
                item["type"], item["resourceId"] = new_type, new_id
                item["url"] = None  # derived by Shopify from the resource
            return item

        return self.menu_transform_items(handle, fn, dry_run=dry_run)

    def menu_add_item(
        self,
        handle,
        title,
        item_type,
        resource_id=None,
        url=None,
        parent_title=None,
        position=None,
        dry_run=False,
    ):
        """Insert an item, optionally under the parent whose title matches `parent_title`
        (e.g. adding a new season under "Collection"). `position` defaults to appending;
        0 puts it first, which is what a newest-first archive list wants."""
        menu = self.menu_by_handle(handle)
        new_item = {
            "title": title,
            "type": item_type,
            "resourceId": resource_id,
            "url": url,
            "items": [],
        }

        if parent_title:
            parents = [
                i
                for i in self.walk_menu_items(menu["items"])
                if i["title"] == parent_title
            ]
            assert (
                len(parents) == 1
            ), f'{"Multiple" if parents else "No"} items titled {parent_title!r} in {handle}'
            siblings = parents[0].setdefault("items", []) or []
            parents[0]["items"] = siblings
        else:
            siblings = menu["items"]

        if any(s["title"] == title for s in siblings):
            logger.info(
                f"{handle}: item {title!r} already present under {parent_title or 'root'}"
            )
            return None
        siblings.insert(len(siblings) if position is None else position, new_item)

        logger.info(
            ("DRY RUN: " if dry_run else "")
            + f"{handle}: ADD {title!r} ({item_type}) under {parent_title or 'root'} at {position if position is not None else 'end'}"
        )
        if dry_run:
            return None
        return self.menu_update_items(handle, menu["items"])

    def menu_remove_items(self, handle, titles, dry_run=False):
        """Drop every item (and its children) whose title is in `titles`."""
        titles = set(titles)
        return self.menu_transform_items(
            handle,
            lambda item: None if item["title"] in titles else item,
            dry_run=dry_run,
        )
