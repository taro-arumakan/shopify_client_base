import json
import logging
import re

logger = logging.getLogger(__name__)


class OnlineStore:

    def themes_by_names(self, names):
        query = """
        query themesByQuery($names: [String!]) {
            themes(first: 30, names: $names) {
                nodes {
                    name
                    id
                    role
                    prefix
                }
            }
        }
        """
        variables = {"names": names}
        res = self.run_query(query, variables)
        return res["themes"]["nodes"]

    def theme_json_to_dict(self, content):
        # Remove C-style comments
        content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
        return json.loads(content)

    def current_theme(self):
        themes = self.themes_by_names("*")
        for theme in themes:
            if theme["role"] == "MAIN":
                return theme

    def current_color_swatch_config(self):
        theme = self.current_theme()
        setting_file = self.theme_file_by_theme_name_and_file_name(
            theme["name"], "config/settings_data.json"
        )
        res = self.theme_json_to_dict(setting_file[0]["body"]["content"])
        return res["current"].get("color_swatch_config")

    def theme_file_by_theme_name_and_file_name(self, theme_name, file_name):
        query = """
            query {
                themes(names:"%s" first:1) {
                    nodes {
                        files(filenames:"*%s*" first:50) {
                            nodes {
                                filename
                                body {
                                    ... on OnlineStoreThemeFileBodyText {
                                        content
                                    }
                                }
                            }
                        }
                    }
                }
            }
            """ % (
            theme_name,
            file_name,
        )
        res = self.run_query(query)
        return res["themes"]["nodes"][0]["files"]["nodes"]

    def theme_by_name(self, theme_name):
        themes = self.themes_by_names(theme_name)
        assert (
            len(themes) == 1
        ), f"{'Multiple' if themes else 'No'} themes found for {theme_name!r}"
        return themes[0]

    def theme_file_content(self, theme, file_name):
        """Exact-name match: the filenames query is a substring search."""
        nodes = self.theme_file_by_theme_name_and_file_name(theme["name"], file_name)
        exact = [n for n in nodes if n["filename"] == file_name]
        assert exact, f"theme file not found: {file_name!r} in {theme['name']!r}"
        return exact[0]["body"]["content"]

    @staticmethod
    def theme_json_blocks(data, section_type=None, block_type=None):
        """Yield (section_key, block_key, settings) in render order."""
        sections = data.get("sections") or {}
        for section_key in data.get("order") or sections:
            section = sections.get(section_key) or {}
            if section_type and section.get("type") != section_type:
                continue
            blocks = section.get("blocks") or {}
            for block_key in section.get("block_order") or blocks:
                block = blocks.get(block_key) or {}
                if block_type and block.get("type") != block_type:
                    continue
                yield section_key, block_key, block.get("settings") or {}

    def theme_id_by_theme_name(self, theme_name):
        query = (
            """
        query {
            themes(names:"%s" first:1) {
                nodes {
                    id
                    name
                }
            }
        }
        """
            % theme_name
        )
        res = self.run_query(query)
        assert (
            len(res["themes"]["nodes"]) == 1
        ), f"{'Multiple' if res["themes"]["nodes"] else 'No'} theme found for '{theme_name}'"
        return res["themes"]["nodes"][0]["id"]

    def publish_theme(self, theme_id):
        query = """
        mutation themePublish($id: ID!) {
            themePublish(id: $id) {
                theme {
                    id
                    name
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {"id": self.sanitize_id(theme_id, prefix="OnlineStoreTheme")}
        res = self.run_query(query=query, variables=variables)
        if errors := res["themePublish"]["userErrors"]:
            raise RuntimeError(f"Failed to publish {theme_id}: {errors}")
        return res["themePublish"]["theme"]

    def pages_by_query(self, query_string, sort_key="PUBLISHED_AT"):
        query = """
        query pagesByQuery($query_string: String!, $sort_key: PageSortKeys!) {
            pages(first: 50, query: $query_string, sortKey: $sort_key) {
                nodes {
                    id
                    handle
                    title
                    createdAt
                    publishedAt
                    templateSuffix
                }
            }
        }
        """
        variables = {"query_string": query_string, "sort_key": sort_key}
        res = self.run_query(query, variables)
        return res["pages"]["nodes"]

    def pages_by_title(self, title, sort_key="PUBLISHED_AT"):
        if "*" in title:
            query_string = f"title:{title}"
        else:
            query_string = f"title:'{title.replace("'", "\\'")}'"
        return self.pages_by_query(query_string, sort_key)

    def page_id_by_title(self, page_title):
        res = self.pages_by_title(page_title)
        if len(res) != 1:
            raise RuntimeError(f"Error getting page id for {page_title}: {res}")
        return res[0]["id"]

    def page_create(
        self,
        title,
        body,
        handle=None,
        is_published=True,
        template_suffix=None,
        metafields=None,
    ):
        """
        Create an Online Store page. `body` is HTML, not plain text.

        `handle` is optional — Shopify derives one from the title if it is
        omitted, which for a Japanese title gives an unusable URL-escaped slug,
        so pass it explicitly for anything customers will link to.
        """
        query = """
        mutation CreatePage($page: PageCreateInput!) {
            pageCreate(page: $page) {
                page {
                    id
                    title
                    handle
                    isPublished
                    publishedAt
                    templateSuffix
                }
                userErrors {
                    code
                    field
                    message
                }
            }
        }
        """
        page = {"title": title, "body": body, "isPublished": is_published}
        if handle:
            page["handle"] = handle
        if template_suffix:
            page["templateSuffix"] = template_suffix
        if metafields:
            page["metafields"] = metafields
        variables = {"page": page}
        res = self.run_query(query, variables)
        if errors := res["pageCreate"]["userErrors"]:
            raise RuntimeError(f"Failed to create a page: {errors}")
        return res["pageCreate"]["page"]

    def page_update(
        self,
        page_id,
        title=None,
        body=None,
        handle=None,
        is_published=None,
        template_suffix=None,
        metafields=None,
        redirect_new_handle=None,
    ):
        """
        Update an Online Store page. Only the arguments passed are sent, so a
        field left out keeps its current value rather than being cleared.

        `redirect_new_handle` asks Shopify to leave a redirect behind when the
        handle changes, so existing links to the old URL keep working.
        """
        query = """
        mutation UpdatePage($id: ID!, $page: PageUpdateInput!) {
            pageUpdate(id: $id, page: $page) {
                page {
                    id
                    title
                    handle
                    isPublished
                    publishedAt
                    templateSuffix
                }
                userErrors {
                    code
                    field
                    message
                }
            }
        }
        """
        page = {}
        for key, value in (
            ("title", title),
            ("body", body),
            ("handle", handle),
            ("isPublished", is_published),
            ("templateSuffix", template_suffix),
            ("metafields", metafields),
            ("redirectNewHandle", redirect_new_handle),
        ):
            if value is not None:
                page[key] = value
        assert page, "page_update called with nothing to update"
        variables = {"id": self.sanitize_id(page_id, prefix="Page"), "page": page}
        res = self.run_query(query, variables)
        if errors := res["pageUpdate"]["userErrors"]:
            raise RuntimeError(f"Failed to update a page: {errors}")
        return res["pageUpdate"]["page"]

    def url_redirects_by_query(self, query_string):
        query = """
        query urlRedirectsByQuery($query_string: String!) {
            urlRedirects(first: 50, query: $query_string) {
                nodes {
                    id
                    path
                    target
                }
            }
        }
        """
        variables = {"query_string": query_string}
        res = self.run_query(query, variables)
        return res["urlRedirects"]["nodes"]

    def delete_url_redirect(self, redirect_id):
        query = """
        mutation urlRedirectDelete($id: ID!) {
            urlRedirectDelete(id: $id) {
                deletedUrlRedirectId
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {"id": redirect_id}
        res = self.run_query(query, variables)
        if res["urlRedirectDelete"]["userErrors"]:
            raise RuntimeError(
                f"Error deleting URL redirect: {res['urlRedirectDelete']['userErrors']}"
            )
        return res["urlRedirectDelete"]["deletedUrlRedirectId"]

    def create_url_redirect(self, from_path, to_path):
        for p in from_path, to_path:
            assert p.startswith(
                "/"
            ), "from and to paths starting with a leading slash relative to the top domain are expected"

        if exists := self.url_redirects_by_query(f"path:'{to_path}'"):
            logger.info(f"Redirect exists at target {to_path}, deleting")
            for r in exists:
                self.delete_url_redirect(r["id"])

        query = """
        mutation urlRedirectCreate($input: UrlRedirectInput!) {
            urlRedirectCreate(urlRedirect: $input) {
                urlRedirect {
                    id
                    path
                    target
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """
        variables = {"input": {"path": from_path, "target": to_path}}
        res = self.run_query(query, variables)
        if res["urlRedirectCreate"]["userErrors"]:
            raise RuntimeError(
                f"Error creating URL redirect: {res['urlRedirectCreate']['userErrors']}"
            )
        return res["urlRedirectCreate"]["urlRedirect"]

    def upsert_theme_file(self, theme_id, file_name, contents):
        query = """
            mutation themeFilesUpsert($files: [OnlineStoreThemeFilesUpsertFileInput!]!, $themeId: ID!) {
                themeFilesUpsert(files: $files, themeId: $themeId) {
                    upsertedThemeFiles {
                        filename
                    }
                    userErrors {
                        field
                        message
                    }
                }
            }
        """
        variables = {
            "themeId": theme_id,
            "files": [
                {"filename": file_name, "body": {"type": "TEXT", "value": contents}},
            ],
        }
        res = self.run_query(query, variables)
        if res["themeFilesUpsert"]["userErrors"]:
            raise RuntimeError(
                f"Error updating a theme file: {res['themeFilesUpsert']['userErrors']}"
            )
        return res["themeFilesUpsert"]["upsertedThemeFiles"]
