---
name: account-book-triage
description: >-
  Audits, enriches and prioritises a book of CRM accounts against live Apollo.io
  company data, then emits a ranked next-best-action list plus a reviewable set
  of data conflicts. Use when someone asks to clean up, audit, enrich, score,
  rank, prioritise or triage accounts, a territory, a target list, a book of
  business or a CRM export; when they mention stale, incomplete, duplicate or
  conflicting account records, missing firmographics, or data hygiene; or when
  they ask which accounts a rep should work next and why. Reads a CSV export or
  Apollo accounts and can write cleaned fields back. Do NOT use for
  contact-level or person-level prospecting, finding email addresses or phone
  numbers, or building a list of people to contact — those Apollo endpoints
  require a paid plan and this skill is company-level only.
license: MIT
compatibility: >-
  Requires Python 3.9+, pyyaml and requests. Needs network access to
  api.apollo.io and an APOLLO_API_KEY environment variable for live runs;
  runs fully offline against the bundled fixture without either.
metadata:
  author: rcollins
  version: "1.1"
---

# Account Book Triage

Turn a stale CRM account export into a ranked, defensible list of what a rep
should do next — and a separate list of records nobody should act on until a
human resolves them.

## The problem this solves

A rep opens their book of 200 accounts on Monday. Roughly a third have missing
firmographics, a slice were last verified two years ago, and a handful point at
companies that have since been acquired. Nothing in the CRM distinguishes the
account that is hiring six Account Executives this month from the one that has
been dormant since 2023.

So the rep works the list top to bottom, alphabetically, and the ops team fields
another "the data is bad" complaint.

This skill separates two questions that CRMs habitually blend:

1. **Is this record fit to act on?** (completeness, freshness, agreement with a
   source of truth)
2. **Is something happening at this company worth an hour?** (GTM hiring,
   recency, ICP fit)

Blending those into one number hides the problem. A hot account with a broken
record is not a medium-priority account — it is a hot account you cannot route.

## What it does NOT do

- **No person-level data.** Apollo's people search and person enrichment
  endpoints are paid-plan only. This skill is company-level by design. See
  `references/apollo-endpoints.md` for the verified entitlement matrix.
- **Never auto-overwrites a populated field.** Empty fields get patched from
  Apollo; disagreements get reported for a human. See "Guardrails".
- **Never invents a score.** Every weight lives in `assets/scoring.yaml`.

## Prerequisites

```bash
pip install -r requirements.txt        # pyyaml, requests
export APOLLO_API_KEY=...              # live runs only; never commit this
```

The input is a CSV with at least `account_id`, `account_name`, `domain`.
Optional but strongly recommended: `industry`, `employee_count`, `hq_city`,
`hq_state`, `phone`, `linkedin_url`, `owner`, `lifecycle_stage`,
`last_enriched`. Column mapping is in `references/field-mapping.md`.

## Workflow

### 1. Establish the input and confirm scope

Identify the CSV and roughly how many accounts it holds. **Before any live run,
state the credit cost out loud**: enrichment costs 1 Apollo credit per company
and the Free plan allows 75 per month. A 200-account book cannot be enriched on
a free plan in one pass. If the book exceeds the budget, say so and offer to
narrow it (by owner, by lifecycle stage) rather than silently truncating.

### 2. Dry run against the fixture first

```bash
python scripts/run_triage.py \
  --fixtures ../tests/fixtures/apollo_capture.json \
  --as-of 2026-09-10
```

This costs nothing and proves the pipeline works before spending credits.

### 3. Live run

```bash
python scripts/run_triage.py \
  --book path/to/accounts.csv \
  --credit-budget 40
```

The client refuses to exceed `--credit-budget` and raises **before** the call
rather than discovering the overspend afterwards. Enrichment results are cached
on disk for 14 days, so re-running during development is free.

### 4. Read the output, in this order

| File | What it is | Who acts on it |
|---|---|---|
| `out/triage_report.md` | Ranked actions, human-readable | The rep |
| `out/conflicts_for_review.csv` | Disagreements needing a decision | Ops |
| `out/accounts_patched.csv` | The book with safe gap-fills applied | Ops |
| `out/triage_report.json` | Full component breakdown | Debugging / audit |

Lead with the **FIX_DATA** accounts, not the ENGAGE_NOW ones. Those are the
records that would have wasted a rep's time, and surfacing them is the part a
CRM report cannot do.

### 5. Write back (explicit opt-in)

```bash
python scripts/run_triage.py --book accounts.csv --apply-writeback
```

Off by default. Only gap-fills are ever written — conflicts are excluded by
construction. Always show the dry-run `writeback_log.json` before applying.

## Interpreting the four actions

- **FIX_DATA** — record is not trustworthy. Either health is below 50, or there
  is a high-severity identity conflict. Do not route to a rep.
- **ENGAGE_NOW** — live hiring signal, clean enough record. Worth this week.
- **NURTURE** — real but not urgent. Keep in sequence.
- **DEPRIORITIZE** — no live signal. Recheck next quarter.

Ordering within a band is by opportunity score, descending.

## Guardrails

These are not style preferences. Violating them makes the tool worse than
nothing:

1. **A gap is not a conflict.** An empty field can be filled from Apollo. A
   populated field that disagrees with Apollo must NOT be overwritten — the rep
   who typed it may have been on a call with the company last week, and the
   vendor may simply be wrong. Report both values and let a human choose.

2. **Not every domain disagreement means the CRM is wrong.** The audit
   distinguishes three cases, and they have opposite remedies:
   - `vendor_suspect` — Apollo returned a deployment or CDN host (observed
     live: a `.vercel.app` preview URL as a company's primary domain). The CRM
     is right. Do not patch, do not block.
   - `tld_variance` — same brand, different TLD. Worth confirming, not blocking.
   - `identity` — a different company entirely, almost always an acquisition.
     **This one is a hard stop** regardless of how well the account scores.

3. **Never present a score without its components.** A rep who cannot see why
   an account scored 71 will not trust the 71. The JSON output carries the full
   breakdown and the reasons that fired.

4. **Do not re-derive the weights.** If asked to change prioritisation, edit
   `assets/scoring.yaml` and re-run. Do not reason about ranking in prose and
   present the result as the tool's output.

5. **Check the distribution, not just that it ran.** If most of the book lands
   in one action band, the rubric is broken and needs recalibrating — a signal
   that fires for almost every record is not a signal. This happened during
   development; see the calibration note in `assets/scoring.yaml`.

## Reference material

Load these only when needed:

- `references/apollo-endpoints.md` — verified Free-plan entitlements, credit
  costs, rate and batch limits, and the exact 403 behaviour on gated endpoints.
- `references/field-mapping.md` — CSV ↔ Apollo field mapping, normalisation
  rules, and how to adapt the skill to a different CRM export.

## Layout

```
account-book-triage/
├── SKILL.md
├── scripts/
│   ├── apollo_client.py   # the only module that talks to Apollo
│   ├── audit.py           # gaps / staleness / conflicts (pure, testable)
│   ├── signals.py         # job postings + news -> scoreable numbers
│   ├── score.py           # applies scoring.yaml; no judgement of its own
│   └── run_triage.py      # orchestrator CLI
├── references/
└── assets/
    ├── scoring.yaml               # the rubric — every weight a human owns
    └── sample_account_book.csv    # 15 accounts with seeded real-world defects
```
