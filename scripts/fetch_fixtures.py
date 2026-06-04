# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "python-dotenv>=1.0",
# ]
# ///
"""Fetch a diverse set of legal documents from Lawstronaut for use as fixtures.

Reads credentials from .env, logs in to filerskeepersapi.co, discovers
jurisdictions and portals, then samples one document from as many distinct
(jurisdiction, portal) pairs as possible until TARGET_COUNT documents have
been saved. Files land in data/samples/ following the existing
<iso>-<document_id>-v<version>.{md,meta.json} convention. An index of
captured fixtures is written to data/samples/fixtures.json.

Run with:  uv run scripts/fetch_fixtures.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
FIXTURES_INDEX = SAMPLES_DIR / "fixtures.json"

DEFAULT_API_URL = "https://api.lawstronaut.com/v2"
DEFAULT_AUTH_URL = "https://filerskeepersapi.co/auth/login"

TARGET_COUNT = 30
HTTP_TIMEOUT = 60.0


@dataclass
class Token:
    bearer: str
    expires_at: float
    email: str = field(repr=False)
    password: str = field(repr=False)
    auth_url: str = DEFAULT_AUTH_URL

    @property
    def near_expiry(self) -> bool:
        return time.time() >= self.expires_at - 60


def login(client: httpx.Client, email: str, password: str, auth_url: str) -> Token:
    resp = client.post(auth_url, json={"email": email, "password": password}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    # Response shape: {status, message, data: {..., token: {token_type, refresh_token, expires_in}}}
    token_blob = body.get("token") if isinstance(body.get("token"), dict) else body
    bearer = token_blob.get("refresh_token") or token_blob.get("access_token") or token_blob.get("token")
    if not isinstance(bearer, str):
        raise RuntimeError(f"login response missing bearer string; outer={list(payload)}, body={list(body)}, token={list(token_blob) if isinstance(token_blob, dict) else type(token_blob).__name__}")
    expires_in = int(token_blob.get("expires_in") or body.get("expires_in") or payload.get("expires_in") or 1800)
    return Token(bearer=bearer, expires_at=time.time() + expires_in,
                 email=email, password=password, auth_url=auth_url)


def maybe_refresh(client: httpx.Client, token: Token) -> Token:
    if token.near_expiry:
        return login(client, token.email, token.password, token.auth_url)
    return token


def authed_get(client: httpx.Client, token: Token, base_url: str, path: str, **params: Any) -> tuple[Token, httpx.Response]:
    token = maybe_refresh(client, token)
    headers = {"Authorization": f"Bearer {token.bearer}"}
    resp = client.get(f"{base_url}{path}", headers=headers, params=params, timeout=HTTP_TIMEOUT)
    return token, resp


def fetch_jurisdictions(client: httpx.Client, token: Token, base_url: str) -> tuple[Token, list[dict[str, Any]]]:
    token, resp = authed_get(client, token, base_url, "/jurisdictions")
    resp.raise_for_status()
    return token, resp.json().get("data", [])


def fetch_portals_for_iso(client: httpx.Client, token: Token, base_url: str, iso: str) -> tuple[Token, list[dict[str, Any]]]:
    """Fetch portals for one jurisdiction. /v2/portals appears to require iso."""
    token, resp = authed_get(client, token, base_url, "/portals", iso=iso)
    if resp.status_code != 200:
        return token, []
    return token, resp.json().get("data", [])


def fetch_all_portals(client: httpx.Client, token: Token, base_url: str, isos: list[str]) -> tuple[Token, list[dict[str, Any]]]:
    """Walk every known iso and collect portals; tag each portal with its iso."""
    all_portals: list[dict[str, Any]] = []
    for iso in isos:
        token, page = fetch_portals_for_iso(client, token, base_url, iso)
        for p in page:
            p["_iso"] = iso
        all_portals.extend(page)
    return token, all_portals


def country_to_iso(jurisdictions: list[dict[str, Any]]) -> dict[str, str]:
    """Build country-name → ISO map. Prefer 'country' type rows."""
    mapping: dict[str, str] = {}
    for j in jurisdictions:
        name = (j.get("name") or "").strip()
        iso = (j.get("iso") or "").strip()
        if not name or not iso:
            continue
        if j.get("type") == "country":
            mapping[name] = iso
    # second pass — fill in anything missed
    for j in jurisdictions:
        name = (j.get("name") or "").strip()
        iso = (j.get("iso") or "").strip()
        if name and iso and name not in mapping:
            mapping[name] = iso
    return mapping


def pick_pairs(portals: list[dict[str, Any]], iso_map: dict[str, str], target: int) -> list[tuple[str, str, str]]:
    """Round-robin across jurisdictions; return (iso, portal_url, portal_name) tuples."""
    by_iso: dict[str, list[tuple[int, str, str]]] = {}
    for p in portals:
        iso = p.get("_iso")
        if not iso:
            country = (p.get("jurisdiction") or {}).get("country") or ""
            iso = iso_map.get(country.strip())
        portal_url = p.get("url") or ""
        portal_name = p.get("name") or portal_url
        language = p.get("language") or ""
        if not iso or not portal_url:
            continue
        # Prefer English to keep fixtures readable; non-English still allowed if we run short
        priority = 0 if language.lower() == "english" else 1
        by_iso.setdefault(iso, []).append((priority, portal_url, portal_name))
    # sort each jurisdiction's portals so English comes first
    sorted_by_iso = {iso: [(u, n) for _p, u, n in sorted(plist)] for iso, plist in by_iso.items()}
    isos = sorted(sorted_by_iso.keys())
    picks: list[tuple[str, str, str]] = []
    while len(picks) < target and any(sorted_by_iso[iso] for iso in isos):
        for iso in isos:
            if not sorted_by_iso[iso]:
                continue
            url, name = sorted_by_iso[iso].pop(0)
            picks.append((iso, url, name))
            if len(picks) >= target:
                break
    return picks


def _get_with_retry(client: httpx.Client, token: Token, base_url: str, path: str, retries: int = 2, **params: Any) -> tuple[Token, httpx.Response | None]:
    """GET with one retry on timeout. Returns None response on persistent failure."""
    for attempt in range(retries + 1):
        try:
            return authed_get(client, token, base_url, path, **params)
        except httpx.TimeoutException:
            if attempt == retries:
                return token, None
    return token, None


def fetch_one(client: httpx.Client, token: Token, base_url: str, iso: str, portal_url: str) -> tuple[Token, dict[str, Any] | None]:
    # Metadata. Note: language="English" causes HTTP 400 in practice, so we omit it
    # and rely on the per-portal language field if we want to skip non-English.
    token, meta_resp = _get_with_retry(client, token, base_url, "/contents",
                                       iso=iso, portal=portal_url, limit=1)
    if meta_resp is None or meta_resp.status_code != 200:
        return token, None
    meta_records = meta_resp.json().get("data", [])
    if not meta_records:
        return token, None
    meta = meta_records[0]

    # Markdown content for same record. Filter by document_id to ensure alignment with the meta record.
    document_id = meta.get("document_id")
    token, md_resp = _get_with_retry(client, token, base_url, "/contents/markdown",
                                     iso=iso, portal=portal_url, limit=1)
    if md_resp is None or md_resp.status_code != 200:
        return token, None
    md_records = md_resp.json().get("data", [])
    if not md_records:
        return token, None
    md_record = md_records[0]
    content_markdown = md_record.get("content_markdown") or md_record.get("markdown") or ""
    if not content_markdown.strip():
        return token, None

    return token, {
        "iso": iso,
        "portal_url": portal_url,
        "meta": meta,
        "content_markdown": content_markdown,
        "md_document_id": str(md_record.get("document_id") or document_id or ""),
    }


def existing_fixture_slugs() -> set[str]:
    if not SAMPLES_DIR.exists():
        return set()
    return {p.stem for p in SAMPLES_DIR.glob("*.md")}


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    email = os.environ.get("LAWSTRONAUT_EMAIL")
    password = os.environ.get("LAWSTRONAUT_PASSWORD")
    base_url = (os.environ.get("LAWSTRONAUT_API_URL") or DEFAULT_API_URL).rstrip("/")
    auth_url = os.environ.get("LAWSTRONAUT_AUTH_URL") or DEFAULT_AUTH_URL
    for label, url in (("LAWSTRONAUT_API_URL", base_url), ("LAWSTRONAUT_AUTH_URL", auth_url)):
        if not url.startswith(("http://", "https://")):
            print(f"{label} is missing the scheme; assuming https://", file=sys.stderr)
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    if not auth_url.startswith(("http://", "https://")):
        auth_url = "https://" + auth_url
    if not email or not password:
        print("Missing LAWSTRONAUT_EMAIL / LAWSTRONAUT_PASSWORD in .env", file=sys.stderr)
        return 1

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    already_have = existing_fixture_slugs()

    with httpx.Client() as client:
        print(f"Logging in to {auth_url} …")
        token = login(client, email, password, auth_url)
        print(f"  ✓ bearer obtained (TTL {int(token.expires_at - time.time())}s)")

        print("Fetching jurisdictions …")
        token, jurisdictions = fetch_jurisdictions(client, token, base_url)
        iso_map = country_to_iso(jurisdictions)
        print(f"  ✓ {len(jurisdictions)} jurisdictions, {len(iso_map)} country→iso mappings")

        isos = sorted({j["iso"] for j in jurisdictions if j.get("iso") and j.get("type") == "country"})
        print(f"Fetching portals for {len(isos)} country jurisdictions …")
        token, portals = fetch_all_portals(client, token, base_url, isos)
        print(f"  ✓ {len(portals)} portals total")

        picks = pick_pairs(portals, iso_map, TARGET_COUNT * 3)  # over-pick: many portals will be empty
        print(f"Targeting up to {TARGET_COUNT} fixtures across {len(picks)} (jurisdiction, portal) candidates")

        captured: list[dict[str, Any]] = []
        for iso, portal_url, portal_name in picks:
            if len(captured) >= TARGET_COUNT:
                break
            try:
                token, doc = fetch_one(client, token, base_url, iso, portal_url)
            except Exception as exc:
                print(f"  ✗ {iso} / {portal_url}: {exc}")
                continue
            if not doc:
                print(f"  · {iso} / {portal_url}: no data")
                continue

            meta = doc["meta"]
            document_id = str(meta.get("document_id") or doc["md_document_id"] or "")
            version = meta.get("version") or 1
            if not document_id:
                continue
            slug = f"{iso.lower()}-{document_id}-v{version}"
            if slug in already_have:
                print(f"  = {slug} already present, skipping")
                continue

            md_path = SAMPLES_DIR / f"{slug}.md"
            meta_path = SAMPLES_DIR / f"{slug}.meta.json"
            md_path.write_text(doc["content_markdown"])
            meta_with_provenance: dict[str, Any] = {
                "_provenance": {
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "endpoint_metadata": f"GET {base_url}/contents?iso={iso}&portal={portal_url}&limit=1",
                    "endpoint_markdown": f"GET {base_url}/contents/markdown?iso={iso}&portal={portal_url}&limit=1",
                    "portal_name": portal_name,
                },
                **meta,
            }
            meta_path.write_text(json.dumps(meta_with_provenance, indent=2, default=str))
            size_kb = len(doc["content_markdown"].encode("utf-8")) / 1024
            captured.append({
                "iso": iso,
                "portal_url": portal_url,
                "portal_name": portal_name,
                "document_id": document_id,
                "version": version,
                "title": meta.get("title") or "",
                "type_of_authority": meta.get("type_of_authority") or "",
                "language": meta.get("language") or "",
                "markdown_kb": round(size_kb, 1),
                "files": {"markdown": md_path.name, "meta": meta_path.name},
            })
            already_have.add(slug)
            print(f"  ✓ {iso} / {portal_url} → {slug} ({size_kb:.1f} KB)")

        captured.sort(key=lambda c: (c["iso"], c["portal_url"]))
        FIXTURES_INDEX.write_text(json.dumps({
            "_provenance": {
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "target_count": TARGET_COUNT,
            },
            "fixtures": captured,
        }, indent=2))
        print(f"\nWrote {len(captured)} fixtures and {FIXTURES_INDEX.relative_to(REPO_ROOT)}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
