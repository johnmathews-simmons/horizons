# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "python-dotenv>=1.0",
# ]
# ///
"""Fetch the Dutch GDPR implementing law (UAVG / AVG) from Lawstronaut.

Logs in against filerskeepersapi.co, searches NL metadata for GDPR-related
titles, prints the candidate matches, then pulls the markdown for the best one.

Run with creds in .env or env vars:
    LAWSTRONAUT_EMAIL=... LAWSTRONAUT_PASSWORD=... uv run scripts/fetch_nl_gdpr.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

AUTH_URL = os.environ.get("LAWSTRONAUT_AUTH_URL", "https://filerskeepersapi.co/auth/login")
API_URL = os.environ.get("LAWSTRONAUT_API_URL", "https://api.lawstronaut.com/v2")
TIMEOUT = 60.0

# Dutch GDPR == "AVG" (Algemene verordening gegevensbescherming); the national
# implementing act is the "Uitvoeringswet Algemene verordening gegevensbescherming".
TITLE_QUERIES = [
    "Uitvoeringswet Algemene verordening gegevensbescherming",
    "Algemene verordening gegevensbescherming",
    "gegevensbescherming",
    "GDPR",
]


def login(client: httpx.Client, email: str, password: str) -> str:
    resp = client.post(AUTH_URL, json={"email": email, "password": password}, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    blob = body.get("token") if isinstance(body.get("token"), dict) else body
    bearer = blob.get("refresh_token") or blob.get("access_token") or blob.get("token")
    if not isinstance(bearer, str):
        raise RuntimeError(f"no bearer in login response: {list(payload)}")
    return bearer


def get(client: httpx.Client, bearer: str, path: str, **params) -> httpx.Response:
    headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}
    return client.get(f"{API_URL}{path}", headers=headers, params=params, timeout=TIMEOUT)


def main() -> int:
    load_dotenv()
    email = os.environ.get("LAWSTRONAUT_EMAIL")
    password = os.environ.get("LAWSTRONAUT_PASSWORD")
    if not email or not password:
        print("ERROR: set LAWSTRONAUT_EMAIL and LAWSTRONAUT_PASSWORD (in .env or env).", file=sys.stderr)
        return 2

    with httpx.Client() as client:
        bearer = login(client, email, password)
        print(f"logged in; token acquired ({len(bearer)} chars)\n")

        candidates: list[dict] = []
        seen: set = set()
        for q in TITLE_QUERIES:
            resp = get(client, bearer, "/contents", iso="NL", title=q, limit=20)
            if resp.status_code != 200:
                print(f"  [{q!r}] -> HTTP {resp.status_code}")
                continue
            rows = resp.json().get("data", [])
            print(f"  [{q!r}] -> {len(rows)} rows")
            for r in rows:
                key = (str(r.get("document_id")), r.get("version"))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(r)

        if not candidates:
            print("\nNo NL GDPR candidates found. Try inspecting /contents?iso=NL manually.")
            return 1

        print(f"\n{len(candidates)} candidate(s):\n")
        for r in candidates:
            print(f"  doc {r.get('document_id')} v{r.get('version')}  "
                  f"[{r.get('type_of_authority')}]  {r.get('title')!r}")
            print(f"      pub={r.get('publication_date')} eff={r.get('effective_date')} "
                  f"portal={r.get('portal')} link={r.get('legal_link')}")

        # Pull markdown for the top candidate.
        top = candidates[0]
        doc_id = top.get("document_id")
        print(f"\nFetching markdown for doc {doc_id} v{top.get('version')} ...")
        md_resp = get(client, bearer, "/contents/markdown", iso="NL",
                      title=top.get("title"), limit=1)
        if md_resp.status_code != 200:
            print(f"markdown fetch -> HTTP {md_resp.status_code}: {md_resp.text[:300]}")
            return 1
        data = md_resp.json().get("data", [])
        if not data:
            print("markdown response had empty data.")
            return 1
        md = data[0].get("markdown") or data[0].get("content_markdown") or ""
        out = Path(__file__).resolve().parent.parent / "data" / "samples" / f"nl-{doc_id}-gdpr.md"
        out.write_text(md, encoding="utf-8")
        print(f"wrote {len(md):,} chars -> {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
