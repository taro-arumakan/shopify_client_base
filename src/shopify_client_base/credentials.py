import os
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv


@dataclass
class Credentials:
    shop_name: str
    access_token: str
    google_credential_path: str
    google_sheet_id: str | None


def credentials(shop_name, dotenv_path=None):
    """Read a store's credentials from the environment, loading .env first.

    Expects `<shop_name>-ACCESS_TOKEN`, `GOOGLE_CREDENTIAL_PATH` and optionally
    `<shop_name>-GSPREAD_ID`. The .env is looked up from the current directory
    upwards unless `dotenv_path` is given.
    """
    load_dotenv(dotenv_path or find_dotenv(usecwd=True), override=True)
    return Credentials(
        shop_name=shop_name,
        access_token=os.environ[f"{shop_name}-ACCESS_TOKEN"],
        google_credential_path=os.environ["GOOGLE_CREDENTIAL_PATH"],
        google_sheet_id=os.environ.get(f"{shop_name}-GSPREAD_ID"),
    )
