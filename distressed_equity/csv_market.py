from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .market import PricePoint, SecurityIdentity


def _date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


class CsvMarketProvider:
    """Bulk-safe provider for vendor/CRSP-like exports after column normalization.

    securities CSV required columns:
      security_id,symbol,name
    optional:
      exchange,asset_type,start_date,end_date,status

    prices CSV required columns:
      security_id,date,close
    optional:
      symbol,adjusted_close,volume,shares_outstanding

    A permanent `security_id` is strongly preferred over ticker as the join key.
    """

    name = "csv"
    bulk_safe = True

    def __init__(self, securities_csv: str | Path, prices_csv: str | Path) -> None:
        self.securities_csv = Path(securities_csv)
        self.prices_csv = Path(prices_csv)
        self._securities = self._load_securities()
        self._prices = self._load_prices()

    def _load_securities(self) -> tuple[SecurityIdentity, ...]:
        rows: list[SecurityIdentity] = []
        with self.securities_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"security_id", "symbol", "name"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"securities CSV must include {sorted(required)}")
            for raw in reader:
                security_id = (raw.get("security_id") or "").strip()
                symbol = (raw.get("symbol") or "").strip()
                if not security_id or not symbol:
                    continue
                rows.append(
                    SecurityIdentity(
                        security_id=security_id,
                        symbol=symbol,
                        name=(raw.get("name") or "").strip(),
                        exchange=(raw.get("exchange") or "").strip() or None,
                        asset_type=(raw.get("asset_type") or "").strip() or None,
                        start_date=_date(raw.get("start_date")),
                        end_date=_date(raw.get("end_date")),
                        status=(raw.get("status") or "").strip() or None,
                        provider=self.name,
                    )
                )
        return tuple(rows)

    def _load_prices(self) -> dict[str, tuple[PricePoint, ...]]:
        buckets: dict[str, list[PricePoint]] = {}
        symbol_by_id = {security.security_id: security.symbol for security in self._securities}
        with self.prices_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"security_id", "date", "close"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"prices CSV must include {sorted(required)}")
            for raw in reader:
                security_id = (raw.get("security_id") or "").strip()
                dt = _date(raw.get("date"))
                close = _float(raw.get("close"))
                if not security_id or dt is None or close is None:
                    continue
                adjusted_close = _float(raw.get("adjusted_close"))
                symbol = (raw.get("symbol") or symbol_by_id.get(security_id) or security_id).strip()
                buckets.setdefault(security_id, []).append(
                    PricePoint(
                        security_id=security_id,
                        symbol=symbol,
                        date=dt,
                        close=(adjusted_close if adjusted_close is not None else close),
                        adjusted=(adjusted_close is not None),
                        volume=_float(raw.get("volume")),
                        shares_outstanding=_float(raw.get("shares_outstanding")),
                        source=f"CSV:{self.prices_csv.name}",
                    )
                )
        return {
            security_id: tuple(sorted(points, key=lambda point: point.date))
            for security_id, points in buckets.items()
        }

    def universe(self, as_of: date) -> tuple[SecurityIdentity, ...]:
        return tuple(
            security
            for security in self._securities
            if (security.start_date is None or security.start_date <= as_of)
            and (security.end_date is None or security.end_date >= as_of)
        )

    def price_on_or_before(self, security: SecurityIdentity, as_of: date) -> PricePoint | None:
        eligible = [
            point for point in self._prices.get(security.security_id, ()) if point.date <= as_of
        ]
        if not eligible:
            return None
        point = max(eligible, key=lambda item: item.date)
        # Exact market-cap reconstruction should use the as-traded close. The
        # normalized CSV schema stores adjusted_close separately, so reload the
        # raw row is deliberately avoided here. Users that need exact market cap
        # should omit adjusted_close-only exports and include raw close.
        if point.adjusted:
            with self.prices_csv.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                raw_candidates = [
                    row
                    for row in reader
                    if (row.get("security_id") or "").strip() == security.security_id
                    and _date(row.get("date")) == point.date
                ]
            if raw_candidates:
                raw_close = _float(raw_candidates[-1].get("close"))
                if raw_close is not None:
                    return PricePoint(
                        security_id=security.security_id,
                        symbol=security.symbol,
                        date=point.date,
                        close=raw_close,
                        adjusted=False,
                        volume=point.volume,
                        shares_outstanding=point.shares_outstanding,
                        source=point.source,
                    )
        return PricePoint(
            security_id=security.security_id,
            symbol=security.symbol,
            date=point.date,
            close=point.close,
            adjusted=False,
            volume=point.volume,
            shares_outstanding=point.shares_outstanding,
            source=point.source,
        )

    def adjusted_history(
        self,
        security: SecurityIdentity,
        start: date,
        end: date,
    ) -> tuple[PricePoint, ...]:
        return tuple(
            point
            for point in self._prices.get(security.security_id, ())
            if start <= point.date <= end
        )
