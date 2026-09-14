from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .contract_parties import normalize_party_name
from .sec import SEC_WEB_BASE, normalize_cik

SEC_CIK_LOOKUP_URL = f"{SEC_WEB_BASE}/Archives/edgar/cik-lookup-data.txt"


class SecTextClientLike(Protocol):
    user_agent: str
    session: object

    def _throttle(self) -> None: ...


@dataclass(frozen=True)
class SecCikLookupMatch:
    name: str
    normalized_name: str
    cik: str
    source: str = SEC_CIK_LOOKUP_URL


def _decode_response(response: object) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content.decode("latin-1")
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    raise ValueError("SEC CIK lookup response had neither bytes content nor text")


def parse_cik_lookup_matches(
    payload: str,
    normalized_names: Iterable[str],
) -> dict[str, tuple[SecCikLookupMatch, ...]]:
    """Extract exact-normalized name matches from the SEC's cumulative CIK list.

    The SEC file is formatted as `ENTITY NAME:CIK:`. Entity names can themselves
    contain colons, so parsing is deliberately performed from the right after
    removing the trailing delimiter.
    """

    targets = {str(item).strip() for item in normalized_names if str(item).strip()}
    found: dict[str, dict[tuple[str, str], SecCikLookupMatch]] = {
        target: {} for target in targets
    }
    if not targets:
        return {}

    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.endswith(":"):
            line = line[:-1]
        name, separator, cik_text = line.rpartition(":")
        if not separator:
            continue
        name = name.strip()
        cik_text = cik_text.strip()
        if not name or not cik_text.isdigit() or len(cik_text) > 10:
            continue
        normalized = normalize_party_name(name)
        if normalized not in targets:
            continue
        cik = normalize_cik(cik_text)
        match = SecCikLookupMatch(name=name, normalized_name=normalized, cik=cik)
        found[normalized][(cik, name)] = match

    return {
        target: tuple(sorted(items.values(), key=lambda item: (item.cik, item.name)))
        for target, items in found.items()
    }


def fetch_cik_lookup_matches(
    client: SecTextClientLike,
    normalized_names: Iterable[str],
) -> dict[str, tuple[SecCikLookupMatch, ...]]:
    """Fetch the official SEC cumulative CIK/name list once and match requested names.

    The raw text is cached on the client instance so repeated resolver passes do not
    redownload the multi-megabyte SEC file. The file remains candidate-generation
    evidence only; callers must independently confirm historical identity.
    """

    targets = {str(item).strip() for item in normalized_names if str(item).strip()}
    if not targets:
        return {}

    raw_text = getattr(client, "_cik_lookup_raw_text", None)
    if not isinstance(raw_text, str):
        client._throttle()
        response = client.session.get(
            SEC_CIK_LOOKUP_URL,
            headers={
                "User-Agent": client.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "text/plain, */*",
            },
            timeout=60,
        )
        response.raise_for_status()
        raw_text = _decode_response(response)
        try:
            setattr(client, "_cik_lookup_raw_text", raw_text)
        except Exception:
            pass

    return parse_cik_lookup_matches(raw_text, targets)
