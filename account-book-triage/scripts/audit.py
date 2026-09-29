"""
audit.py — deterministic record-quality analysis.

Three defect classes, deliberately kept separate because they imply different
remedies:

  GAP        the CRM field is empty and Apollo can fill it        -> auto-patch
  STALENESS  the record has not been verified in a long time      -> re-verify
  CONFLICT   the CRM and Apollo disagree on a populated field     -> HUMAN REVIEW

The conflict/gap distinction is the whole design. Auto-filling an empty field is
safe. Overwriting a populated field because a vendor disagrees is how you
destroy a CRM: the rep who typed "Bozeman" may have been on a call with the
company last week, and Apollo may be wrong. So conflicts are never auto-applied
— they are reported with both values and a reason, and a human decides.

Every function here is pure and dependency-free so it can be unit-tested
without a network or an API key.
"""

from __future__ import annotations

import csv
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

# Fields the audit can safely copy from Apollo into an empty CRM field.
APOLLO_FIELD_MAP = {
    "industry": "industry",
    "employee_count": "estimated_num_employees",
    "hq_city": "city",
    "hq_state": "state",
    "phone": "phone",
    "linkedin_url": "linkedin_url",
}

_DOMAIN_STRIP = re.compile(r"^(https?://)?(www\.)?", re.I)
_LEGAL_SUFFIX = re.compile(
    r"\b(inc|inc\.|llc|ltd|corp|corporation|co|company|holdings|group|"
    r"technologies|technology|software|labs|io)\b",
    re.I,
)


def normalize_domain(raw: Optional[str]) -> str:
    """Reduce a hand-entered domain to a comparable form.

    Real CRM exports contain 'https://www.Gong.io/', 'GONG.IO', and 'gong.io'
    as three different accounts. Normalizing before comparison is what turns
    those into one.
    """
    if not raw:
        return ""
    value = _DOMAIN_STRIP.sub("", raw.strip().lower())
    return value.split("/")[0].strip().rstrip(".")


def normalize_company_name(raw: Optional[str]) -> str:
    if not raw:
        return ""
    value = raw.strip().lower()
    value = re.sub(r"[^\w\s]", " ", value)
    value = _LEGAL_SUFFIX.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


# Hosts that indicate the VENDOR indexed a deployment artifact rather than the
# company's real site. Observed live: Apollo returned
# "gong-next-sanity-web.vercel.app" as Gong's primary_domain.
_SUSPECT_HOST = re.compile(
    r"\.(vercel\.app|netlify\.app|herokuapp\.com|pages\.dev|"
    r"github\.io|azurewebsites\.net|cloudfront\.net|web\.app)$",
    re.I,
)


def _second_level_label(domain: str) -> str:
    """'outreach.ai' -> 'outreach'. Crude but sufficient for brand comparison."""
    parts = [p for p in domain.split(".") if p]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")


def classify_domain_conflict(crm_domain: str, apollo_domain: str) -> Dict[str, Any]:
    """Decide what a domain disagreement actually means.

    Three different situations get flattened into "domain mismatch" by naive
    implementations, and they have opposite remedies:

      vendor_suspect  Apollo indexed a preview/CDN host. The CRM is RIGHT and
                      the vendor is wrong. Do not block the rep; do not patch.
      tld_variance    Same brand, different TLD (outreach.io vs outreach.ai).
                      Usually a rebrand or vendor variance. Worth a look, not
                      a blocker.
      identity        Different company entirely — almost always an acquisition
                      the CRM has not absorbed. This one is a hard stop.

    Only `identity` is high severity, because only `identity` means a rep is
    about to contact the wrong organization.
    """
    base = {"field": "domain", "crm": crm_domain, "apollo": apollo_domain}

    if _SUSPECT_HOST.search(apollo_domain):
        return {
            **base,
            "severity": "medium",
            "kind": "vendor_suspect",
            "reason": (
                "Apollo returned a deployment/CDN host, not a corporate domain. "
                "The vendor record is suspect here, not the CRM — keep the CRM "
                "value and do not patch from this enrichment."
            ),
        }

    if _second_level_label(crm_domain) == _second_level_label(apollo_domain):
        return {
            **base,
            "severity": "medium",
            "kind": "tld_variance",
            "reason": (
                "Same brand on a different TLD. Likely a rebrand or vendor "
                "variance rather than a different company. Confirm which domain "
                "is canonical before bulk email."
            ),
        }

    return {
        **base,
        "severity": "high",
        "kind": "identity",
        "reason": (
            "Apollo resolved this domain to a different company. Almost always "
            "an acquisition the CRM has not absorbed. Do not contact until "
            "resolved — outreach would reach the wrong organization."
        ),
    }


def is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def to_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_date(value: Any) -> Optional[date]:
    if is_blank(value):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def load_book(csv_path: str) -> List[Dict[str, Any]]:
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _relative_diff(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1.0)
    return abs(a - b) / denom


def audit_record(
    record: Dict[str, Any],
    org: Optional[Dict[str, Any]],
    config: Dict[str, Any],
    as_of: date,
) -> Dict[str, Any]:
    """Compare one CRM row against its Apollo enrichment."""
    dh = config["data_health"]
    required = dh["required_fields"]
    numeric_threshold = dh.get("numeric_conflict_threshold", 0.25)

    gaps: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    patch: Dict[str, Any] = {}

    requested_domain = normalize_domain(record.get("domain"))

    if org is None:
        # Unresolvable is its own failure mode: every required field is a gap
        # and nothing can be proposed.
        for field in required:
            if is_blank(record.get(field)):
                gaps.append({"field": field, "reason": "empty; not resolvable in Apollo"})
        return {
            "resolved": False,
            "gaps": gaps,
            "conflicts": [],
            "patch": {},
            "days_since_enriched": _days_since(record, as_of),
            "apollo_org_id": None,
        }

    # --- gaps: empty in CRM, present in Apollo -> safe to fill ---------------
    for field in required:
        apollo_key = APOLLO_FIELD_MAP.get(field)
        if not apollo_key:
            continue
        crm_value = record.get(field)
        apollo_value = org.get(apollo_key)
        if is_blank(crm_value):
            if not is_blank(apollo_value):
                gaps.append({"field": field, "filled_with": apollo_value, "source": "apollo"})
                patch[field] = apollo_value
            else:
                gaps.append({"field": field, "reason": "empty in both CRM and Apollo"})

    # --- conflict: identity. Apollo resolved us somewhere else --------------
    apollo_domain = normalize_domain(org.get("primary_domain"))
    if apollo_domain and requested_domain and apollo_domain != requested_domain:
        conflicts.append(classify_domain_conflict(requested_domain, apollo_domain))

    crm_name = normalize_company_name(record.get("account_name"))
    apollo_name = normalize_company_name(org.get("name"))
    if crm_name and apollo_name and crm_name != apollo_name:
        if crm_name not in apollo_name and apollo_name not in crm_name:
            conflicts.append({
                "field": "account_name",
                "crm": record.get("account_name"),
                "apollo": org.get("name"),
                "severity": "high",
                "reason": (
                    "Company name disagrees entirely. Usually an acquisition the "
                    "CRM has not absorbed."
                ),
            })

    # --- conflict: numeric drift -------------------------------------------
    crm_employees = to_int(record.get("employee_count"))
    apollo_employees = to_int(org.get("estimated_num_employees"))
    if crm_employees and apollo_employees:
        drift = _relative_diff(crm_employees, apollo_employees)
        if drift > numeric_threshold:
            conflicts.append({
                "field": "employee_count",
                "crm": crm_employees,
                "apollo": apollo_employees,
                "severity": "medium",
                "reason": f"headcount differs by {drift:.0%} (threshold {numeric_threshold:.0%})",
            })

    # --- conflict: categorical ---------------------------------------------
    for field, apollo_key, severity in (
        ("industry", "industry", "low"),
        ("hq_city", "city", "low"),
        ("hq_state", "state", "low"),
    ):
        crm_value = record.get(field)
        apollo_value = org.get(apollo_key)
        if is_blank(crm_value) or is_blank(apollo_value):
            continue
        if str(crm_value).strip().lower() != str(apollo_value).strip().lower():
            conflicts.append({
                "field": field,
                "crm": crm_value,
                "apollo": apollo_value,
                "severity": severity,
                "reason": "CRM and Apollo disagree on a populated field",
            })

    return {
        "resolved": True,
        "gaps": gaps,
        "conflicts": conflicts,
        "patch": patch,
        "days_since_enriched": _days_since(record, as_of),
        "apollo_org_id": org.get("id"),
    }


def _days_since(record: Dict[str, Any], as_of: date) -> Optional[int]:
    last = parse_date(record.get("last_enriched"))
    return (as_of - last).days if last else None


def data_health_score(audit: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the data_health section of scoring.yaml. Pure arithmetic."""
    dh = config["data_health"]
    weights = dh["weights"]
    required = dh["required_fields"]

    # completeness — gaps that were fillable still count as gaps; the record is
    # only healthy once the patch is applied.
    missing = len([g for g in audit["gaps"]])
    present = max(len(required) - missing, 0)
    completeness = (present / len(required)) * weights["completeness"]

    # freshness — linear decay between fresh_days and stale_days
    fresh_days = dh["freshness"]["fresh_days"]
    stale_days = dh["freshness"]["stale_days"]
    days = audit["days_since_enriched"]
    if days is None:
        freshness = 0.0
    elif days <= fresh_days:
        freshness = float(weights["freshness"])
    elif days >= stale_days:
        freshness = 0.0
    else:
        span = stale_days - fresh_days
        freshness = weights["freshness"] * (1 - (days - fresh_days) / span)

    # consistency
    penalty = dh["consistency"]["penalty_per_conflict"] * len(audit["conflicts"])
    consistency = max(weights["consistency"] - penalty, 0.0)

    total = completeness + freshness + consistency
    return {
        "completeness": round(completeness, 1),
        "freshness": round(freshness, 1),
        "consistency": round(consistency, 1),
        "total": round(total, 1),
    }
