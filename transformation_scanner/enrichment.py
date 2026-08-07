from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .dart_client import DartClient, DartClientError
from .document_parser import (
    extract_business_purpose_phrases,
    extract_document_rows,
    extract_financial_snapshot,
    parse_number,
)
from .models import Disclosure, EventClassification


@dataclass(frozen=True)
class EnrichmentResult:
    facts: dict[str, Any]
    warnings: tuple[str, ...] = ()


def _number(row: Mapping[str, Any], key: str) -> float | None:
    return parse_number(row.get(key))


def _sum_numbers(*values: float | None) -> float | None:
    materialized = [float(value) for value in values if value is not None]
    return sum(materialized) if materialized else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator) / float(denominator) * 100.0


def _text(row: Mapping[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def _contains_any(value: object, tokens: tuple[str, ...]) -> bool:
    text = str(value or "")
    return any(token in text for token in tokens)


def _acquisition_facts(row: Mapping[str, Any]) -> dict[str, Any]:
    post_stake = _number(row, "atinh_eqrt")
    purpose = _text(row, "inh_pp")
    payment = _text(row, "dl_pym")
    target_business = _text(row, "iscmp_mbsn")
    facts: dict[str, Any] = {
        "structured_kind": "acquisition",
        "target_name": _text(row, "iscmp_cmpnm"),
        "target_country": _text(row, "iscmp_nt"),
        "target_business": target_business,
        "acquisition_amount": _number(row, "inhdtl_inhprc"),
        "acquirer_total_assets": _number(row, "inhdtl_tast"),
        "asset_ratio_pct": _number(row, "inhdtl_tast_vs"),
        "acquirer_equity": _number(row, "inhdtl_ecpt"),
        "equity_ratio_pct": _number(row, "inhdtl_ecpt_vs"),
        "post_stake_pct": post_stake,
        "purpose": purpose,
        "payment_terms": payment,
        "scheduled_date": _text(row, "inh_prd"),
        "counterparty": _text(row, "dlptn_cmpnm"),
        "external_valuation": _text(row, "exevl_atn"),
        "external_valuation_opinion": _text(row, "exevl_op"),
        "put_option": _text(row, "popt_ctr_atn"),
        "put_option_terms": _text(row, "popt_ctr_cn"),
    }
    facts["control_acquisition"] = bool(
        (post_stake is not None and post_stake >= 50.0)
        or _contains_any(purpose, ("경영권", "지배력", "최대주주", "종속회사"))
    )
    facts["new_business_language"] = _contains_any(
        f"{purpose} {target_business}",
        ("신규사업", "사업다각화", "신성장", "사업영역 확대", "포트폴리오"),
    )
    facts["debt_like_payment"] = _contains_any(
        payment,
        ("차입", "대출", "전환사채", "신주인수권부사채", "교환사채", "사채", "인수금융"),
    )
    return facts


def _bond_facts(row: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    is_cb = kind == "cb"
    share_count_key = "cvisstk_cnt" if is_cb else "nstk_isstk_cnt"
    dilution_key = "cvisstk_tisstk_vs" if is_cb else "nstk_isstk_tisstk_vs"
    price_key = "cv_prc" if is_cb else "ex_prc"
    floor_key = "act_mktprcfl_cvprc_lwtrsprc"
    return {
        "structured_kind": kind,
        "instrument": "CB" if is_cb else "BW",
        "round": _text(row, "bd_tm"),
        "issue_amount": _number(row, "bd_fta"),
        "coupon_rate_pct": _number(row, "bd_intr_ex"),
        "yield_to_maturity_pct": _number(row, "bd_intr_sf"),
        "maturity_date": _text(row, "bd_mtd"),
        "issue_method": _text(row, "bdis_mthn"),
        "conversion_or_exercise_price": _number(row, price_key),
        "reset_floor_price": _number(row, floor_key),
        "dilution_shares": _number(row, share_count_key),
        "dilution_ratio_pct": _number(row, dilution_key),
        "funds_facility": _number(row, "fdpp_fclt"),
        "funds_business_acquisition": _number(row, "fdpp_bsninh"),
        "funds_operations": _number(row, "fdpp_op"),
        "funds_debt_repayment": _number(row, "fdpp_dtrp"),
        "funds_other_company_securities": _number(row, "fdpp_ocsa"),
        "funds_other": _number(row, "fdpp_etc"),
        "payment_date": _text(row, "pymd"),
    }


def _equity_issuance_facts(row: Mapping[str, Any]) -> dict[str, Any]:
    new_shares = _sum_numbers(_number(row, "nstk_ostk_cnt"), _number(row, "nstk_estk_cnt"))
    existing_shares = _sum_numbers(
        _number(row, "bfic_tisstk_ostk"),
        _number(row, "bfic_tisstk_estk"),
    )
    return {
        "structured_kind": "equity_issuance",
        "instrument": "paid_in_capital_increase",
        "new_shares": new_shares,
        "existing_shares_before": existing_shares,
        "dilution_ratio_pct": _ratio(new_shares, existing_shares),
        "issue_method": _text(row, "ic_mthn"),
        "funds_facility": _number(row, "fdpp_fclt"),
        "funds_business_acquisition": _number(row, "fdpp_bsninh"),
        "funds_operations": _number(row, "fdpp_op"),
        "funds_debt_repayment": _number(row, "fdpp_dtrp"),
        "funds_other_company_securities": _number(row, "fdpp_ocsa"),
        "funds_other": _number(row, "fdpp_etc"),
    }


def normalize_structured_facts(structured_kind: str, row: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(structured_kind)
    if kind == "acquisition":
        return _acquisition_facts(row)
    if kind in {"cb", "bw"}:
        return _bond_facts(row, kind=kind)
    if kind == "equity_issuance":
        return _equity_issuance_facts(row)
    return {"structured_kind": kind, "raw_structured": dict(row)}


class DisclosureEnricher:
    def __init__(self, *, client: DartClient, include_documents: bool = False) -> None:
        self._client = client
        self._include_documents = bool(include_documents)

    def enrich(
        self,
        *,
        disclosure: Disclosure,
        classification: EventClassification,
    ) -> EnrichmentResult:
        facts: dict[str, Any] = {}
        warnings: list[str] = []

        if classification.structured_kind:
            try:
                row = self._client.fetch_matching_structured_row(
                    structured_kind=classification.structured_kind,
                    disclosure=disclosure,
                    fallback_lookback_days=400 if classification.is_correction else 0,
                )
            except DartClientError as exc:
                warnings.append(f"structured enrichment 실패: {exc}")
                row = None
            if row is None:
                warnings.append(
                    f"접수번호와 일치하는 {classification.structured_kind} 구조화 행을 찾지 못함"
                )
            else:
                facts.update(normalize_structured_facts(classification.structured_kind, row))

        should_fetch_document = self._include_documents and (
            classification.requires_document or not facts
        )
        if should_fetch_document:
            try:
                document_zip = self._client.fetch_document_zip(rcept_no=disclosure.rcept_no)
                rows = extract_document_rows(document_zip)
                purpose_phrases = extract_business_purpose_phrases(rows)
                financial_snapshot = extract_financial_snapshot(rows)
                facts["document_row_count"] = len(rows)
                if purpose_phrases:
                    facts["business_purpose_phrases"] = purpose_phrases
                if financial_snapshot:
                    facts["document_financial_snapshot"] = financial_snapshot
                    facts["document_financial_snapshot_is_heuristic"] = True
            except (DartClientError, OSError, ValueError) as exc:
                warnings.append(f"원문 enrichment 실패: {exc}")

        return EnrichmentResult(facts=facts, warnings=tuple(warnings))
