# Apollo API reference — verified entitlements

Everything here was confirmed with live calls on **2026-09-10** against a
**Free-plan** account using a master key. Nothing in this file is inferred from
documentation; where the docs and the API disagreed, the API won.

## The finding that matters most

Apollo's developer docs state that free accounts *"must be registered with a
work email address to use certain people and organization search, enrichment,
and record-retrieval endpoints."*

**That is not the operative constraint.** The account used here is registered to
a personal Gmail address, and `organizations/enrich` returns full firmographics
without complaint. What actually gates access is the **plan tier**, and Apollo
says so in the 403 body:

```
The api/v1/mixed_people/search API is not included in your Free plan and is
not accessible, even with a master key. All paid plans include full API access.
```

Practical consequence: moving a free account to a work-email domain does **not**
unlock people search. Only a paid plan does. (A work email *is* separately
required for Apollo's MCP connector, which refuses personal domains — a
different mechanism with a different remedy.)

## Available on the Free plan

| Endpoint | Method | Credits | Used by this skill |
|---|---|---|---|
| `/api/v1/users/api_profile` | GET | 0 | auth health probe |
| `/api/v1/organizations/enrich` | POST | 1 / org | single enrichment |
| `/api/v1/organizations/bulk_enrich` | POST | 1 / org | primary enrichment path |
| `/api/v1/organizations/search` | POST | 1 / page | not used |
| `/api/v1/organizations/{id}/job_postings` | GET | 0 | **hiring signal** |
| `/api/v1/news_articles/search` | POST | 0 | **news signal** |
| `/api/v1/accounts/search` | POST | 0 | write-back lookup |
| `/api/v1/accounts` | POST | 0 | write-back create |
| `/api/v1/accounts/{id}` | PUT | 0 | write-back update |

Also listed as Free-plan available but unused here: `contacts/*`,
`deals/api/v1/opportunities/*`, `tasks/*`, `labels/*`, `fields/*`,
`usage_stats/*`.

## Blocked on the Free plan (HTTP 403)

- `/api/v1/mixed_people/search` — people search
- `/api/v1/people/match`, `/api/v1/people/bulk_match` — person enrichment
- `/api/v1/people/show`
- `/api/v1/organizations/show`
- `/api/v1/mixed_companies/search`

This is why the skill is company-level. Any feature requiring a named contact,
an email address or a phone number for a person is a paid-plan extension, not an
oversight.

## Limits discovered empirically

- **`bulk_enrich` accepts at most 10 domains per call.** Documented nowhere we
  could find; a 15-domain payload returns
  `{"error_code": "RECORD_LIMIT_EXCEEDED", "error_message": "domain count
  cannot be more than 10"}`. The client chunks accordingly.
- **Free plan credit ceiling: 75 / month.** Enrichment is 1 credit per company,
  so a 200-account book cannot be enriched on a free plan in a single pass.
- **API keys are displayed exactly once**, in the creation modal. The key list
  offers only regenerate and delete — there is no reveal. Capture the value at
  creation or regenerate.
- **Keys belong in the `x-api-key` header.** Apollo's own console warns that
  passing keys as URL parameters is deprecated.

## Data-quality caveats observed in live responses

These are real, from the captured sample, and they are the reason the audit
treats enrichment as evidence rather than truth:

| Requested | Apollo returned | What it means |
|---|---|---|
| `gong.io` | `gong-next-sanity-web.vercel.app` | vendor indexed a preview deployment |
| `outreach.io` | `outreach.ai` | TLD variance / rebrand |
| `drift.com` | **Salesloft** (`salesloft.com`) | acquisition absorbed into acquirer |
| `snowflake.com` | HQ "St. Cloud, Minnesota" | questionable; widely reported as Bozeman MT |

Apollo's news feed also carries a meaningful share of SEO listicles and
comparison-site spam that mention a company without indicating anything about
its buying cycle — which is why `news_recency` carries the lowest weight in the
rubric.

## Environment note

Some sandboxed environments block egress to `api.apollo.io` at the proxy
(`403` on `CONNECT`). If live calls fail with a tunnel error rather than an HTTP
status, the network is the problem, not the key — verify with
`curl -v https://api.apollo.io/api/v1/users/api_profile`.
