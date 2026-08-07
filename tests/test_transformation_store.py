from transformation_scanner.models import Disclosure, RelatedEvent
from transformation_scanner.scoring import score_candidate
from transformation_scanner.store import ScannerStore
from transformation_scanner.taxonomy import classify_report


def test_store_deduplicates_receipts_and_returns_recent_events(tmp_path) -> None:
    disclosure = Disclosure(
        corp_cls="K",
        corp_name="A사",
        corp_code="00000001",
        stock_code="000001",
        report_nm="주요사항보고서(타법인 주식 및 출자증권 양수결정)",
        rcept_no="20260101000001",
        flr_nm="A사",
        rcept_dt="20260101",
    )
    classification = classify_report(disclosure.report_nm)
    candidate = score_candidate(
        disclosure=disclosure,
        classification=classification,
        facts={"control_acquisition": True, "post_stake_pct": 80.0},
    )

    with ScannerStore(tmp_path / "state.sqlite3") as store:
        assert store.seen_receipt(disclosure.rcept_no) is False
        store.save_candidate(candidate)
        assert store.seen_receipt(disclosure.rcept_no) is True

        events = store.recent_events(
            corp_code="00000001",
            start_date="20251201",
            end_date="20260131",
        )

    assert len(events) == 1
    assert isinstance(events[0], RelatedEvent)
    assert events[0].subtype == "equity_or_business_acquisition"
