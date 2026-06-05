#!/usr/bin/env python
"""Regenerate ``docs/api/endpoints.md`` from the live FastAPI OpenAPI.

Run from the repo root or anywhere with the workspace's venv active:

    uv run packages/horizons-api/scripts/regen_endpoints_md.py

Builds a ``FastAPI`` app via ``horizons_api.app.create_app``, reads
``app.openapi()``, and writes a tag-grouped Markdown reference to
``docs/api/endpoints.md``. Idempotent: running twice produces a
byte-identical file (modulo Python's dict ordering, which we sort).

The script is intentionally minimal — it renders the wire shape, not
the prose. Long-form discussion (auth flow, cache semantics, etc.) lives
in sibling docs (``auth.md``, ``concepts.md``) and is linked from the
generated file's preamble.

The pre-commit hook in ``.pre-commit-config.yaml`` runs this script and
fails when ``docs/api/endpoints.md`` is stale relative to the live
OpenAPI. To clear the diff: re-run the script and commit the result.

The script imports ``horizons_api.app`` and needs the env vars the
factory reads (``HORIZONS_JWT_*``, ``HORIZONS_DB_URL``,
``HORIZONS_CORS_ORIGINS``). Any value is fine; the script does not open
a Postgres connection or mint a token — it constructs the app and
generates the schema only. Sensible defaults are wired below so casual
runs Just Work.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

# ---- ephemeral env so create_app() succeeds without real config -------------
#
# The factory reads RSA PEMs to build the local JWT provider. We mint a
# fresh keypair every run so the script never depends on environment
# state. The OpenAPI generation path never uses the provider — schemas
# come from route annotations — so the key is throwaway.
def _ephemeral_keys() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = k.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public = (
        k.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public


def _bootstrap_env() -> None:
    private, public = _ephemeral_keys()
    defaults = {
        "HORIZONS_JWT_PRIVATE_KEY_PEM": private,
        "HORIZONS_JWT_PUBLIC_KEY_PEM": public,
        "HORIZONS_JWT_ISSUER": "horizons-api-openapi-regen",
        "HORIZONS_JWT_AUDIENCE": "horizons-api-openapi-regen",
        "HORIZONS_CORS_ORIGINS": "",
        "HORIZONS_DB_URL": "postgresql+asyncpg://x:x@localhost:5432/x",
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, v)


# ---- rendering --------------------------------------------------------------


_METHOD_ORDER = ("get", "post", "patch", "put", "delete", "head", "options")
_DEFAULT_TAG = "untagged"


def _ref_name(ref: str) -> str:
    """``#/components/schemas/Foo`` -> ``Foo``."""
    return ref.rsplit("/", 1)[-1]


def _resolve_schema(schema: Mapping[str, Any], components: Mapping[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        name = _ref_name(schema["$ref"])
        return dict(components.get(name, {}))
    return dict(schema)


def _short_type(schema: Mapping[str, Any]) -> str:
    """Render a schema's type for the param/field columns."""
    if "$ref" in schema:
        return _ref_name(schema["$ref"])
    if "anyOf" in schema:
        parts = [
            _short_type(s)
            for s in schema["anyOf"]
            if not (s.get("type") == "null")
        ]
        # Pydantic's ``int | None`` shows up as anyOf [int, null]; emit
        # the non-null branch with a ``?`` so the wire-shape reader
        # picks up nullability without parsing JSON Schema directly.
        has_null = any(s.get("type") == "null" for s in schema["anyOf"])
        text = " | ".join(parts) if parts else "any"
        return f"{text}?" if has_null else text
    t = schema.get("type")
    if t == "array":
        item = schema.get("items", {})
        return f"array<{_short_type(item)}>"
    if t == "string" and schema.get("format"):
        return f"string ({schema['format']})"
    if t is None:
        return "object"
    return str(t)


def _render_parameters(params: Iterable[Mapping[str, Any]]) -> str:
    lines = [
        "| In | Name | Type | Required | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    has_any = False
    for p in sorted(params, key=lambda x: (x.get("in", ""), x.get("name", ""))):
        has_any = True
        schema = p.get("schema", {})
        descr = (p.get("description") or "").replace("\n", " ").strip() or "—"
        required = "yes" if p.get("required") else "no"
        lines.append(
            f"| `{p.get('in', '?')}` | `{p.get('name', '?')}` | `{_short_type(schema)}` | {required} | {descr} |"
        )
    if not has_any:
        return ""
    return "\n".join(lines) + "\n"


def _render_request_body(
    request_body: Mapping[str, Any] | None,
    components: Mapping[str, Any],
) -> str:
    if not request_body:
        return ""
    content = request_body.get("content", {})
    json_body = content.get("application/json")
    if not json_body:
        return ""
    schema = _resolve_schema(json_body.get("schema", {}), components)
    return _render_object_schema(schema, components, label="Request body")


def _render_responses(
    responses: Mapping[str, Any],
    components: Mapping[str, Any],
) -> str:
    lines = ["**Responses**", "", "| Status | Shape | Description |", "| --- | --- | --- |"]
    has_any = False
    for code in sorted(responses.keys()):
        spec = responses[code]
        description = (spec.get("description") or "").replace("\n", " ").strip() or "—"
        content = spec.get("content", {})
        json_body = content.get("application/json")
        if json_body:
            schema = json_body.get("schema", {})
            shape = _short_type(schema)
        else:
            shape = "—"
        lines.append(f"| `{code}` | `{shape}` | {description} |")
        has_any = True
    if not has_any:
        return ""
    return "\n".join(lines) + "\n"


def _render_object_schema(
    schema: Mapping[str, Any],
    components: Mapping[str, Any],
    *,
    label: str,
) -> str:
    if not schema:
        return ""
    schema = _resolve_schema(schema, components)
    if schema.get("type") != "object" and "properties" not in schema:
        # Non-object request bodies (rare here) — show the type tersely.
        return f"**{label}** `{_short_type(schema)}`\n"
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    if not props:
        return f"**{label}** _(no fields)_\n"
    lines = [
        f"**{label}**",
        "",
        "| Field | Type | Required | Description |",
        "| --- | --- | --- | --- |",
    ]
    for name in sorted(props):
        field = props[name]
        descr = (field.get("description") or "").replace("\n", " ").strip() or "—"
        lines.append(
            f"| `{name}` | `{_short_type(field)}` | "
            f"{'yes' if name in required else 'no'} | {descr} |"
        )
    return "\n".join(lines) + "\n"


def _operations_by_tag(spec: Mapping[str, Any]) -> dict[str, list[tuple[str, str, dict[str, Any]]]]:
    out: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method not in _METHOD_ORDER:
                continue
            tags = op.get("tags") or [_DEFAULT_TAG]
            for tag in tags:
                out.setdefault(tag, []).append((path, method, op))
    for tag in out:
        out[tag].sort(key=lambda t: (t[0], _METHOD_ORDER.index(t[1])))
    return out


def _render_operation(
    path: str,
    method: str,
    op: Mapping[str, Any],
    components: Mapping[str, Any],
) -> str:
    title = f"### `{method.upper()} {path}`"
    summary = (op.get("summary") or "").strip()
    description = (op.get("description") or "").strip()
    parts = [title, ""]
    if summary:
        parts.append(summary)
        parts.append("")
    if description and description != summary:
        parts.append(description)
        parts.append("")
    rendered_params = _render_parameters(op.get("parameters", []))
    if rendered_params:
        parts.append("**Parameters**")
        parts.append("")
        parts.append(rendered_params)
    body = _render_request_body(op.get("requestBody"), components)
    if body:
        parts.append(body)
    responses = _render_responses(op.get("responses", {}), components)
    if responses:
        parts.append(responses)
    return "\n".join(parts).rstrip() + "\n"


_PREAMBLE = """# Horizons API — Endpoint reference

*Auto-generated from the live FastAPI OpenAPI spec by
[`scripts/regen_endpoints_md.py`](../../packages/horizons-api/scripts/regen_endpoints_md.py).
Do not hand-edit — the pre-commit hook fails if this file drifts from
the generated output.*

Sibling docs:

- [`README.md`](README.md) — overview of the docs/api directory.
- [`getting-started.md`](getting-started.md) — auth flow for the
  upstream Lawstronaut API.
- [`auth.md`](auth.md) — Horizons login / refresh / logout posture.
- [`horizons-primitives.md`](horizons-primitives.md) — design-of-record
  for the three primitives (`/v1/discovery` / `/v1/temporal` /
  `/v1/differential`).
- [`lawstronaut-endpoints.md`](lawstronaut-endpoints.md) — upstream
  Lawstronaut v2 reference (separate API; Horizons consumes this).
- [`operational-notes.md`](operational-notes.md) — Lawstronaut
  refresh cadence, pricing, MCP, and other facts that shape design.

Conventions:

- All Horizons endpoints live under `/v1/...`. The `/openapi.json`
  spec at the API root is the source of truth.
- Per-user endpoints (`/v1/me/*`, `/v1/auth/*`) carry
  `Cache-Control: private, no-store`.
- The admin surface (`/v1/admin/*`) returns 403 (not 404) for
  authenticated non-admin callers — the documented exception to the
  "404 not 403" rule, because the prefix is explicitly administrative.
- Type column annotations: `T?` denotes a nullable field;
  `array<T>` denotes a JSON array of `T`; `T (uuid)` /
  `T (date-time)` reflect a JSON Schema `format`.
"""


def render_markdown(spec: Mapping[str, Any]) -> str:
    components = spec.get("components", {}).get("schemas", {})
    by_tag = _operations_by_tag(spec)
    if not by_tag:
        return _PREAMBLE + "\n_(no endpoints registered)_\n"

    body_parts: list[str] = [_PREAMBLE]
    body_parts.append("## Table of contents\n")
    for tag in sorted(by_tag):
        body_parts.append(f"- [{tag}](#{tag})")
    body_parts.append("")

    for tag in sorted(by_tag):
        body_parts.append(f"## {tag}\n")
        for path, method, op in by_tag[tag]:
            body_parts.append(_render_operation(path, method, op, components))
            body_parts.append("")
    text = "\n".join(body_parts).rstrip() + "\n"
    return text


# ---- entrypoint -------------------------------------------------------------


def regenerate(spec: Mapping[str, Any], output: Path) -> str:
    md = render_markdown(spec)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")
    return md


def _repo_root() -> Path:
    # This file lives at ``packages/horizons-api/scripts/regen_endpoints_md.py``,
    # so three ``parent`` hops land at the repo root regardless of cwd.
    return Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    check_only = "--check" in argv

    _bootstrap_env()

    from horizons_api.app import create_app

    app = create_app()
    spec = app.openapi()

    output = _repo_root() / "docs" / "api" / "endpoints.md"
    new_text = render_markdown(spec)

    if check_only:
        existing = output.read_text(encoding="utf-8") if output.exists() else ""
        if existing == new_text:
            return 0
        # Show a short, machine-readable hint so CI logs are actionable.
        sys.stderr.write(
            "docs/api/endpoints.md is stale relative to the live OpenAPI.\n"
            "Run: uv run packages/horizons-api/scripts/regen_endpoints_md.py\n"
        )
        return 1

    output.write_text(new_text, encoding="utf-8")
    if "--print-spec-path" in argv:
        # Sanity hook for one-off debugging; emits the OpenAPI to stdout.
        json.dump(spec, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
