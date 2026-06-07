# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "python-dotenv>=1.0",
# ]
# ///
"""List portals and a sample of recent documents for UK + EU.

One-off exploration tool used to decide which Lawstronaut portals to draw
from when expanding the curated_set.yaml UK/EU clusters.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTH_URL = "https://filerskeepersapi.co/auth/login"
API_URL = "https://api.lawstronaut.com/v2"


def login(client: httpx.Client, email: str, password: str) -> str:
    r = client.post(AUTH_URL, json={"email": email, "password": password}, timeout=60)
    r.raise_for_status()
    body = r.json().get("data", r.json())
    tk = body.get("token", body)
    return tk.get("refresh_token") or tk.get("access_token") or tk.get("token")


def get(client: httpx.Client, bearer: str, path: str, **params) -> dict:
    r = client.get(
        f"{API_URL}{path}",
        headers={"Authorization": f"Bearer {bearer}"},
        params=params,
        timeout=60,
    )
    if r.status_code != 200:
        return {"_status": r.status_code, "_body": r.text[:200]}
    return r.json()


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    email = os.environ["LAWSTRONAUT_EMAIL"]
    password = os.environ["LAWSTRONAUT_PASSWORD"]

    with httpx.Client() as client:
        bearer = login(client, email, password)
        print(f"# logged in, bearer {bearer[:8]}…\n")

        jx = get(client, bearer, "/jurisdictions").get("data", [])
        print(f"# {len(jx)} jurisdictions total")
        # Look for UK / GB / EU isos and any aliases
        for j in jx:
            iso = (j.get("iso") or "").upper()
            name = j.get("name") or ""
            typ = j.get("type") or ""
            if iso in {"GB", "UK", "EU"} or "united kingdom" in name.lower() or "european" in name.lower():
                print(f"  iso={iso!r:8} type={typ!r:14} name={name!r}")
        print()

        for iso in ("GB", "UK", "EU"):
            print(f"# --- portals for iso={iso} ---")
            portals = get(client, bearer, "/portals", iso=iso).get("data", [])
            if not portals or (isinstance(portals, dict) and portals.get("_status")):
                print(f"  (no portals or error: {portals})")
                continue
            for p in portals:
                print(f"  url={p.get('url'):50}  name={p.get('name')!r:50}  lang={p.get('language')!r}")
            print()

        # Sample a few documents per UK portal to see what content looks like.
        for iso in ("GB", "UK", "EU"):
            portals = get(client, bearer, "/portals", iso=iso).get("data", [])
            if not isinstance(portals, list):
                continue
            print(f"# --- sample documents per portal for iso={iso} ---")
            for p in portals:
                url = p.get("url")
                if not url:
                    continue
                listing = get(client, bearer, "/contents", iso=iso, portal=url, limit=3).get("data", [])
                if isinstance(listing, list):
                    print(f"  portal={url}  ({len(listing)} sampled)")
                    for d in listing:
                        title = (d.get("title") or "")[:100]
                        lang = d.get("language") or ""
                        did = d.get("document_id")
                        ver = d.get("version")
                        print(f"    id={did} v{ver} lang={lang!r:12} title={title!r}")
                else:
                    print(f"  portal={url}  (listing error: {listing})")
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
