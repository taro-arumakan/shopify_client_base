import http.client
import io
import logging
import os
import pathlib
import re
import socket
import ssl
import time
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from PIL import Image

logger = logging.getLogger(__name__)

# Transient network errors worth retrying on Drive calls (long batch runs hit the
# occasional socket timeout / 5xx that should not abort the whole job). HTTPException
# covers IncompleteRead, which is what a download truncated mid-body raises and is not an
# OSError -- one of those aborted a 26AW registration run partway through a product.
_DRIVE_TRANSIENT = (
    TimeoutError,
    ConnectionError,
    socket.timeout,
    ssl.SSLError,
    OSError,
    http.client.HTTPException,
)


def _is_transient_drive_error(exc):
    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", 0) >= 500
    return isinstance(exc, _DRIVE_TRANSIENT)


def _execute_with_retry(request, what, max_retries=5):
    """Execute a Drive request, retrying transient errors.

    Only for idempotent reads (list / get): a create, copy or delete that
    succeeded server-side before erroring would be repeated. A lone backend 500
    on a folder lookup once failed a brand in the daily Instagram capture.
    """
    for attempt in range(max_retries):
        try:
            return request.execute()
        except Exception as e:
            if not _is_transient_drive_error(e) or attempt == max_retries - 1:
                raise
            wait = 3 * (attempt + 1)
            logger.warning(
                f"Drive {what} transient error ({e}); "
                f"retrying in {wait}s ({attempt + 1}/{max_retries})"
            )
            time.sleep(wait)


class GoogleDriveApiInterface:
    """
    Google Drive API Interface, inherited by GoogleApiInterface.
    """

    @staticmethod
    def natural_compare(k):
        def convert(text):
            return int(text) if text.isdigit() else text.lower()

        return [convert(c) for c in re.split("([0-9]+)", k)]

    def drive_images_to_local(
        self, folder_id, local_dir, filename_prefix="", sort_key_func=natural_compare
    ):
        os.makedirs(local_dir, exist_ok=True)
        image_details = self.get_drive_image_details(folder_id)
        image_details.sort(
            key=lambda f: sort_key_func(f["name"])
        )  # sort by natural order
        file_ids = [image["id"] for image in image_details]
        local_paths = [
            os.path.join(
                local_dir, f"{filename_prefix}_{str(seq).zfill(3)}_{image['name']}"
            )
            for seq, image in enumerate(image_details)
        ]
        return [
            self.download_and_process_image(file_id, local_path)
            for file_id, local_path in zip(file_ids, local_paths)
        ]

    def get_drive_image_details(self, folder_id):
        results = _execute_with_retry(
            self.drive_service.files().list(
                q=f"'{folder_id}' in parents",
                pageSize=1000,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            ),
            f"list in {folder_id}",
        )
        items = results.get("files", [])
        return [
            item
            for item in items
            if item["mimeType"].startswith("image/")
            and not item["name"].startswith(".")
        ]

    def download_and_process_image(self, file_id, local_path):
        if not os.path.exists(local_path):
            logger.info(f"  starting download of {file_id} to {local_path}")
            self.download_file_from_drive(file_id, local_path)
            local_path = self.resize_image_to_limit(local_path, local_path)
        return local_path

    def download_file_from_drive(self, file_id, destination_path, max_retries=5):
        """Download a Drive file, writing to `<destination_path>.part` first.

        download_and_process_image skips any path that already exists, so a
        half-written file left at destination_path by a timeout would be taken
        for a finished download on the next run and uploaded as-is. The bytes
        only get their real name once the transfer completed, and a failed
        attempt takes its `.part` with it.
        """
        request = self.drive_service.files().get_media(fileId=file_id)
        partial_path = f"{destination_path}.part"
        try:
            with io.FileIO(partial_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while done is False:
                    for attempt in range(max_retries):
                        try:
                            # next_chunk backs off internally on a transient
                            # error; the loop around it covers the rest.
                            status, done = downloader.next_chunk(
                                num_retries=max_retries
                            )
                        except Exception as e:
                            if (
                                not _is_transient_drive_error(e)
                                or attempt == max_retries - 1
                            ):
                                raise
                            wait = 3 * (attempt + 1)
                            logger.warning(
                                f"Drive download transient error for {file_id} ({e}); "
                                f"retrying in {wait}s ({attempt + 1}/{max_retries})"
                            )
                            time.sleep(wait)
                        else:
                            # Asking for a range past the end of a finished
                            # download earns a 416, so stop at the first
                            # chunk that comes back.
                            logger.debug(f"Download {int(status.progress() * 100)}%.")
                            break
            os.replace(partial_path, destination_path)
        except BaseException:
            pathlib.Path(partial_path).unlink(missing_ok=True)
            raise

    @staticmethod
    def rename_file_extension(file_path, image_mode):
        ext = {"RGBA": ".png"}.get(image_mode, ".jpg")
        p = pathlib.Path(file_path)
        if ext != p.suffix:
            new_path = os.path.join(p.parent, f"{p.stem}{ext}")
            logger.info(f"renaming output file to {new_path}")
            os.rename(file_path, new_path)
            return new_path
        return file_path

    @staticmethod
    def resize_image_to_limit(image_path, output_path, max_megapixels=15, max_mb=15):
        file_size_mb = os.path.getsize(image_path) / (1024 * 1024)
        with Image.open(image_path) as img:
            current_megapixels = (img.width * img.height) / 1_000_000
            if current_megapixels > max_megapixels or file_size_mb > max_mb:
                mp_scale = (
                    (max_megapixels / current_megapixels) ** 0.5
                    if current_megapixels > max_megapixels
                    else 1.0
                )
                size_scale = (
                    (max_mb / file_size_mb) ** 0.5 if file_size_mb > max_mb else 1.0
                )
                scale_factor = min(mp_scale, size_scale)

                new_width = int(img.width * scale_factor)
                new_height = int(img.height * scale_factor)

                resized_img = img.resize((new_width, new_height), Image.LANCZOS)
                if resized_img.mode == "RGBA":
                    kwargs = dict(format="PNG", optimize=True, compress_level=9)
                else:
                    kwargs = dict(format="JPEG", quality=85)
                resized_img.save(output_path, **kwargs)
                logger.info(
                    f"Image resized to {new_width}x{new_height} pixels and saved as {kwargs}"
                )
            image_mode = img.mode
        return GoogleDriveApiInterface.rename_file_extension(output_path, image_mode)

    def find_by_folder_id_by_name(
        self, parent_folder_id, item_name, item_type=None, exact_name=True
    ):
        """
        Find an item by its name inside a given parent folder.

        :param parent_folder_id: ID of the parent folder where the search is performed.
        :param item_name: Name of the item to search for.
        :return: item meta of the folder if found, None otherwise.
        """

        item_name = item_name.replace("'", "\\'")
        s = f"name{'=' if exact_name else ' contains '}'{item_name}'"
        query = f"'{parent_folder_id}' in parents and {s} and trashed=false"
        if item_type == "folder":
            query += " and mimeType='application/vnd.google-apps.folder'"
        results = _execute_with_retry(
            self.drive_service.files().list(
                q=query,
                pageSize=10,  # Assuming there are not too many items with the same name
                fields="files(id, name, webViewLink)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            ),
            f"lookup in {parent_folder_id}",
        )
        items = results.get("files", [])
        if items and len(items) > 1:
            raise RuntimeError(
                f"Multiple items found with the name '{item_name}' in parent folder '{parent_folder_id}'."
            )
        elif items:
            return items[0]

    def find_folder_id_by_name(self, parent_folder_id, folder_name):
        folder = self.find_by_folder_id_by_name(
            parent_folder_id=parent_folder_id, item_name=folder_name, item_type="folder"
        )
        if folder:
            return folder["id"]

    def find_or_create_folder_by_name(self, parent_folder_id, folder_name):
        if folder_id := self.find_folder_id_by_name(
            parent_folder_id=parent_folder_id, folder_name=folder_name
        ):
            res = folder_id
        else:
            file_metadata = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_folder_id],
            }

            folder = (
                self.drive_service.files()
                .create(body=file_metadata, fields="id", supportsAllDrives=True)
                .execute()
            )

            res = folder["id"]
        return res

    def list_folders(self, parent_folder_id):
        query = f"'{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder'"
        results = _execute_with_retry(
            self.drive_service.files().list(
                q=query,
                pageSize=1000,
                fields="files(id, name, webViewLink)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            ),
            f"list in {parent_folder_id}",
        )
        return results["files"]

    def list_files_in_folder(self, folder_id):
        """All non-folder files directly under a folder, as [{id, name}, ...]."""
        query = (
            f"'{folder_id}' in parents and trashed=false "
            "and mimeType != 'application/vnd.google-apps.folder'"
        )
        results = _execute_with_retry(
            self.drive_service.files().list(
                q=query,
                pageSize=1000,
                fields="files(id, name)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            ),
            f"list in {folder_id}",
        )
        return results.get("files", [])

    def upload_to_drive(self, filepath, mimetype, folder_id):
        """upload a local file to Google Drive folder"""
        media = MediaIoBaseUpload(
            open(filepath, "rb"), mimetype=mimetype, resumable=True
        )
        f = (
            self.drive_service.files()
            .create(
                body={"name": os.path.basename(filepath), "parents": [folder_id]},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        logger.info(f"Uploaded: {f.get('id')}")

    def replace_or_upload_to_drive(self, filepath, mimetype, folder_id):
        """Upload a local file, replacing any existing file of the same name.

        ``upload_to_drive`` always creates, so re-running an extraction would pile
        up duplicates. When a same-named file already exists we overwrite its
        contents in place via files().update (the service account can edit files it
        created on the shared drive but cannot always hard-delete them), otherwise
        we create a new one.

        A transient Drive error is retried, but a create is not idempotent: if it
        succeeded server-side before erroring, the retry can create a second copy
        (Drive search is eventually consistent, so the new file may not be seen
        yet). To keep the one-file-per-name invariant we look up existing copies via
        the folder's child listing, update the first, and trash any extras — which
        both prevents new duplicates and self-heals any left by an earlier run.
        """
        name = os.path.basename(filepath)
        max_retries = 5
        for attempt in range(max_retries):
            try:
                media = MediaIoBaseUpload(
                    open(filepath, "rb"), mimetype=mimetype, resumable=True
                )
                existing = [
                    f for f in self.list_files_in_folder(folder_id) if f["name"] == name
                ]
                if existing:
                    keep_id = existing[0]["id"]
                    self.drive_service.files().update(
                        fileId=keep_id, media_body=media, supportsAllDrives=True
                    ).execute()
                    logger.info(f"Updated: {keep_id} ({name})")
                    for dup in existing[1:]:
                        self.drive_service.files().update(
                            fileId=dup["id"],
                            body={"trashed": True},
                            supportsAllDrives=True,
                        ).execute()
                        logger.info(f"Trashed duplicate: {dup['id']} ({name})")
                else:
                    self.drive_service.files().create(
                        body={"name": name, "parents": [folder_id]},
                        media_body=media,
                        fields="id",
                        supportsAllDrives=True,
                    ).execute()
                    logger.info(f"Uploaded: {name}")
                return
            except Exception as e:
                if not _is_transient_drive_error(e) or attempt == max_retries - 1:
                    raise
                wait = 3 * (attempt + 1)
                logger.warning(
                    f"Drive upload transient error for {name} ({e}); "
                    f"retrying in {wait}s ({attempt + 1}/{max_retries})"
                )
                time.sleep(wait)

    def make_public_by_file_id(self, file_id):
        self.drive_service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True,
        ).execute()

    def make_private_by_file_id(self, file_id):
        # List permissions and delete the 'anyone' one
        perms = _execute_with_retry(
            self.drive_service.permissions().list(
                fileId=file_id,
                supportsAllDrives=True,
            ),
            f"permission list for {file_id}",
        )
        for perm in perms.get("permissions", []):
            if perm["type"] == "anyone":
                self.drive_service.permissions().delete(
                    fileId=file_id,
                    permissionId=perm["id"],
                    supportsAllDrives=True,
                ).execute()

    def get_direct_url(self, file_id):
        return f"https://drive.google.com/uc?export=download&id={file_id}"  # Direct download link format
