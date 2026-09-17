"""Google Analytics 4 — pull website traffic and conversions."""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Metric, RunReportRequest, OrderBy
)

load_dotenv()

PROPERTY_ID = os.getenv("GA_PROPERTY_ID")
client = BetaAnalyticsDataClient()


def fetch_overview(days=7):
    """Top-line metrics: sessions, users, pageviews, etc."""
    request = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name="newUsers"),
            Metric(name="screenPageViews"),
            Metric(name="averageSessionDuration"),
            Metric(name="bounceRate"),
            Metric(name="engagedSessions"),
        ],
    )
    response = client.run_report(request)
    if not response.rows:
        return {}
    row = response.rows[0].metric_values
    return {
        "sessions": int(row[0].value),
        "active_users": int(row[1].value),
        "new_users": int(row[2].value),
        "pageviews": int(row[3].value),
        "avg_session_seconds": round(float(row[4].value), 1),
        "bounce_rate": round(float(row[5].value) * 100, 1),
        "engaged_sessions": int(row[6].value),
    }


def fetch_by_source(days=7, limit=10):
    """Where traffic comes from (google, instagram, direct, etc.)"""
    request = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        dimensions=[Dimension(name="sessionSource"), Dimension(name="sessionMedium")],
        metrics=[Metric(name="sessions"), Metric(name="activeUsers"), Metric(name="bounceRate")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
        limit=limit,
    )
    response = client.run_report(request)
    sources = []
    for row in response.rows:
        sources.append({
            "source": row.dimension_values[0].value,
            "medium": row.dimension_values[1].value,
            "sessions": int(row.metric_values[0].value),
            "users": int(row.metric_values[1].value),
            "bounce_rate": round(float(row.metric_values[2].value) * 100, 1),
        })
    return sources


def fetch_top_pages(days=7, limit=10):
    """Most-viewed pages."""
    request = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
        metrics=[Metric(name="screenPageViews"), Metric(name="averageSessionDuration")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"), desc=True)],
        limit=limit,
    )
    response = client.run_report(request)
    pages = []
    for row in response.rows:
        pages.append({
            "path": row.dimension_values[0].value,
            "title": row.dimension_values[1].value,
            "views": int(row.metric_values[0].value),
            "avg_time": round(float(row.metric_values[1].value), 1),
        })
    return pages


def fetch_by_country(days=7, limit=10):
    """Which countries visit the site."""
    request = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        dimensions=[Dimension(name="country")],
        metrics=[Metric(name="activeUsers"), Metric(name="sessions")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="activeUsers"), desc=True)],
        limit=limit,
    )
    response = client.run_report(request)
    return [
        {
            "country": row.dimension_values[0].value,
            "users": int(row.metric_values[0].value),
            "sessions": int(row.metric_values[1].value),
        }
        for row in response.rows
    ]


def fetch_by_device(days=7):
    """Mobile vs desktop vs tablet."""
    request = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        dimensions=[Dimension(name="deviceCategory")],
        metrics=[Metric(name="activeUsers"), Metric(name="sessions")],
    )
    response = client.run_report(request)
    return [
        {
            "device": row.dimension_values[0].value,
            "users": int(row.metric_values[0].value),
            "sessions": int(row.metric_values[1].value),
        }
        for row in response.rows
    ]


def fetch_all(days=7):
    return {
        "overview": fetch_overview(days),
        "sources": fetch_by_source(days),
        "top_pages": fetch_top_pages(days),
        "countries": fetch_by_country(days),
        "devices": fetch_by_device(days),
        "days": days,
    }


if __name__ == "__main__":
    print(f"📊 Google Analytics — last 7 days\n")

    o = fetch_overview()
    print("OVERVIEW")
    for k, v in o.items():
        print(f"  {k}: {v}")

    print("\nTOP SOURCES")
    for s in fetch_by_source(limit=5):
        print(f"  {s['source']} / {s['medium']}: {s['sessions']} sessions, {s['users']} users")

    print("\nTOP PAGES")
    for p in fetch_top_pages(limit=5):
        print(f"  {p['views']} views — {p['title'][:50]}")

    print("\nTOP COUNTRIES")
    for c in fetch_by_country(limit=5):
        print(f"  {c['country']}: {c['users']} users")

    print("\nDEVICES")
    for d in fetch_by_device():
        print(f"  {d['device']}: {d['users']} users")