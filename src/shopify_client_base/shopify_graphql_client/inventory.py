import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class Inventory:
    """
    This class provides methods to manage inventory in a Shopify store. Inherited by the ShopifyGraphqlClient class.
    """

    """ inventory management """

    location_id_by_name_cache = {}

    def location_id_by_name(self, name):
        if name not in self.location_id_by_name_cache:
            query = (
                """
            {
                locations(first:10, query:"name:%s") {
                    nodes {
                        id
                    }
                }
            }
            """
                % name
            )
            res = self.run_query(query)
            res = res["locations"]["nodes"]
            assert (
                len(res) == 1
            ), f'{"Multiple" if res else "No"} locations found for {name}: {res}'
            self.location_id_by_name_cache[name] = res[0]["id"]
        return self.location_id_by_name_cache[name]

    def enable_and_activate_inventory_by_product_id(self, product_id, location_names):
        product = self.product_by_id(product_id)
        return self.enable_and_activate_inventories_by_skus(
            [v["sku"] for v in product["variants"]["nodes"]], location_names
        )

    def enable_and_activate_inventories_by_skus(self, skus, location_names):
        inventory_item_ids = self.inventory_item_ids_by_skus(skus)
        return self.enable_and_activate_inventories_by_inventory_item_ids(
            inventory_item_ids, location_names
        )

    def enable_and_activate_inventories_by_inventory_item_ids(
        self, inventory_item_ids, location_names
    ):
        logger.info(f"activating inventory {inventory_item_ids} in {location_names}")
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(
                executor.map(
                    lambda inventory_item_id: self.enable_and_activate_inventory_by_inventory_item_id(
                        inventory_item_id, location_names
                    ),
                    inventory_item_ids,
                )
            )
        return results

    def enable_and_activate_inventory_by_inventory_item_id(
        self, inventory_item_id, location_names
    ):
        ress = [self.update_inventory_tracking(inventory_item_id, True)]
        for location_name in location_names:
            ress.append(
                self.activate_inventory_item(
                    inventory_item_id, self.location_id_by_name(location_name)
                )
            )
        return ress

    def enable_and_activate_inventory_by_sku(self, sku, location_names):
        inventory_item_id = self.inventory_item_id_by_sku(sku)
        return self.enable_and_activate_inventory_by_inventory_item_id(
            inventory_item_id=inventory_item_id, location_names=location_names
        )

    def disable_inventory_tracking_by_variant_id(self, variant_id):
        inventory_item_id = self.inventory_item_by_variant_id(variant_id)
        return self.update_inventory_tracking(inventory_item_id, False)

    def disable_inventory_tracking_by_sku(self, sku):
        inventory_item_id = self.inventory_item_id_by_sku(sku)
        return self.update_inventory_tracking(inventory_item_id, False)

    def update_inventory_item(self, inventory_item_id, input_data: dict):
        query = """
        mutation inventoryItemUpdate($id: ID!, $input: InventoryItemInput!) {
            inventoryItemUpdate(id: $id, input: $input) {
                inventoryItem {
                    id
                    tracked
                    measurement {
                      id
                    }
                }
                userErrors {
                    message
                }
            }
        }
        """
        variables = {"id": inventory_item_id, "input": input_data}
        res = self.run_query(query, variables)
        if res["inventoryItemUpdate"]["userErrors"]:
            raise Exception(
                f"Error updating inventory quantity: {res['inventoryItemUpdate']['userErrors']}"
            )
        return res["inventoryItemUpdate"]["inventoryItem"]

    def update_inventory_tracking(self, inventory_item_id, tracked=True):
        input_data = {"tracked": tracked}
        return self.update_inventory_item(inventory_item_id, input_data)

    def update_inventory_item_weight(
        self, inventory_item_id, weight: float, unit: str = "KILOGRAMS"
    ):
        input_data = {"measurement": {"weight": {"value": weight, "unit": unit}}}
        return self.update_inventory_item(inventory_item_id, input_data)

    def update_inventory_item_weight_by_sku(
        self, sku, weight: float, unit: str = "KILOGRAMS"
    ):
        inventory_item_id = self.inventory_item_id_by_sku(sku)
        return self.update_inventory_item_weight(inventory_item_id, weight, unit)

    def activate_inventory_item(self, inventory_item_id, location_id, available=0):
        query = """
        mutation ActivateInventoryItem($inventoryItemId: ID!, $locationId: ID!%s) {
          inventoryActivate(inventoryItemId: $inventoryItemId, locationId: $locationId%s) {
                inventoryLevel {
                    id
                    quantities(names: ["available"]) {
                        name
                        quantity
                    }
                    item {
                        id
                    }
                    location {
                        id
                    }
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """ % (
            (", $available: Int", ", available: $available") if available else ("", "")
        )
        variables = {
            "inventoryItemId": inventory_item_id,
            "locationId": location_id,
        }
        if available:
            variables["available"] = available
        res = self.run_query(query, variables)
        if user_errors := res["inventoryActivate"]["userErrors"]:
            raise RuntimeError(f"Failed to activate an inventory item: {user_errors}")
        return res["inventoryActivate"]["inventoryLevel"]

    def inventory_items_by_query(self, query_string):
        query = """
        query inventoryItemsByQuery($query_string: String!) {
            inventoryItems(query:$query_string, first:100) {
                nodes{
                    id
                    sku
                    tracked
                    inventoryLevels(first:100) {
                        nodes {
                            id
                            quantities(names: ["available"]) {
                                name
                                quantity
                            }
                        }
                    }
                }
            }
        }"""
        variables = {"query_string": query_string}
        res = self.run_query(query, variables)
        return res["inventoryItems"]["nodes"]

    def inventory_items_by_skus(self, skus: list[str]):
        q = " OR ".join(f"sku:'{sku}'" for sku in skus)
        return self.inventory_items_by_query(q)

    def inventory_item_by_sku(self, sku):
        res = self.inventory_items_by_skus([sku])
        assert (
            len(res) == 1
        ), f'{"Multiple" if res else "No"} inventoryItems found for {sku}: {res}'
        return res[0]

    def inventory_item_ids_by_skus(self, skus):
        inventory_items = self.inventory_items_by_skus(skus)
        return [r["id"] for r in inventory_items]

    def inventory_item_id_by_sku(self, sku):
        res = self.inventory_item_by_sku(sku)
        return res["id"]

    def inventory_item_by_variant_id(self, variant_id):
        variant = self.variant_by_variant_id(variant_id)
        return self.inventory_item_by_sku(variant["sku"])

    def set_inventory_quantity_by_sku_and_location_id(self, sku, location_id, quantity):
        inventory_item_id = self.inventory_item_id_by_sku(sku)
        query = """
        mutation inventorySetQuantities($locationId: ID!, $inventoryItemId: ID!, $quantity: Int!) {
            inventorySetQuantities(
                input: {name: "available", reason: "correction",
                        quantities: [{inventoryItemId: $inventoryItemId,
                                    locationId: $locationId,
                                    quantity: $quantity,
                                    changeFromQuantity: null}]}
            ) {
                inventoryAdjustmentGroup {
                    id
                    changes {
                        name
                        delta
                        quantityAfterChange
                    }
                    reason
                }
                userErrors {
                    message
                    code
                    field
                }
            }
        }
        """
        variables = {
            "inventoryItemId": inventory_item_id,
            "locationId": location_id,
            "quantity": quantity,
        }
        res = self.run_query(query, variables)
        if res["inventorySetQuantities"]["userErrors"]:
            raise Exception(
                f"Error updating inventory quantity: {res['inventorySetQuantities']['userErrors']}"
            )
        updates = res["inventorySetQuantities"]["inventoryAdjustmentGroup"]
        if not updates:
            logger.info(
                f"no updates found after updating inventory of {sku} to {quantity}"
            )
        return updates
