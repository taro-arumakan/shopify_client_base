import collections
import copy
import datetime
import json
import logging
import os
import re
import string
import time
from shopify_client_base.shopify_graphql_client.article_json_template import article_json_template

logger = logging.getLogger(__name__)

additional_punctuation_chars = "‘’“” "
punctuation_chrs = (
    "".join([s for s in string.punctuation if s not in ["-"]])
    + additional_punctuation_chars
)
punctuation_translator = str.maketrans({s: "_" for s in punctuation_chrs})


class Article:

    def shopify_sanitized_filename(self, filename):
        name, ext = os.path.splitext(filename)
        sanitized_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
        sanitized_name = sanitized_name.rstrip("_")
        return f"{sanitized_name}{ext}"

    def shopify_compatible_name(self, name):
        name = self.punctuations_to_underscore(name)
        if name.startswith("_"):
            name = name[1:]
        return name

    def punctuations_to_underscore(self, s):
        parts = s.lower().split(" ")
        parts = [part.translate(punctuation_translator) for part in parts]
        return "-".join(parts)

    def blogs_by_query(self, query_string):
        query = """
        query blogsByQuery($query_string: String!) {
            blogs(first: 50, query: $query_string) {
                nodes {
                    id
                    handle
                    title
                    commentPolicy
                    createdAt
                    updatedAt
                    templateSuffix
                    tags
                    articles(first:100) {
                        nodes {
                            id
                            title
                            publishedAt
                            templateSuffix
                        }
                    }
                }
            }
        }
        """
        variables = {"query_string": query_string}
        res = self.run_query(query, variables)
        return res["blogs"]["nodes"]

    def blog_by_blog_title(self, blog_title):
        blogs = self.blogs_by_query(f"title:'{blog_title.replace("'", "\\'")}'")
        if len(blogs) != 1:
            raise RuntimeError(
                f"{'Multiple' if blogs else 'No'} blog articles found for {blog_title}: {blogs}"
            )
        return blogs[0]

    def blog_id_by_blog_title(self, blog_title):
        return self.blog_by_blog_title(blog_title)["id"]

    def articles_by_title(self, title):
        query = """
        query articlesByQuery($query_string: String!) {
            articles(first: 50, query: $query_string) {
                nodes {
                    id
                    handle
                    title
                    image {
                        altText
                        id
                        url
                    }
                    templateSuffix
                }
            }
        }
        """
        variables = {"query_string": f"title:'{title.replace("'", "\\'")}'"}
        res = self.run_query(query, variables)
        return res["articles"]["nodes"]

    def article_create(
        self,
        blog_title,
        title,
        template_suffix,
        media_url=None,
        is_published=True,
        author_name="Taro Nakamura",
        tags=None,
        metafields=None,
    ):
        blog_id = self.blog_id_by_blog_title(blog_title)
        query = """
        mutation CreateArticle($article: ArticleCreateInput!) {
            articleCreate(article: $article) {
                article {
                    id
                    title
                    author {
                        name
                    }
                    handle
                    body
                    summary
                    tags
                    image {
                        altText
                        url
                        id
                    }
                }
                userErrors {
                    code
                    field
                    message
                }
            }
        }
        """
        article = {
            "blogId": blog_id,
            "title": title,
            "templateSuffix": template_suffix,
            "isPublished": is_published,
            "author": {"name": author_name},
        }
        if media_url:
            article["image"] = {"url": media_url, "altText": f"{title} cover image"}
        if tags:
            article["tags"] = tags
        if metafields:
            # Written with the article so a rejected metafield cannot leave a
            # created-but-unpopulated article behind.
            article["metafields"] = metafields
        variables = {"article": article}
        res = self.run_query(query, variables)
        if errors := res["articleCreate"]["userErrors"]:
            raise RuntimeError(f"Failed to create an article: {errors}")
        return res["articleCreate"]["article"]

    def article_update_published_at_by_article_id(
        self, article_id, published_at: datetime.datetime
    ):
        assert (
            published_at.tzinfo
        ), f"published_at must be timezone-aware: {published_at}"
        query = """
        mutation UpdateArticle($id: ID!, $article: ArticleUpdateInput!) {
            articleUpdate(id: $id, article: $article) {
                article {
                    id
                    title
                    handle
                    image {
                        altText
                        originalSrc
                    }
                }
                userErrors {
                    code
                    field
                    message
                }
            }
        }
        """
        variables = {
            "id": article_id,
            "article": {
                "publishDate": published_at.isoformat(),
            },
        }
        res = self.run_query(query, variables)
        if errors := res["articleUpdate"]["userErrors"]:
            raise RuntimeError(f"failed to update article publication time: {errors}")
        return res["articleUpdate"]

    def sort_articles_by_title(self, blog_title, article_titles_in_order):
        """
        swap publication datetime of articles in the order of article_titles_in_order variable.
        first in the list becomes the newest, last in the list becomes the oldest.
        """
        articles = self.blog_by_blog_title(blog_title)["articles"]["nodes"]
        articles = [
            article
            for article in articles
            if article["title"] in article_titles_in_order
        ]
        c = collections.Counter([article["title"] for article in articles])
        title_duplicate_articles = {k: v for k, v in c.items() if v > 1}
        assert (
            not title_duplicate_articles
        ), f"multiple articles with the same title: {title_duplicate_articles}"
        published_ats = sorted(article["publishedAt"] for article in articles)
        for i, article_title in enumerate(reversed(article_titles_in_order)):
            published_at = published_ats[i]
            article = [
                article for article in articles if article["title"] == article_title
            ][0]
            self.article_update_published_at_by_article_id(
                article["id"], datetime.datetime.fromisoformat(published_at)
            )

    def reverse_articles(self, blog_title):
        articles = self.blog_by_blog_title(blog_title)["articles"]["nodes"]
        published_ats = [article["publishedAt"] for article in articles]
        assert published_ats == sorted(
            published_ats
        ), "articles returned by GraphQL is not in order  of publication time"
        self.sort_articles_by_title(
            blog_title, [article["title"] for article in articles]
        )

    def article_from_image_file_names_and_product_titles(
        self,
        theme_name,
        theme_dir,
        blog_title,
        article_title,
        thumbnail_image_file_name,
        article_image_file_names,
        product_titles=None,
        publish_article=True,
        template_json=None,
    ):
        self.write_json_from_image_file_names_and_product_titles(
            theme_dir,
            blog_title,
            article_title,
            article_image_file_names,
            product_titles=product_titles,
            template_json=template_json,
        )
        if publish_article:
            self.add_article(
                blog_title,
                article_title,
                thumbnail_image_name=thumbnail_image_file_name,
                theme_name=theme_name,
            )

    def update_image_file_extensions(self, image_file_names):
        """
        Some files can be uploaded by a wrong extension,
        which then gets updated by Shopify to the correct extension,
        e.g. .jpg to .png
        """
        res = []
        for filename in image_file_names:
            f = self.file_by_file_name(filename)
            res.append(f["image"]["url"].rsplit("/", 1)[-1].split("?")[0])
        return res

    def json_from_image_file_names_and_product_titles(
        self,
        image_file_names,
        product_titles=None,
        template_json=None,
    ):
        image_file_names = self.update_image_file_extensions(image_file_names)
        sections_dict = self.to_template_sections(image_file_names, product_titles)
        return self.sections_dict_to_json(sections_dict, template_json)

    def write_json_from_image_file_names_and_product_titles(
        self,
        theme_dir,
        blog_title,
        article_title,
        image_file_names,
        product_titles=None,
        template_json=None,
    ):
        theme_file_path = self.article_template_path(
            theme_dir, blog_title, article_title
        )
        json_contents = self.json_from_image_file_names_and_product_titles(
            image_file_names, product_titles, template_json
        )
        with open(theme_file_path, "w") as of:
            of.write(json_contents)

    def to_template_sections(self, file_names, product_titles):
        sections = self.to_images_list_sections_dict(file_names)
        if product_titles:
            sections.update(
                self.to_featured_product_sections_dict(product_titles=product_titles)
            )
        return sections

    def to_images_list_sections_dict(self, file_names):
        base_attrs = {
            "type": "image",
            "settings": {
                "image_link": "",
                "margin_top": 10,
                "margin_bottom": 10,
                "max_width": 600,
                "mobile_max_width": 400,
            },
        }
        sections = {}
        section_count = 0
        for i, filename in enumerate(file_names):
            if i % 10 == 0:
                if section_count:
                    sections.update(section)
                section_count += 1
                section = {
                    f"images_list_{section_count}": {
                        "type": "images-list",
                        "blocks": {},
                        "block_order": [],
                        "name": "t:sections.images_list.presets.images_list.name",
                        "settings": {
                            "color_scheme": "",
                            "image_position": "center",
                            "overlay_color": "#000000",
                            "overlay_opacity": 0,
                        },
                    }
                }

            block_name = f"image_{str(i).zfill(3)}"
            block = {block_name: copy.deepcopy(base_attrs)}
            block[block_name]["settings"]["image"] = f"shopify://shop_images/{filename}"
            section[f"images_list_{section_count}"]["blocks"].update(block)
            section[f"images_list_{section_count}"]["block_order"].append(block_name)
        sections.update(section)
        return sections

    def to_featured_product_sections_dict(self, product_titles):
        base_attrs = {
            "type": "featured-product",
            "blocks": {
                "title_HFiUhq": {"type": "title", "settings": {"heading_tag": "h3"}},
                "price_8YCmTm": {
                    "type": "price",
                    "settings": {"show_taxes_notice": False},
                },
            },
            "block_order": ["title_HFiUhq", "price_8YCmTm"],
            "custom_css": [
                ".section-spacing {padding-block-start: 0;}",
                ".container {padding: 0;}",
            ],
            "name": "t:sections.featured_product.presets.featured_product.name",
            "settings": {
                "color_scheme": "",
                "product": None,
                "separate_section_with_border": False,
                "container_size": "full",
                "product_info_size": 30,
                "center_basic_info": True,
                "desktop_media_layout": "carousel_dots",
                "desktop_media_grid_gap": 30,
                "mobile_controls": "dots",
                "enable_media_autoplay": False,
                "enable_video_looping": True,
                "enable_image_zoom": False,
                "max_image_zoom_level": 3,
                "subheading": "",
                "title": "",
                "content": "",
            },
        }
        sections = {}
        for i, product_title in enumerate(product_titles):
            product = self.product_by_title(product_title)
            section_name = f"featured_product_{i}"
            section = {section_name: copy.deepcopy(base_attrs)}
            section[section_name]["settings"]["product"] = product["handle"]
            sections.update(section)
        return sections

    def sections_dict_to_json(self, sections_dict, template_json=None):
        output_dict = json.loads(template_json or article_json_template())
        output_dict["sections"].update(sections_dict)
        output_dict["order"] += sections_dict.keys()
        return json.dumps(output_dict, indent=2, ensure_ascii=False)

    def add_article_with_media_url(
        self,
        blog_title,
        article_title,
        thumbnail_media_url,
        theme_name,
        template_contents=None,
        is_published=True,
    ):
        template_name = self.article_template_name(blog_title, article_title)
        article_template_file_name = f"article.{template_name}.json"
        if template_contents is not None:
            self.upsert_theme_file(
                self.theme_id_by_theme_name(theme_name),
                f"templates/{article_template_file_name}",
                template_contents,
            )
        while not self.theme_file_by_theme_name_and_file_name(
            theme_name, article_template_file_name
        ):
            logger.info(f"awaiting upload of {article_template_file_name}")
            time.sleep(0.5)  # wait for the new json file upload
        return self.article_create(
            blog_title=blog_title,
            title=article_title,
            template_suffix=template_name,
            media_url=thumbnail_media_url,
            is_published=is_published,
        )

    def add_article(self, blog_title, article_title, thumbnail_image_name, theme_name):
        media = self.file_by_file_name(thumbnail_image_name)
        media_url = media["image"]["url"]
        return self.add_article_with_media_url(
            blog_title, article_title, media_url, theme_name
        )

    def article_template_path(self, theme_dir, blog_title, article_title):
        return os.path.join(
            theme_dir,
            f"templates/article.{self.article_template_name(blog_title, article_title)}.json",
        )

    def article_template_name(self, blog_title, article_title):
        return f"{blog_title.lower()}-{self.punctuations_to_underscore(article_title)}"
