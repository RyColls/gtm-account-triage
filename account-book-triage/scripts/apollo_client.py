"""
apollo_client.py — the only module that talks to Apollo.

Design notes (these are the interview answers, so they live next to the code):

1. CREDIT GUARD. The Free plan allows 75 credits/month and organization
   enrichment costs 1 credit per company. A careless re-run over a 15-account
   book costs 15 credits; five debugging runs exhaust the month. So the client
   refuses to spend past a configured budget and raises before the call, not
   after. This is a functional requirement, not politeness.

2. ON-DISK CACHE. Enrichment answers change on the order of weeks, so caching
   them for a TTL is both correct and the difference between a skill you can
   iterate on and one you can run twice.

3. FIXTURE MODE. Tests must be reproducible and free. `--fixtures` swaps live
   HTTP for a captured JSON payload with identical shape, so the whole pipeline
   is testable with no key and no spend.

4. NO KEY IN URLS. Apollo's own console warns that API keys in query params are
   deprecated. The key travels in the x-api-key header and is read from the
   APOLLO_API_KEY environment variable — never a literal, never committed.
"""

from __future__ import annotations

import json
import os
import time
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

BASE_URL = "https://api.apollo.io"

# Endpoints verified as available on the Free plan on 2026-09-10.
# See references/apollo-endpoints.md for the full entitlement matrix.
EP_API_PROFILE = "/api/v1/users/api_profile"
EP_BULK_ENRICH = "/api/v1/organizations/bulk_enrich"
EP_ENRICH = "/api/v1/organizations/enrich"
EP_ORG_SEARCH = "/api/v1/organizations/search"
EP_JOB_POSTINGS = "/api/v1/organizations/{org_id}/job_postings"
EP_NEWS = "/api/v1/news_articles/search"
EP_ACCOUNTS_SEARCH = "/api/v1/accounts/search"
EP_ACCOUNTS_CREATE = "/api/v1/accounts"
EP_ACCOUNTS_UPDATE = "/api/v1/accounts/{account_id}"

# Apollo rejects bulk_enrich payloads above this size. Discovered empirically:
# 15 domains returned RECORD_LIMIT_EXCEEDED / "domain count cannot be more
# than 10". The docs did not state it; the API did.
BULK_ENRICH_MAX_DOMAINS = 10

CREDIT_COST = {
    "bulk_enrich": 1,   # per domain
    "enrich": 1,        # per domain
    "org_search": 1,    # per page
    "job_postings": 0,
    "news": 0,
}


class ApolloError(RuntimeError):
    pass


class PlanRestrictionError(ApolloError):
    """Raised on 403 responses that indicate a plan-tier entitlement problem.

    Kept distinct from generic failures because the remedy is completely
    different: no amount of retrying fixes a Free-plan restriction, and the
    skill should say so plainly rather than looking broken.
    """


class CreditBudgetExceeded(ApolloError):
    pass


class ApolloClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_dir: str | Path = ".apollo_cache",
        cache_ttl_days: int = 14,
        credit_budget: int = 40,
        fixtures_path: Optional[str | Path] = None,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self.fixtures = None
        if fixtures_path:
            with open(fixtures_path, "r", encoding="utf-8") as fh:
                self.fixtures = json.load(fh)

        self.api_key = api_key or os.environ.get("APOLLO_API_KEY")
        if not self.api_key and self.fixtures is None:
            raise ApolloError(
                "APOLLO_API_KEY is not set. Export it, or run with --fixtures "
                "to use the captured test payload."
            )

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_seconds = cache_ttl_days * 86400
        self.credit_budget = credit_budget
        self.credits_spent = 0
        self.timeout = timeout
        self.max_retries = max_retries
        self.call_log: List[Dict[str, Any]] = []

    # ---------------------------------------------------------------- caching
    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:20]
        return self.cache_dir / f"{digest}.json"

    def _cache_get(self, key: str) -> Optional[Any]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl_seconds:
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    def _cache_put(self, key: str, value: Any) -> None:
        try:
            with open(self._cache_path(key), "w", encoding="utf-8") as fh:
                json.dump(value, fh)
        except OSError:
            pass  # a failed cache write must never fail the run

    # ---------------------------------------------------------------- credits
    def _spend(self, units: int, what: str) -> None:
        if units <= 0:
            return
        if self.credits_spent + units > self.credit_budget:
            raise CreditBudgetExceeded(
                f"Refusing to spend {units} credit(s) on {what}: would exceed "
                f"the budget of {self.credit_budget} (already spent "
                f"{self.credits_spent}). Raise --credit-budget deliberately, "
                f"or narrow the account book."
            )
        self.credits_spent += units

    # ------------------------------------------------------------------- HTTP
    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        if requests is None:
            raise ApolloError("The `requests` package is required for live calls.")

        url = BASE_URL + path
        headers = {
            "Content-Type": "application/json",
            "accept": "application/json",
            "Cache-Control": "no-cache",
            "x-api-key": self.api_key,
        }

        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = requests.request(
                    method, url, headers=headers, json=payload, timeout=self.timeout
                )
            except Exception as exc:  # network-level
                last_err = exc
                time.sleep(2 ** attempt)
                continue

            self.call_log.append({"method": method, "path": path, "status": resp.status_code})

            if resp.status_code == 403:
                body = resp.text[:400]
                raise PlanRestrictionError(
                    f"Apollo refused {path} with 403. This is almost certainly a "
                    f"plan-tier entitlement, not a bad key. Response: {body}"
                )
            if resp.status_code == 429:
                time.sleep(2 ** attempt * 2)
                last_err = ApolloError("rate limited")
                continue
            if resp.status_code >= 500:
                time.sleep(2 ** attempt)
                last_err = ApolloError(f"server error {resp.status_code}")
                continue
            if resp.status_code >= 400:
                raise ApolloError(f"{path} returned {resp.status_code}: {resp.text[:400]}")

            try:
                return resp.json()
            except json.JSONDecodeError as exc:
                raise ApolloError(f"{path} returned non-JSON: {resp.text[:200]}") from exc

        raise ApolloError(f"{path} failed after {self.max_retries} attempts: {last_err}")

    # -------------------------------------------------------------- endpoints
    def api_profile(self) -> dict:
        """Cheap authentication probe. Costs nothing; call it before a batch."""
        if self.fixtures is not None:
            return {"email": "fixture-mode@example.com", "fixture": True}
        return self._request("GET", EP_API_PROFILE)

    def bulk_enrich(self, domains: List[str]) -> Dict[str, dict]:
        """Enrich domains, returning {requested_domain: organization}.

        Keyed by the domain we ASKED for, not the one Apollo returns. Apollo
        frequently answers with a different primary_domain — a marketing
        redirect, a stale CDN host, or the acquirer after a merger — and the
        audit needs to see that disagreement rather than have it silently
        normalised away.
        """
        out: Dict[str, dict] = {}
        to_fetch: List[str] = []

        for domain in domains:
            if self.fixtures is not None:
                org = self.fixtures.get("enrich", {}).get(domain)
                if org:
                    out[domain] = org
                continue
            cached = self._cache_get(f"enrich::{domain}")
            if cached is not None:
                out[domain] = cached
            else:
                to_fetch.append(domain)

        if self.fixtures is not None:
            return out

        for i in range(0, len(to_fetch), BULK_ENRICH_MAX_DOMAINS):
            chunk = to_fetch[i : i + BULK_ENRICH_MAX_DOMAINS]
            self._spend(len(chunk) * CREDIT_COST["bulk_enrich"], f"bulk_enrich({len(chunk)})")
            data = self._request("POST", EP_BULK_ENRICH, {"domains": chunk})
            orgs = data.get("organizations") or []

            # Apollo returns results positionally for the chunk it was given.
            for requested, org in zip(chunk, orgs):
                if not org:
                    continue
                out[requested] = org
                self._cache_put(f"enrich::{requested}", org)

        return out

    def job_postings(self, org_id: str) -> List[dict]:
        if self.fixtures is not None:
            return []  # signals are pre-aggregated in fixture mode
        cached = self._cache_get(f"jobs::{org_id}")
        if cached is not None:
            return cached
        data = self._request("GET", EP_JOB_POSTINGS.format(org_id=org_id))
        postings = data.get("organization_job_postings") or []
        self._cache_put(f"jobs::{org_id}", postings)
        return postings

    def news_articles(self, org_id: str, per_page: int = 10) -> List[dict]:
        if self.fixtures is not None:
            return []
        cached = self._cache_get(f"news::{org_id}")
        if cached is not None:
            return cached
        data = self._request(
            "POST", EP_NEWS, {"organization_ids": [org_id], "per_page": per_page}
        )
        articles = data.get("news_articles") or []
        self._cache_put(f"news::{org_id}", articles)
        return articles

    # ----------------------------------------------------------- write-back
    def find_account(self, domain: str) -> Optional[dict]:
        if self.fixtures is not None:
            return None
        data = self._request(
            "POST", EP_ACCOUNTS_SEARCH, {"q_organization_name": domain, "per_page": 10}
        )
        for acct in data.get("accounts") or []:
            if (acct.get("domain") or "").lower() == domain.lower():
                return acct
        return None

    def upsert_account(self, domain: str, fields: dict, dry_run: bool = True) -> dict:
        """Create or update an Apollo account.

        dry_run defaults to True on purpose. A hygiene tool that writes to a CRM
        by accident is worse than no hygiene tool, so the caller must opt in to
        mutation explicitly.
        """
        if dry_run or self.fixtures is not None:
            return {"dry_run": True, "domain": domain, "would_write": fields}

        existing = self.find_account(domain)
        if existing:
            return self._request(
                "PUT", EP_ACCOUNTS_UPDATE.format(account_id=existing["id"]), fields
            )
        payload = dict(fields)
        payload.setdefault("domain", domain)
        return self._request("POST", EP_ACCOUNTS_CREATE, payload)
