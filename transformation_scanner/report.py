from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from .models import ScoredCandidate
from .pipeline import ScanResult


def _format_number(value: object, *, suffix: str = "") -> str:
    try:
        if value is None or value == "":
            return "-"
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{number:,.0f}{suffix}"
    return f"{number:,.2f}{suffix}"


def _fact_lines(facts: Mapping[str, object]) -> list[str]:
    keys = (
        ("target_name", "인수대상", ""),
        ("target_business", "인수대상 주요사업", ""),
        ("acquisition_amount", "인수금액", "원"),
        ("asset_ratio_pct", "총자산 대비", "%"),
        ("equity_ratio_pct", "자기자본 대비", "%"),
        ("post_stake_pct", "인수 후 지분", "%"),
        ("issue_amount", "발행금액", "원"),
        ("dilution_shares", "잠재 신주", "주"),
        ("dilution_ratio_pct", "잠재 희석률", "%"),
        ("conversion_or_exercise_price", "전환·행사가", "원"),
        ("funds_business_acquisition", "영업양수자금", "원"),
        ("funds_other_company_securities", "타법인 증권 취득자금", "원"),
    )
    lines: list[str] = []
    for key, label, suffix in keys:
        if facts.get(key) in {None, ""}:
            continue
        lines.append(f"- {label}: {_format_number(facts.get(key), suffix=suffix)}")
    if facts.get("purpose"):
        lines.append(f"- 목적: {facts['purpose']}")
    if facts.get("payment_terms"):
        lines.append(f"- 지급조건: {facts['payment_terms']}")
    phrases = facts.get("business_purpose_phrases")
    if isinstance(phrases, list) and phrases:
        lines.append("- 사업목적 관련 원문: " + " / ".join(str(item) for item in phrases[:3]))
    return lines


def candidate_to_markdown(candidate: ScoredCandidate) -> str:
    disclosure = candidate.disclosure
    lines = [
        f"## [{candidate.band.value.upper()}] {disclosure.corp_name}({disclosure.stock_code})",
        "",
        f"- 공시: [{disclosure.report_nm}]({disclosure.viewer_url})",
        f"- 접수일: {disclosure.rcept_dt}",
        f"- 분류: `{candidate.classification.category.value}` / `{candidate.classification.subtype}`",
        f"- 주목도: **{candidate.attention_score:.1f}** / 자금조달 위험: **{candidate.financing_risk_score:.1f}**",
    ]
    if candidate.cluster_tags:
        lines.append(f"- 사건 클러스터: {', '.join(candidate.cluster_tags)}")
    if candidate.related_receipts:
        lines.append(f"- 연관 접수번호: {', '.join(candidate.related_receipts)}")

    fact_lines = _fact_lines(candidate.facts)
    if fact_lines:
        lines.extend(["", "### 구조화 사실", *fact_lines])
    if candidate.reasons:
        lines.extend(["", "### 왜 봐야 하나", *[f"- {item}" for item in candidate.reasons]])
    if candidate.risk_reasons:
        lines.extend(["", "### 자금조달·거버넌스 위험", *[f"- {item}" for item in candidate.risk_reasons]])
    if candidate.warnings:
        lines.extend(["", "### 데이터 경고", *[f"- {item}" for item in candidate.warnings]])
    lines.append("")
    return "\n".join(lines)


def render_markdown(result: ScanResult, candidates: Iterable[ScoredCandidate]) -> str:
    selected = tuple(candidates)
    lines = [
        "# DART 변신기업 탐색기",
        "",
        f"- 생성시각: {datetime.now().isoformat(timespec='seconds')}",
        f"- 조회 공시: {result.total_disclosures}건",
        f"- 분류 대상: {result.classified_disclosures}건",
        f"- 이미 처리되어 제외: {result.skipped_seen}건",
        f"- 비상장 법인 제외: {result.skipped_unlisted}건",
        f"- 리포트 후보: {len(selected)}건",
        "",
        "> 이 점수는 매수 신호가 아니라, 기업 성격이 바뀔 가능성이 큰 공시를 먼저 읽기 위한 연구 우선순위다.",
        "",
    ]
    if not selected:
        lines.append("조건을 충족한 신규 후보가 없습니다.\n")
        return "\n".join(lines)

    lines.extend(
        [
            "|등급|회사|공시|주목도|자금조달 위험|",
            "|---|---|---|---:|---:|",
        ]
    )
    for candidate in selected:
        disclosure = candidate.disclosure
        lines.append(
            f"|{candidate.band.value}|{disclosure.corp_name}({disclosure.stock_code})|"
            f"[{disclosure.report_nm}]({disclosure.viewer_url})|"
            f"{candidate.attention_score:.1f}|{candidate.financing_risk_score:.1f}|"
        )
    lines.append("")
    for candidate in selected:
        lines.append(candidate_to_markdown(candidate))
    return "\n".join(lines)


def write_outputs(
    *,
    result: ScanResult,
    candidates: Iterable[ScoredCandidate],
    output_dir: str | Path,
    run_id: str,
) -> tuple[Path, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    selected = tuple(candidates)
    jsonl_path = directory / f"{run_id}_candidates.jsonl"
    markdown_path = directory / f"{run_id}_report.md"

    with jsonl_path.open("w", encoding="utf-8") as handle:
        for candidate in selected:
            handle.write(json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True, default=str))
            handle.write("\n")
    markdown_path.write_text(render_markdown(result, selected), encoding="utf-8")
    return jsonl_path, markdown_path
