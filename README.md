# Account Book Triage

An Agent Skill that turns a stale CRM account export into a ranked,
defensible list of what a sales rep should do next — and a separate list of
records nobody should act on until a human resolves them.

Grounded in live [Apollo.io](https://apollo.io) company data: firmographic
enrichment, open job postings, and news.

**One-pager** — [read it rendered](https://rycolls.github.io/gtm-account-triage/) · [PDF](docs/account-book-triage-onepager.pdf)
GitHub displays `.html` files as source rather than rendering them, so use one of those links.

**Demo** — _Loom link to be added._

---

## The problem

A rep opens a book of 200 accounts on Monday. A third have missing
firmographics. Some were last verified two years ago. A handful point at
companies that have since been acquired. Nothing in the CRM distinguishes the
account hiring six Account Executives this month from the one dormant since
2023.

So the rep works the list alphabetically, and ops fields another "the data is
bad" complaint.

Most tools answer *"which accounts are hot?"* and quietly assume the records
are trustworthy. This one answers two questions and refuses to blend them:

| Question | Output |
|---|---|
| Is this record **fit to act on**? | `data_health` 0–100 — completeness, freshness, agreement with source of truth |
| Is something **happening** here? | `opportunity` 0–100 — GTM hiring intensity, recency, ICP fit |

A hot account with a broken record is not a medium-priority account. It is a
hot account you cannot route — and that distinction is the whole point.

## What it produces

```
out/
├── triage_report.md          # ranked actions, for the rep
├── conflicts_for_review.csv  # disagreements needing a human decision
├── accounts_patched.csv      # the book with safe gap-fills applied
├── triage_report.json        # full score breakdown, for audit
└── writeback_log.json        # what was (or would be) written to Apollo
```

Sample, from the 15-account demo book:

| # | Account | Action | Opp | Health | Next best action |
|---|---------|--------|-----|--------|------------------|
| 1 | HubSpot | **ENGAGE_NOW** | 85 | 63 | Work HubSpot this week — 15 open GTM roles of 25. Newest posting 0d ago. |
| 4 | Gong | **ENGAGE_NOW** | 78 | 93 | Work Gong this week — 10 open GTM roles of 25. Hiring: Mid Market Account Executive, France. |
| 9 | Drift | **FIX_DATA** | 72 | 50 | **Do not route Drift yet** — unresolved identity conflict on domain, account_name. |
| 14 | Clari | NURTURE | 40 | 100 | Keep in sequence — 1 open role, 462 days old. |
| 15 | Segment | DEPRIORITIZE | 32 | 62 | No live hiring or news signal this quarter. |

Row 9 is the one worth looking at. **Drift scores 72 on opportunity and is
still blocked**, because Apollo resolves `drift.com` to Salesloft — Drift was
acquired, and the CRM never absorbed it. A rep working that row would have
contacted the wrong company. Score alone would have sent them.

## Quick start

```bash
git clone <this repo> && cd gtm-account-triage
pip install -r requirements.txt

# Offline — no API key, no credits, real captured Apollo data
cd account-book-triage
python scripts/run_triage.py \
  --fixtures ../tests/fixtures/apollo_capture.json \
  --as-of 2026-09-10
```

Live:

```bash
export APOLLO_API_KEY=your_key_here     # never commit this
python scripts/run_triage.py --book assets/sample_account_book.csv --credit-budget 40
```

Write cleaned fields back to Apollo (opt-in; dry-run otherwise):

```bash
python scripts/run_triage.py --book assets/sample_account_book.csv --apply-writeback
```

Tests:

```bash
python -m pytest tests/ -q        # 32 tests, no network, no key
```

## Architecture

```
CSV export ─┐
            ├─► audit.py ──► gaps / staleness / conflicts ─┐
Apollo ─────┘   (pure, testable, no network)               │
  bulk_enrich                                              ├─► score.py ──► route ──► report
  job_postings ─► signals.py ──► scoreable numbers ────────┘   (applies      (rubric
  news_articles                                                 scoring.yaml)  gates)
                                                                    │
                                       accounts/create|update ◄─────┘  (gap-fills only)
```

**The division of labour is the design decision.** `scripts/` does everything
deterministic — API calls, gap detection, scoring arithmetic, writes. `SKILL.md`
holds only workflow and judgment. `assets/scoring.yaml` holds every weight, so
a RevOps lead can disagree with a number, change it, and re-run.

The model never computes a score, never invents a weight, and never phrases a
next-best-action — those come from templates fed by structured data, because
the inputs are already structured and an LLM would only add a hallucination
surface.

## Design decisions worth defending

**Why a skill and not a script?** The deterministic work belongs in scripts and
is here. What a bare script cannot carry is the judgment around it: when to dry
run, how to read credit cost aloud before spending it, why a conflict must never
be auto-applied, which of three kinds of domain mismatch actually blocks
outreach. That is procedural knowledge an operator needs at the moment of use,
which is what `SKILL.md` is for.

**Why not just call the Apollo MCP directly?** Both paths were tested, so this
is an observation rather than an argument.

MCP gives an agent tool access, not a method. Asked to "clean up my accounts"
against raw MCP tools, a model will enrich, then reason its way to a ranking in
prose — with weights nobody can audit and an order that changes between runs.
The rubric-in-a-file design makes the ranking reproducible and arguable, and it
survives the model being swapped underneath.

It also does not buy access. Calling people search through the Apollo MCP
returns the identical Free-plan refusal as the REST endpoint
(`error_code: API_INACCESSIBLE`). **MCP is a transport, not a permission** — the
plan gates at the API layer regardless of how a client reaches it. Worth knowing
before designing around a connector.

One genuine difference: connecting the MCP granted a one-time 100-credit bonus
(`mcp_credit_grant` in the profile response). Helpful for a POC, but it is
onboarding, not entitlement, so the client's credit budget still assumes the
75/month ceiling.

**Why gaps and conflicts are handled differently.** Filling an empty field from
a vendor is safe. Overwriting a populated field because a vendor disagrees is
how you destroy a CRM — the rep who typed "Bozeman" may have been on a call last
week, and the vendor may be wrong. Gaps auto-patch; conflicts go to a human with
both values and a reason.

**What was deliberately left out.** Contact-level mapping — who to actually
email — because Apollo's person endpoints are paid-plan only. See
`account-book-triage/references/apollo-endpoints.md` for the verified
entitlement matrix. Also skipped: dedupe/merge (a genuinely hard problem that
deserves its own tool), and any attempt at intent data.

## Where AI helped, and where it was wrong

AI wrote most of this code, and the write-up is honest about the parts it got
wrong, because those are the interesting parts.

**Helped:** scaffolding the client, the CSV/date normalization edge cases, the
report formatting, and the test suite. Roughly an afternoon's work compressed
into an hour.

**Wrong, caught by checking:**

1. **The documentation was believed over the API.** Apollo's docs say free
   accounts need a work-email address for search and enrichment. Reasoning from
   that, the initial conclusion was that a Gmail-registered account was blocked.
   A live call disproved it in thirty seconds: enrichment works fine, and the
   real gate is **plan tier**, stated plainly in the 403 body. The remedy that
   had been proposed — move the account to a work domain — would have cost an
   hour and fixed nothing.

2. **The first scoring rubric was confidently useless.** It ranked 13 of 15
   accounts as `ENGAGE_NOW`. Two specific defects: `hiring_recency` awarded full
   marks to 13/15 because companies this size post roles continuously, and
   `gtm_hiring_intensity` saturated at a 40% GTM mix — roughly the *normal* mix
   for B2B SaaS, so it measured "is a software company," not "is scaling
   revenue." Both look entirely reasonable on the page. Only the output
   distribution exposed them. The fix is in `scoring.yaml`'s calibration note.

3. **A test asserted the wrong thing.** `"Revenue Manager"` was written into the
   GTM-title test as a positive case. It is usually a revenue-*recognition*
   accounting role, and it appears in Snowflake's live postings — counting it
   would have inflated their hiring signal with a finance hire. The code was
   right; the test was wrong.

4. **Domain mismatches were nearly flattened into one rule.** Live data forced
   the distinction: Apollo returns a `.vercel.app` preview host for Gong (vendor
   wrong, CRM right), `outreach.ai` for `outreach.io` (same brand), and
   Salesloft for `drift.com` (genuine acquisition). One rule would have either
   blocked three good accounts or let the acquired one through.

The general lesson: AI-generated logic fails most often where it looks most
reasonable. Checking the *output distribution* caught what reading the code did
not.

## What would need hardening for production

- **Credits.** 75/month on the Free plan; 1 per company enriched. A 200-account
  book needs a paid plan or a scheduled trickle. The client enforces a budget
  and caches for 14 days, but real scale needs a queue and a spend dashboard.
- **Auth.** This POC uses a master key. Production should use a scoped key
  limited to the seven endpoints actually called, rotated, in a secrets manager
  — not an env var on a laptop.
- **Write-back safety.** Currently dry-run by default with gap-fills only. Needs
  an idempotency key, a reversible audit log, and a staged rollout before
  pointing it at a live CRM.
- **Rubric governance.** `scoring.yaml` drives who a sales team calls. It should
  be version-controlled with review, and changes should be backtested against
  closed-won history rather than reasoned about — the weights here are informed
  guesses, and nothing in this repo validates them against outcomes.
- **Signal quality.** Apollo's news feed carries meaningful listicle spam. A
  production version should filter by article category and source reputation, or
  drop news for a real intent source.
- **Scale.** Everything is in-memory and single-threaded. Fine at 200 accounts,
  wrong at 50,000.

## Repository layout

```
gtm-account-triage/
├── README.md
├── requirements.txt
├── account-book-triage/          # ← the Agent Skill
│   ├── SKILL.md
│   ├── scripts/                  # apollo_client, audit, signals, score, run_triage
│   ├── references/               # endpoint entitlements, field mapping
│   └── assets/                   # scoring.yaml, sample_account_book.csv
└── tests/
    ├── fixtures/apollo_capture.json
    └── test_pipeline.py
```

## Notes on the demo data

`assets/sample_account_book.csv` is a synthetic CRM export over **15 real
company domains**, with defects seeded deliberately: missing firmographics,
stale enrichment dates back to 2021, headcounts off by 60%+, and a city
mismatch.

The enrichment and signals in `tests/fixtures/apollo_capture.json` are **real
Apollo responses** captured on 2026-09-10, aggregated to the fields the scorer
consumes. Tests run against that capture so the suite is reproducible and costs
no credits.

The most interesting defects were not seeded — they came back from the live API.

## License

MIT
