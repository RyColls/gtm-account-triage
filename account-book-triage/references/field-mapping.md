# Field mapping and normalization

## CSV ↔ Apollo

| CSV column | Apollo field (`organizations/enrich`) | Gap-fillable | Conflict-checked |
|---|---|---|---|
| `account_id` | — | no | no |
| `account_name` | `name` | no | **yes** (high severity) |
| `domain` | `primary_domain` | no | **yes** (classified, see below) |
| `industry` | `industry` | yes | yes (low) |
| `employee_count` | `estimated_num_employees` | yes | yes (medium, >25% drift) |
| `hq_city` | `city` | yes | yes (low) |
| `hq_state` | `state` | yes | yes (low) |
| `phone` | `phone` | yes | no |
| `linkedin_url` | `linkedin_url` | yes | no |
| `owner` | — | no | no |
| `lifecycle_stage` | — | no | no |
| `arr_usd` | — | no | no |
| `last_enriched` | — | no | drives freshness |

`account_name` and `domain` are deliberately **not** gap-fillable. They are
identity fields: if they are empty or wrong, the right response is a human
decision, not a silent vendor overwrite.

## Write-back mapping (`accounts/create` / `accounts/update`)

Only these are ever written, and only when the CSV field was empty:

| Patch field | Apollo account field |
|---|---|
| `phone` | `phone` |
| `hq_city` | `city` |
| `hq_state` | `state` |

`industry` and `employee_count` are audited and reported but not written back,
because Apollo derives its own values for those on the account object and a
write would simply fight the vendor's own enrichment.

## Normalization rules

**Domains.** Strip scheme, `www.`, path and trailing dot; lowercase. So
`https://www.Gong.io/`, `GONG.IO` and `gong.io` all reduce to `gong.io`. Real
CRM exports contain all three as separate accounts.

**Company names.** Lowercase, strip punctuation, then strip legal and generic
suffixes (`inc`, `llc`, `ltd`, `corp`, `co`, `holdings`, `group`,
`technologies`, `software`, `labs`, `io`). `Pendo.io, Inc.` → `pendo`.

A name conflict is raised only when neither normalized name contains the other.
That containment rule is what stops `Segment` vs `Twilio Segment` from being
flagged as an acquisition — it is the same company under its parent's branding,
and blocking a rep over it would be a false positive.

**Dates.** `%Y-%m-%d`, `%m/%d/%Y`, `%d/%m/%Y` accepted. Unparseable dates score
zero freshness rather than raising, because a malformed date and a missing one
mean the same thing operationally: the record's age is unknown.

## Domain conflict classification

| Kind | Trigger | Severity | Gates outreach |
|---|---|---|---|
| `vendor_suspect` | Apollo host matches a deployment/CDN pattern (`*.vercel.app`, `*.netlify.app`, `*.herokuapp.com`, `*.pages.dev`, `*.github.io`, `*.azurewebsites.net`, `*.cloudfront.net`, `*.web.app`) | medium | no |
| `tld_variance` | Second-level labels match, TLDs differ | medium | no |
| `identity` | Second-level labels differ | **high** | **yes** |

## Adapting to another CRM export

Change `APOLLO_FIELD_MAP` in `scripts/audit.py` and `required_fields` in
`assets/scoring.yaml`. For a Salesforce Account export the usual mapping is
`Name` → `account_name`, `Website` → `domain`, `Industry` → `industry`,
`NumberOfEmployees` → `employee_count`, `BillingCity` → `hq_city`,
`BillingState` → `hq_state`, `LastModifiedDate` → `last_enriched`.

Note that `LastModifiedDate` is a weaker freshness proxy than a dedicated
enrichment timestamp — any field edit refreshes it, including ones that have
nothing to do with firmographic accuracy. A dedicated `Last_Enriched__c` field
is worth the five minutes it takes to add.
