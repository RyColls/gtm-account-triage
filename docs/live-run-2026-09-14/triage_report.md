# Account Book Triage

_Generated 2026-09-14 · source: fixtures_

| # | Account | Action | Opp | Health | Next best action |
|---|---------|--------|-----|--------|------------------|
| 1 | HubSpot | **ENGAGE_NOW** | 85 | 63 | Work HubSpot this week — 15 open GTM roles of 25. Newest posting 0d ago. Lead with revenue-team scaling. |
| 2 | Looker | **ENGAGE_NOW** | 84 | 70 | Work Looker this week — 12 open GTM roles of 17. Newest posting 5d ago. Lead with revenue-team scaling. |
| 3 | Outreach | **ENGAGE_NOW** | 84 | 68 | Work Outreach this week — 11 open GTM roles of 25. Newest posting 0d ago. Lead with revenue-team scaling. |
| 4 | Gong | **ENGAGE_NOW** | 78 | 93 | Work Gong this week — 10 open GTM roles of 25. Hiring: Mid Market Account Executive, France. Newest posting 1d ago. Lead with revenue-team scaling. |
| 5 | Klaviyo | **ENGAGE_NOW** | 78 | 93 | Work Klaviyo this week — 10 open GTM roles of 25. Hiring: Product Marketing Manager - Mobile New San Francisco, CA. Newest posting 0d ago. Lead with revenue-team scaling. |
| 6 | Pendo | **ENGAGE_NOW** | 74 | 93 | Work Pendo this week — 11 open GTM roles of 25. Newest posting 5d ago. Lead with revenue-team scaling. |
| 7 | Amplitude | **ENGAGE_NOW** | 72 | 89 | Work Amplitude this week — 10 open GTM roles of 25. Hiring: Senior Sales Engineer - Enterprise (West). Newest posting 4d ago. Lead with revenue-team scaling. |
| 8 | 6sense | **ENGAGE_NOW** | 72 | 73 | Work 6sense this week — 9 open GTM roles of 25. Newest posting 0d ago. Lead with revenue-team scaling. |
| 9 | Drift | **FIX_DATA** | 72 | 50 | Do not route Drift yet — unresolved identity conflict on domain, account_name. Apply the patch, resolve conflicts, re-run. |
| 10 | Mixpanel | **NURTURE** | 68 | 62 | Keep Mixpanel in sequence — real but not urgent (10 GTM roles open). Revisit if hiring accelerates. |
| 11 | ZoomInfo | **NURTURE** | 66 | 100 | Keep ZoomInfo in sequence — real but not urgent (8 GTM roles open). Revisit if hiring accelerates. |
| 12 | Datadog | **NURTURE** | 50 | 63 | Keep Datadog in sequence — real but not urgent (8 GTM roles open). Revisit if hiring accelerates. |
| 13 | Snowflake | **NURTURE** | 40 | 53 | Keep Snowflake in sequence — real but not urgent (5 GTM roles open). Revisit if hiring accelerates. |
| 14 | Clari | **NURTURE** | 40 | 100 | Keep Clari in sequence — real but not urgent (0 GTM roles open). Revisit if hiring accelerates. |
| 15 | Segment | **DEPRIORITIZE** | 32 | 62 | Deprioritize Segment — no live hiring or news signal this quarter. Recheck next quarter rather than spending rep time now. |

## Conflicts requiring human review

### HubSpot (hubspot.com)
- **employee_count** — CRM `3000` vs Apollo `9100` · _headcount differs by 67% (threshold 25%)_

### Outreach (outreach.io)
- **domain** — CRM `outreach.io` vs Apollo `outreach.ai` · _Same brand on a different TLD. Likely a rebrand or vendor variance rather than a different company. Confirm which domain is canonical before bulk email._

### Gong (gong.io)
- **domain** — CRM `gong.io` vs Apollo `gong-next-sanity-web.vercel.app` · _Apollo returned a deployment/CDN host, not a corporate domain. The vendor record is suspect here, not the CRM — keep the CRM value and do not patch from this enrichment._

### Drift (drift.com)
- **domain** — CRM `drift.com` vs Apollo `salesloft.com` · _Apollo resolved this domain to a different company. Almost always an acquisition the CRM has not absorbed. Do not contact until resolved — outreach would reach the wrong organisation._
- **account_name** — CRM `Drift` vs Apollo `Salesloft` · _Company name disagrees entirely. Usually an acquisition the CRM has not absorbed._

### Datadog (datadoghq.com)
- **employee_count** — CRM `1200` vs Apollo `8100` · _headcount differs by 85% (threshold 25%)_

