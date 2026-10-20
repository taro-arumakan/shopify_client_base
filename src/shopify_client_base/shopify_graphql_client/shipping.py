import logging
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


class Shipping:
    def delivery_profiles(self):
        query = """
        query GetDeliveryProfiles {
            deliveryProfiles(first: 50, merchantOwnedOnly: true) {
                nodes {
                    id
                    name
                    profileLocationGroups {
                        locationGroup {
                            id
                        }
                        locationGroupZones(first: 50) {
                            nodes {
                                zone {
                                    id
                                    name
                                }
                                methodDefinitions(first: 50) {
                                    nodes {
                                        id
                                        name
                                        rateProvider {
                                            __typename
                                            ... on DeliveryRateDefinition {
                                                id
                                                price { amount currencyCode }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        res = self.run_query(query)
        return res["deliveryProfiles"]["nodes"]

    def update_delivery_profile(self, profile_id, profile_input):
        mutation = """
        mutation deliveryProfileUpdate($id: ID!, $profile: DeliveryProfileInput!) {
            deliveryProfileUpdate(id: $id, profile: $profile) {
                profile { id name }
                userErrors { field message }
            }
        }
        """
        res = self.run_query(mutation, {"id": profile_id, "profile": profile_input})
        if user_errors := res["deliveryProfileUpdate"]["userErrors"]:
            raise RuntimeError(f"Error updating delivery profile: {user_errors}")
        return res["deliveryProfileUpdate"]["profile"]

    def delivery_profile_by_name(self, name="General profile"):
        res = self.delivery_profiles()
        for dp in res:
            if dp["name"] == name:
                return dp
        else:
            raise RuntimeError(f"profile {name} not found")

    def location_group_zone_by_name(self, delivery_profile, zone_name):
        location_groups = delivery_profile["profileLocationGroups"]
        assert (
            len(location_groups) == 1
        ), f"multiple location zones not supported: {location_groups}"
        for location_group_zone in location_groups[0]["locationGroupZones"]["nodes"]:
            if location_group_zone["zone"]["name"] == zone_name:
                return location_group_zone
        raise RuntimeError(
            f"zone {zone_name} not found in profile {delivery_profile['name']}"
        )

    def update_delivery_flat_rate(
        self,
        new_price,
        profile_name="General profile",
        zone_name="国内配送",
        currency_code="JPY",
        new_method_name=None,
        testrun=True,
    ):
        """Replace the zone's method definition with a flat-rate one.

        Implemented as delete + create rather than update: when the existing
        method definition has rate-range conditions ("discount" rates such as a
        free-shipping campaign), Shopify rejects `methodDefinitionsToUpdate`
        with "This method definition cannot be updated because it uses new
        configurations...". Deleting the old method cascades the conditions
        away, then we create a fresh flat-rate method in the same mutation.
        """
        dp = self.delivery_profile_by_name(profile_name)
        profile_id = dp["id"]
        location_group = dp["profileLocationGroups"][0]
        zone = self.location_group_zone_by_name(dp, zone_name)
        method_nodes = zone["methodDefinitions"]["nodes"]

        base_method = next(m for m in method_nodes if "?" not in m["id"])
        method_name = new_method_name or base_method["name"]

        profile_input = {
            "methodDefinitionsToDelete": [base_method["id"]],
            "locationGroupsToUpdate": [
                {
                    "id": location_group["locationGroup"]["id"],
                    "zonesToUpdate": [
                        {
                            "id": zone["zone"]["id"],
                            "methodDefinitionsToCreate": [
                                {
                                    "name": method_name,
                                    "active": True,
                                    "rateDefinition": {
                                        "price": {
                                            "amount": str(new_price),
                                            "currencyCode": currency_code,
                                        }
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        logger.info(
            f"Replacing {profile_name} / {zone_name} method: "
            f"'{base_method['name']}' ({base_method['rateProvider']['price']['amount']} "
            f"{base_method['rateProvider']['price']['currencyCode']}) "
            f"-> '{method_name}' ({new_price} {currency_code})"
        )
        if testrun:
            logger.info("Test run mode - no changes will be made")
            return profile_input
        else:
            return self.update_delivery_profile(profile_id, profile_input)
