"""
Regression tests for the triage pipeline.

These run entirely against the captured fixture: no API key, no network, no
credits. That matters more than usual here — the Free plan allows 75 enrichment
credits a month, so a test suite that hit the live API would be unrunnable
within a week of normal development.

The scoring tests deliberately assert HAND-COMPUTED values. If someone edits
scoring.yaml, these fail loudly and tell them the ranking moved — which is the
correct behavior for a rubric that drives who a sales team calls.
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "account-book-triage"
sys.path.insert(0, str(SKILL / "scripts"))

import audit as audit_mod  # noqa: E402
import score as score_mod  # noqa: E402
import signals as signals_mod  # noqa: E402

AS_OF = date(2026, 9, 10)
FIXTURES = json.loads((ROOT / "tests" / "fixtures" / "apollo_capture.json").read_text())
CONFIG = yaml.safe_load((SKILL / "assets" / "scoring.yaml").read_text())
BOOK = {r["domain"]: r for r in audit_mod.load_book(str(SKILL / "assets" / "sample_account_book.csv"))}


# --------------------------------------------------------------- normalizing
@pytest.mark.parametrize("raw,expected", [
    ("https://www.Gong.io/", "gong.io"),
    ("GONG.IO", "gong.io"),
    ("gong.io", "gong.io"),
    ("http://datadoghq.com/pricing", "datadoghq.com"),
    ("", ""),
    (None, ""),
])
def test_normalize_domain(raw, expected):
    assert audit_mod.normalize_domain(raw) == expected


def test_normalize_company_name_strips_legal_suffixes():
    assert audit_mod.normalize_company_name("Pendo.io, Inc.") == "pendo"
    assert audit_mod.normalize_company_name("Acme Technologies LLC") == "acme"


# ---------------------------------------------------------- domain conflicts
def test_vercel_host_is_vendor_suspect_not_identity():
    """Apollo really does return a Vercel preview host as Gong's primary_domain.

    The CRM is right and the vendor is wrong. Misclassifying this as an identity
    conflict would block a rep from working a perfectly good account.
    """
    c = audit_mod.classify_domain_conflict("gong.io", "gong-next-sanity-web.vercel.app")
    assert c["kind"] == "vendor_suspect"
    assert c["severity"] == "medium"


def test_same_brand_different_tld_is_not_identity():
    c = audit_mod.classify_domain_conflict("outreach.io", "outreach.ai")
    assert c["kind"] == "tld_variance"
    assert c["severity"] == "medium"


def test_different_company_is_high_severity_identity():
    c = audit_mod.classify_domain_conflict("drift.com", "salesloft.com")
    assert c["kind"] == "identity"
    assert c["severity"] == "high"


# ------------------------------------------------------------- GTM classifier
@pytest.mark.parametrize("title", [
    "Commercial Account Executive",
    "Senior Sales Engineer - Key Accounts (AMER - East)",
    "Sales Development Representative",
    "Revenue Operations Manager",
    "Product Marketing Manager - Mobile",
])
def test_gtm_titles_match(title):
    assert signals_mod.is_gtm_title(title)


@pytest.mark.parametrize("title", [
    "Senior AI Engineer - Notebooks",
    "Software Engineering Intern",
    "IT Support Technician Intern",
    "Revenue Cycle Specialist",     # healthcare billing, not GTM
    "Sales Tax Analyst",            # finance, not GTM
    # "Revenue Manager" is a revenue-RECOGNITION role in most companies
    # (ASC 606, accounting) rather than a revenue-ORG role. It appears in
    # Snowflake's live postings and would inflate their GTM signal if counted.
    # Only "revenue operations" / "revenue enablement" are GTM.
    "Revenue Manager",
    "Senior Revenue Accountant",
])
def test_non_gtm_titles_do_not_match(title):
    assert not signals_mod.is_gtm_title(title)


# ------------------------------------------------------------------- scoring
def _score(domain):
    org = FIXTURES["enrich"][domain]
    sig = FIXTURES["signals"][domain]
    return score_mod.opportunity_score(sig, org, CONFIG)


def test_hubspot_score_matches_hand_computation():
    # 15/25 GTM = 60%; (0.60-0.25)/(0.55-0.25) = 1.17 -> capped -> 45
    # newest posting 0d -> 15 | newest news 8d -> 10
    # 9,100 employees is OUTSIDE the 200-5,000 ICP but inside 2x -> 5, +10 industry
    s = _score("hubspot.com")
    assert s["gtm_hiring_intensity"] == 45.0
    assert s["hiring_recency"] == 15
    assert s["news_recency"] == 10
    assert s["icp_fit"] == 15
    assert s["total"] == 85.0


def test_clari_scores_zero_intensity_below_posting_floor():
    # 1 posting is below min_postings_for_signal(5): one role is not a trend.
    s = _score("clari.com")
    assert s["gtm_hiring_intensity"] == 0.0
    assert s["hiring_recency"] == 0      # 462 days stale
    assert s["total"] == 40.0


def test_snowflake_below_baseline_gtm_mix_scores_zero_intensity():
    # 5/25 = 20%, under the 25% baseline SaaS mix -> no scaling signal.
    s = _score("snowflake.com")
    assert s["gtm_hiring_intensity"] == 0.0


def test_in_icp_beats_out_of_icp_on_fit():
    assert _score("looker.com")["icp_fit"] == 30      # 650 employees, in band
    assert _score("datadoghq.com")["icp_fit"] == 15   # 8,100, near band only


# ------------------------------------------------------------------- routing
def test_identity_conflict_gates_regardless_of_opportunity():
    """The Drift/Salesloft case — the regression this suite exists for.

    Under the v1 rubric this account routed to ENGAGE_NOW at 96/100 while the
    CRM pointed at a company that no longer exists independently.
    """
    routed = score_mod.route(
        data_health=95.0, opportunity=99.0, config=CONFIG,
        has_high_severity_conflict=True,
    )
    assert routed["action"] == "FIX_DATA"


def test_clean_high_opportunity_routes_to_engage():
    routed = score_mod.route(90.0, 75.0, CONFIG, False)
    assert routed["action"] == "ENGAGE_NOW"


def test_low_health_gates_before_opportunity():
    routed = score_mod.route(40.0, 95.0, CONFIG, False)
    assert routed["action"] == "FIX_DATA"


# --------------------------------------------------------------------- audit
def test_gap_is_patched_but_conflict_is_never_auto_applied():
    """The core safety property.

    Empty field + Apollo has a value -> propose a patch.
    Populated field that disagrees   -> report it, never overwrite.
    """
    record = BOOK["6sense.com"]                  # industry + employee_count blank
    org = FIXTURES["enrich"]["6sense.com"]
    a = audit_mod.audit_record(record, org, CONFIG, AS_OF)
    assert "industry" in a["patch"]
    assert "employee_count" in a["patch"]

    conflicted = BOOK["datadoghq.com"]           # CRM says 1,200; Apollo says 8,100
    org = FIXTURES["enrich"]["datadoghq.com"]
    a = audit_mod.audit_record(conflicted, org, CONFIG, AS_OF)
    fields_in_conflict = {c["field"] for c in a["conflicts"]}
    assert "employee_count" in fields_in_conflict
    assert "employee_count" not in a["patch"], "conflicts must never auto-patch"


def test_staleness_decays_health():
    fresh = audit_mod.data_health_score(
        {"gaps": [], "conflicts": [], "days_since_enriched": 10}, CONFIG)
    stale = audit_mod.data_health_score(
        {"gaps": [], "conflicts": [], "days_since_enriched": 900}, CONFIG)
    assert fresh["freshness"] == 30.0
    assert stale["freshness"] == 0.0
    assert fresh["total"] > stale["total"]


def test_unresolvable_domain_yields_no_patch():
    a = audit_mod.audit_record({"domain": "nonexistent-xyz.com"}, None, CONFIG, AS_OF)
    assert a["resolved"] is False
    assert a["patch"] == {}
