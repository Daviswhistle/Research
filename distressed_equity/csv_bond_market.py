from __future__ import annotations

import csv
from datetime import date
import math
from pathlib import Path
import re

from .bond_market import BondBenchmarkObservation, BondMarketObservation


def _identifier(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return cleaned or None


def _float(value: str | None, field: str, row_number: int) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bond observations CSV row {row_number}: {field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"bond observations CSV row {row_number}: {field} must be finite")
    return parsed


class CsvBondMarketProvider:
    """Bulk-safe provider for normalized TRACE/WRDS/vendor bond exports.

    Required columns:
      date
      at least one identifier column: cusip or isin

    Market columns (at least one per row):
      price_pct_par (alias: price)
      yield_pct (alias: yield)
      spread_bps

    Optional:
      volume, benchmark_yield_pct, source

    The provider deliberately does not infer CUSIP/ISIN from issuer/name strings.
    Upstream raw TRACE/WRDS files should be normalized to this small schema so the
    provenance and unit choices are explicit and reproducible.
    """

    name = "csv_bond_market"
    bulk_safe = True

    def __init__(self, observations_csv: str | Path) -> None:
        self.observations_csv = Path(observations_csv)
        self._by_cusip: dict[str, tuple[BondMarketObservation, ...]] = {}
        self._by_isin: dict[str, tuple[BondMarketObservation, ...]] = {}
        self._load()

    def _load(self) -> None:
        cusip_buckets: dict[str, list[BondMarketObservation]] = {}
        isin_buckets: dict[str, list[BondMarketObservation]] = {}
        with self.observations_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            if "date" not in fields:
                raise ValueError("bond observations CSV must include date")
            if not ({"cusip", "isin"} & fields):
                raise ValueError("bond observations CSV must include cusip or isin")
            if not ({"price_pct_par", "price", "yield_pct", "yield", "spread_bps"} & fields):
                raise ValueError(
                    "bond observations CSV must include price_pct_par/price, yield_pct/yield, or spread_bps"
                )
            for row_number, raw in enumerate(reader, 2):
                raw_date = (raw.get("date") or "").strip()
                if not raw_date:
                    continue
                try:
                    observation_date = date.fromisoformat(raw_date[:10])
                except ValueError as exc:
                    raise ValueError(
                        f"bond observations CSV row {row_number}: date must be YYYY-MM-DD"
                    ) from exc
                cusip = _identifier(raw.get("cusip"))
                isin = _identifier(raw.get("isin"))
                if not cusip and not isin:
                    continue
                price = _float(raw.get("price_pct_par") or raw.get("price"), "price_pct_par", row_number)
                yield_pct = _float(raw.get("yield_pct") or raw.get("yield"), "yield_pct", row_number)
                spread_bps = _float(raw.get("spread_bps"), "spread_bps", row_number)
                benchmark = _float(
                    raw.get("benchmark_yield_pct"),
                    "benchmark_yield_pct",
                    row_number,
                )
                volume = _float(raw.get("volume"), "volume", row_number)
                if price is None and yield_pct is None and spread_bps is None:
                    continue
                if price is not None and price < 0:
                    raise ValueError(
                        f"bond observations CSV row {row_number}: price_pct_par cannot be negative"
                    )
                if volume is not None and volume < 0:
                    raise ValueError(
                        f"bond observations CSV row {row_number}: volume cannot be negative"
                    )
                source = (raw.get("source") or "").strip() or f"CSV:{self.observations_csv.name}"
                observation = BondMarketObservation(
                    date=observation_date,
                    cusip=cusip,
                    isin=isin,
                    price_pct_par=price,
                    yield_pct=yield_pct,
                    volume=volume,
                    benchmark_yield_pct=benchmark,
                    spread_bps=spread_bps,
                    source=source,
                )
                if cusip:
                    cusip_buckets.setdefault(cusip, []).append(observation)
                if isin:
                    isin_buckets.setdefault(isin, []).append(observation)

        def freeze(buckets: dict[str, list[BondMarketObservation]]) -> dict[str, tuple[BondMarketObservation, ...]]:
            return {
                key: tuple(sorted(rows, key=lambda item: item.date))
                for key, rows in buckets.items()
            }

        self._by_cusip = freeze(cusip_buckets)
        self._by_isin = freeze(isin_buckets)

    def observations(
        self,
        *,
        cusip: str | None,
        isin: str | None,
        start: date,
        end: date,
    ) -> tuple[BondMarketObservation, ...]:
        if end < start:
            raise ValueError("bond observation end date cannot precede start date")
        candidates: list[BondMarketObservation] = []
        clean_cusip = _identifier(cusip)
        clean_isin = _identifier(isin)
        if clean_cusip:
            candidates.extend(self._by_cusip.get(clean_cusip, ()))
        if clean_isin:
            candidates.extend(self._by_isin.get(clean_isin, ()))
        seen: set[tuple[object, ...]] = set()
        output: list[BondMarketObservation] = []
        for row in sorted(candidates, key=lambda item: item.date):
            if not start <= row.date <= end:
                continue
            key = (
                row.date,
                row.cusip,
                row.isin,
                row.price_pct_par,
                row.yield_pct,
                row.volume,
                row.benchmark_yield_pct,
                row.spread_bps,
                row.source,
            )
            if key in seen:
                continue
            seen.add(key)
            output.append(row)
        return tuple(output)

    def benchmark_on_or_before(
        self,
        *,
        as_of: date,
        remaining_years: float,
    ) -> BondBenchmarkObservation | None:
        # Normalized CSV rows may carry a benchmark_yield_pct directly. A separate
        # Treasury file is intentionally not guessed or silently joined here.
        return None
