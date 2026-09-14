"""
score.py — applies assets/scoring.yaml. Contains no judgment of its own.

Every threshold, weight and bucket is read from the YAML. If you want to argue
with the ranking, argue with that file. This module only does arithmetic, and
it returns the component breakdown alongside the total so any score can be
recomputed by hand from the output.

That auditability is the point: a rep who cannot see why an account scored 71
will not trust the 71.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _bucket_points(value: Optional[int], buckets: List[Dict[str, Any]], default: int) -> int:
    if value is None:
        return default
    for bucket in buckets:
        if value <= bucket["max_days"]:
            return bucket["points"]
    return default


def icp_fit(org: Optional[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    icp = config["icp"]
    rules = config["opportunity"]["icp_fit"]
    lo = icp["employee_range"]["min"]
    hi = icp["employee_range"]["max"]
    mult = rules["near_range_multiplier"]

    points = 0
    reasons: List[str] = []

    employees = (org or {}).get("estimated_num_employees")
    try:
        employees = int(employees) if employees is not None else None
    except (TypeError, ValueError):
        employees = None

    if employees is not None:
        if lo <= employees <= hi:
            points += rules["in_range_points"]
            reasons.append(f"headcount {employees:,} inside ICP {lo:,}-{hi:,}")
        elif lo / mult <= employees <= hi * mult:
            points += rules["near_range_points"]
            reasons.append(f"headcount {employees:,} near ICP band")
        else:
            reasons.append(f"headcount {employees:,} outside ICP band")
    else:
        reasons.append("headcount unknown")

    industry = ((org or {}).get("industry") or "").lower()
    if industry and any(t.lower() in industry for t in icp["target_industries"]):
        points += rules["industry_match_points"]
        reasons.append(f"industry '{industry}' matches ICP")

    return {"points": points, "reasons": reasons}


def opportunity_score(
    signals: Dict[str, Any],
    org: Optional[Dict[str, Any]],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    opp = config["opportunity"]
    weights = opp["weights"]
    reasons: List[str] = []

    # --- GTM hiring intensity ----------------------------------------------
    intensity_cfg = opp["gtm_hiring_intensity"]
    jobs_total = signals.get("jobs_total") or 0
    jobs_gtm = signals.get("jobs_gtm") or 0

    if jobs_total < intensity_cfg["min_postings_for_signal"]:
        intensity = 0.0
        reasons.append(
            f"only {jobs_total} open role(s) — below the "
            f"{intensity_cfg['min_postings_for_signal']}-posting floor, "
            f"hiring mix is not meaningful"
        )
    else:
        # Scored on EXCESS over the baseline SaaS GTM mix, not raw ratio.
        # Raw ratio rewarded every software company equally; see the calibration
        # note in scoring.yaml.
        baseline = intensity_cfg.get("baseline_ratio", 0.0)
        saturation = intensity_cfg["saturation_ratio"]
        ratio = jobs_gtm / jobs_total
        span = max(saturation - baseline, 1e-9)
        scaled = min(max((ratio - baseline) / span, 0.0), 1.0)
        intensity = scaled * weights["gtm_hiring_intensity"]
        if ratio <= baseline:
            reasons.append(
                f"{jobs_gtm}/{jobs_total} open roles are GTM ({ratio:.0%}) — at or "
                f"below the {baseline:.0%} baseline mix, no scaling signal"
            )
        else:
            reasons.append(
                f"{jobs_gtm}/{jobs_total} open roles are GTM ({ratio:.0%}), "
                f"{ratio - baseline:+.0%} over baseline"
            )

    # --- hiring recency -----------------------------------------------------
    recency_cfg = opp["hiring_recency"]
    days_job = signals.get("days_since_newest_job")
    hiring_recency = _bucket_points(days_job, recency_cfg["buckets"], recency_cfg["default_points"])
    if days_job is None:
        reasons.append("no dated job postings")
    else:
        reasons.append(f"most recent posting {days_job}d ago")

    # --- news recency -------------------------------------------------------
    news_cfg = opp["news_recency"]
    days_news = signals.get("days_since_newest_news")
    news_recency = _bucket_points(days_news, news_cfg["buckets"], news_cfg["default_points"])
    if days_news is not None:
        reasons.append(f"most recent news {days_news}d ago")

    # --- ICP fit ------------------------------------------------------------
    fit = icp_fit(org, config)
    reasons.extend(fit["reasons"])

    total = intensity + hiring_recency + news_recency + fit["points"]

    return {
        "gtm_hiring_intensity": round(intensity, 1),
        "hiring_recency": hiring_recency,
        "news_recency": news_recency,
        "icp_fit": fit["points"],
        "total": round(total, 1),
        "reasons": reasons,
    }


def route(
    data_health: float,
    opportunity: float,
    config: Dict[str, Any],
    has_high_severity_conflict: bool = False,
) -> Dict[str, str]:
    """First matching rule in scoring.yaml's `routing` list wins."""
    for rule in config["routing"]:
        when = rule.get("when") or {}
        meaning = rule.get("meaning", "").strip()
        if not when:
            return {"action": rule["action"], "meaning": meaning}
        if when.get("has_high_severity_conflict") and has_high_severity_conflict:
            return {"action": rule["action"], "meaning": meaning}
        if "data_health_below" in when and data_health < when["data_health_below"]:
            return {"action": rule["action"], "meaning": meaning}
        if "opportunity_at_least" in when and opportunity >= when["opportunity_at_least"]:
            return {"action": rule["action"], "meaning": meaning}
    return {"action": "DEPRIORITIZE", "meaning": ""}


def next_best_action(row: Dict[str, Any]) -> str:
    """One concrete sentence a rep can act on, assembled from what actually fired.

    Deliberately template-driven rather than model-generated. The inputs are
    already structured; asking an LLM to phrase them adds a hallucination
    surface for no gain in precision.
    """
    action = row["action"]
    name = row["account_name"]
    signals = row["signals"]
    conflicts = row["audit"]["conflicts"]
    gaps = row["audit"]["gaps"]

    if action == "FIX_DATA":
        parts = []
        if gaps:
            parts.append(f"{len(gaps)} missing field(s)")
        if conflicts:
            high = [c for c in conflicts if c["severity"] == "high"]
            if high:
                fields = ", ".join(c["field"] for c in high)
                parts.append(f"unresolved identity conflict on {fields}")
            else:
                parts.append(f"{len(conflicts)} field conflict(s)")
        detail = "; ".join(parts) if parts else "record incomplete"
        return f"Do not route {name} yet — {detail}. Apply the patch, resolve conflicts, re-run."

    if action == "ENGAGE_NOW":
        titles = signals.get("sample_gtm_titles") or []
        hook = f" Hiring: {titles[0]}." if titles else ""
        days = signals.get("days_since_newest_job")
        when = f" Newest posting {days}d ago." if days is not None else ""
        return (
            f"Work {name} this week — {signals.get('jobs_gtm', 0)} open GTM roles "
            f"of {signals.get('jobs_total', 0)}.{hook}{when} "
            f"Lead with revenue-team scaling."
        )

    if action == "NURTURE":
        return (
            f"Keep {name} in sequence — real but not urgent "
            f"({signals.get('jobs_gtm', 0)} GTM roles open). Revisit if hiring accelerates."
        )

    return (
        f"Deprioritize {name} — no live hiring or news signal this quarter. "
        f"Recheck next quarter rather than spending rep time now."
    )
