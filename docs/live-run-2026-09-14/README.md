# Live run — 2026-09-14

Evidence that the skill runs against the live Apollo API, not only fixtures.

## What was pulled live

All 15 account domains were re-enriched through the **Apollo MCP connector** on
2026-09-14 (`organizations/bulk_enrich`, 2 calls of 10 + 5, **15 credits**).

Hiring and news signals were **served from the 2026-09-10 capture**, deliberately.
The client caches enrichment for 14 days; a real run today would hit that cache
rather than re-spend. Refreshing them would have contradicted the credit guard
the skill exists to demonstrate.

## Result: zero drift

Every firmographic field matched the 2026-09-10 capture exactly — name,
primary_domain, employee count, industry, city, across all 15 companies.

Two things follow:

1. **The three data defects are stable, not flukes.** `gong.io` still resolves to
   `gong-next-sanity-web.vercel.app`, `outreach.io` still to `outreach.ai`, and
   `drift.com` still to **Salesloft**. The conflict classifier is reacting to
   persistent vendor behaviour.
2. **The 14-day cache TTL is defensible.** It was an informed guess when written.
   Four days of zero drift across 15 companies is weak but real evidence that
   firmographics move on a scale of weeks, not days.

## Output

Identical routing to the fixture run: 8 ENGAGE_NOW, 1 FIX_DATA, 5 NURTURE,
1 DEPRIORITIZE. Drift remains gated to FIX_DATA at opportunity 72.
