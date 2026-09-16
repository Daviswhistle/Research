from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class DebtInstrumentSnapshot:
    as_of_date: date
    source_accession: str
    name: str
    instrument_type: str | None = None
    principal: float | None = None
    maturity_date: date | None = None
    maturity_year: int | None = None
    coupon_pct: float | None = None
    benchmark: str | None = None
    spread_bps: float | None = None
    seniority: str | None = None
    secured: bool | None = None
    currency: str = "USD"
    cusip: str | None = None
    isin: str | None = None
    commitment: float | None = None
    drawn: float | None = None
    available: float | None = None
    source_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DebtInstrumentVersion:
    stable_id: str
    snapshot: DebtInstrumentSnapshot
    match_confidence: str
    match_score: float | None = None
    matched_from_accession: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DebtInstrumentChange:
    stable_id: str
    from_accession: str
    to_accession: str
    from_date: date
    to_date: date
    changed_fields: tuple[str, ...]
    before: dict[str, Any]
    after: dict[str, Any]
    classification: tuple[str, ...]


@dataclass(frozen=True)
class DebtInstrumentLedger:
    versions: tuple[DebtInstrumentVersion, ...]
    changes: tuple[DebtInstrumentChange, ...]
    unmatched_versions: tuple[str, ...]
    warnings: tuple[str, ...]


_STOPWORDS = {
    "the", "a", "an", "of", "and", "due", "notes", "note", "senior", "secured",
    "unsecured", "facility", "credit", "term", "loan", "revolving", "revolver",
}


def _clean_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return cleaned or None


def _normalized_name_tokens(name: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", name.lower())
    return frozenset(word for word in words if word not in _STOPWORDS)


def _name_similarity(left: str, right: str) -> float:
    a = _normalized_name_tokens(left)
    b = _normalized_name_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _effective_maturity(snapshot: DebtInstrumentSnapshot) -> tuple[int | None, int | None, int | None]:
    if snapshot.maturity_date is not None:
        return snapshot.maturity_date.year, snapshot.maturity_date.month, snapshot.maturity_date.day
    return snapshot.maturity_year, None, None


def _explicit_identifier_conflict(left: DebtInstrumentSnapshot, right: DebtInstrumentSnapshot) -> bool:
    left_cusip, right_cusip = _clean_identifier(left.cusip), _clean_identifier(right.cusip)
    if left_cusip and right_cusip and left_cusip != right_cusip:
        return True
    left_isin, right_isin = _clean_identifier(left.isin), _clean_identifier(right.isin)
    return bool(left_isin and right_isin and left_isin != right_isin)


def _explicit_identifier_match(left: DebtInstrumentSnapshot, right: DebtInstrumentSnapshot) -> bool:
    if _explicit_identifier_conflict(left, right):
        return False
    left_cusip, right_cusip = _clean_identifier(left.cusip), _clean_identifier(right.cusip)
    if left_cusip and right_cusip and left_cusip == right_cusip:
        return True
    left_isin, right_isin = _clean_identifier(left.isin), _clean_identifier(right.isin)
    return bool(left_isin and right_isin and left_isin == right_isin)


def instrument_match_score(previous: DebtInstrumentSnapshot, current: DebtInstrumentSnapshot) -> float:
    """Heuristic identity score. A high score is not proof of amendment identity."""
    if _explicit_identifier_conflict(previous, current):
        return -1.0
    if _explicit_identifier_match(previous, current):
        return 100.0

    score = 0.0
    similarity = _name_similarity(previous.name, current.name)
    score += 35.0 * similarity

    if previous.instrument_type and current.instrument_type:
        if previous.instrument_type.lower() == current.instrument_type.lower():
            score += 20.0

    previous_maturity = _effective_maturity(previous)
    current_maturity = _effective_maturity(current)
    if previous_maturity[0] is not None and current_maturity[0] is not None:
        if previous_maturity == current_maturity:
            score += 20.0
        elif previous_maturity[0] == current_maturity[0]:
            score += 12.0
        elif abs(previous_maturity[0] - current_maturity[0]) == 1:
            score += 2.0

    if previous.coupon_pct is not None and current.coupon_pct is not None:
        difference = abs(previous.coupon_pct - current.coupon_pct)
        if difference <= 0.01:
            score += 10.0
        elif difference <= 0.25:
            score += 5.0

    if previous.seniority and current.seniority:
        if previous.seniority.lower() == current.seniority.lower():
            score += 8.0
    if previous.secured is not None and current.secured is not None and previous.secured == current.secured:
        score += 5.0
    if previous.currency and current.currency and previous.currency.upper() == current.currency.upper():
        score += 2.0
    return score


def _seed_stable_id(snapshot: DebtInstrumentSnapshot) -> str:
    explicit = _clean_identifier(snapshot.cusip) or _clean_identifier(snapshot.isin)
    if explicit:
        prefix = "CUSIP" if _clean_identifier(snapshot.cusip) else "ISIN"
        return f"{prefix}:{explicit}"
    maturity = snapshot.maturity_date.isoformat() if snapshot.maturity_date else str(snapshot.maturity_year or "unknown")
    material = "|".join(
        [
            snapshot.name.lower().strip(),
            (snapshot.instrument_type or "").lower().strip(),
            maturity,
            f"{snapshot.coupon_pct:.4f}" if snapshot.coupon_pct is not None else "",
            (snapshot.seniority or "").lower().strip(),
            str(snapshot.secured),
        ]
    )
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]
    return f"DEBT:{digest}"


def _snapshot_from_dict(raw: dict[str, Any]) -> DebtInstrumentSnapshot:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("debt instrument name is required")
    accession = str(raw.get("source_accession") or "").strip()
    if not accession:
        raise ValueError(f"{name}: source_accession is required")
    as_of_raw = raw.get("as_of_date")
    if not isinstance(as_of_raw, str) or not as_of_raw:
        raise ValueError(f"{name}: as_of_date is required")
    maturity_date = None
    if raw.get("maturity_date"):
        maturity_date = date.fromisoformat(str(raw["maturity_date"])[:10])
    maturity_year = raw.get("maturity_year")
    if maturity_year is not None:
        maturity_year = int(maturity_year)
        if maturity_year < 1900 or maturity_year > 2200:
            raise ValueError(f"{name}: invalid maturity_year")
    if maturity_date and maturity_year and maturity_date.year != maturity_year:
        raise ValueError(f"{name}: maturity_date and maturity_year disagree")

    def optional_float(key: str) -> float | None:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{name}: {key} must be numeric")
        return float(value)

    numeric_nonnegative = ("principal", "commitment", "drawn", "available")
    values = {key: optional_float(key) for key in numeric_nonnegative}
    if any(value is not None and value < 0 for value in values.values()):
        raise ValueError(f"{name}: principal/commitment/drawn/available cannot be negative")

    secured = raw.get("secured")
    if secured is not None and not isinstance(secured, bool):
        raise ValueError(f"{name}: secured must be boolean or null")

    return DebtInstrumentSnapshot(
        as_of_date=date.fromisoformat(as_of_raw[:10]),
        source_accession=accession,
        name=name,
        instrument_type=(str(raw["instrument_type"]).strip() if raw.get("instrument_type") else None),
        principal=values["principal"],
        maturity_date=maturity_date,
        maturity_year=maturity_year or (maturity_date.year if maturity_date else None),
        coupon_pct=optional_float("coupon_pct"),
        benchmark=(str(raw["benchmark"]).strip() if raw.get("benchmark") else None),
        spread_bps=optional_float("spread_bps"),
        seniority=(str(raw["seniority"]).strip() if raw.get("seniority") else None),
        secured=secured,
        currency=str(raw.get("currency") or "USD").upper(),
        cusip=(str(raw["cusip"]) if raw.get("cusip") else None),
        isin=(str(raw["isin"]) if raw.get("isin") else None),
        commitment=values["commitment"],
        drawn=values["drawn"],
        available=values["available"],
        source_refs=tuple(str(item) for item in raw.get("source_refs", []) if str(item).strip()),
        notes=tuple(str(item) for item in raw.get("notes", []) if str(item).strip()),
    )


def debt_snapshots_from_dict(raw: dict[str, Any] | list[dict[str, Any]]) -> tuple[DebtInstrumentSnapshot, ...]:
    rows = raw if isinstance(raw, list) else raw.get("instruments", [])
    if not isinstance(rows, list):
        raise ValueError("instruments must be a list")
    return tuple(_snapshot_from_dict(item) for item in rows)


def _change_fields(previous: DebtInstrumentSnapshot, current: DebtInstrumentSnapshot) -> tuple[str, ...]:
    fields = (
        "name", "instrument_type", "principal", "maturity_date", "maturity_year", "coupon_pct",
        "benchmark", "spread_bps", "seniority", "secured", "currency", "commitment", "drawn", "available",
    )
    return tuple(field for field in fields if getattr(previous, field) != getattr(current, field))


def _classify_maturity_change(previous: DebtInstrumentSnapshot, current: DebtInstrumentSnapshot) -> str:
    if previous.maturity_date is not None and current.maturity_date is not None:
        if current.maturity_date > previous.maturity_date:
            return "maturity_extended"
        if current.maturity_date < previous.maturity_date:
            return "maturity_accelerated"
        return "maturity_changed"

    old_year = previous.maturity_year or (previous.maturity_date.year if previous.maturity_date else None)
    new_year = current.maturity_year or (current.maturity_date.year if current.maturity_date else None)
    if old_year is None or new_year is None:
        return "maturity_changed"
    if new_year > old_year:
        return "maturity_extended"
    if new_year < old_year:
        return "maturity_accelerated"
    if (previous.maturity_date is None) != (current.maturity_date is None):
        return "maturity_precision_changed"
    return "maturity_changed"


def _classify_changes(fields: tuple[str, ...], previous: DebtInstrumentSnapshot, current: DebtInstrumentSnapshot) -> tuple[str, ...]:
    classes: list[str] = []
    if any(field in fields for field in ("maturity_date", "maturity_year")):
        classes.append(_classify_maturity_change(previous, current))
    if "principal" in fields:
        classes.append("principal_changed")
    if any(field in fields for field in ("coupon_pct", "benchmark", "spread_bps")):
        classes.append("pricing_changed")
    if any(field in fields for field in ("seniority", "secured")):
        classes.append("ranking_or_security_changed")
    if any(field in fields for field in ("commitment", "drawn", "available")):
        classes.append("facility_capacity_or_usage_changed")
    if "name" in fields and not classes:
        classes.append("label_changed")
    return tuple(classes)


def build_debt_instrument_ledger(
    snapshots: Iterable[DebtInstrumentSnapshot],
    *,
    match_threshold: float = 55.0,
    ambiguity_margin: float = 8.0,
) -> DebtInstrumentLedger:
    """Match instrument versions through time without pretending ambiguous matches are exact.

    A row is matched only to an instrument seen before the current as-of date.
    Exact CUSIP/ISIN matches dominate. Conflicting explicit identifiers are a hard
    non-match. Heuristic matches below the threshold or within `ambiguity_margin`
    of the second-best candidate start a new stable ID.
    """
    ordered = sorted(snapshots, key=lambda item: (item.as_of_date, item.source_accession, item.name))
    versions: list[DebtInstrumentVersion] = []
    latest_by_stable_id: dict[str, DebtInstrumentVersion] = {}
    used_by_date: dict[date, set[str]] = {}
    unmatched: list[str] = []
    warnings: list[str] = []

    for snapshot in ordered:
        candidates: list[tuple[float, DebtInstrumentVersion]] = []
        identifier_conflicts: list[str] = []
        for stable_id, prior_version in latest_by_stable_id.items():
            if prior_version.snapshot.as_of_date >= snapshot.as_of_date:
                continue
            if stable_id in used_by_date.setdefault(snapshot.as_of_date, set()):
                continue
            if _explicit_identifier_conflict(prior_version.snapshot, snapshot):
                identifier_conflicts.append(stable_id)
                continue
            score = instrument_match_score(prior_version.snapshot, snapshot)
            candidates.append((score, prior_version))
        candidates.sort(key=lambda item: item[0], reverse=True)

        matched: DebtInstrumentVersion | None = None
        confidence = "new"
        match_score: float | None = None
        version_warnings: list[str] = []
        if identifier_conflicts:
            version_warnings.append(
                "explicit identifier conflict excluded prior instrument(s): "
                + ", ".join(identifier_conflicts)
            )
        exact_matches = [item for item in candidates if _explicit_identifier_match(item[1].snapshot, snapshot)]
        if len(exact_matches) == 1:
            match_score, matched = exact_matches[0]
            confidence = "exact_identifier"
        elif len(exact_matches) > 1:
            version_warnings.append("multiple prior instruments share the same explicit identifier; not auto-matched")
        elif candidates and candidates[0][0] >= match_threshold:
            best_score, best = candidates[0]
            second_score = candidates[1][0] if len(candidates) > 1 else None
            if second_score is None or best_score - second_score >= ambiguity_margin:
                matched = best
                match_score = best_score
                confidence = "high" if best_score >= 75 else "medium"
            else:
                version_warnings.append(
                    f"ambiguous identity: best={best_score:.1f}, second={second_score:.1f}; new stable ID created"
                )

        if matched is not None:
            stable_id = matched.stable_id
            used_by_date[snapshot.as_of_date].add(stable_id)
            version = DebtInstrumentVersion(
                stable_id=stable_id,
                snapshot=snapshot,
                match_confidence=confidence,
                match_score=match_score,
                matched_from_accession=matched.snapshot.source_accession,
                warnings=tuple(version_warnings),
            )
        else:
            stable_id = _seed_stable_id(snapshot)
            if stable_id in latest_by_stable_id:
                suffix = hashlib.sha1(
                    f"{snapshot.source_accession}|{snapshot.as_of_date}|{snapshot.name}".encode("utf-8")
                ).hexdigest()[:6]
                stable_id = f"{stable_id}:{suffix}"
            unmatched.append(stable_id)
            version = DebtInstrumentVersion(
                stable_id=stable_id,
                snapshot=snapshot,
                match_confidence="new",
                warnings=tuple(version_warnings),
            )
        versions.append(version)
        latest_by_stable_id[stable_id] = version
        warnings.extend(version_warnings)

    changes: list[DebtInstrumentChange] = []
    by_id: dict[str, list[DebtInstrumentVersion]] = {}
    for version in versions:
        by_id.setdefault(version.stable_id, []).append(version)
    for stable_id, rows in by_id.items():
        rows.sort(key=lambda item: item.snapshot.as_of_date)
        for previous_version, current_version in zip(rows, rows[1:]):
            previous = previous_version.snapshot
            current = current_version.snapshot
            fields = _change_fields(previous, current)
            if not fields:
                continue
            before = {field: getattr(previous, field) for field in fields}
            after = {field: getattr(current, field) for field in fields}
            changes.append(
                DebtInstrumentChange(
                    stable_id=stable_id,
                    from_accession=previous.source_accession,
                    to_accession=current.source_accession,
                    from_date=previous.as_of_date,
                    to_date=current.as_of_date,
                    changed_fields=fields,
                    before=before,
                    after=after,
                    classification=_classify_changes(fields, previous, current),
                )
            )

    return DebtInstrumentLedger(
        versions=tuple(versions),
        changes=tuple(changes),
        unmatched_versions=tuple(unmatched),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def debt_instrument_ledger_to_dict(ledger: DebtInstrumentLedger) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if hasattr(value, "__dataclass_fields__"):
            return convert(asdict(value))
        return value

    return convert(ledger)
