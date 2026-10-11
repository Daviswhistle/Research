from pathlib import Path

from opportunity_scanner.order_intake_email import intake_email, parse_email_order

CATALOG = {
    "SKU-A": {"price": "12.50", "inventory": 100},
    "SKU-B": {"price": "9.00", "inventory": 5},
}

FIXTURES = Path(__file__).resolve().parent.parent / "examples" / "order_intake"


def test_clean_email_is_ready_for_draft():
    result = intake_email(FIXTURES / "email_clean.txt", CATALOG)
    assert result["status"] == "ready_for_draft"
    assert result["exceptions"] == []
    assert result["order_total"] == "143.00"
    assert [line["sku"] for line in result["normalized_lines"]] == ["SKU-A", "SKU-B"]


def test_clean_eml_parses_text_body():
    result = intake_email(FIXTURES / "email_clean.eml", CATALOG)
    assert result["status"] == "ready_for_draft"
    assert result["order_total"] == "143.00"


def test_bad_email_quarantines_without_draft():
    result = intake_email(FIXTURES / "email_bad.txt", CATALOG)
    assert result["status"] == "needs_review"
    codes = {item["code"] for item in result["exceptions"]}
    assert "unparseable_line" in codes
    assert "missing_currency" in codes
    # The one clean line is still identified, but nothing is draftable.
    assert result["normalized_lines"] != []


def test_instruction_lines_are_not_silently_dropped():
    order, parse_exceptions = parse_email_order(
        "PO: PO-9\nCustomer: C\nCurrency: USD\nPlease expedite this order\n"
    )
    assert [item.code for item in parse_exceptions] == [
        "unparseable_line",
        "no_order_lines_parsed",
    ]
    assert order["lines"] == []


def test_html_only_email_quarantines():
    raw = (
        "From: buyer@example.com\n"
        "To: orders@example.com\n"
        "Subject: order\n"
        'Content-Type: text/html; charset="utf-8"\n'
        "\n"
        "<p>PO: PO-9</p>\n"
    )
    path = FIXTURES / "_tmp_html_only.eml"
    try:
        path.write_text(raw, encoding="utf-8")
        result = intake_email(path, CATALOG)
    finally:
        path.unlink(missing_ok=True)
    assert result["status"] == "needs_review"
    assert {item["code"] for item in result["exceptions"]} >= {"no_text_body"}
