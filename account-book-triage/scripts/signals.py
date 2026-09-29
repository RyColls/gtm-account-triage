"""
signals.py — turn raw Apollo job postings and news into scoreable numbers.

Why hiring is the primary signal: a company posting Account Executive, SDR and
RevOps roles is actively building the revenue motion that VantaOps instruments.
That is a durable, observable buying indicator, unlike "visited the pricing
page" which most teams cannot see anyway.

Why news is weighted lower: the captured Apollo news feed contains a large share
of SEO listicles and vendor-comparison spam ("Best enterprise web hosting
platforms compared", "Clari vs PointClickCare: Revenue, Funding & Team size
compared"). Those mention a company without saying anything about its buying
cycle. News corroborates; it does not trigger.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

# Titles that indicate revenue-org headcount.
#
# Hand-built and hand-checked rather than model-inferred. An LLM asked to
# classify titles will happily call "Revenue Cycle Specialist" a GTM role — that
# is a healthcare billing job. The negative list below exists because of exactly
# that class of false positive.
GTM_TITLE_PATTERN = re.compile(
    r"\b("
    r"sales|account executive|\bae\b|sdr|bdr|business development|"
    r"revenue operations|revops|revenue operations|go.?to.?market|\bgtm\b|"
    r"demand gen|growth marketing|product marketing|field marketing|"
    r"customer success|solutions engineer|sales engineer|solutions consultant|"
    r"partnerships|channel|sales enablement|sales ops"
    r")\b",
    re.I,
)

# Titles that look GTM but are not revenue-org roles.
#
# Note on "Revenue Manager": deliberately NOT counted. In most companies that
# is a revenue-recognition/accounting role (ASC 606), not a sales role. It
# appears in Snowflake's live postings, and counting it would inflate their
# hiring signal with a finance hire. Only "revenue operations" and "revenue
# enablement" are treated as GTM — hence the absence of a bare `revenue` term
# in the pattern above.
GTM_TITLE_EXCLUSIONS = re.compile(
    r"\b("
    r"revenue cycle|revenue accountant|revenue accounting|revenue recognition|"
    r"medical billing|sales tax|sales audit|"
    r"retail associate|store manager"
    r")\b",
    re.I,
)


def is_gtm_title(title: Optional[str]) -> bool:
    if not title:
        return False
    if GTM_TITLE_EXCLUSIONS.search(title):
        return False
    return bool(GTM_TITLE_PATTERN.search(title))


def _parse_iso(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def summarize(
    postings: List[Dict[str, Any]],
    articles: List[Dict[str, Any]],
    as_of: date,
) -> Dict[str, Any]:
    """Reduce raw API payloads to the handful of numbers scoring.yaml consumes."""
    gtm = [p for p in postings if is_gtm_title(p.get("title"))]

    posting_ages = []
    for p in postings:
        posted = _parse_iso(p.get("posted_at"))
        if posted:
            posting_ages.append((as_of - posted).days)

    article_ages = []
    for a in articles:
        published = _parse_iso(a.get("published_at") or a.get("date"))
        if published:
            article_ages.append((as_of - published).days)

    return {
        "jobs_total": len(postings),
        "jobs_gtm": len(gtm),
        "jobs_last_30d": len([d for d in posting_ages if d <= 30]),
        "days_since_newest_job": min(posting_ages) if posting_ages else None,
        "news_total": len(articles),
        "days_since_newest_news": min(article_ages) if article_ages else None,
        "sample_gtm_titles": [p.get("title") for p in gtm[:3]],
        "sample_headlines": [a.get("title") for a in articles[:2]],
    }


def empty_summary() -> Dict[str, Any]:
    return {
        "jobs_total": 0,
        "jobs_gtm": 0,
        "jobs_last_30d": 0,
        "days_since_newest_job": None,
        "news_total": 0,
        "days_since_newest_news": None,
        "sample_gtm_titles": [],
        "sample_headlines": [],
    }
