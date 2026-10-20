"""Publish a drop to the sales channels a scheduled launch had to leave behind.

Shopify only honours `PublicationInput.publishDate` on the Online Store -
"Only online store channels support future publishing". Passing a future date
to Shop, Google & YouTube or Facebook & Instagram does not schedule anything,
it publishes immediately, which puts the drop in front of shoppers days before
the store launch. So `publish_by_product_or_collection_id` publishes only the
Online Store whenever a scheduled time is given, and this mixin is what fills
in the rest once the launch has actually happened.

Nothing has to be scheduled per drop. `publish_by_product_or_collection_id`
tags what it defers `pending-channel-publish`, and an hourly sweeper job
(calling `sweep_pending_channel_publishes` for every shop) drains that queue:
publish with a scheduled_time and the other channels catch up on their own.
That is the whole point - the other channels are not time-sensitive, so a
developer should never have to remember them.

    client = BrandClient()                                 # any Client subclass
    client.sweep_pending_channel_publishes(dry_run=True)   # the worker
    client.catch_up_other_channels_by_tag("26_oct_1")      # force one drop now
    client.unpublish_other_channels("26_oct_1")            # the repair

`catch_up_other_channels_by_tag` takes any tag, so it is also how a product
unpublished by hand gets back onto its channels: `unpublish_other_channels`
does not re-add `pending-channel-publish`, so the sweeper will not find it.
"""

import datetime
import logging

from shopify_client_base.shopify_graphql_client.publications import (
    ONLINE_STORE,
    PENDING_CHANNEL_PUBLISH,
)

LAUNCH_DATETIME_NAMESPACE = "custom"
LAUNCH_DATETIME_KEY = "launch_datetime"

logger = logging.getLogger(__name__)


def _parse_publish_date(value):
    if not value:
        return None
    # Shopify returns ISO 8601 with a Z suffix.
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


class PublicationCatchUp:
    """Orchestration over the client's own publication primitives.

    Inherited by ShopifyGraphqlClient, like MergeProductsAsVariants and
    ProductVariantsToProducts. It owns no GraphQL of its own: everything it
    needs - products_by_tag, publications, product_publication_states, the
    publish/unpublish pair, product_metafield_by_product_id,
    remove_product_tags - is already on self.
    """

    @staticmethod
    def online_store_is_live(states, launch_datetime=None, now=None):
        """Has the Online Store launch already happened for this product?

        Returns (True, "") once it has, and (False, reason) while it has not,
        so the caller can log why a product was left alone.
        """
        now = now or datetime.datetime.now(datetime.timezone.utc)
        # A drop can sit published on the Online Store before it opens, gated
        # by the theme on custom.launch_datetime: in stock, but with no
        # add-to-cart button. Nothing in the publication state says so, so
        # check the gate first - otherwise the other channels start selling it
        # at the moment the Online Store publication flips, which is days
        # before the drop opens.
        launch = _parse_publish_date(launch_datetime)
        if launch and launch > now:
            return False, f"launch_datetime is {launch}"
        state = next(
            (s for s in states if s["publication"]["name"] == ONLINE_STORE), None
        )
        if state is None:
            return False, f"not on the {ONLINE_STORE} at all"
        publish_date = _parse_publish_date(state.get("publishDate"))
        if publish_date and publish_date > now:
            return False, f"{ONLINE_STORE} publish scheduled for {publish_date}"
        if not state["isPublished"]:
            return False, f"{ONLINE_STORE} publication is not live yet"
        return True, ""

    def product_launch_datetime(self, product_id):
        """The product's gated launch time, or None when it isn't gated."""
        metafield = self.product_metafield_by_product_id(
            product_id, LAUNCH_DATETIME_NAMESPACE, LAUNCH_DATETIME_KEY
        )
        return metafield["value"] if metafield else None

    def _tagged_products(self, tag):
        products = self.products_by_tag(tag)
        if not products:
            raise RuntimeError(f"no products tagged {tag!r} in {self.shop_name}")
        logger.info(f"{len(products)} product(s) tagged {tag!r} in {self.shop_name}")
        return products

    def _publish_to(self, product_id, publications, dry_run):
        if dry_run:
            return
        for publication in publications:
            self.publish_by_product_or_collection_id_and_publication_id(
                product_or_collection_id=product_id,
                publication_id=publication["id"],
            )

    def catch_up_other_channels(self, products, dry_run=False, now=None):
        """Publish each product to every channel its scheduled launch skipped.

        Products whose Online Store launch has not happened yet are left alone
        - publishing them now is the very thing this is meant to prevent, so
        they come back under "skipped" for the caller to decide about.

        Takes products rather than a tag because its two callers come by them
        differently: the sweeper already holds the queue it read, and
        catch_up_other_channels_by_tag resolves a tag.
        """
        if not products:
            return {"published": {}, "up_to_date": [], "skipped": {}}

        all_publications = self.publications(include_products=False)
        published, skipped, up_to_date = {}, {}, []

        for product in products:
            title = product["title"]
            states = self.product_publication_states(product["id"])
            live, reason = self.online_store_is_live(
                states,
                launch_datetime=self.product_launch_datetime(product["id"]),
                now=now,
            )
            if not live:
                logger.info(f"{self.shop_name}: {title} still waiting - {reason}")
                skipped[title] = reason
                continue

            pending = self.other_channels(
                states, published=False, all_publications=all_publications
            )
            if not pending:
                logger.info(f"{self.shop_name}: {title} is already on every channel")
                up_to_date.append(title)
                continue

            names = [p["name"] for p in pending]
            logger.info(
                f"{self.shop_name}: {'would publish' if dry_run else 'publishing'} "
                f"{title} to {', '.join(names)}"
            )
            self._publish_to(product["id"], pending, dry_run)
            published[title] = names

        return {"published": published, "up_to_date": up_to_date, "skipped": skipped}

    def catch_up_other_channels_by_tag(self, tag, dry_run=False, now=None):
        """catch_up_other_channels for every product carrying `tag`.

        The manual path. The sweeper is the automatic one and needs no tag;
        this is for forcing a named drop before the next sweep, or for a
        product carrying no `pending-channel-publish` tag to be found by.
        """
        res = self.catch_up_other_channels(
            self._tagged_products(tag), dry_run=dry_run, now=now
        )
        logger.info(
            f"catch-up {'(dry run) ' if dry_run else ''}done: "
            f"{len(res['published'])} published, "
            f"{len(res['up_to_date'])} already current, "
            f"{len(res['skipped'])} skipped"
        )
        if res["skipped"] and not res["published"]:
            # Asked for a named drop and given nothing back: either the tag is
            # wrong or the launch has not happened. Both are mistakes worth
            # stopping on, since the caller picked this over waiting for the
            # sweeper, for which the same state is the ordinary one.
            raise RuntimeError(
                f"nothing to catch up for {tag!r}: every product was skipped - "
                f"{res['skipped']}"
            )
        return res

    def sweep_pending_channel_publishes(self, dry_run=False, now=None):
        """Drain the queue of drops whose launch left other channels behind.

        The same catch-up, over a fixed tag that
        publish_by_product_or_collection_id applies, so no one has to schedule
        one per drop. Run it on an interval.

        Two differences from the by-tag path, both because this is a worker
        rather than a one-shot: a product still waiting for its launch is
        normal rather than an error, and an empty sweep is a success; and a
        product that has reached every channel loses the tag, which is what
        takes it out of the queue.
        """
        products = self.products_by_tag(PENDING_CHANNEL_PUBLISH)
        if not products:
            logger.info(f"{self.shop_name}: nothing queued")
            return {"published": {}, "waiting": {}, "cleared": []}

        res = self.catch_up_other_channels(products, dry_run=dry_run, now=now)

        # Everything that did not get skipped is on every channel now, whether
        # this run put it there or it already was. Dequeued only after those
        # publishes, never before: a product that drops out of the queue
        # without being published is one nobody will notice is missing.
        # Iterating `products` rather than the result keeps the original order
        # and keeps the id, which the result - keyed by title - does not carry.
        cleared = []
        for product in products:
            title = product["title"]
            if title in res["skipped"]:
                continue
            logger.info(
                f"{self.shop_name}: {'would clear' if dry_run else 'clearing'} "
                f"{PENDING_CHANNEL_PUBLISH!r} from {title}"
            )
            if not dry_run:
                self.remove_product_tags(product["id"], PENDING_CHANNEL_PUBLISH)
            cleared.append(title)

        logger.info(
            f"{self.shop_name}: sweep {'(dry run) ' if dry_run else ''}done - "
            f"{len(res['published'])} published, {len(cleared)} cleared, "
            f"{len(res['skipped'])} still waiting"
        )
        return {
            "published": res["published"],
            "waiting": res["skipped"],
            "cleared": cleared,
        }

    def unpublish_other_channels(self, tag, dry_run=False):
        """Take tagged products back off every channel but the Online Store.

        The repair for products published to Shop / Google / Meta ahead of
        their launch. The Online Store publication, scheduled or live, is
        never touched.

        Note this does not re-add `pending-channel-publish`, so the sweeper
        will not pick these up again: catch_up_other_channels(tag) is how they
        go back on once the launch has happened.
        """
        unpublished = {}

        for product in self._tagged_products(tag):
            title = product["title"]
            states = self.product_publication_states(product["id"])
            live = self.other_channels(states, published=True)
            if not live:
                logger.info(f"{title} is on no channel but the {ONLINE_STORE}")
                continue
            names = [p["name"] for p in live]
            logger.info(
                f"{'would unpublish' if dry_run else 'unpublishing'} {title} "
                f"from {', '.join(names)}"
            )
            if not dry_run:
                # By publication id, not name: product_publication_states
                # already carries the ids, and the name-based call would
                # re-query every publication (which lists 250 products each)
                # per product.
                for publication in live:
                    self.unpublish_by_product_or_collection_id_and_publication_id(
                        product_or_collection_id=product["id"],
                        publication_id=publication["id"],
                    )
            unpublished[title] = names

        logger.info(
            f"unpublish {'(dry run) ' if dry_run else ''}done: "
            f"{len(unpublished)} product(s) taken off other channels"
        )
        return unpublished
