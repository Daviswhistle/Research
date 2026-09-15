from __future__ import annotations

from datetime import date
import json

import pytest

from distressed_equity.agent_results import agent_result_from_dict, validate_agent_result
from distressed_equity.covenants import (
    CovenantDefinition,
    CovenantEbitdaBridge,
    CovenantInputs,
    calculate_covenant_headroom,
    covenant_risk_from_result,
)
from distressed_equity.csv_market import CsvMarketProvider
from distressed_equity.debt_instruments import DebtInstrumentSnapshot, build_debt_instrument_ledger
from distressed_equity.engine import time_to_liquidity_exhaustion
from distressed_equity.models import (
    CapitalStructure,
    CaseInput,
    CovenantRisk,
    DebtObligation,
    LiquidityProfile,
    ProbabilityView,
    Scenario,
)
from distressed_equity.orchestration_cli import _bundle_from_frozen_packet
from distressed_equity.replay import run_historical_replay
from distressed_equity.screen_cli import main as screen_main
from distressed_equity.screening import ScreeningCandidate, screen_candidate
from distressed_equity.sec import SecClient, SecFiling
from distressed_equity.sec_instruments import filing_index_url
from distressed_equity.source_graph import resolve_source_document_graph


def _scenario(name: str, ev: float) -> Scenario:
    return Scenario(name=name, enterprise_value=ev, exit_net_debt=0.0)


def test_conflicting_explicit_debt_identifiers_never_merge():
    first = DebtInstrumentSnapshot(
        as_of_date=date(2022, 1, 1),
        source_accession="0000000000-22-000001",
        name="5% Senior Notes due 2028",
        instrument_type="notes",
        maturity_year=2028,
        coupon_pct=5.0,
        cusip="123456789",
    )
    second = DebtInstrumentSnapshot(
        as_of_date=date(2022, 6, 1),
        source_accession="0000000000-22-000002",
        name="5% Senior Notes due 2028",
        instrument_type="notes",
        maturity_year=2028,
        coupon_pct=5.0,
        cusip="987654321",
    )
    ledger = build_debt_instrument_ledger((first, second))
    assert len({item.stable_id for item in ledger.versions}) == 2
    assert ledger.versions[1].matched_from_accession is None
    assert any("explicit identifier conflict" in warning for warning in ledger.warnings)


def test_month_zero_payment_is_applied_before_runway_projection():
    case = CaseInput(
        ticker="ZERO",
        company_name="Zero Month Co",
        capital_structure=CapitalStructure(current_price=1.0, current_shares=100.0),
        liquidity=LiquidityProfile(
            starting_liquidity=50.0,
            monthly_free_cash_flow=(100.0,),
            mandatory_payments={0: 60.0},
            recovery_month=1,
        ),
        scenarios=(_scenario("base", 500.0), _scenario("distress", 0.0)),
        probability_view=ProbabilityView(target_cagr=0.15, horizon_years=3),
    )
    assert time_to_liquidity_exhaustion(case) == 0

    candidate = ScreeningCandidate(
        ticker="ZERO",
        company_name="Zero Month Co",
        capital_structure=CapitalStructure(current_price=1.0, current_shares=100.0),
        base_scenario=_scenario("base", 500.0),
        downside_scenario=_scenario("downside", 0.0),
        starting_liquidity=50.0,
        monthly_cash_burn=0.0,
        recovery_month=1,
        debt_obligations=(
            DebtObligation(
                name="Immediate payment",
                amount=60.0,
                due_month=0,
                cash_payment_required=True,
                refinancing_required=False,
            ),
        ),
        critical_assumptions_complete=True,
    )
    result = screen_candidate(candidate)
    assert result.time_to_death_months == 0
    assert result.survival_gate == "FAIL"


def test_indeterminate_covenant_becomes_pre_recovery_research_blocker():
    headroom = calculate_covenant_headroom(
        CovenantDefinition(
            name="Springing leverage",
            kind="max_net_leverage",
            threshold=5.0,
            test_month=2,
            springing=True,
            springing_revolver_drawn_pct=0.35,
        ),
        CovenantEbitdaBridge(base_ebitda=100.0),
        CovenantInputs(gross_debt=300.0, unrestricted_cash=20.0),
    )
    risk = covenant_risk_from_result(headroom)
    assert risk["unresolved"] is True
    assert risk["test_month"] == 2

    candidate = ScreeningCandidate(
        ticker="COV",
        company_name="Covenant Co",
        capital_structure=CapitalStructure(current_price=1.0, current_shares=100.0),
        base_scenario=_scenario("base", 500.0),
        downside_scenario=_scenario("downside", 0.0),
        starting_liquidity=500.0,
        monthly_cash_burn=1.0,
        recovery_month=3,
        covenants=(CovenantRisk(name="Springing leverage", test_month=2, unresolved=True),),
        critical_assumptions_complete=True,
    )
    result = screen_candidate(candidate)
    assert result.survival_gate == "RESEARCH"
    assert result.pre_recovery_covenants == ("Springing leverage",)


def test_agent_scenario_validation_rejects_malformed_optional_financing():
    raw = {
        "schema_version": "1",
        "task_name": "business_and_normalization_auditor",
        "analysis_date": "2022-12-31",
        "status": "complete",
        "evidence": [
            {
                "evidence_id": "e1",
                "claim": "scenario evidence",
                "source": "primary filing",
                "published_on": "2022-12-01",
                "evidence_type": "fact",
            }
        ],
        "patches": [
            {
                "path": "base_scenario",
                "value": {
                    "enterprise_value": 1000,
                    "exit_net_debt": 100,
                    "exit_senior_claims": 0,
                    "financing": {"capital_raised": 100},
                    "critical_assumptions": [],
                    "notes": [],
                },
                "evidence_refs": ["e1"],
                "confidence": "high",
            }
        ],
        "unresolved": [],
        "warnings": [],
    }
    validation = validate_agent_result(
        agent_result_from_dict(raw), expected_analysis_date=date(2022, 12, 31)
    )
    assert not validation.valid
    assert any("issue_price is required" in error for error in validation.errors)


class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _JsonSession:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, url, **kwargs):
        return _JsonResponse(self.mapping[url])


def test_sec_snapshot_does_not_backfill_post_cutoff_current_name():
    cik = "0001326801"
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    session = _JsonSession(
        {
            submissions_url: {
                "name": "Meta Platforms, Inc.",
                "formerNames": [
                    {"name": "Facebook Inc", "from": "2005-05-06", "to": "2021-10-27"}
                ],
                "filings": {"recent": {}, "files": []},
            },
            facts_url: {"facts": {}},
        }
    )
    client = SecClient(
        user_agent="Research test@example.com",
        session=session,
        min_interval_seconds=0.1,
        sleeper=lambda _: None,
    )
    snapshot = client.snapshot(cik=cik, analysis_date=date(2020, 12, 31))
    assert snapshot.company_name == "Facebook Inc"
    assert snapshot.company_name != "Meta Platforms, Inc."


def test_frozen_workspace_rejects_mismatched_requested_identity():
    packet = {
        "snapshot": {
            "ticker": "ABC",
            "cik": "0000000001",
            "company_name": "ABC Co",
            "analysis_date": "2022-12-31",
            "warnings": [],
        }
    }
    with pytest.raises(ValueError, match="ticker"):
        _bundle_from_frozen_packet(
            packet,
            date(2022, 12, 31),
            requested_ticker="XYZ",
        )
    with pytest.raises(ValueError, match="CIK"):
        _bundle_from_frozen_packet(
            packet,
            date(2022, 12, 31),
            requested_cik="2",
        )


class _TextResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _TextSession:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, url, **kwargs):
        if url not in self.mapping:
            raise AssertionError(f"unexpected URL: {url}")
        return _TextResponse(self.mapping[url])


class _GraphClient:
    user_agent = "Research test@example.com"

    def __init__(self, filings, mapping):
        self._filings = filings
        self.session = _TextSession(mapping)

    def _throttle(self):
        return None

    def filings_as_of(self, cik, analysis_date, forms=()):
        allowed = set(forms)
        return tuple(
            item
            for item in self._filings
            if item.filing_date <= analysis_date and (not allowed or item.form in allowed)
        )


def _filing(cik, accession, filed, form, primary):
    return SecFiling(
        cik=str(cik).zfill(10),
        accession_number=accession,
        filing_date=filed,
        form=form,
        primary_document=primary,
        report_date=filed,
    )


def test_source_graph_never_resolves_edge_to_node_omitted_by_max_nodes():
    root = _filing("1111111", "0001111111-22-000001", date(2022, 6, 30), "10-Q", "root.htm")
    old = _filing("1111111", "0001111111-19-000003", date(2019, 5, 10), "8-K", "old8k.htm")
    old_base = filing_index_url(old).rsplit("/", 1)[0]
    mapping = {
        root.archive_url: (
            "<html><body>Reference is made to Exhibit 10.1 to Form 8-K filed May 10, 2019 "
            "under accession 0001111111-19-000003.</body></html>"
        ),
        filing_index_url(old): (
            '<html><body><table>'
            '<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>'
            '<tr><td>1</td><td>Primary</td><td><a href="old8k.htm">old8k.htm</a></td><td>8-K</td><td>1</td></tr>'
            '<tr><td>2</td><td>Credit Agreement</td><td><a href="ex10-1.htm">ex10-1.htm</a></td><td>EX-10.1</td><td>1</td></tr>'
            '</table></body></html>'
        ),
    }
    graph = resolve_source_document_graph(
        _GraphClient((root, old), mapping),
        cik=root.cik,
        analysis_date=date(2022, 12, 31),
        seed_filings=(root,),
        max_depth=2,
        max_nodes=2,
    )
    node_ids = {node.node_id for node in graph.nodes}
    resolved = [edge for edge in graph.edges if edge.status == "resolved"]
    assert resolved
    assert all(edge.to_node_id in node_ids for edge in resolved)
    assert len(node_ids) == 2
    assert any(node.url == old_base + "/ex10-1.htm" for node in graph.nodes)


def test_csv_unknown_asset_type_is_not_treated_as_definite_non_stock(tmp_path):
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    securities.write_text(
        "security_id,symbol,name,start_date,end_date\n"
        "perm1,ABC,ABC Co,2010-01-01,\n",
        encoding="utf-8",
    )
    prices.write_text(
        "security_id,date,close,adjusted_close\n"
        "perm1,2019-01-02,100,100\n"
        "perm1,2020-12-31,20,20\n",
        encoding="utf-8",
    )
    run = run_historical_replay(
        CsvMarketProvider(securities, prices),
        date(2020, 12, 31),
    )
    assert run.scanned_security_count == 1
    assert len(run.candidates) == 1


def test_csv_replay_refuses_survivorship_claim_without_membership_columns(tmp_path):
    securities = tmp_path / "securities.csv"
    prices = tmp_path / "prices.csv"
    securities.write_text(
        "security_id,symbol,name\nperm1,ABC,ABC Co\n",
        encoding="utf-8",
    )
    prices.write_text(
        "security_id,date,close,adjusted_close\n"
        "perm1,2019-01-02,100,100\n"
        "perm1,2020-12-31,20,20\n",
        encoding="utf-8",
    )
    provider = CsvMarketProvider(securities, prices)
    with pytest.raises(ValueError, match="survivorship"):
        run_historical_replay(provider, date(2020, 12, 31))
    run = run_historical_replay(
        provider,
        date(2020, 12, 31),
        allow_survivorship_unsafe_universe=True,
    )
    assert "WARNING" in run.survivorship_guard
    assert "unsafe" in run.survivorship_guard.lower()


def test_screen_cli_preserves_duplicate_ticker_candidate_identity(tmp_path):
    universe = tmp_path / "universe.json"
    tasks = tmp_path / "tasks.json"
    common = {
        "ticker": "DUP",
        "capital_structure": {
            "current_price": 1,
            "current_shares": 100,
            "net_debt": 0,
            "other_senior_claims": 0,
        },
        "base_scenario": {"enterprise_value": 500, "exit_net_debt": 0},
        "downside_scenario": {"enterprise_value": 0, "exit_net_debt": 0},
        "critical_assumptions_complete": False,
    }
    rows = [
        {**common, "company_name": "First Variant", "analysis_date": "2020-12-31"},
        {**common, "company_name": "Second Variant", "analysis_date": "2021-12-31"},
    ]
    universe.write_text(json.dumps(rows), encoding="utf-8")
    assert screen_main([str(universe), "--tasks-output", str(tasks)]) == 0
    manifest = json.loads(tasks.read_text(encoding="utf-8"))
    assert {
        (item["company_name"], item["analysis_date"])
        for item in manifest
    } == {
        ("First Variant", "2020-12-31"),
        ("Second Variant", "2021-12-31"),
    }
