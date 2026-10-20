import calendar
import csv
import datetime
import logging
import shutil
import time

logger = logging.getLogger(__name__)

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from matplotlib.gridspec import GridSpec


class Analytics:

    def run_shopifyql(self, shpoifyql_query, to_dataframe=True):
        query = (
            """
        query {
            shopifyqlQuery(query: "%s") {
                tableData {
                    columns {
                        name
                        dataType
                        displayName
                    }
                    rows
                }
                parseErrors
            }
        }
        """
            % shpoifyql_query
        )
        res = self.run_query(query)
        if error := res["shopifyqlQuery"]["parseErrors"]:
            raise RuntimeError(f"Failed to run ShpifyQL query: {error}")
        res = res["shopifyqlQuery"]["tableData"]
        return self.tabledata_to_dataframe(res) if to_dataframe else res

    def tabledata_to_dataframe(self, shopifyql_res):
        df = pd.DataFrame(shopifyql_res["rows"])
        df = df[[c["name"] for c in shopifyql_res["columns"]]]
        int_cols = [
            c["name"] for c in shopifyql_res["columns"] if c["dataType"] == "INTEGER"
        ]
        money_cols = [
            c["name"] for c in shopifyql_res["columns"] if c["dataType"] == "MONEY"
        ]
        percentage_cols = [
            c["name"] for c in shopifyql_res["columns"] if c["dataType"] == "PERCENT"
        ]
        date_cols = [
            c["name"]
            for c in shopifyql_res["columns"]
            if c["dataType"] == "DAY_TIMESTAMP"
        ]
        df[int_cols] = df[int_cols].apply(pd.to_numeric, errors="coerce").astype(int)
        df[money_cols] = df[money_cols].apply(pd.to_numeric, errors="coerce")
        df[percentage_cols] = (
            df[percentage_cols].apply(pd.to_numeric, errors="coerce") * 100
        )
        df[date_cols] = df[date_cols].apply(pd.to_datetime, errors="coerce")
        return df

    def run_shopifyql_resilient(
        self, shopifyql_query, to_dataframe=True, max_retries=8
    ):
        """run_shopifyql with exponential backoff on Shopify API throttling.

        Bulk analytics extraction fires many heavy ShopifyQL queries in a row and
        regularly trips the THROTTLED rate limit, so retry those transparently.
        """
        for attempt in range(max_retries):
            try:
                return self.run_shopifyql(shopifyql_query, to_dataframe=to_dataframe)
            except RuntimeError as e:
                throttled = "THROTTLED" in str(e) or "Rate limited" in str(e)
                if not throttled or attempt == max_retries - 1:
                    raise
                wait = 2 + 2 * attempt
                logger.info(
                    f"{self.__class__.__name__} throttled, retrying in {wait}s "
                    f"({attempt + 1}/{max_retries})"
                )
                time.sleep(wait)

    def report_sales_by_sku(self, date_from, date_to, to_dataframe=True):
        shopifyql_query = f"""
            FROM sales
            SHOW day, order_name, sale_id, discount_code, line_type, product_title_at_time_of_sale
                AS TITLE, product_variant_sku_at_time_of_sale AS SKU,
                product_variant_compare_at_price AS ORIGINAL_PRICE, product_variant_price AS SELLING_PRICE,
                gross_sales, net_returns, discounts,
                net_sales, shipping_charges, shipping_returned
            GROUP BY day, order_name, sale_id, discount_code, line_type, TITLE, SKU, ORIGINAL_PRICE, SELLING_PRICE
            TIMESERIES day
            SINCE {date_from:%Y-%m-%d} UNTIL {date_to:%Y-%m-%d}
            ORDER BY day ASC, order_name ASC, line_type ASC, SKU ASC
        """
        return self.run_shopifyql(shopifyql_query, to_dataframe=to_dataframe)

    def report_net_sales_by_product(self, date_from, date_to, to_dataframe=True):
        shopifyql_query = f"""
            FROM sales
            SHOW net_sales
            WHERE product_title IS NOT NULL
            GROUP BY product_title, product_vendor, product_type WITH TOTALS, CURRENCY 'JPY'
            SINCE {date_from:%Y-%m-%d} UNTIL {date_to:%Y-%m-%d}
            ORDER BY net_sales DESC
        """
        return self.run_shopifyql(shopifyql_query, to_dataframe=to_dataframe)

    def report_sales_amount_by_day(self, date_from, date_to, to_dataframe=True):
        shopifyql_query = f"""
            FROM sales
            SHOW net_sales + shipping_charges AS sales_amount
            TIMESERIES day WITH TOTALS, CURRENCY 'JPY'
            SINCE {date_from:%Y-%m-%d} UNTIL {date_to:%Y-%m-%d}
            ORDER BY day ASC
        """
        return self.run_shopifyql(shopifyql_query, to_dataframe=to_dataframe)

    def report_average_order_value_by_day(self, date_from, date_to, to_dataframe=True):
        shopifyql_query = f"""
            FROM sales
            SHOW orders, average_order_value
            WHERE excludes_post_order_adjustments = true
            TIMESERIES day WITH TOTALS, CURRENCY 'JPY'
            SINCE {date_from:%Y-%m-%d} UNTIL {date_to:%Y-%m-%d}
            ORDER BY day ASC
        """
        return self.run_shopifyql(shopifyql_query, to_dataframe=to_dataframe)

    def report_sessions_by(
        self, date_from, date_to, timeseries_by="day", to_dataframe=True
    ):
        shopifyql_query = f"""
            FROM sessions
            SHOW sessions, conversion_rate
            TIMESERIES {timeseries_by} WITH TOTALS, CURRENCY 'JPY'
            SINCE {date_from:%Y-%m-%d} UNTIL {date_to:%Y-%m-%d}
            ORDER BY {timeseries_by} ASC
        """
        return self.run_shopifyql(shopifyql_query, to_dataframe=to_dataframe)

    def report_sessions_by_day(self, date_from, date_to, to_dataframe=True):
        return self.report_sessions_by(date_from, date_to, "day", to_dataframe)

    def report_sessions_by_week(self, date_from, date_to, to_dataframe=True):
        return self.report_sessions_by(date_from, date_to, "week", to_dataframe)

    def report_sessions_by_month(self, date_from, date_to, to_dataframe=True):
        return self.report_sessions_by(date_from, date_to, "month", to_dataframe)

    def report_customer_type(self, date_from, date_to, to_dataframe=True):
        shopifyql_query = f"""
            FROM sales
            SHOW customers
            WHERE new_or_returning_customer IS NOT NULL
            GROUP BY new_or_returning_customer
            SINCE {date_from:%Y-%m-%d} UNTIL {date_to:%Y-%m-%d}
            ORDER BY new_or_returning_customer ASC
            LIMIT 2
        """
        return self.run_shopifyql(shopifyql_query, to_dataframe=to_dataframe)

    def report_conversion_breakdown(self, date_from, date_to, to_dataframe=True):
        shopifyql_query = f"""
            FROM sessions
            SHOW sessions, sessions_with_cart_additions, sessions_that_reached_checkout,
                sessions_that_completed_checkout
            WHERE human_or_bot_session IN ('human', 'bot')
            SINCE {date_from:%Y-%m-%d} UNTIL {date_to:%Y-%m-%d}
        """
        return self.run_shopifyql(shopifyql_query, to_dataframe=to_dataframe)

    def report_sales_kpi_by(
        self, date_from, date_to, timeseries_by="month", to_dataframe=True
    ):
        shopifyql_query = f"""
            FROM sales
            SHOW average_order_value, net_sales, orders, returning_customer_rate
            TIMESERIES {timeseries_by} WITH CURRENCY 'JPY'
            SINCE {date_from:%Y-%m-%d} UNTIL {date_to:%Y-%m-%d}
        """
        return self.run_shopifyql(shopifyql_query, to_dataframe=to_dataframe)

    def report_sales_kpi_by_month(self, date_from, date_to, to_dataframe=True):
        return self.report_sales_kpi_by(date_from, date_to, "month", to_dataframe)

    def report_sales_kpi_by_week(self, date_from, date_to, to_dataframe=True):
        return self.report_sales_kpi_by(date_from, date_to, "week", to_dataframe)

    def report_sales_kpi_by_day(self, date_from, date_to, to_dataframe=True):
        return self.report_sales_kpi_by(date_from, date_to, "day", to_dataframe)

    def dashboard_stats_shopify(self, report_date, timeseries_by="month"):
        assert timeseries_by in ["week", "month"]
        date_to = report_date
        date_from = (
            report_date - datetime.timedelta(days=6)
            if timeseries_by == "week"
            else (datetime.date(report_date.year, report_date.month, 1))
        )
        sessions = self.report_sessions_by(date_from, date_to, timeseries_by)
        sales_kpis = self.report_sales_kpi_by(date_from, date_to, timeseries_by)
        assert len(sessions) == len(sales_kpis) == 1
        res = pd.merge(sessions, sales_kpis, on=timeseries_by, how="outer").sort_values(
            timeseries_by, ascending=False
        )
        res.at[0, timeseries_by] = f"{date_to:%Y-%m-%d}"
        return res

    def run_monthly_report(
        self, report_func, report_year, report_month, to_dataframe=True
    ):
        return report_func(
            date_from=datetime.date(report_year, report_month, 1),
            date_to=datetime.date(
                report_year,
                report_month,
                calendar.monthrange(report_year, report_month)[1],
            ),
            to_dataframe=to_dataframe,
        )

    def generate_monthly(self, generate_func, report_year, report_month, **kwargs):
        return generate_func(
            date_from=datetime.date(report_year, report_month, 1),
            date_to=datetime.date(
                report_year,
                report_month,
                calendar.monthrange(report_year, report_month)[1],
            ),
            **kwargs,
        )

    def inject_dataframe_to_xlsx(
        self, output_path, sheet_name, dataframe, startrow, header=False
    ):
        """
        Inject the dataframe to an xlsx file at output_path, at the specified startrow

        :param output_path: where the xlsx file is
        :param sheet_name: sheet name to insert the DataFrame to
        :param dataframe: DataFrame being injected
        :param startrow: 0 based row index
        :param header: whether to include header or not
        """
        # 'overlay' mode, 'a' stands for append
        with pd.ExcelWriter(
            output_path, engine="openpyxl", mode="a", if_sheet_exists="overlay"
        ) as writer:
            dataframe.to_excel(
                writer,
                sheet_name=sheet_name,
                startrow=startrow,
                index=False,
                header=header,
            )

    def generate_sales_report(
        self, template_path, sheet_name, output_path, date_from, date_to
    ):
        shutil.copyfile(template_path, output_path)
        df = self.report_sales_by_sku(date_from, date_to)
        self.inject_dataframe_to_xlsx(
            output_path=output_path,
            sheet_name=sheet_name,
            dataframe=df,
            startrow=20,
            header=False,
        )

    def generate_kpi_by_month_report(
        self, template_path, sheet_name, output_path, date_from, date_to
    ):
        shutil.copyfile(template_path, output_path)
        df = self.report_kpi_by_month(date_from, date_to)
        self.inject_dataframe_to_xlsx(
            output_path=output_path,
            sheet_name=sheet_name,
            dataframe=df.transpose().reset_index(),
            startrow=0,
            header=False,
        )

    def generate_sales_by_product_graph(self, output_path, date_from, date_to):
        df = self.report_net_sales_by_product(date_from, date_to)
        df = df[["product_title", "net_sales"]].sort_values(
            by="net_sales", ascending=False
        )

        top_8 = df.head(8).copy()  # 2. Group Top 8 + Others
        others_val = df.iloc[8:]["net_sales"].sum()
        others_df = pd.DataFrame([{"product_title": "Others", "net_sales": others_val}])
        plot_df = pd.concat([top_8, others_df], ignore_index=True)

        # indices: 0:Blue, 1:Orange, 2:Green, 3:Red, 4:Purple, 5:Brown, 6:Pink, 7:Gray, 9:Cyan (for Others)
        tab10 = plt.get_cmap("tab10").colors
        custom_colors = list(tab10[:8]) + [tab10[9]]

        fig, ax = plt.subplots(figsize=(12, 10))

        wedges, texts, autotexts = ax.pie(
            plot_df["net_sales"],
            labels=plot_df["product_title"],
            autopct="%1.1f%%",
            startangle=90,  # Start at top center
            counterclock=False,  # Go clockwise
            colors=custom_colors,
            pctdistance=0.75,  # Position percentages inside the slices
            labeldistance=1.05,  # Position labels just outside
            wedgeprops={
                "linewidth": 1.5,
                "edgecolor": "white",
                "width": 1,  # Thick donut: covers full area towards center
            },
            textprops={"fontsize": 9},
        )

        for autotext in autotexts:  # 5. Fine-tune Text Styling
            autotext.set_fontsize(8)
            autotext.set_weight("bold")
            autotext.set_color("black")

        for text in texts:
            text.set_fontsize(8)

        plt.title(
            "Product Sales Share — Top Products + Others",
            fontsize=16,
            pad=30,
            fontweight="bold",
        )
        ax.axis("equal")

        plt.tight_layout()
        plt.savefig(output_path, dpi=300)
        plt.close(fig)

    def generate_store_kpi_by_day_graph(self, output_path, date_from, date_to):

        df_net_sales = self.report_sales_amount_by_day(date_from, date_to)
        df_aov = self.report_average_order_value_by_day(date_from, date_to)
        df_cvr = self.report_sessions_by_day(date_from, date_to)

        df = pd.merge(df_aov, df_cvr, on="day", how="outer").sort_values("day")
        df = pd.merge(df, df_net_sales, on="day", how="outer").sort_values("day")
        df["day"] = pd.to_datetime(df["day"])

        # 2. KPI Calculations (for the left panel)
        sales_amount = df["sales_amount"].sum()
        total_sessions = df["sessions"].sum()
        avg_cvr = float(df["conversion_rate__totals"][0])
        avg_aov = int(df["average_order_value__totals"][0])

        # 3. Setup Layout (Left: Summary, Right: Charts)
        fig = plt.figure(figsize=(14, 10), facecolor="white")
        gs = GridSpec(3, 2, width_ratios=[1, 3], figure=fig, hspace=0.4, wspace=0.1)

        # --- LEFT PANEL: Summary Text ---
        # We use a single axis for the whole left side to place text accurately
        ax_text = fig.add_subplot(gs[:, 0])
        ax_text.axis("off")

        kpis = [
            ("TOTAL SALES", f"¥{int(sales_amount):,}", 0.85),
            ("TOTAL SESSIONS", f"{int(total_sessions):,}", 0.60),
            ("AVERAGE CVR", f"{avg_cvr:.2f}%", 0.35),
            ("AVERAGE AOV", f"¥{int(avg_aov):,}", 0.10),
        ]

        for label, val, y_pos in kpis:
            ax_text.text(
                0.1, y_pos + 0.05, label, fontsize=10, color="gray", fontweight="bold"
            )
            ax_text.text(0.1, y_pos, val, fontsize=24, fontweight="bold")
            ax_text.axhline(
                y_pos - 0.08, xmin=0.1, xmax=0.9, color="lightgray", linewidth=0.5
            )

        # --- RIGHT PANEL: Charts ---

        # Chart 1: Sales (Bars) & Sessions (Line) - Dual Axis
        ax1 = fig.add_subplot(gs[0, 1])
        ax1_twin = ax1.twinx()

        ax1.bar(
            df["day"], df["sales_amount"], color="#4E91C2", alpha=0.8, label="Sales"
        )
        ax1_twin.plot(
            df["day"],
            df["sessions"],
            color="#F39233",
            marker="o",
            markersize=3,
            label="sessions",
        )

        ax1.set_title("Sales & Sessions", fontsize=12, fontweight="bold", loc="left")
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{int(x):,}"))

        # Chart 2: Conversion Rate (Line)
        ax2 = fig.add_subplot(gs[1, 1])
        cvr_data = df["conversion_rate"]
        ax2.plot(df["day"], cvr_data, color="#31A354", marker="o", markersize=3)
        ax2.axhline(
            cvr_data.mean(), color="#31A354", linestyle="--", alpha=0.4
        )  # Average line
        ax2.set_title("conversion_rate", fontsize=12, fontweight="bold", loc="left")
        ax2.set_ylabel("%")

        # Chart 3: Average Order Value (Line)
        ax3 = fig.add_subplot(gs[2, 1])
        ax3.plot(
            df["day"],
            df["average_order_value"],
            color="#D62728",
            marker="o",
            markersize=3,
        )
        ax3.axhline(
            df["average_order_value"].mean(), color="#D62728", linestyle="--", alpha=0.4
        )
        ax3.set_title("Average Order Value", fontsize=12, fontweight="bold", loc="left")
        ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{int(x):,}"))

        # Clean up formatting for all charts
        for ax in [ax1, ax2, ax3]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
            ax.tick_params(axis="both", labelsize=9)
            ax.grid(axis="y", linestyle="--", alpha=0.3)
            for spine in ["top", "right"]:
                ax.spines[spine].set_visible(False)

        plt.savefig(output_path, dpi=300, bbox_inches="tight")
        plt.close(fig)

    def generate_customer_type_donut(self, output_path, date_from, date_to):

        df = self.report_customer_type(date_from, date_to)
        df.columns = ["customer_type", "count"]
        df = df.set_index("customer_type").reindex(["Returning", "New"]).reset_index()

        total_customers = df["count"].sum()
        colors = ["#F39233", "#4E91C2"]

        fig, ax = plt.subplots(figsize=(8, 9), facecolor="white")

        wedges, texts, autotexts = ax.pie(
            df["count"],
            labels=df["customer_type"],
            autopct="%1.1f%%",
            startangle=90,  # Top center
            counterclock=False,  # Clockwise
            colors=colors,
            pctdistance=0.75,
            wedgeprops={
                "linewidth": 2,
                "edgecolor": "white",
                "width": 0.6,  # Thick donut
            },
            textprops={"fontsize": 12, "fontweight": "bold"},
        )

        for autotext in autotexts:
            autotext.set_color("white")
            autotext.set_fontsize(11)

        plt.title(
            "Customer Mix - New vs Returning", fontsize=16, fontweight="bold", pad=20
        )

        # 5. Add "Total Customers" at the bottom center
        fig.text(
            0.5,
            0.05,
            f"Total Customers: {int(total_customers)}",
            ha="center",
            fontsize=14,
            fontweight="bold",
            color="#848484",
        )

        ax.axis("equal")

        # Adjust layout to make room for the bottom text
        plt.tight_layout(rect=[0, 0.08, 1, 1])

        try:
            plt.savefig(output_path, dpi=300, bbox_inches="tight")
        finally:
            plt.close(fig)

    def generate_conversion_breakdown(self, output_path, date_from, date_to):
        df = self.report_conversion_breakdown(date_from, date_to)

        metrics = {
            "Add to Cart": "sessions_with_cart_additions",
            "Checkout": "sessions_that_reached_checkout",
            "Purchase": "sessions_that_completed_checkout",
        }

        labels = ["Add to Cart", "Checkout", "Purchase"]
        vals = [df[metrics[l]].sum() for l in labels]

        # 2. Assign specific colors per requirement
        # Add to Cart: Yellow, Checkout: Blue, Purchase: Green
        colors = ["#F7B500", "#4E91C2", "#00A859"]

        # 3. Setup Plot
        fig, ax = plt.subplots(figsize=(10, 5), facecolor="white")
        y = np.arange(len(labels))
        height = 0.5

        # 4. Horizontal Bars with specific colors
        ax.barh(y, vals, height, color=colors, alpha=0.9)

        # 5. Styling & Invert Y-axis for funnel flow
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=12, fontweight="bold")
        ax.invert_yaxis()  # Add to Cart at top

        # Hide spines for a clean look
        for spine in ["top", "right", "bottom", "left"]:
            ax.spines[spine].set_visible(False)
        ax.set_xticks([])
        ax.tick_params(left=False)

        # 6. Data Labels (Counts) positioned at the end of each bar
        for i, val in enumerate(vals):
            ax.text(
                val + (max(vals) * 0.01),
                i,
                f"{int(val):,}",
                va="center",
                fontsize=12,
                fontweight="bold",
                color=colors[i],
            )

        ax.set_title(
            "Conversion Funnel - Add to Cart to Purchase",
            fontsize=16,
            fontweight="bold",
            loc="left",
            pad=25,
        )

        plt.tight_layout()
        try:
            plt.savefig(output_path, dpi=300, bbox_inches="tight")
        finally:
            plt.close(fig)

    def analytics_export_queries(self, date_from, date_to, timeseries_by):
        """ShopifyQL for every report card at the top of Shopify Admin -> Analytics.

        Keyed by the report's English name (Shopify's own card titles). Each value
        reproduces the report the staff exports by hand. ``timeseries_by`` is
        ``"month"`` (year-over-year comparison) or ``"day"`` (trace a single month).
        """
        by = timeseries_by
        span = f"SINCE {date_from:%Y-%m-%d} UNTIL {date_to:%Y-%m-%d}"
        jpy = "WITH CURRENCY 'JPY'"
        order = f"ORDER BY {by} ASC"
        return {
            "Total sales over time": (
                "FROM sales SHOW orders, gross_sales, discounts, returns, net_sales, "
                "shipping_charges, duties, additional_fees, taxes, total_sales "
                f"TIMESERIES {by} {jpy} {span} {order}"
            ),
            "Average order value over time": (
                "FROM sales SHOW gross_sales, discounts, orders, average_order_value "
                f"TIMESERIES {by} {jpy} {span} {order}"
            ),
            "Sessions over time": (
                "FROM sessions SHOW online_store_visitors, sessions "
                f"TIMESERIES {by} {span} {order}"
            ),
            "Conversion rate over time": (
                "FROM sessions SHOW sessions, sessions_with_cart_additions, "
                "sessions_that_reached_checkout, sessions_that_completed_checkout, "
                f"conversion_rate TIMESERIES {by} {span} {order}"
            ),
            "Conversion rate breakdown": (
                "FROM sessions SHOW sessions, sessions_with_cart_additions, "
                "sessions_that_reached_checkout, sessions_that_completed_checkout, "
                f"conversion_rate TIMESERIES {by} {span} {order}"
            ),
            "New and returning customers over time": (
                "FROM sales SHOW customers, orders, total_sales "
                f"GROUP BY new_or_returning_customer TIMESERIES {by} {jpy} {span} {order}"
            ),
            "New and returning customers": (
                "FROM sales SHOW customers "
                f"GROUP BY new_or_returning_customer TIMESERIES {by} {span} {order}"
            ),
            "Total sales by product": (
                "FROM sales SHOW net_items_sold, gross_sales, discounts, returns, "
                "net_sales, taxes, total_sales "
                "GROUP BY product_title, product_vendor, product_type "
                f"TIMESERIES {by} {jpy} {span} {order}"
            ),
            "Total sales by referrer": (
                "FROM sales SHOW orders, total_sales, gross_sales, net_sales "
                "GROUP BY order_referrer_source, order_referrer_name "
                f"TIMESERIES {by} {jpy} {span} {order}"
            ),
            "Sales attributed to marketing": (
                "FROM sales SHOW orders, total_sales, gross_sales, net_sales "
                "GROUP BY referring_channel, referring_medium "
                f"TIMESERIES {by} {jpy} {span} {order}"
            ),
            "Sessions by referrer": (
                "FROM sessions SHOW online_store_visitors, sessions "
                "GROUP BY referrer_source, referrer_name, session_city "
                f"TIMESERIES {by} {span} {order}"
            ),
            "Sessions by landing page": (
                "FROM sessions SHOW sessions, conversion_rate "
                "GROUP BY landing_page_path "
                f"TIMESERIES {by} {span} {order}"
            ),
            "Conversion by channel": (
                "FROM sessions SHOW sessions, conversion_rate "
                "GROUP BY referring_channel "
                f"TIMESERIES {by} {span} {order}"
            ),
            "Sales by discount": (
                "FROM sales SHOW net_sales, orders, discounts "
                "GROUP BY discount_name "
                f"TIMESERIES {by} {jpy} {span} {order}"
            ),
        }

    def write_analytics_report_csv(self, shopifyql_query, output_path):
        """Run a ShopifyQL query and dump the raw rows to CSV.

        Mirrors a genuine Shopify Analytics export: Shopify's own column display
        names as the header, unformatted values straight from the API (no type
        coercion or percentage scaling), UTF-8 BOM so Excel reads it cleanly.
        """
        res = self.run_shopifyql_resilient(shopifyql_query, to_dataframe=False)
        columns = res["columns"]
        headers = [c.get("displayName") or c["name"] for c in columns]
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in res["rows"]:
                writer.writerow([row.get(c["name"]) for c in columns])
        return output_path
