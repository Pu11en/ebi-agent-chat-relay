"""The harness side of the shared project catalog.

Claude, Codex and DSH never receive the catalog; they receive one short hint
(:func:`build_catalog_hint`, injected into the per-turn developer instructions
by ``_run_helper``) that says how to *ask* for it when a request is about
finding, choosing or working in a project. The ask goes to the local control
plane — ``GET /api/projects``, ``POST /api/projects/resolve``,
``GET /api/projects/{key}`` — so every harness and the Discord launcher share
one answer for one identity.

``python -m claude_discord.catalog_query <text>`` is the same call as a
command: it reads ``CCDB_API_URL`` / ``CCDB_API_SECRET`` from the environment
the runner already injects, talks only to a loopback address, and prints the
bounded JSON the endpoint returned. It never scans a directory itself.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

#: ``(method, url, body, headers, timeout) -> (status, body bytes)``.
Transport = Callable[[str, str, bytes | None, dict[str, str], float], tuple[int, bytes]]

DEFAULT_TIMEOUT = 5.0
_MAX_LIMIT = 200

_HINT = """\
## Project catalog (ask, never guess)
Only when a request is about finding, choosing or working in a project: query \
the catalog and bind the `path` it returns. It answers with bounded metadata, \
never a folder's contents.
```bash
curl -s "$CCDB_API_URL/api/projects?q=<name>" -H "Authorization: Bearer $CCDB_API_SECRET"
curl -s -X POST "$CCDB_API_URL/api/projects/resolve" -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $CCDB_API_SECRET" -d '{"text": "<name, or Owner'"'"'s name>"}'
```
`kind` in the resolve answer: `local_available` (use `path`), `local_unavailable` \
(say so), `remote_target` (another computer — hand off, never a local same-named \
folder), `ambiguous_owner` (ask which), `no_match`. For any other request, skip this."""


def build_catalog_hint() -> str:
    """The concise, project-free invocation hint every harness receives."""
    return _HINT


class CatalogQueryError(RuntimeError):
    """The control plane refused or could not be reached."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _urllib_transport(
    method: str, url: str, body: bytes | None, headers: dict[str, str], timeout: float
) -> tuple[int, bytes]:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback only
            return int(response.status), response.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except URLError as exc:
        raise CatalogQueryError(f"control plane unreachable: {exc.reason}") from exc


class CatalogQueryClient:
    """A tiny client for the catalog endpoints; loopback only, bounded results."""

    def __init__(
        self,
        base_url: str,
        *,
        secret: str | None = None,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        parts = urlsplit(base_url)
        host = parts.hostname or ""
        try:
            local = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if parts.scheme != "http" or not local:
            raise CatalogQueryError(
                "CCDB_API_URL must be a loopback http address; refusing to send a catalog "
                f"query to {base_url!r}"
            )
        self._base = base_url.rstrip("/")
        self._secret = secret or None
        self._transport = transport or _urllib_transport
        self._timeout = timeout

    def list(
        self, text: str = "", *, owner: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        params: dict[str, str] = {}
        if text.strip():
            params["q"] = text.strip()
        if owner:
            params["owner"] = owner
        if limit is not None:
            params["limit"] = str(max(1, min(int(limit), _MAX_LIMIT)))
        query = f"?{urlencode(params)}" if params else ""
        return self._request("GET", f"/api/projects{query}")

    def resolve(self, text: str, *, owner: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text}
        if owner:
            payload["owner"] = owner
        return self._request("POST", "/api/projects/resolve", payload)

    def find(self, key: str) -> dict[str, Any]:
        return self._request("GET", f"/api/projects/{quote(key, safe='')}")

    def _request(self, method: str, path: str, payload: object | None = None) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._secret:
            headers["Authorization"] = f"Bearer {self._secret}"
        status, raw = self._transport(method, self._base + path, body, headers, self._timeout)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            data = {}
        if status >= 400:
            message = data.get("error") if isinstance(data, dict) else None
            raise CatalogQueryError(
                str(message or f"control plane answered {status}"), status=status
            )
        if not isinstance(data, dict):
            raise CatalogQueryError("control plane answered with a non-object", status=status)
        return data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m claude_discord.catalog_query",
        description="Query this computer's project catalog through the local control plane.",
    )
    parser.add_argument("text", nargs="?", default="", help="project name or owner phrase")
    parser.add_argument("--owner", help="scope to one owner (local) or name a remote one")
    parser.add_argument("--limit", type=int, help="at most this many projects")
    parser.add_argument("--resolve", action="store_true", help="one typed resolution")
    parser.add_argument("--key", help="revalidate one catalog identity key")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the query and print bounded JSON. 0 ok, 1 refused, 2 not configured."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    source: Mapping[str, str] = os.environ if env is None else env
    args = _parser().parse_args(list(argv) if argv is not None else None)
    base_url = source.get("CCDB_API_URL", "").strip()
    if not base_url:
        print("CCDB_API_URL is not set: no local control plane to ask", file=err)
        return 2
    try:
        client = CatalogQueryClient(
            base_url, secret=source.get("CCDB_API_SECRET"), transport=transport
        )
        if args.key:
            result = client.find(args.key)
        elif args.resolve:
            if not args.text.strip():
                print("--resolve needs the text to resolve", file=err)
                return 2
            result = client.resolve(args.text, owner=args.owner)
        else:
            result = client.list(args.text, owner=args.owner, limit=args.limit)
    except CatalogQueryError as exc:
        print(f"catalog query failed: {exc}", file=err)
        return 1
    json.dump(result, out, indent=2, sort_keys=True)
    out.write("\n")
    return 0


__all__ = [
    "CatalogQueryClient",
    "CatalogQueryError",
    "build_catalog_hint",
    "main",
]


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
