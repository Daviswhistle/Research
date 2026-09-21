from __future__ import annotations

import json

from opportunity_scanner.filters import (
    MinimumEvidenceGate,
    ProhibitedGate,
    ValidationBudgetGate,
)
from opportunity_scanner.scoring import OpportunityAxisScorer
from opportunity_scanner.selector import ParetoLayerSelector
from opportunity_scanner.source import JsonlOpportunitySource
from research_pipeline.models import ResearchCandidate, ResearchQuery


def _candidate(candidate_id: str, **scores: float) -> ResearchCandidate:
    return ResearchCandidate(
        candidate_id=candidate_id,
        title=candidate_id,
        source="test",
        scores=dict(scores),
    )


def test_jsonl_source_loads_candidate(tmp_path):
    path = tmp_path / "opportunities.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "x",
                "title": "X",
                "mechanism": "test",
                "facts": {"independent_revenue_signals": 2},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    items = JsonlOpportunitySource(path).fetch(ResearchQuery())
    assert len(items) == 1
    assert items[0].candidate_id == "x"
    assert "test" in items[0].tags


def test_evidence_and_budget_gates():
    candidate = ResearchCandidate(
        candidate_id="x",
        title="X",
        source="test",
        payload={
            "facts": {"independent_revenue_signals": 2},
            "validation": {"cash_usd": 50, "human_hours": 4},
        },
    )
    query = ResearchQuery(context={"max_cash_usd": 100, "max_human_hours": 8})
    assert MinimumEvidenceGate(minimum=2).keep(query, candidate)
    assert ValidationBudgetGate().keep(query, candidate)


def test_axis_scorer_separates_facts_and_priors():
    candidate = ResearchCandidate(
        candidate_id="x",
        title="X",
        source="test",
        payload={
            "facts": {"independent_revenue_signals": 4},
            "hypotheses": {
                "demand_strength": 0.8,
                "automation_fit": 0.9,
                "repeatability": 0.7,
                "distribution_access": 0.6,
                "upside_convexity": 0.5,
            },
            "validation": {
                "cash_usd": 0,
                "human_hours": 8,
                "feedback_days": 7,
            },
            "risks": {
                "capital_at_risk": 0.1,
                "platform_policy": 0.2,
                "legal": 0.05,
            },
        },
    )
    scores = OpportunityAxisScorer().score(ResearchQuery(), candidate)
    assert scores["evidence_quality"] == 1.0
    assert scores["cash_ease"] == 1.0
    assert scores["human_ease"] == 0.5
    assert scores["feedback_speed"] == 0.5
    assert scores["safety"] == 0.8
    assert scores["feasibility_floor"] == 0.8


def test_prohibited_gate_rejects_only_prohibited():
    query = ResearchQuery()

    def _payload(status: str) -> ResearchCandidate:
        return ResearchCandidate(
            candidate_id=status,
            title=status,
            source="test",
            payload={"permission_status": status},
        )

    assert ProhibitedGate().keep(query, _payload("allowed"))
    # authorized-only passes the gate: the experiment design (e.g. historical
    # benchmark before live testing) must enforce the authorization boundary.
    assert ProhibitedGate().keep(query, _payload("authorized-only"))
    assert not ProhibitedGate().keep(query, _payload("prohibited"))


def test_pareto_tie_break_prefers_lower_human_hours():
    low_hours = _candidate(
        "low-hours",
        demand_strength=0.8,
        evidence_quality=0.8,
        automation_fit=0.8,
        distribution_access=0.8,
        cash_ease=0.8,
        human_ease=0.9,
        feedback_speed=0.8,
        safety=0.8,
        feasibility_floor=0.8,
    )
    high_hours = _candidate(
        "high-hours",
        demand_strength=0.8,
        evidence_quality=0.8,
        automation_fit=0.8,
        distribution_access=0.8,
        cash_ease=0.8,
        human_ease=0.2,
        feedback_speed=0.8,
        safety=0.8,
        feasibility_floor=0.8,
    )
    selected = ParetoLayerSelector().select(
        ResearchQuery(limit=1),
        [high_hours, low_hours],
    )
    assert [item.candidate_id for item in selected] == ["low-hours"]


def test_pareto_selector_prefers_non_dominated_candidate():
    better = _candidate(
        "better",
        demand_strength=0.9,
        evidence_quality=1.0,
        automation_fit=0.9,
        distribution_access=0.8,
        cash_ease=0.9,
        feedback_speed=0.8,
        safety=0.9,
        feasibility_floor=0.9,
    )
    worse = _candidate(
        "worse",
        demand_strength=0.8,
        evidence_quality=0.8,
        automation_fit=0.8,
        distribution_access=0.7,
        cash_ease=0.8,
        feedback_speed=0.7,
        safety=0.8,
        feasibility_floor=0.8,
    )
    selected = ParetoLayerSelector().select(
        ResearchQuery(limit=1),
        [worse, better],
    )
    assert [item.candidate_id for item in selected] == ["better"]
