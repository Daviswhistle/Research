from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExceptionItem:
    code: str
    message: str
    line_index: int | None = None
    sku: str | None = None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def validate_order(
    order: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    *,
    seen_purchase_orders: set[str] | None = None,
    price_tolerance: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    """Validate an extracted order before any write to Shopify/ERP.

    An LLM may extract fields upstream, but pricing, inventory, SKU validity,
    duplicates, and approval gates are decided by deterministic code.
    """

    seen_purchase_orders = seen_purchase_orders or set()
    exceptions: list[ExceptionItem] = []

    po_number = str(order.get("purchase_order_number") or "").strip()
    customer_id = str(order.get("customer_id") or "").strip()
    currency = str(order.get("currency") or "").strip().upper()
    lines = order.get("lines")

    if not po_number:
        exceptions.append(ExceptionItem("missing_po_number", "Purchase order number is required."))
    elif po_number in seen_purchase_orders:
        exceptions.append(ExceptionItem("duplicate_po", f"Purchase order {po_number} was already processed."))

    if not customer_id:
        exceptions.append(ExceptionItem("missing_customer", "Customer identifier is required."))
    if not currency:
        exceptions.append(ExceptionItem("missing_currency", "Currency is required."))
    if not isinstance(lines, list) or not lines:
        exceptions.append(ExceptionItem("missing_lines", "At least one order line is required."))
        lines = []

    normalized_lines: list[dict[str, Any]] = []
    total = Decimal("0")

    for index, raw_line in enumerate(lines):
        sku = str(raw_line.get("sku") or "").strip()
        quantity = _decimal(raw_line.get("quantity"))
        unit_price = _decimal(raw_line.get("unit_price"))

        if not sku:
            exceptions.append(ExceptionItem("missing_sku", "SKU is required.", index))
            continue
        if quantity is None or quantity <= 0 or quantity != quantity.to_integral_value():
            exceptions.append(ExceptionItem("invalid_quantity", "Quantity must be a positive integer.", index, sku))
            continue
        if unit_price is None or unit_price < 0:
            exceptions.append(ExceptionItem("invalid_price", "Unit price must be non-negative.", index, sku))
            continue

        item = catalog.get(sku)
        if item is None:
            exceptions.append(ExceptionItem("unknown_sku", f"SKU {sku} is not in the catalog.", index, sku))
            continue

        expected_price = _decimal(item.get("price"))
        available = _decimal(item.get("inventory"))

        if expected_price is None:
            exceptions.append(ExceptionItem("catalog_price_missing", f"Catalog price missing for {sku}.", index, sku))
        elif abs(unit_price - expected_price) > price_tolerance:
            exceptions.append(
                ExceptionItem(
                    "price_mismatch",
                    f"{sku}: order price {unit_price} != catalog price {expected_price}.",
                    index,
                    sku,
                )
            )

        if available is None:
            exceptions.append(ExceptionItem("inventory_missing", f"Inventory missing for {sku}.", index, sku))
        elif quantity > available:
            exceptions.append(
                ExceptionItem(
                    "insufficient_inventory",
                    f"{sku}: requested {quantity}, available {available}.",
                    index,
                    sku,
                )
            )

        line_total = quantity * unit_price
        total += line_total
        normalized_lines.append(
            {
                "sku": sku,
                "quantity": int(quantity),
                "unit_price": str(unit_price),
                "line_total": str(line_total),
            }
        )

    return {
        "status": "ready_for_draft" if not exceptions else "needs_review",
        "purchase_order_number": po_number or None,
        "customer_id": customer_id or None,
        "currency": currency or None,
        "normalized_lines": normalized_lines,
        "order_total": str(total),
        "exceptions": [asdict(item) for item in exceptions],
    }


def _load_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate extracted B2B orders before ERP/Shopify writes.")
    parser.add_argument("--order", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--seen-pos")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    seen: set[str] = set()
    if args.seen_pos:
        loaded = _load_json(args.seen_pos)
        if isinstance(loaded, list):
            seen = {str(item) for item in loaded}
    result = validate_order(_load_json(args.order), _load_json(args.catalog), seen_purchase_orders=seen)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "ready_for_draft" else 2


if __name__ == "__main__":
    raise SystemExit(main())
