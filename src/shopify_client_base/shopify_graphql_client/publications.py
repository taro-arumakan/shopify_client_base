import datetime
import logging
from shopify_client_base.exceptions import NoProductsFoundException

logger = logging.getLogger(__name__)

# The only channel whose publishDate Shopify honours - see
# publish_by_product_or_collection_id.
ONLINE_STORE = "Online Store"

# Marks a product whose launch left channels behind, so the sweeper can find it
# without being told a per-drop tag. The queue lives in Shopify rather than in a
# scheduler: the tag goes on here and comes off once the other channels are
# published, so a missed or late sweep loses nothing.
PENDING_CHANNEL_PUBLISH = "pending-channel-publish"


class Publications:
    def publications(self, include_products=True):
        """Every publication on the shop.

        `include_products=False` leaves out the 250 products per publication,
        which is all most callers want: the set of channels that exist. That
        set is also the only way to tell which channels a product is *not* on,
        since those leave no record in the product's own publication states -
        see `other_channels`.
        """
        query = """
        query publications{
            publications(first:100) {
                nodes {
                    id
                    name
                    catalog {
                        title
                        status
                    }
                    %s
                }
            }
        }
        """ % (
            """
            products(first:250) {
                nodes {
                    id
                    title
                    variants(first:30) {
                        nodes {
                            id
                            title
                            sku
                        }
                    }
                }
            }"""
            if include_products
            else ""
        )
        res = self.run_query(query)
        return res["publications"]["nodes"]

    def publication_by_publication_name(self, name):
        publications = self.publications()
        for publication in publications:
            if publication["name"] == name:
                return publication

    def online_store_publication(self):
        publication = self.publication_by_publication_name(ONLINE_STORE)
        if len(publication["products"]["nodes"]) == 250:
            raise RuntimeError(
                f"number of products included in the publicaiton may have exceeded the query limit. check the products."
            )
        return publication

    def publish_by_product_or_collection_id(
        self,
        product_or_collection_id,
        scheduled_time: datetime.datetime = None,
        online_store_only=False,
    ):
        logger.info(
            f"Publishing {product_or_collection_id} {f'at {scheduled_time}' if scheduled_time else 'immediately'}"
        )
        publications = self.publications(include_products=False)
        params = {"product_or_collection_id": product_or_collection_id}
        skipped = []
        for publication in publications:
            params["publication_id"] = publication["id"]
            is_online_store = publication["name"] == ONLINE_STORE
            if scheduled_time and is_online_store:
                self.publish_by_product_or_collection_id_and_publication_id(
                    scheduled_time=scheduled_time, **params
                )
            elif is_online_store or not (scheduled_time or online_store_only):
                self.publish_by_product_or_collection_id_and_publication_id(**params)
            else:
                skipped.append(publication["name"])
        if skipped:
            # Shopify ignores publishDate outside the Online Store, so publishing
            # these now would put the product live ahead of the launch.
            logger.info(
                f"  not publishing to {', '.join(skipped)}: only the {ONLINE_STORE} "
                f"honours a publish date. Tagging {PENDING_CHANNEL_PUBLISH!r} for "
                f"the sweeper to pick up once {scheduled_time} has passed."
            )
            if "/Product/" in product_or_collection_id:
                self.add_product_tags(product_or_collection_id, PENDING_CHANNEL_PUBLISH)
            else:
                # Collections cannot carry tags, so there is nothing for the
                # sweeper to find. Say so rather than leave it to be discovered
                # when the drop is missing from half its channels.
                logger.warning(
                    f"  {product_or_collection_id} is not a product: catch it up "
                    f"by hand after {scheduled_time}"
                )

    def activate_and_publish_by_product_id(
        self, product_id, scheduled_time: datetime.datetime = None
    ):
        """
        publish the product immediately or at a scheduled time, and activate it
        """
        product_id = self.sanitize_id(product_id)
        self.publish_by_product_or_collection_id(product_id, scheduled_time)
        self.update_product_status(product_id, "ACTIVE")

    def publish_by_product_or_collection_id_and_publication_id(
        self,
        product_or_collection_id,
        publication_id,
        scheduled_time: datetime.datetime = None,
    ):
        query = """
        mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
            publishablePublish(id: $id, input: $input) {
                publishable {
                    ... on Product {
                        id
                        title
                    }
                    ... on Collection {
                        id
                        title
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
            "id": product_or_collection_id,
            "input": {"publicationId": publication_id},
        }
        if scheduled_time:
            assert (
                scheduled_time.tzinfo
            ), f"scheduled_time must be timezone-aware: {scheduled_time}"
            variables["input"]["publishDate"] = scheduled_time.isoformat()
        res = self.run_query(query, variables)
        if user_errors := res["publishablePublish"]["userErrors"]:
            raise RuntimeError(f"Failed to publish product: {user_errors}")
        return res["publishablePublish"]["publishable"]

    def unpublish_by_product_or_collection_id_and_publication_id(
        self, product_or_collection_id, publication_id
    ):
        query = """
        mutation publishableUnpublish($id: ID!, $input: [PublicationInput!]!) {
            publishableUnpublish(id: $id, input: $input) {
                publishable {
                    ... on Product {
                        id
                        title
                    }
                    ... on Collection {
                        id
                        title
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
            "id": product_or_collection_id,
            "input": {"publicationId": publication_id},
        }
        res = self.run_query(query, variables)
        if user_errors := res["publishableUnpublish"]["userErrors"]:
            raise RuntimeError(f"Failed to unpublish product: {user_errors}")
        return res["publishableUnpublish"]["publishable"]

    def unpublish_by_product_or_collection_id(
        self, product_or_collection_id, publication_names
    ):
        """Unpublish from the named publications.

        The names are required on purpose: unpublishing everything would take
        the Online Store down with the rest, and a scheduled Online Store
        publication is usually the one thing worth keeping.
        """
        publication_names = set(publication_names)
        logger.info(
            f"Unpublishing {product_or_collection_id} from "
            f"{', '.join(sorted(publication_names))}"
        )
        res = []
        for publication in self.publications(include_products=False):
            if publication["name"] in publication_names:
                res.append(
                    self.unpublish_by_product_or_collection_id_and_publication_id(
                        product_or_collection_id, publication["id"]
                    )
                )
                publication_names.discard(publication["name"])
        if publication_names:
            raise RuntimeError(
                f"no such publication(s): {', '.join(sorted(publication_names))}"
            )
        return res

    def product_publication_states(self, product_id):
        """The sales channels this product has a publication record for.

        NOT every channel that exists. `onlyPublished: false` returns the
        record for a publication that is scheduled but not yet live, with
        isPublished false and its publishDate - which is how the catch-up
        tells a scheduled launch from one that has already happened. It does
        not invent records for channels the product was never on or has been
        unpublished from: Shopify deletes the ResourcePublication, and the
        channel then disappears from this response entirely.

        So this answers "where is it published", never "where is it missing".
        For that, diff against `publications(include_products=False)` - see
        `other_channels` below.
        """
        query = """
        query productPublications($id: ID!) {
            product(id: $id) {
                id
                title
                resourcePublicationsV2(first: 50, onlyPublished: false) {
                    nodes {
                        isPublished
                        publishDate
                        publication {
                            id
                            name
                        }
                    }
                }
            }
        }
        """
        product_id = self.sanitize_id(product_id)
        res = self.run_query(query, {"id": product_id})
        if not (product := res.get("product")):
            raise NoProductsFoundException(f"No product found for {product_id}")
        return product["resourcePublicationsV2"]["nodes"]

    @staticmethod
    def other_channels(states, published, all_publications=None):
        """Publications besides the Online Store, filtered by publish state.

        `published=True` reads straight off `states`, which is exactly what
        those records describe.

        `published=False` cannot, and needs `all_publications`. A product's
        publication states only carry the channels it has a record for:
        Shopify deletes the ResourcePublication on unpublish, so a channel the
        product was taken off is absent from `states` altogether rather than
        present with isPublished false. Read naively, a product that was
        unpublished from every channel looks like one that is already on all
        of them - and the sweeper then dequeues it having published nothing,
        silently. The channels it is missing from are the shop's publications
        minus the ones it is published to, so that list has to be passed in.

        A record with isPublished false is real too - a publication scheduled
        and not yet live - and counts as pending just the same.
        """
        if published:
            return [
                s["publication"]
                for s in states
                if s["publication"]["name"] != ONLINE_STORE and s["isPublished"]
            ]
        if all_publications is None:
            raise ValueError(
                "all_publications is required to find the channels a product "
                "is not on: absent channels leave no record in its publication "
                "states"
            )
        live_names = {s["publication"]["name"] for s in states if s["isPublished"]}
        return [
            publication
            for publication in all_publications
            if publication["name"] != ONLINE_STORE
            and publication["name"] not in live_names
        ]
