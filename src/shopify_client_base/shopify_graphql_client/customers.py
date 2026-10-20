import datetime
import logging
import string
import zoneinfo

logger = logging.getLogger(__name__)


class Customers:
    def customers_by_query(
        self, customers_query_string, sort_key="NAME", orders_query_string=None
    ):
        query = string.Template(
            """
        query customersByQuery($$customers_query_string: String!) {
            customers(first: 200, query: $$customers_query_string, sortKey: $sort_key) {
                nodes {
                    id
                    firstName
                    lastName
                    defaultEmailAddress {
                        emailAddress
                        marketingState
                    }
                    tags
                    $orders_fragment
                }
            }
        }
        """
        )
        if orders_query_string:
            orders_fragment = string.Template(
                """
            orders(first: 20, query: "$orders_query_string") {
                nodes {
                    id
                    cancelledAt
                }
            }
            """
            ).substitute(orders_query_string=orders_query_string)
        else:
            orders_fragment = ""
        query = query.substitute(sort_key=sort_key, orders_fragment=orders_fragment)
        variables = {"customers_query_string": customers_query_string}
        res = self.run_query(query, variables)
        return res["customers"]["nodes"]

    def customers_by_last_abandand_order_date(
        self,
        last_abandand_order_after: datetime.datetime,
    ):
        if isinstance(last_abandand_order_after, datetime.date):
            logger.warning(
                "Querying without timezone in consideration. 'last_abandand_order_date' stored in Shopify are in UTC"
            )
        elif not last_abandand_order_after.tzinfo:
            last_abandand_order_after = last_abandand_order_after.astimezone(
                zoneinfo.ZoneInfo("Asia/Tokyo")
            ).astimezone(zoneinfo.ZoneInfo("UTC"))
        query_string = (
            f"last_abandoned_order_date:>={last_abandand_order_after:%Y-%m-%d}"
        )
        return self.customers_by_query(query_string)

    def update_customer_tags(self, customer_id, tags):
        query = """
        mutation customerUpdate($input: CustomerInput!) {
            customerUpdate(input: $input) {
                customer {
                    id
                    tags
                    firstName
                    lastName
                }
                userErrors {
                    message
                    field
                }
            }
        }
        """
        variables = {
            "input": {
                "id": self.sanitize_id(customer_id, prefix="Customer"),
                "tags": tags,
            }
        }
        res = self.run_query(query, variables)
        if errors := res["customerUpdate"]["userErrors"]:
            raise RuntimeError(f"Failed to update tags: {errors}")
        return res["customerUpdate"]["customer"]
