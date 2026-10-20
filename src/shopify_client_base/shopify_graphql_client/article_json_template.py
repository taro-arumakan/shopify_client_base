def article_json_template():
    return r"""{
  "wrapper": "div.fixed-width-wrapper",
  "sections": {
    "rich_text_rRYzmM": {
      "type": "rich-text",
      "blocks": {
        "liquid_RCdgVw": {
          "type": "liquid",
          "settings": {
            "liquid": "<div class=\"blog-title reveal_tran_lr\">\n{{ blog.title }}\n</div>"
          }
        },
        "heading_t3gXjy": {
          "type": "heading",
          "settings": {
            "text": "{{ article.title }}",
            "heading_tag": "h1"
          }
        }
      },
      "block_order": [
        "liquid_RCdgVw",
        "heading_t3gXjy"
      ],
      "custom_css": [
        "p.h1.heading {margin-block-start: 0;}",
        ".prose {margin-left: 0; margin-right: 0;}"
      ],
      "name": "t:sections.rich_text.presets.rich_text.name",
      "settings": {
        "color_scheme": "",
        "separate_section_with_border": false,
        "content_width": "lg",
        "text_position": "start",
        "remove_vertical_spacing": false
      }
    },
    "main": {
      "type": "main-article",
      "disabled": true,
      "settings": {
        "color_scheme": "",
        "show_image": true,
        "enable_parallax": true,
        "image_size": "medium",
        "content_width": "xs",
        "show_date": true,
        "show_category": true,
        "show_author": true,
        "show_share_buttons": true,
        "show_sticky_bar": true,
        "toolbar_color_scheme": ""
      }
    }
  },
  "order": [
    "rich_text_rRYzmM",
    "main"
  ]
}
"""
