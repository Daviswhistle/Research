from __future__ import annotations

import argparse
import email
import email.policy
import json
import re
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from .order_intake_demo import ExceptionItem, validate_order

#: Strict plain-text order format. Anything that is not a header or an item
#: line is a parse exception — the parser never guesses.
EMAIL_FORMAT = """\
PO: PO-1002
Customer: CUST-42
Currency: USD

SKU-A x 10 @ 12.50
SKU-B x 2 @ 9.00
"""

_HEADER_PATTERNS = {
    "purchase_order_number": re.compile(
        r"^\s*(?:PO|P\.O\.|PO\s*Number|Order(?:\s*Number)?)\s*:(.*)$",
        re.IGNORECASE,
    ),
    "customer_id": re.compile(r"^\s*Customer(?:\s*ID)?\s*:(.*)$", re.IGNORECASE),
    "currency": re.compile(r"^\s*Currency\s*:(.*)$", re.IGNORECASE),
}

_ITEM_PATTERN = re.compile(r"^\s*(\S+)\s+[xX×]\s*(\d+)\s*@\s*(\d+(?:\.\d+)?)\s*$")

# Pure pleasantries carry no order content and are ignored. Anything else that
# is not a header or an item line quarantines: the parser never guesses, and
# instructions it cannot fulfill (e.g. "expedite") must surface in review.
_PLEASANTRY_PATTERN = re.compile(
    r"^\s*(?:hi|hello|hey|dear|thanks|thank you|regards|best regards|"
    r"kind regards|sincerely|cheers|best)\b[^:\d]*$",
    re.IGNORECASE,
)

_EMAIL_ERROR_MESSAGES = {
    "unreadable_email": "Email file could not be read.",
    "no_text_body": "Email has no text/plain body to parse.",
}


def extract_text_body(path: str | Path) -> tuple[str | None, str | None]:
    """Return (body, error_code) for a .txt or .eml file.

    Only the text/plain part of an .eml is trusted. HTML-only mail,
    attachments, and quoted replies are not parsed — they quarantine.
    """

    raw = Path(path).read_bytes()
    if Path(path).suffix.lower() != ".eml":
        try:
            return raw.decode("utf-8"), None
        except UnicodeDecodeError:
            return None, "unreadable_email"

    try:
        message = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:
        return None, "unreadable_email"

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return str(part.get_content()), None
                except Exception:
                    return None, "unreadable_email"
        return None, "no_text_body"

    if message.get_content_type() != "text/plain":
        return None, "no_text_body"
    try:
        content = message.get_content()
    except Exception:
        return None, "unreadable_email"
    return (content if isinstance(content, str) else str(content)), None


def parse_email_order(body: str) -> tuple[dict[str, Any], list[ExceptionItem]]:
    """Parse an email body into the order dict accepted by validate_order."""

    headers: dict[str, str] = {}
    lines: list[dict[str, Any]] = []
    exceptions: list[ExceptionItem] = []

    for lineno, raw_line in enumerate(body.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or _PLEASANTRY_PATTERN.match(raw_line):
            continue
        for key, pattern in _HEADER_PATTERNS.items():
            match = pattern.match(raw_line)
            if match:
                headers.setdefault(key, match.group(1).strip())
                break
        else:
            match = _ITEM_PATTERN.match(raw_line)
            if match:
                lines.append(
                    {
                        "sku": match.group(1),
                        "quantity": int(match.group(2)),
                        "unit_price": match.group(3),
                    }
                )
            else:
                exceptions.append(
                    ExceptionItem(
                        "unparseable_line",
                        f"Line {lineno} is not a header or 'SKU x QTY @ PRICE': {stripped!r}.",
                        None,
                        None,
                    )
                )

    if not lines:
        if exceptions:
            exceptions.append(
                ExceptionItem(
                    "no_order_lines_parsed", "No parseable order lines found."
                )
            )
        else:
            exceptions.append(
                ExceptionItem(
                    "empty_email_body", "Email body contains no order content."
                )
            )

    order = {
        "purchase_order_number": headers.get("purchase_order_number", ""),
        "customer_id": headers.get("customer_id", ""),
        "currency": headers.get("currency", ""),
        "lines": lines,
    }
    return order, exceptions


def intake_email(
    email_path: str | Path,
    catalog: dict[str, dict[str, Any]],
    *,
    seen_purchase_orders: set[str] | None = None,
    price_tolerance: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    """Parse an order email, then run the deterministic validation gate.

    Parse problems never produce a draft on their own: any parse exception
    forces needs_review even if the parsed lines would otherwise validate.
    """

    body, error = extract_text_body(email_path)
    parse_exceptions: list[ExceptionItem] = []
    if error is not None:
        parse_exceptions.append(ExceptionItem(error, _EMAIL_ERROR_MESSAGES[error]))
        order: dict[str, Any] = {
            "purchase_order_number": "",
            "customer_id": "",
            "currency": "",
            "lines": [],
        }
    else:
        assert body is not None
        order, parse_exceptions = parse_email_order(body)

    result = validate_order(
        order,
        catalog,
        seen_purchase_orders=seen_purchase_orders,
        price_tolerance=price_tolerance,
    )
    if parse_exceptions:
        result["exceptions"] = [asdict(item) for item in parse_exceptions] + result[
            "exceptions"
        ]
        result["status"] = "needs_review"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse an order email (.txt/.eml) and validate before any ERP/Shopify write."
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--seen-pos")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    seen: set[str] = set()
    if args.seen_pos:
        loaded = json.loads(Path(args.seen_pos).read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            seen = {str(item) for item in loaded}
    result = intake_email(args.email, catalog, seen_purchase_orders=seen)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "ready_for_draft" else 2


if __name__ == "__main__":
    raise SystemExit(main())
