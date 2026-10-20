import datetime
import unittest

from shopify_client_base.shopify_graphql_client.publication_catch_up import PublicationCatchUp
from shopify_client_base.shopify_graphql_client.publications import Publications

NOW = datetime.datetime(2026, 10, 14, 12, 0, tzinfo=datetime.timezone.utc)

ONLINE_STORE = {"id": "gid://shopify/Publication/1", "name": "Online Store"}
POS = {"id": "gid://shopify/Publication/2", "name": "Point of Sale"}
SHOP = {"id": "gid://shopify/Publication/3", "name": "Shop"}
GOOGLE = {"id": "gid://shopify/Publication/4", "name": "Google & YouTube"}


def state(publication, is_published, publish_date=None):
    return {
        "publication": publication,
        "isPublished": is_published,
        "publishDate": publish_date,
    }


class FakeClient(PublicationCatchUp, Publications):
    """Mixes in the real code under test; only the GraphQL-backed primitives
    are stubbed. other_channels is inherited, not faked, so the diff it does
    is the one production runs."""

    shop_name = "example-shop"

    def __init__(
        self, products, states_by_id, launch_datetime=None, all_publications=None
    ):
        self.products = products
        self.states_by_id = states_by_id
        self.launch_datetime = launch_datetime
        # Default to exactly the publications the fixture names, so a test that
        # spells out every channel in its states keeps its original meaning.
        # A test about channels the product has NO record for passes its own.
        if all_publications is None:
            seen, all_publications = set(), []
            for states in states_by_id.values():
                for s in states:
                    if s["publication"]["name"] not in seen:
                        seen.add(s["publication"]["name"])
                        all_publications.append(s["publication"])
        self.all_publications = all_publications
        self.published = []
        self.unpublished = []
        self.untagged = []

    def products_by_tag(self, tag):
        return self.products

    def publications(self, include_products=True):
        return self.all_publications

    def product_publication_states(self, product_id):
        return self.states_by_id[product_id]

    def product_metafield_by_product_id(self, product_id, namespace, key):
        if self.launch_datetime is None:
            return None
        return {"id": "gid://shopify/Metafield/1", "value": self.launch_datetime}

    def publish_by_product_or_collection_id_and_publication_id(
        self, product_or_collection_id, publication_id
    ):
        self.published.append((product_or_collection_id, publication_id))

    def unpublish_by_product_or_collection_id_and_publication_id(
        self, product_or_collection_id, publication_id
    ):
        self.unpublished.append((product_or_collection_id, publication_id))

    def remove_product_tags(self, product_id, tags):
        self.untagged.append((product_id, tags))


def client_for(
    states,
    product_id="gid://shopify/Product/1",
    title="JACKET",
    launch_datetime=None,
    all_publications=None,
):
    return FakeClient(
        products=[{"id": product_id, "title": title}],
        states_by_id={product_id: states},
        launch_datetime=launch_datetime,
        all_publications=all_publications,
    )


class TestOnlineStoreIsLive(unittest.TestCase):
    def test_live_when_published_with_a_past_date(self):
        past = "2026-10-14T02:00:00Z"
        live, reason = PublicationCatchUp.online_store_is_live(
            [state(ONLINE_STORE, True, past)], now=NOW
        )
        self.assertTrue(live)
        self.assertEqual(reason, "")

    def test_live_when_published_with_no_date(self):
        live, _ = PublicationCatchUp.online_store_is_live(
            [state(ONLINE_STORE, True)], now=NOW
        )
        self.assertTrue(live)

    def test_not_live_while_the_publish_date_is_in_the_future(self):
        future = "2026-10-20T03:00:00Z"
        live, reason = PublicationCatchUp.online_store_is_live(
            [state(ONLINE_STORE, False, future)], now=NOW
        )
        self.assertFalse(live)
        self.assertIn("scheduled for", reason)

    def test_not_live_when_a_future_date_claims_to_be_published(self):
        # Belt and braces: the date wins over isPublished either way round.
        future = "2026-10-20T03:00:00Z"
        live, _ = PublicationCatchUp.online_store_is_live(
            [state(ONLINE_STORE, True, future)], now=NOW
        )
        self.assertFalse(live)

    def test_not_live_when_unpublished_without_a_date(self):
        live, reason = PublicationCatchUp.online_store_is_live(
            [state(ONLINE_STORE, False)], now=NOW
        )
        self.assertFalse(live)
        self.assertIn("not live yet", reason)

    def test_not_live_while_the_launch_datetime_is_in_the_future(self):
        # Published on the Online Store, but the theme is still gating it.
        live, reason = PublicationCatchUp.online_store_is_live(
            [state(ONLINE_STORE, True)],
            launch_datetime="2026-10-20T03:00:00Z",
            now=NOW,
        )
        self.assertFalse(live)
        self.assertIn("launch_datetime", reason)

    def test_live_once_the_launch_datetime_has_passed(self):
        live, reason = PublicationCatchUp.online_store_is_live(
            [state(ONLINE_STORE, True)],
            launch_datetime="2026-10-14T02:00:00Z",
            now=NOW,
        )
        self.assertTrue(live)
        self.assertEqual(reason, "")

    def test_a_launch_datetime_with_an_offset_is_understood(self):
        # What the metafield actually holds: JST rather than a Z suffix.
        live, _ = PublicationCatchUp.online_store_is_live(
            [state(ONLINE_STORE, True)],
            launch_datetime="2026-10-20T12:00:00+09:00",
            now=NOW,
        )
        self.assertFalse(live)

    def test_not_live_when_absent(self):
        live, reason = PublicationCatchUp.online_store_is_live(
            [state(SHOP, True)], now=NOW
        )
        self.assertFalse(live)
        self.assertIn("not on the Online Store", reason)


class TestOtherChannels(unittest.TestCase):
    STATES = [
        state(ONLINE_STORE, True),
        state(POS, False),
        state(SHOP, True),
    ]

    ALL = [ONLINE_STORE, POS, SHOP]

    def test_excludes_the_online_store_either_way(self):
        self.assertEqual(
            Publications.other_channels(
                self.STATES, published=False, all_publications=self.ALL
            ),
            [POS],
        )
        self.assertEqual(
            Publications.other_channels(self.STATES, published=True), [SHOP]
        )

    def test_a_channel_with_no_record_at_all_is_pending(self):
        # Shopify deletes the ResourcePublication on unpublish, so a product
        # taken off a channel carries no state for it - not one saying false.
        self.assertEqual(
            Publications.other_channels(
                [state(ONLINE_STORE, True)],
                published=False,
                all_publications=[ONLINE_STORE, POS, SHOP, GOOGLE],
            ),
            [POS, SHOP, GOOGLE],
        )

    def test_asking_what_is_unpublished_without_the_shop_list_is_refused(self):
        # Answering from `states` alone is what silently loses a drop.
        with self.assertRaises(ValueError) as ctx:
            Publications.other_channels(self.STATES, published=False)
        self.assertIn("all_publications is required", str(ctx.exception))


class TestCatchUpOtherChannels(unittest.TestCase):
    def test_publishes_the_pending_channels_once_the_store_is_live(self):
        client = client_for(
            [
                state(ONLINE_STORE, True, "2026-10-14T02:00:00Z"),
                state(SHOP, False),
                state(GOOGLE, False),
            ]
        )
        res = client.catch_up_other_channels_by_tag("26_oct_1", now=NOW)
        self.assertEqual(
            client.published,
            [
                ("gid://shopify/Product/1", SHOP["id"]),
                ("gid://shopify/Product/1", GOOGLE["id"]),
            ],
        )
        self.assertEqual(res["published"], {"JACKET": ["Shop", "Google & YouTube"]})

    def test_never_publishes_the_online_store_itself(self):
        client = client_for(
            [state(ONLINE_STORE, False, "2026-10-20T03:00:00Z"), state(SHOP, False)]
        )
        with self.assertRaises(RuntimeError) as ctx:
            client.catch_up_other_channels_by_tag("26_oct_1", now=NOW)
        self.assertIn("every product was skipped", str(ctx.exception))
        self.assertEqual(client.published, [])

    def test_a_dry_run_publishes_nothing(self):
        client = client_for([state(ONLINE_STORE, True), state(SHOP, False)])
        res = client.catch_up_other_channels_by_tag("26_oct_1", dry_run=True, now=NOW)
        self.assertEqual(client.published, [])
        self.assertEqual(res["published"], {"JACKET": ["Shop"]})

    def test_re_running_after_a_catch_up_is_a_no_op(self):
        client = client_for([state(ONLINE_STORE, True), state(SHOP, True)])
        res = client.catch_up_other_channels_by_tag("26_oct_1", now=NOW)
        self.assertEqual(client.published, [])
        self.assertEqual(res["up_to_date"], ["JACKET"])
        self.assertEqual(res["skipped"], {})

    def test_a_partial_skip_still_publishes_the_rest(self):
        client = FakeClient(
            products=[
                {"id": "gid://shopify/Product/1", "title": "LIVE"},
                {"id": "gid://shopify/Product/2", "title": "SCHEDULED"},
            ],
            states_by_id={
                "gid://shopify/Product/1": [
                    state(ONLINE_STORE, True),
                    state(SHOP, False),
                ],
                "gid://shopify/Product/2": [
                    state(ONLINE_STORE, False, "2026-10-20T03:00:00Z"),
                    state(SHOP, False),
                ],
            },
        )
        res = client.catch_up_other_channels_by_tag("26_oct_1", now=NOW)
        self.assertEqual(client.published, [("gid://shopify/Product/1", SHOP["id"])])
        self.assertEqual(list(res["published"]), ["LIVE"])
        self.assertEqual(list(res["skipped"]), ["SCHEDULED"])

    def test_an_unknown_tag_fails_loudly(self):
        client = FakeClient(products=[], states_by_id={})
        with self.assertRaises(RuntimeError) as ctx:
            client.catch_up_other_channels_by_tag("nope", now=NOW)
        self.assertIn("no products tagged", str(ctx.exception))


class TestUnpublishOtherChannels(unittest.TestCase):
    def test_removes_the_live_other_channels_only(self):
        client = client_for(
            [
                state(ONLINE_STORE, True),
                state(SHOP, True),
                state(GOOGLE, True),
                state(POS, False),
            ]
        )
        res = client.unpublish_other_channels("26_oct_1")
        self.assertEqual(
            client.unpublished,
            [
                ("gid://shopify/Product/1", SHOP["id"]),
                ("gid://shopify/Product/1", GOOGLE["id"]),
            ],
        )
        self.assertEqual(res, {"JACKET": ["Shop", "Google & YouTube"]})

    def test_leaves_a_scheduled_online_store_publication_alone(self):
        client = client_for(
            [state(ONLINE_STORE, False, "2026-10-20T03:00:00Z"), state(SHOP, True)]
        )
        client.unpublish_other_channels("26_oct_1")
        self.assertEqual(client.unpublished, [("gid://shopify/Product/1", SHOP["id"])])

    def test_nothing_to_do(self):
        client = client_for([state(ONLINE_STORE, True), state(SHOP, False)])
        self.assertEqual(client.unpublish_other_channels("26_oct_1"), {})
        self.assertEqual(client.unpublished, [])

    def test_a_dry_run_unpublishes_nothing(self):
        client = client_for([state(ONLINE_STORE, True), state(SHOP, True)])
        res = client.unpublish_other_channels("26_oct_1", dry_run=True)
        self.assertEqual(client.unpublished, [])
        self.assertEqual(res, {"JACKET": ["Shop"]})


class TestSweepPendingChannelPublishes(unittest.TestCase):
    """The recurring worker: publish what is due, leave what is not, and take
    finished products out of the queue."""

    def test_a_product_still_waiting_is_left_alone_and_stays_queued(self):
        client = client_for(
            [
                state(ONLINE_STORE, False, "2026-10-20T03:00:00Z"),
                state(SHOP, False),
            ]
        )
        res = client.sweep_pending_channel_publishes(now=NOW)

        self.assertEqual(client.published, [])
        # Still queued: dropping the tag now would lose the drop entirely.
        self.assertEqual(client.untagged, [])
        self.assertIn("JACKET", res["waiting"])

    def test_a_product_gated_by_launch_datetime_stays_queued(self):
        # The case the Online Store publish date cannot express: live on the
        # Online Store ahead of the drop, with the theme hiding the buy button.
        # Publishing the other channels here would put it on sale early.
        client = client_for(
            [state(ONLINE_STORE, True), state(SHOP, False)],
            launch_datetime="2026-10-20T12:00:00+09:00",
        )
        res = client.sweep_pending_channel_publishes(now=NOW)

        self.assertEqual(client.published, [])
        self.assertEqual(client.untagged, [])
        self.assertIn("JACKET", res["waiting"])

    def test_a_launched_product_is_published_then_dequeued(self):
        client = client_for(
            [
                state(ONLINE_STORE, True, "2026-10-14T02:00:00Z"),
                state(SHOP, False),
                state(GOOGLE, False),
            ]
        )
        res = client.sweep_pending_channel_publishes(now=NOW)

        self.assertEqual(
            [pid for _, pid in client.published], [SHOP["id"], GOOGLE["id"]]
        )
        self.assertEqual(len(client.untagged), 1)
        self.assertEqual(res["published"]["JACKET"], ["Shop", "Google & YouTube"])
        self.assertEqual(res["cleared"], ["JACKET"])

    def test_a_product_already_everywhere_is_just_dequeued(self):
        client = client_for(
            [
                state(ONLINE_STORE, True, "2026-10-14T02:00:00Z"),
                state(SHOP, True),
            ]
        )
        res = client.sweep_pending_channel_publishes(now=NOW)

        self.assertEqual(client.published, [])
        self.assertEqual(len(client.untagged), 1)
        self.assertEqual(res["cleared"], ["JACKET"])

    def test_an_empty_queue_is_a_success_not_a_failure(self):
        # Between drops this is the normal state; the per-drop catch-up raises
        # here, and a recurring worker must not.
        client = FakeClient(products=[], states_by_id={})
        res = client.sweep_pending_channel_publishes(now=NOW)
        self.assertEqual(res, {"published": {}, "waiting": {}, "cleared": []})

    def test_a_dry_run_neither_publishes_nor_dequeues(self):
        client = client_for(
            [
                state(ONLINE_STORE, True, "2026-10-14T02:00:00Z"),
                state(SHOP, False),
            ]
        )
        client.sweep_pending_channel_publishes(dry_run=True, now=NOW)
        self.assertEqual(client.published, [])
        self.assertEqual(client.untagged, [])

    def test_a_product_with_no_record_on_any_other_channel_is_published(self):
        # The regression. After unpublish_other_channels repairs an early
        # publish, the product's states carry only the Online Store. Reading
        # what is pending off those states alone finds nothing, so the sweeper
        # published nothing and then cleared the tag - dropping the product out
        # of the queue that was the only record it still needed publishing.
        client = client_for(
            [state(ONLINE_STORE, True, "2026-10-14T02:00:00Z")],
            all_publications=[ONLINE_STORE, POS, SHOP, GOOGLE],
        )
        res = client.sweep_pending_channel_publishes(now=NOW)

        self.assertEqual(
            client.published,
            [
                ("gid://shopify/Product/1", POS["id"]),
                ("gid://shopify/Product/1", SHOP["id"]),
                ("gid://shopify/Product/1", GOOGLE["id"]),
            ],
        )
        self.assertEqual(
            res["published"],
            {"JACKET": ["Point of Sale", "Shop", "Google & YouTube"]},
        )
        # Only now is it safe to dequeue.
        self.assertEqual(res["cleared"], ["JACKET"])

    def test_a_product_still_waiting_keeps_its_missing_channels_queued(self):
        # Same shape, but before the launch: nothing is published and the tag
        # stays, so the next sweep after the launch still finds the work.
        client = client_for(
            [state(ONLINE_STORE, True)],
            launch_datetime="2026-10-20T12:00:00+09:00",
            all_publications=[ONLINE_STORE, POS, SHOP, GOOGLE],
        )
        res = client.sweep_pending_channel_publishes(now=NOW)

        self.assertEqual(client.published, [])
        self.assertEqual(client.untagged, [])
        self.assertIn("JACKET", res["waiting"])


class TestCatchUpWithNoOtherChannelRecords(unittest.TestCase):
    """The same blind spot on the per-drop entry point."""

    def test_publishes_channels_the_product_has_no_record_for(self):
        client = client_for(
            [state(ONLINE_STORE, True, "2026-10-14T02:00:00Z")],
            all_publications=[ONLINE_STORE, POS, SHOP, GOOGLE],
        )
        res = client.catch_up_other_channels_by_tag("26_oct_1", now=NOW)

        self.assertEqual(
            res["published"],
            {"JACKET": ["Point of Sale", "Shop", "Google & YouTube"]},
        )
        self.assertEqual(res["up_to_date"], [])


class TestCatchUpTakesProducts(unittest.TestCase):
    """The core the sweeper and the by-tag path share."""

    def test_publishes_the_products_it_is_handed(self):
        # No tag lookup at all: the caller already has the products.
        client = client_for(
            [state(ONLINE_STORE, True, "2026-10-14T02:00:00Z"), state(SHOP, False)]
        )
        res = client.catch_up_other_channels(client.products, now=NOW)

        self.assertEqual(client.published, [("gid://shopify/Product/1", SHOP["id"])])
        self.assertEqual(res["published"], {"JACKET": ["Shop"]})

    def test_an_empty_list_does_not_even_ask_for_the_publications(self):
        client = FakeClient(products=[], states_by_id={})
        res = client.catch_up_other_channels([], now=NOW)

        self.assertEqual(res, {"published": {}, "up_to_date": [], "skipped": {}})
        self.assertEqual(client.published, [])

    def test_it_does_not_raise_on_an_all_skipped_run(self):
        # The raise belongs to the by-tag path, which was asked for a named
        # drop. The sweeper shares this core and must not blow up when nothing
        # has launched yet - the ordinary state between drops.
        client = client_for(
            [state(ONLINE_STORE, False, "2026-10-20T03:00:00Z"), state(SHOP, False)]
        )
        res = client.catch_up_other_channels(client.products, now=NOW)

        self.assertEqual(res["published"], {})
        self.assertIn("JACKET", res["skipped"])


if __name__ == "__main__":
    unittest.main()
