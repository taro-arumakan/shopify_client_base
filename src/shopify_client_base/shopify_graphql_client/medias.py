from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import time
import statistics
import requests

logger = logging.getLogger(__name__)


def is_evenly_spaced_stdev(lst, max_stddev=6.85):
    if len(lst) < 3:
        return True
    diffs = [b - a for a, b in zip(lst, lst[1:])]
    return statistics.stdev(diffs) <= max_stddev


class Medias:

    EXCLUDED_MEDIA_EXTENSTIONS = (".psd",)

    def medias_by_product_id(self, product_id):
        query = """
        query ProductMediaStatusByID($productId: ID!) {
            product(id: $productId) {
                media(first: 250) {
                    nodes {
                        id
                        alt
                        ... on MediaImage {
                            image{
                                url
                            }
                        }
                        mediaContentType
                        status
                        mediaErrors {
                            code
                            details
                            message
                        }
                        mediaWarnings {
                            code
                            message
                        }
                    }
                }
            }
        }
        """
        variables = {"productId": self.sanitize_id(product_id)}
        res = self.run_query(query, variables)
        return res["product"]["media"]["nodes"]

    def check_media_spacing(self, product_id):
        all_variants = self.product_variants_by_product_id(product_id)
        all_medias = self.medias_by_product_id(product_id)  # sorted by position
        for v in all_variants:
            logger.info(
                f"media url for {v['displayName']}: {v['media']['nodes'][0]['image']['url']}"
            )
        all_media_ids = [m["id"] for m in all_medias]
        all_media_start_positions = sorted(
            set(
                [
                    all_media_ids.index(variant["media"]["nodes"][0]["id"])
                    for variant in all_variants
                ]
                + [len(all_medias)]
            )
        )
        logger.info("All media URLs:")
        for idx, media in enumerate(all_medias):
            logger.info(
                f"{media['image']['url']}{' *' if idx in all_media_start_positions else ''}"
            )

        if not is_evenly_spaced_stdev(all_media_start_positions):
            error_message = f"media start positions are not evenly spaced: {all_media_start_positions}"
            error_message += "\nAll media URLs:"
            for media in all_medias:
                error_message += f"\n{media['image']['url']}"
            for v in all_variants:
                error_message += f"\nmedia url for {v['displayName']}: {v['media']['nodes'][0]['image']['url']}"
            raise RuntimeError(error_message)

    def medias_by_variant_id(self, variant_id):
        product_id = self.product_id_by_variant_id(variant_id)
        all_medias = self.medias_by_product_id(product_id)  # sorted by position
        all_media_ids = [m["id"] for m in all_medias]
        all_variants = self.product_variants_by_product_id(product_id)
        target_variant = [v for v in all_variants if v["id"] == variant_id]
        assert (
            len(target_variant) == 1
        ), f"{'No' if not target_variant else 'Multiple'} target variants: target_variants"
        target_variant = target_variant[0]
        target_media_start_position = all_media_ids.index(
            target_variant["media"]["nodes"][0]["id"]
        )

        # can have multiple variants for the same media e.g. size variations
        all_media_start_positions = sorted(
            set(
                [
                    all_media_ids.index(variant["media"]["nodes"][0]["id"])
                    for variant in all_variants
                ]
                + [len(all_medias)]
            )
        )
        assert is_evenly_spaced_stdev(
            all_media_start_positions
        ), f"media start positions are not evenly spaced: {all_media_start_positions}"
        target_media_end_position = all_media_start_positions[
            all_media_start_positions.index(target_media_start_position) + 1
        ]
        return all_medias[target_media_start_position:target_media_end_position]

    def medias_by_sku(self, sku):
        return self.medias_by_variant_id(self.variant_id_by_sku(sku))

    def reorder_product_medias(self, product_id, media_ids_in_order):
        """Move the given medias to the positions they hold in media_ids_in_order.

        Shopify has no insert: an upload always lands at the end, so putting images in the
        middle means uploading and then moving them. Pass every media of the product, in
        the order you want; the mutation runs as a job, so it is waited on before
        returning, otherwise a following read still sees the old order.
        """
        query = """
        mutation reorderProductMedia($id: ID!, $moves: [MoveInput!]!) {
            productReorderMedia(id: $id, moves: $moves) {
                job { id done }
                mediaUserErrors { code field message }
            }
        }
        """
        moves = [
            {"id": media_id, "newPosition": str(position)}
            for position, media_id in enumerate(media_ids_in_order)
        ]
        res = self.run_query(
            query, {"id": self.sanitize_id(product_id), "moves": moves}
        )
        if errors := res["productReorderMedia"]["mediaUserErrors"]:
            raise RuntimeError(f"Failed to reorder medias: {errors}")
        self.wait_for_job(res["productReorderMedia"]["job"]["id"])
        return res

    def media_by_product_id_by_file_name(self, product_id, name):
        medias = self.medias_by_product_id(self.sanitize_id(product_id))
        for media in medias:
            if name.rsplit(".", 1)[0] in media["image"]["url"]:
                return media

    def files_by_name_prefix(self, file_name):
        query = (
            """
        query {
            files(first:50 query:"filename:%s*") {
            nodes {
                id
                ... on MediaImage {
                    image {
                        url
                    }
                }
            }
            }
        }
        """
            % file_name.rsplit(".", 1)[0]
        )
        return self.run_query(query)["files"]["nodes"]

    def file_by_file_name(self, file_name):
        """20251113 FIXME
        I don't understand why but querying for a single file name did not work for only these two files,
        out of all 31 files prefixed with the same string.
        25_Winter_CP_2.jpg
        25_Winter_CP_25.jpg
        Relates to how Shopify indexes the files? For now query with relaxed criteria, then filter locally.
        """
        res = self.files_by_name_prefix(file_name)
        if len(res) > 1:
            res = [r for r in res if self._file_url(r).endswith(file_name)]
        assert (
            len(res) == 1
        ), f'{"Multiple" if res else "No"} files found for {file_name}: {res}'
        return res[0]

    def image_file_exists(self, name_or_url):
        """Exact name, extension included - unlike file_by_file_name, which
        returns a lone prefix match as-is."""
        file_name = name_or_url.rsplit("/", 1)[-1]
        return any(
            self._file_url(r).endswith(f"/{file_name}")
            for r in self.files_by_name_prefix(file_name)
        )

    @staticmethod
    def _file_url(node):
        return ((node.get("image") or {}).get("url") or "").rsplit("?", 1)[0]

    def file_id_by_file_name(self, file_name):
        res = self.file_by_file_name(file_name)
        return res["id"]

    def assign_existing_image_to_products_by_id(self, media_id, product_ids):
        query = """
        mutation FileUpdate($input: [FileUpdateInput!]!) {
            fileUpdate(files: $input) {
                userErrors {
                    code
                    field
                    message
                }
                files {
                    alt
                }
            }
        }
        """
        variables = {
            "input": {
                "id": media_id,
                "referencesToAdd": [
                    self.sanitize_id(product_id) for product_id in product_ids
                ],
            }
        }
        res = self.run_query(query, variables)
        if user_errors := res["fileUpdate"]["userErrors"]:
            raise RuntimeError(f"Failed to assign media to products: {user_errors}")
        return res

    def assign_images_to_product(self, resource_urls, alts, product_id):
        mutation_query = """
        mutation productCreateMedia($media: [CreateMediaInput!]!, $productId: ID!) {
            productCreateMedia(media: $media, productId: $productId) {
                media {
                    id
                    alt
                    mediaContentType
                    status
                }
                userErrors {
                    field
                    message
                }
                product {
                    id
                    title
                }
            }
        }
        """

        medias = [
            {"originalSource": url, "alt": alt, "mediaContentType": "IMAGE"}
            for url, alt in zip(resource_urls, alts)
        ]

        variables = {"media": medias, "productId": self.sanitize_id(product_id)}

        res = self.run_query(mutation_query, variables)

        logger.debug("Initial media status:")
        logger.debug(res)

        if res["productCreateMedia"]["userErrors"]:
            raise RuntimeError(
                f"Failed to assign images to product: {res['productCreateMedia']['userErrors']}"
            )

        status = self.wait_for_media_processing_completion(product_id)
        if not status:
            raise Exception("Error during media processing")

        return res

    def assign_image_to_skus_by_position(self, product_id, image_position, skus):
        logger.info(f"assigning a variant image to {skus}")
        media_nodes = self.medias_by_product_id(product_id)
        media_id = media_nodes[image_position]["id"]
        return self.assign_image_to_skus(product_id, media_id, skus)

    def assign_image_to_skus(self, product_id, media_id, skus):
        variant_ids = [self.variant_id_by_sku(sku) for sku in skus]
        return self.assign_image_to_variant_ids(product_id, media_id, variant_ids)

    def assign_image_to_variant_ids(self, product_id, media_id, variant_ids):
        variants = [
            self.variant_by_variant_id(variant_id) for variant_id in variant_ids
        ]
        for variant in variants:
            if len(variant["media"]["nodes"]) > 0:
                self.detach_variant_media(
                    product_id, variant["id"], variant["media"]["nodes"][0]["id"]
                )
        query = """
        mutation productVariantAppendMedia($productId: ID!, $variantMedia: [ProductVariantAppendMediaInput!]!) {
            productVariantAppendMedia(productId: $productId, variantMedia: $variantMedia) {
                product {
                    id
                }
                productVariants {
                    id
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {
            "productId": product_id,
            "variantMedia": [
                {"variantId": vid, "mediaIds": [media_id]} for vid in variant_ids
            ],
        }
        return self.run_query(query, variables)

    def remove_product_media_by_product_id(self, product_id, media_ids=None):
        product_id = self.sanitize_id(product_id)
        if not media_ids:
            media_nodes = self.medias_by_product_id(product_id)
            media_ids = [node["id"] for node in media_nodes]

        if not media_ids:
            logger.debug(f"Nothing to delete for {product_id}")
            return True

        logger.info(f"Going to delete {media_ids} from {product_id}")

        query = """
        mutation deleteProductMedia($productId: ID!, $mediaIds: [ID!]!) {
            productDeleteMedia(productId: $productId, mediaIds: $mediaIds) {
                deletedMediaIds
                product {
                    id
                }
                mediaUserErrors {
                    code
                    field
                    message
                }
            }
        }
        """
        variables = {"productId": product_id, "mediaIds": media_ids}
        res = self.run_query(query, variables)
        logger.info(f"Initial media status for deletion:\n{res}")
        status = self.wait_for_media_processing_completion(product_id)
        if not status:
            raise Exception("Error during media processing")
        return res

    def remove_unrelated_medias_by_variant_ids_to_keep(
        self, product_id, variant_ids_to_keep
    ):
        all_medias = self.medias_by_product_id(product_id)
        keep_medias = sum(
            (self.medias_by_variant_id(vid) for vid in variant_ids_to_keep), []
        )
        media_ids_to_remove = [
            m["id"]
            for m in all_medias
            if m["id"] not in [km["id"] for km in keep_medias]
        ]
        self.remove_product_media_by_product_id(product_id, media_ids_to_remove)

    def detach_variant_media(self, product_id, variant_id, media_id):
        query = """
        mutation productVariantDetachMedia($productId: ID!, $variantMedia: [ProductVariantDetachMediaInput!]!) {
            productVariantDetachMedia(productId: $productId, variantMedia: $variantMedia) {
                product {
                    id
                }
                productVariants {
                    id
                    media(first: 5) {
                        nodes {
                            id
                        }
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
            "productId": self.sanitize_id(product_id),
            "variantMedia": [{"variantId": variant_id, "mediaIds": [media_id]}],
        }
        return self.run_query(query, variables)

    def generate_staged_upload_targets(self, file_names, mime_types):
        query = """
        mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
            stagedUploadsCreate(input: $input) {
                stagedTargets {
                    url
                    resourceUrl
                    parameters {
                        name
                        value
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
            "input": [
                {
                    "resource": "IMAGE",
                    "filename": file_name,
                    "mimeType": mime_type,
                    "httpMethod": "POST",
                }
                for file_name, mime_type in zip(file_names, mime_types)
            ]
        }
        res = self.run_query(query, variables)
        return res["stagedUploadsCreate"]["stagedTargets"]

    def create_files_from_staged_targets(self, resource_urls, alts=None):
        query = """
        mutation fileCreate($files: [FileCreateInput!]!) {
            fileCreate(files: $files) {
                files {
                    id
                    fileStatus
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        alts = alts or [""] * len(resource_urls)
        variables = {
            "files": [
                {"originalSource": url, "contentType": "IMAGE", "alt": alt}
                for url, alt in zip(resource_urls, alts)
            ]
        }
        res = self.run_query(query, variables)
        if errors := res["fileCreate"]["userErrors"]:
            raise RuntimeError(f"Failed to create files: {errors}")
        return res["fileCreate"]["files"]

    def wait_for_file_processing_completion(self, file_ids, timeout_minutes=5):
        query = """
        query fileStatus($ids: [ID!]!) {
            nodes(ids: $ids) {
                ... on MediaImage {
                    id
                    fileStatus
                    image {
                        url
                    }
                }
            }
        }
        """
        poll_interval = 1
        for _ in range(int(timeout_minutes * 60 / poll_interval)):
            nodes = self.run_query(query, {"ids": file_ids})["nodes"]
            if failed := [n for n in nodes if n and n["fileStatus"] == "FAILED"]:
                raise RuntimeError(f"file processing failed: {failed}")
            if all(n and n["fileStatus"] == "READY" for n in nodes):
                return {n["id"]: n["image"]["url"] for n in nodes}
            time.sleep(poll_interval)
        raise TimeoutError(
            f"files not ready after {timeout_minutes} minutes: {file_ids}"
        )

    def upload_and_assign_images_to_product(
        self, product_id, local_paths, remove_existings=True
    ):
        local_paths = [
            path
            for path in local_paths
            if not path.endswith(self.EXCLUDED_MEDIA_EXTENSTIONS)
        ]
        file_names = [local_path.rsplit("/", 1)[-1] for local_path in local_paths]
        mime_types = [
            f"image/{local_path.rsplit('.', 1)[-1].lower()}"
            for local_path in local_paths
        ]
        staged_targets = self.generate_staged_upload_targets(file_names, mime_types)
        logger.info(f"generated staged upload targets: {len(staged_targets)}")
        ress = []
        ress.append(
            self.upload_images_to_shopify_parallel(
                staged_targets, local_paths, mime_types
            )
        )
        if remove_existings:
            ress.append(self.remove_product_media_by_product_id(product_id))
        ress.append(
            self.assign_images_to_product(
                [target["resourceUrl"] for target in staged_targets],
                alts=file_names,
                product_id=product_id,
            )
        )
        return ress

    def upload_image(self, target, local_path, mime_type):
        file_name = local_path.rsplit("/", 1)[-1]
        logger.info(f"  processing {file_name}")
        payload = {
            "Content-Type": mime_type,
            "success_action_status": "201",
            "acl": "private",
        }
        payload.update(
            {param["name"]: param["value"] for param in target["parameters"]}
        )
        try:
            with open(local_path, "rb") as f:
                logger.debug(f"  starting upload of {local_path}")
                response = requests.post(
                    target["url"], files={"file": (file_name, f)}, data=payload
                )
            logger.debug(f"upload response: {response.status_code}")
            if response.status_code != 201:
                logger.error(
                    f"!!! upload failed !!!\n\n{local_path}:\n{target}\n\n{response.text}\n\n"
                )
                response.raise_for_status()
            return response
        except Exception as e:
            logger.error(f"Error uploading {local_path}: {e}")
            raise

    def upload_images_to_shopify_parallel(
        self, staged_targets, local_paths, mime_types, max_workers=20
    ):
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_upload = {
                executor.submit(
                    self.upload_image, target, local_path, mime_type
                ): local_path
                for target, local_path, mime_type in zip(
                    staged_targets, local_paths, mime_types
                )
            }
            for future in as_completed(future_to_upload):
                try:
                    response = future.result()
                    if response is not None:
                        results.append(response)
                except Exception as e:
                    local_path = future_to_upload[future]
                    logger.error(f"Error uploading file {local_path}: {e}")
        return results

    def upload_images_to_shopify(self, staged_targets, local_paths, mime_types):
        return [
            self.upload_image(target, local_path, mime_type)
            for target, local_path, mime_type in zip(
                staged_targets, local_paths, mime_types
            )
        ]

    def wait_for_media_processing_completion(self, product_id, timeout_minutes=10):
        poll_interval = 0.5  # Poll every 0.5 second
        max_attempts = int((timeout_minutes * 60) / poll_interval)
        attempts = 0

        while attempts < max_attempts:
            media_nodes = self.medias_by_product_id(self.sanitize_id(product_id))
            processing_items = [
                node for node in media_nodes if node["status"] == "PROCESSING"
            ]
            failed_items = [node for node in media_nodes if node["status"] == "FAILED"]

            if failed_items:
                logger.info("Some media failed to process:")
                for item in failed_items:
                    logger.info(
                        f"Status: {item['status']}, Errors: {item['mediaErrors']}"
                    )
                return False

            if not processing_items:
                logger.info("All media have completed processing.")
                return True

            logger.info("Media still processing. Waiting...")
            time.sleep(poll_interval)
            attempts += 1

        logger.info("Timeout reached while waiting for media processing completion.")
        return False

    def replace_image_files(self, local_paths):
        """
        Replace media files. Local file names must match with the existing remote files.
        """
        mime_types = [
            f"image/{local_path.rsplit('.', 1)[-1].lower()}"
            for local_path in local_paths
        ]
        staged_targets = self.generate_staged_upload_targets(local_paths, mime_types)
        logger.info(f"generated staged upload targets: {len(staged_targets)}")
        self.upload_images_to_shopify(staged_targets, local_paths, mime_types)
        return self._replace_image_files_with_staging(staged_targets, local_paths)

    def _replace_image_files_with_staging(self, staged_targets, local_paths):
        filenames = [
            self.sanitize_image_name(path.rsplit("/", 1)[-1]) for path in local_paths
        ]
        file_ids = [self.file_id_by_file_name(filename) for filename in filenames]
        resource_urls = [target["resourceUrl"] for target in staged_targets]
        query = """
        mutation FileUpdate($input: [FileUpdateInput!]!) {
            fileUpdate(files: $input) {
                userErrors {
                    code
                    field
                    message
                }
                files {
                    alt
                }
            }
        }
        """
        medias = [
            {"id": file_id, "originalSource": url, "alt": filename}
            for file_id, url, filename in zip(file_ids, resource_urls, filenames)
        ]

        variables = {"input": medias}
        res = self.run_query(query, variables)
        if res["fileUpdate"]["userErrors"]:
            raise RuntimeError(
                f"Failed to assign images to product: {res['fileUpdate']['userErrors']}"
            )
        return res["fileUpdate"]
