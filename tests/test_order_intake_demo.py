from decimal import Decimal

from opportunity_scanner.order_intake_demo import validate_order

CATALOG = {"A": {"price": "10.00", "inventory": 5}}


def test_clean_order_is_ready_for_draft():
    result = validate_order(
        {
            "purchase_order_number": "PO-1",
            "customer_id": "C-1",
            "currency": "USD",
            "lines": [{"sku": "A", "quantity": 2, "unit_price": "10.00"}],
        },
        CATALOG,
    )
    assert result["status"] == "ready_for_draft"
    assert result["exceptions"] == []
    assert result["order_total"] == "20.00"
    assert result["quarantined_lines"] == []
    assert result["quarantined_total"] == "0"


def test_quarantined_lines_are_excluded_from_order_total():
    result = validate_order(
        {
            "purchase_order_number": "PO-2",
            "customer_id": "C-1",
            "currency": "USD",
            "lines": [
                {"sku": "A", "quantity": 2, "unit_price": "10.00"},
                {"sku": "A", "quantity": 7, "unit_price": "9.50"},
            ],
        },
        CATALOG,
        price_tolerance=Decimal("0.01"),
    )
    assert result["status"] == "needs_review"
    assert result["order_total"] == "20.00"
    assert result["quarantined_total"] == "66.50"
    assert [line["sku"] for line in result["normalized_lines"]] == ["A"]
    assert [line["line_index"] for line in result["normalized_lines"]] == [0]
    assert len(result["quarantined_lines"]) == 1
    assert result["quarantined_lines"][0]["line_index"] == 1
    assert set(result["quarantined_lines"][0]["reasons"]) == {
        "price_mismatch",
        "insufficient_inventory",
    }


def test_bad_order_is_quarantined_with_specific_exceptions():
    result = validate_order(
        {
            "purchase_order_number": "PO-OLD",
            "customer_id": "C-1",
            "currency": "USD",
            "lines": [
                {"sku": "A", "quantity": 7, "unit_price": "9.50"},
                {"sku": "X", "quantity": 1, "unit_price": "1.00"},
            ],
        },
        CATALOG,
        seen_purchase_orders={"PO-OLD"},
        price_tolerance=Decimal("0.01"),
    )
    assert result["status"] == "needs_review"
    codes = {item["code"] for item in result["exceptions"]}
    assert {"duplicate_po", "price_mismatch", "insufficient_inventory", "unknown_sku"} <= codes
