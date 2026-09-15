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
    optional for parsing, but required for survivorship-safe replay claims:
      start_date,end_date
    other optional columns:
      exchange,asset_type,status

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
        self.survivorship_safe_universe = False
        self._securities = self._load_securities()
        self._prices, self._raw_prices = self._load_prices()

    def _load_securities(self) -> tuple[SecurityIdentity, ...]:
        rows: list[SecurityIdentity] = []
        with self.securities_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or [])
            required = {"security_id", "symbol", "name"}
            if not required.issubset(fieldnames):
                raise ValueError(f"securities CSV must include {sorted(required)}")
            self.survivorship_safe_universe = {"start_date", "end_date"}.issubset(fieldnames)
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

    def _load_prices(
        self,
    ) -> tuple[dict[str, tuple[PricePoint, ...]], dict[str, tuple[PricePoint, ...]]]:
        adjusted_buckets: dict[str, list[PricePoint]] = {}
        raw_buckets: dict[str, list[PricePoint]] = {}
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
                volume = _float(raw.get("volume"))
                shares = _float(raw.get("shares_outstanding"))
                source = f"CSV:{self.prices_csv.name}"
                raw_buckets.setdefault(security_id, []).append(
                    PricePoint(
                        security_id=security_id,
                        symbol=symbol,
                        date=dt,
                        close=close,
                        adjusted=False,
                        volume=volume,
                        shares_outstanding=shares,
                        source=source,
                    )
                )
                adjusted_buckets.setdefault(security_id, []).append(
                    PricePoint(
                        security_id=security_id,
                        symbol=symbol,
                        date=dt,
                        close=(adjusted_close if adjusted_close is not None else close),
                        adjusted=(adjusted_close is not None),
                        volume=volume,
                        shares_outstanding=shares,
                        source=source,
                    )
                )
        sort = lambda buckets: {
            security_id: tuple(sorted(points, key=lambda point: point.date))
            for security_id, points in buckets.items()
        }
        return sort(adjusted_buckets), sort(raw_buckets)

    def universe(self, as_of: date) -> tuple[SecurityIdentity, ...]:
        return tuple(
            security
            for security in self._securities
            if (security.start_date is None or security.start_date <= as_of)
            and (security.end_date is None or security.end_date >= as_of)
        )

    def price_on_or_before(self, security: SecurityIdentity, as_of: date) -> PricePoint | None:
        eligible = [
            point
            for point in self._raw_prices.get(security.security_id, ())
            if point.date <= as_of
        ]
        return max(eligible, key=lambda point: point.date) if eligible else None

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