# shopify-client-base

A Python client for managing Shopify stores through the Admin GraphQL API, with
Google Drive / Sheets helpers for reading product sheets and images.

- `Client` – one object per store: products, variants, inventory, media,
  collections, menus, metafields, articles, publications, orders, analytics,
  plus Drive/Sheets access.
- `BrandClientBase` – base class for a store-specific client that builds
  products from a Google Sheet. Subclasses set `SHOPNAME`, `VENDOR`,
  `LOCATIONS` and the sheet column maps.

## Install

```toml
# pyproject.toml of the consuming project
dependencies = [
    "shopify-client-base @ git+https://github.com/taro-arumakan/shopify_client_base@v0.1.0",
]
```

## Credentials

`shopify_client_base.credentials(shop_name)` loads `.env` (searched from the
current directory upwards) and reads:

| Variable | |
|---|---|
| `<shop_name>-ACCESS_TOKEN` | Admin API access token |
| `GOOGLE_CREDENTIAL_PATH` | service account JSON for Drive / Sheets |
| `<shop_name>-GSPREAD_ID` | optional product sheet id |

`BrandClientBase.load_credentials` can be overridden to read them from elsewhere.

## Example

```python
from shopify_client_base import BrandClientBase


class ExampleClient(BrandClientBase):
    SHOPNAME = "example-shop"          # <SHOPNAME>.myshopify.com
    VENDOR = "example"
    LOCATIONS = ["Shop location"]


client = ExampleClient()
products = client.products_by_query("status:active")
```

## Development

```sh
uv sync
uv run pytest
```

To work on this package alongside a project that depends on it, install your
checkout into that project's environment with `uv pip install -e ../shopify_client_base`.
