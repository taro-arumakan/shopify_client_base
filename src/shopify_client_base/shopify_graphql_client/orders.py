import datetime
import logging
import time
import zoneinfo

logger = logging.getLogger(__name__)


class Orders:
    def orders_by_query(
        self,
        query_string,
        additional_fields=None,
        sort_key="PROCESSED_AT",
        reverse=False,
    ):
        reverse = "true" if reverse else "false"
        query = """
        query ordersByQuery($query_string: String!) {
            orders(first: 200, query: $query_string, sortKey: %s, reverse: %s) {
                nodes {
                    id
                    name
                    displayFinancialStatus
                    createdAt
                    processedAt
                    cancelledAt
                    closed%s
                    customer {
                        id
                        displayName
                        defaultEmailAddress {
                            emailAddress
                        }
                    }
                }
            }
        }
        """ % (
            sort_key,
            reverse,
            f"\n{'\n'.join(additional_fields)}" if additional_fields else "",
        )
        variables = {"query_string": query_string}
        res = self.run_query(query, variables)
        return res["orders"]["nodes"]

    def order_add_tags(self, order_id, tags):
        """Add one or more tags to an order. Shopify de-duplicates existing tags."""
        if isinstance(tags, str):
            tags = [tags]
        order_id = self.sanitize_id(order_id, prefix="Order")
        query = """
        mutation orderTagsAdd($id: ID!, $tags: [String!]!) {
            tagsAdd(id: $id, tags: $tags) {
                node { id }
                userErrors { field message }
            }
        }
        """
        res = self.run_query(query, {"id": order_id, "tags": tags})
        if errors := res["tagsAdd"]["userErrors"]:
            raise RuntimeError(f"order_add_tags failed for {order_id}: {errors}")
        return res["tagsAdd"]

    def order_fulfillment_orders(self, order_id):
        order_id = self.sanitize_id(order_id, prefix="Order")
        query = """
        query orderFulfillmentOrders($id: ID!) {
            order(id: $id) {
                fulfillmentOrders(first: 20) {
                    nodes { id status }
                }
            }
        }
        """
        res = self.run_query(query, {"id": order_id})
        return res["order"]["fulfillmentOrders"]["nodes"]

    def fulfillment_order_hold(
        self, fulfillment_order_id, reason="OTHER", reason_notes=None, handle=None
    ):
        """Put a fulfillment order on hold so it cannot be fulfilled until released.
        `reason` is a FulfillmentHoldReason; `handle` distinguishes this app's holds."""
        fulfillment_order_id = self.sanitize_id(
            fulfillment_order_id, prefix="FulfillmentOrder"
        )
        query = """
        mutation fulfillmentOrderHold($id: ID!, $fulfillmentHold: FulfillmentOrderHoldInput!) {
            fulfillmentOrderHold(id: $id, fulfillmentHold: $fulfillmentHold) {
                fulfillmentOrder { id status }
                userErrors { field message }
            }
        }
        """
        fulfillment_hold = {"reason": reason}
        if reason_notes:
            fulfillment_hold["reasonNotes"] = reason_notes
        if handle:
            fulfillment_hold["handle"] = handle
        res = self.run_query(
            query, {"id": fulfillment_order_id, "fulfillmentHold": fulfillment_hold}
        )
        if errors := res["fulfillmentOrderHold"]["userErrors"]:
            raise RuntimeError(
                f"fulfillment_order_hold failed for {fulfillment_order_id}: {errors}"
            )
        return res["fulfillmentOrderHold"]

    def orders_by_sku(
        self,
        sku,
        created_after_date: datetime.datetime = None,
        open_only: bool = False,
    ):
        query_string = f"sku:'{sku}'"
        if created_after_date:
            if isinstance(created_after_date, datetime.date):
                logger.warning(
                    "Querying without timezone in consideration. 'processed_at' stored in Shopify are in UTC"
                )
            elif not created_after_date.tzinfo:
                created_after_date = created_after_date.astimezone(
                    zoneinfo.ZoneInfo("Asia/Tokyo")
                ).astimezone(zoneinfo.ZoneInfo("UTC"))
            query_string += f" AND processed_at:>={created_after_date:%Y-%m-%d}"
        if open_only:
            query_string += " AND status:'open' AND NOT financial_status:'expired'"
        return self.orders_by_query(query_string)

    def orders_later_than(self, cutoff_datetime: datetime.datetime):
        if not cutoff_datetime.tzinfo:
            logger.warning("converting parameter to Asia/Tokyo")
            cutoff_datetime = cutoff_datetime.replace(
                tzinfo=zoneinfo.ZoneInfo("Asia/Tokyo")
            )
        return self.orders_by_query(f"processed_at:>='{cutoff_datetime.isoformat()}'")

    def orders_expired_not_closed(self, asof: datetime.datetime = None):
        query = "financial_status:'expired' AND status:'not_closed'"
        if asof:
            query += f" AND processed_at:<='{asof.isoformat()}'"
        return self.orders_by_query(query)

    def wait_for_job(self, job_id, max_wait=60, poll_interval=1):
        query = """
            query GetJob($id: ID!) {
                job(id: $id) {
                    id
                    done
                }
            }
        """
        start_time = time.time()
        while time.time() - start_time < max_wait:
            res = self.run_query(query, {"id": job_id})
            if res["job"]["done"]:
                return True
            time.sleep(poll_interval)
        raise TimeoutError(f"Job {job_id} did not complete within {max_wait} seconds")

    def order_cancel(
        self,
        order_id,
        notify_cusotomer=False,
        reason="OTHER",
        restock=True,
        staff_note="System Cancel",
    ):
        query = """
        mutation OrderCancel($orderId: ID!, $notifyCustomer: Boolean, $refundMethod: OrderCancelRefundMethodInput!, $restock: Boolean!, $reason: OrderCancelReason!, $staffNote: String) {
            orderCancel(orderId: $orderId, notifyCustomer: $notifyCustomer, refundMethod: $refundMethod, restock: $restock, reason: $reason, staffNote: $staffNote) {
                job {
                    id
                    done
                }
                orderCancelUserErrors {
                    field
                    message
                    code
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = dict(
            orderId=order_id,
            refundMethod={"originalPaymentMethodsRefund": True},
            notifyCustomer=notify_cusotomer,
            reason=reason,
            restock=restock,
            staffNote=staff_note,
        )
        res = self.run_query(query, variables)
        if errors := (
            res["orderCancel"]["orderCancelUserErrors"]
            or res["orderCancel"]["userErrors"]
        ):
            raise RuntimeError(f"Order cancel fails: {errors}")
        res = res["orderCancel"]
        job_id = res["job"]["id"]
        self.wait_for_job(job_id)
        return res

    def order_close(self, order_id):
        query = """
        mutation OrderClose($input: OrderCloseInput!) {
            orderClose(input: $input) {
                order {
                    id
                    closed
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        res = self.run_query(query, {"input": {"id": order_id}})
        if errors := res["orderClose"]["userErrors"]:
            raise RuntimeError(f"Order close fails: {errors}")
        return res["orderClose"]

    def order_cancel_and_close(
        self, order, notify_customer=False, staff_note="Auto-cancelled"
    ):
        order_id = order["id"]
        if not order["cancelledAt"]:
            logger.info(
                f"Cancelling order {order['name']}. Customer will {'' if notify_customer else 'not '}be notified."
            )
            self.order_cancel(
                order_id=order_id,
                notify_cusotomer=notify_customer,
                staff_note=staff_note,
            )
        if not order["closed"]:
            logger.info(f"Cancelling order {order['name']}")
            self.order_close(order_id=order_id)

    def get_expired_orders_for_cancellation(
        self, processing_date: datetime.date = None, days_older_than: int = 14
    ):
        """Expired orders older than 14 days that need cancellation and closure"""
        processing_date = processing_date or datetime.date.today()
        processing_date = datetime.datetime.combine(processing_date, datetime.time())
        asof = processing_date - datetime.timedelta(days=days_older_than)
        asof = asof.astimezone(zoneinfo.ZoneInfo("Asia/Tokyo"))
        return self.orders_expired_not_closed(asof=asof)
