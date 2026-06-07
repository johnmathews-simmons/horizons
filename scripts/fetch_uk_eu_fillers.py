# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "httpx>=0.27",
#     "python-dotenv>=1.0",
# ]
# ///
"""Fetch genuine UK / EU fixtures from specific banking-relevant portals.

One-off helper to replace the foreign-origin "filler" docs currently
relabelled as UK / EU in data/curated_set.yaml. Writes new fixture files
under data/samples/<iso>-<id>-v<v>.{md,meta.json} and updates
data/samples/fixtures.json with a fresh row per saved doc.

Per portal we sample up to 5 candidates and pick the largest markdown
(better demo signal than a one-paragraph press item).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
FIXTURES_INDEX = SAMPLES_DIR / "fixtures.json"

AUTH_URL = "https://filerskeepersapi.co/auth/login"
API_URL = "https://api.lawstronaut.com/v2"
HTTP_TIMEOUT = 60.0

# (iso, portal_url, portal_name, candidate_limit)
UK_TARGETS = [
    ("GB", "www.fca.org.uk", "Financial Conduct Authority", 10),
    ("GB", "www.bankofengland.co.uk", "The Bank of England", 10),
    # The PRA Rulebook + Supreme Court portals returned no usable docs in the
    # WU8.7 run (markdown bodies absent / too short); the next two targets
    # were added as substitutes.
    ("GB", "www.catribunal.org.uk", "Competition Appeals Tribunal", 10),
    ("GB", "www.bailii.org", "UK Case Precedents", 10),
    ("GB", "www.psr.org.uk", "UK Payment Systems Regulator", 10),
    ("GB", "www.legislation.gov.uk", "Legislation UK", 10),
    ("GB", "www.parliament.uk", "UK Parliament", 10),
]

EU_TARGETS = [
    ("EU", "eba.europa.eu", "European Banking Authority", 10),
    ("EU", "www.ecb.europa.eu", "European Central Bank", 10),
    ("EU", "www.bankingsupervision.europa.eu", "European Central Bank | Banking Supervision", 10),
    ("EU", "www.srb.europa.eu", "Single Resolution Board", 10),
    ("EU", "www.esma.europa.eu", "European Securities and Markets Authority", 10),
    ("EU", "www.eiopa.europa.eu", "European Insurance and Occupational Pensions Authority", 10),
]


def login(client: httpx.Client, email: str, password: str) -> str:
    r = client.post(AUTH_URL, json={"email": email, "password": password}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    body = r.json().get("data", r.json())
    tk = body.get("token", body)
    return tk.get("refresh_token") or tk.get("access_token") or tk.get("token")


def get(client: httpx.Client, bearer: str, path: str, **params) -> tuple[int, dict | list | None]:
    r = client.get(
        f"{API_URL}{path}",
        headers={"Authorization": f"Bearer {bearer}"},
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, None


def fetch_one_from_portal(
    client: httpx.Client,
    bearer: str,
    iso: str,
    portal_url: str,
    portal_name: str,
    limit: int,
    used_ids: set[str],
) -> dict | None:
    """Pull up to `limit` recent docs from the portal, return the largest unused markdown."""
    status, meta_listing = get(client, bearer, "/contents", iso=iso, portal=portal_url, limit=limit)
    if status != 200 or not isinstance(meta_listing, dict):
        print(f"    ! /contents failed status={status}")
        return None
    meta_records = meta_listing.get("data", [])
    if not meta_records:
        print("    ! /contents returned empty")
        return None

    status, md_listing = get(client, bearer, "/contents/markdown", iso=iso, portal=portal_url, limit=limit)
    if status != 200 or not isinstance(md_listing, dict):
        print(f"    ! /contents/markdown failed status={status}")
        return None
    md_records = md_listing.get("data", [])
    md_by_id = {str(r.get("document_id")): r for r in md_records}

    best: dict | None = None
    best_size = 0
    for meta in meta_records:
        did = str(meta.get("document_id") or "")
        if not did or did in used_ids:
            continue
        md = md_by_id.get(did)
        if not md:
            continue
        content = md.get("content_markdown") or md.get("markdown") or ""
        if not content.strip():
            continue
        size = len(content.encode("utf-8"))
        # Skip trivially short docs (press blurbs)
        if size < 2000:
            continue
        if size > best_size:
            best_size = size
            best = {
                "iso": iso,
                "portal_url": portal_url,
                "portal_name": portal_name,
                "meta": meta,
                "content_markdown": content,
                "size_bytes": size,
            }
    return best


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    email = os.environ["LAWSTRONAUT_EMAIL"]
    password = os.environ["LAWSTRONAUT_PASSWORD"]

    existing_slugs = {p.stem for p in SAMPLES_DIR.glob("*.md")}
    used_ids = {s.split("-")[1] for s in existing_slugs if "-" in s}
    captured: list[dict] = []

    with httpx.Client() as client:
        bearer = login(client, email, password)
        print(f"# logged in\n")

        for cluster_name, targets in (("UK", UK_TARGETS), ("EU", EU_TARGETS)):
            print(f"# === {cluster_name} cluster ===")
            for iso, portal_url, portal_name, limit in targets:
                print(f"  · {iso} / {portal_url} ({portal_name})")
                doc = fetch_one_from_portal(client, bearer, iso, portal_url, portal_name, limit, used_ids)
                if not doc:
                    print(f"    -> no usable doc")
                    continue
                meta = doc["meta"]
                did = str(meta.get("document_id") or "")
                ver = meta.get("version") or 1
                slug = f"{iso.lower()}-{did}-v{ver}"
                md_path = SAMPLES_DIR / f"{slug}.md"
                meta_path = SAMPLES_DIR / f"{slug}.meta.json"

                md_path.write_text(doc["content_markdown"])
                provenance = {
                    "_provenance": {
                        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "endpoint_metadata": f"GET {API_URL}/contents?iso={iso}&portal={portal_url}&limit={limit}",
                        "endpoint_markdown": f"GET {API_URL}/contents/markdown?iso={iso}&portal={portal_url}&limit={limit}",
                        "portal_name": portal_name,
                    },
                    **meta,
                }
                meta_path.write_text(json.dumps(provenance, indent=2, default=str))
                size_kb = doc["size_bytes"] / 1024
                title = (meta.get("title") or "")[:80]
                print(f"    -> {slug}  {size_kb:.1f} KB  {title!r}")

                captured.append({
                    "iso": iso,
                    "portal_url": portal_url,
                    "portal_name": portal_name,
                    "document_id": did,
                    "version": ver,
                    "title": meta.get("title") or "",
                    "type_of_authority": meta.get("type_of_authority") or "",
                    "language": meta.get("language") or "",
                    "markdown_kb": round(size_kb, 1),
                    "files": {"markdown": md_path.name, "meta": meta_path.name},
                })
                used_ids.add(did)

    # Merge into fixtures.json
    if FIXTURES_INDEX.exists():
        existing_index = json.loads(FIXTURES_INDEX.read_text())
    else:
        existing_index = {"_provenance": {}, "fixtures": []}
    existing_index.setdefault("fixtures", []).extend(captured)
    existing_index["fixtures"].sort(key=lambda c: (c["iso"], c["portal_url"], c["document_id"]))
    existing_index["_provenance"]["last_appended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    FIXTURES_INDEX.write_text(json.dumps(existing_index, indent=2))

    print(f"\n# Captured {len(captured)} new fixtures. Updated {FIXTURES_INDEX.relative_to(REPO_ROOT)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
