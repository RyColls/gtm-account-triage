#!/usr/bin/env python3
"""
run_triage.py — orchestrator. Audit -> enrich -> signals -> score -> route -> write.

Run offline against the captured fixture (no key, no credits):

    python scripts/run_triage.py --fixtures ../tests/fixtures/apollo_capture.json

Run live against Apollo:

    export APOLLO_API_KEY=...
    python scripts/run_triage.py --book assets/sample_account_book.csv

Write cleaned records back into Apollo (explicit opt-in, off by default):

    python scripts/run_triage.py --book assets/sample_account_book.csv --apply-writeback
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

import audit as audit_mod  # noqa: E402
import score as score_mod  # noqa: E402
import signals as signals_mod  # noqa: E402
from apollo_client import (  # noqa: E402
    ApolloClient,
    CreditBudgetExceeded,
    PlanRestrictionError,
)

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Triage a book of CRM accounts.")
    p.add_argument("--book", default=str(SKILL_ROOT / "assets" / "sample_account_book.csv"))
    p.add_argument("--config", default=str(SKILL_ROOT / "assets" / "scoring.yaml"))
    p.add_argument("--fixtures", default=None,
                   help="Run offline against a captured Apollo payload.")
    p.add_argument("--out-dir", default="out")
    p.add_argument("--as-of", default=None,
                   help="YYYY-MM-DD. Pins 'today' so runs are reproducible.")
    p.add_argument("--credit-budget", type=int, default=40)
    p.add_argument("--cache-ttl-days", type=int, default=14)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--apply-writeback", action="store_true",
                   help="Actually write to Apollo. Off by default; dry-run otherwise.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    as_of = (
        datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else date.today()
    )

    with open(args.config, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    book = audit_mod.load_book(args.book)
    if args.limit:
        book = book[: args.limit]

    client = ApolloClient(
        cache_ttl_days=args.cache_ttl_days,
        credit_budget=args.credit_budget,
        fixtures_path=args.fixtures,
    )

    # Fail fast and legibly rather than mid-batch.
    try:
        profile = client.api_profile()
    except PlanRestrictionError as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 2
    mode = "fixtures" if args.fixtures else f"live as {profile.get('email', 'unknown')}"
    print(f"[info] mode: {mode}; {len(book)} accounts")

    domains = [audit_mod.normalize_domain(r.get("domain")) for r in book]
    domains = [d for d in domains if d]

    try:
        enriched = client.bulk_enrich(domains)
    except CreditBudgetExceeded as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 3
    except PlanRestrictionError as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 2

    print(f"[info] resolved {len(enriched)}/{len(domains)} domains; "
          f"{client.credits_spent} credit(s) spent")

    rows: List[Dict[str, Any]] = []
    for record in book:
        domain = audit_mod.normalize_domain(record.get("domain"))
        org = enriched.get(domain)

        a = audit_mod.audit_record(record, org, config, as_of)
        health = audit_mod.data_health_score(a, config)

        if args.fixtures:
            sig = (
                json.load(open(args.fixtures, encoding="utf-8"))
                .get("signals", {})
                .get(domain)
                or signals_mod.empty_summary()
            )
        elif org:
            org_id = org.get("id")
            postings = client.job_postings(org_id) if org_id else []
            articles = client.news_articles(org_id) if org_id else []
            sig = signals_mod.summarize(postings, articles, as_of)
        else:
            sig = signals_mod.empty_summary()

        opp = score_mod.opportunity_score(sig, org, config)
        has_high = any(c["severity"] == "high" for c in a["conflicts"])
        routed = score_mod.route(health["total"], opp["total"], config, has_high)

        row = {
            "account_id": record.get("account_id"),
            "account_name": record.get("account_name"),
            "domain": domain,
            "owner": record.get("owner"),
            "lifecycle_stage": record.get("lifecycle_stage"),
            "data_health": health,
            "opportunity": opp,
            "action": routed["action"],
            "action_meaning": routed["meaning"],
            "signals": sig,
            "audit": a,
        }
        row["next_best_action"] = score_mod.next_best_action(row)
        rows.append(row)

    # ENGAGE_NOW first, then by opportunity within each action band.
    order = {"ENGAGE_NOW": 0, "FIX_DATA": 1, "NURTURE": 2, "DEPRIORITIZE": 3}
    rows.sort(key=lambda r: (order.get(r["action"], 9), -r["opportunity"]["total"]))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "triage_report.json", rows, client, as_of, mode)
    _write_markdown(out_dir / "triage_report.md", rows, as_of, mode)
    _write_patched_csv(out_dir / "accounts_patched.csv", book, rows, as_of)
    _write_conflicts_csv(out_dir / "conflicts_for_review.csv", rows)

    writes = _writeback(client, rows, apply=args.apply_writeback)
    _write_json_plain(out_dir / "writeback_log.json", writes)

    _print_summary(rows, client, out_dir, args.apply_writeback)
    return 0


def _writeback(client: ApolloClient, rows, apply: bool):
    """Push safe patches to Apollo accounts.

    Only GAP fills are ever written. Conflicts are excluded by construction —
    a disagreement is a question for a human, not a value to overwrite.
    """
    log = []
    for row in rows:
        patch = row["audit"].get("patch") or {}
        if not patch:
            continue
        fields = {}
        if "phone" in patch:
            fields["phone"] = patch["phone"]
        if "hq_city" in patch:
            fields["city"] = patch["hq_city"]
        if "hq_state" in patch:
            fields["state"] = patch["hq_state"]
        if not fields:
            continue
        result = client.upsert_account(row["domain"], fields, dry_run=not apply)
        log.append({"domain": row["domain"], "applied": apply, "result": result})
    return log


def _write_json(path: Path, rows, client, as_of, mode):
    payload = {
        "generated_at": as_of.isoformat(),
        "mode": mode,
        "credits_spent": client.credits_spent,
        "accounts": rows,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _write_json_plain(path: Path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _write_patched_csv(path: Path, book, rows, as_of):
    by_id = {r["account_id"]: r for r in rows}
    if not book:
        return
    fieldnames = list(book[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for record in book:
            out = dict(record)
            row = by_id.get(record.get("account_id"))
            if row:
                for field, value in (row["audit"].get("patch") or {}).items():
                    if field in out:
                        out[field] = value
                if row["audit"].get("patch"):
                    out["last_enriched"] = as_of.isoformat()
            writer.writerow(out)


def _write_conflicts_csv(path: Path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["account_id", "account_name", "field", "severity", "crm_value",
             "apollo_value", "reason"]
        )
        for row in rows:
            for conflict in row["audit"]["conflicts"]:
                writer.writerow([
                    row["account_id"], row["account_name"], conflict["field"],
                    conflict["severity"], conflict.get("crm"), conflict.get("apollo"),
                    conflict.get("reason"),
                ])


def _write_markdown(path: Path, rows, as_of, mode):
    lines = [
        "# Account Book Triage",
        "",
        f"_Generated {as_of.isoformat()} · source: {mode}_",
        "",
        "| # | Account | Action | Opp | Health | Next best action |",
        "|---|---------|--------|-----|--------|------------------|",
    ]
    for i, row in enumerate(rows, 1):
        lines.append(
            f"| {i} | {row['account_name']} | **{row['action']}** | "
            f"{row['opportunity']['total']:.0f} | {row['data_health']['total']:.0f} | "
            f"{row['next_best_action']} |"
        )

    lines += ["", "## Conflicts requiring human review", ""]
    any_conflict = False
    for row in rows:
        high = [c for c in row["audit"]["conflicts"] if c["severity"] in ("high", "medium")]
        if not high:
            continue
        any_conflict = True
        lines.append(f"### {row['account_name']} ({row['domain']})")
        for c in high:
            lines.append(
                f"- **{c['field']}** — CRM `{c.get('crm')}` vs Apollo "
                f"`{c.get('apollo')}` · _{c['reason']}_"
            )
        lines.append("")
    if not any_conflict:
        lines.append("_None._")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _print_summary(rows, client, out_dir, applied):
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["action"]] = counts.get(row["action"], 0) + 1
    total_conflicts = sum(len(r["audit"]["conflicts"]) for r in rows)
    total_gaps = sum(len(r["audit"]["gaps"]) for r in rows)

    print("\n=== Triage summary ===")
    for action in ("ENGAGE_NOW", "FIX_DATA", "NURTURE", "DEPRIORITIZE"):
        if action in counts:
            print(f"  {action:<14} {counts[action]}")
    print(f"  gaps found     {total_gaps}")
    print(f"  conflicts      {total_conflicts} (held for human review)")
    print(f"  credits spent  {client.credits_spent}")
    print(f"  write-back     {'APPLIED' if applied else 'dry-run'}")
    print(f"\nWrote {out_dir}/triage_report.md, triage_report.json, "
          f"accounts_patched.csv, conflicts_for_review.csv")


if __name__ == "__main__":
    raise SystemExit(main())
