from __future__ import annotations

from datetime import date

import pytest

from distressed_equity.alpha_vantage import AlphaVantageProvider


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        function = params["function"]
        if function == "LISTING_STATUS":
            return FakeResponse(
                "symbol,name,exchange,assetType,ipoDate,delistingDate,status\n"
                "DEAD,Later Delisted Co,NYSE,Stock,2018-01-01,2023-06-30,Active\n"
                "OPEN,Still Listed Co,NASDAQ,Stock,2019-01-01,null,Active\n"
                "ETF1,Fund,NASDAQ,ETF,2015-01-01,,Active\n"
            )
        if function == "TIME_SERIES_DAILY":
            return FakeResponse(
                "timestamp,open,high,low,close,volume\n"
                "2023-01-03,4,4,4,4,100\n"
                "2022-12-30,3,3,3,3,100\n"
            )
        if function == "TIME_SERIES_WEEKLY_ADJUSTED":
            return FakeResponse(
                "timestamp,open,high,low,close,adjusted close,volume,dividend amount\n"
                "2023-01-06,4,4,4,4,4,100,0\n"
                "2022-12-30,3,3,3,3,3,100,0\n"
                "2021-11-05,30,30,30,30,30,100,0\n"
            )
        raise AssertionError(function)


def test_alpha_vantage_historical_universe_keeps_later_delisted_stock():
    session = FakeSession()
    provider = AlphaVantageProvider(api_key="x", session=session)
    universe = provider.universe(date(2022, 12, 31))
    assert [security.symbol for security in universe] == ["DEAD", "OPEN"]
    assert universe[0].end_date == date(2023, 6, 30)
    assert universe[1].end_date is None
    listing_call = session.calls[0]
    assert listing_call["date"] == "2022-12-31"
    assert listing_call["state"] == "active"


def test_alpha_vantage_price_methods_do_not_look_ahead():
    provider = AlphaVantageProvider(api_key="x", session=FakeSession())
    security = provider.universe(date(2022, 12, 31))[0]
    raw = provider.price_on_or_before(security, date(2022, 12, 31))
    adjusted = provider.adjusted_history(
        security,
        date(2021, 1, 1),
        date(2022, 12, 31),
    )
    assert raw.date == date(2022, 12, 30)
    assert raw.close == 3.0
    assert raw.adjusted is False
    assert max(point.date for point in adjusted) == date(2022, 12, 30)
    assert all(point.adjusted for point in adjusted)


def test_alpha_vantage_rejects_pre_2010_listing_replay():
    provider = AlphaVantageProvider(api_key="x", session=FakeSession())
    with pytest.raises(ValueError, match="2010-01-01"):
        provider.universe(date(2009, 12, 31))


def test_alpha_vantage_surfaces_provider_limit_json():
    class LimitSession:
        def get(self, url, params=None, timeout=None):
            return FakeResponse('{"Information":"rate limit"}')

    provider = AlphaVantageProvider(api_key="x", session=LimitSession())
    with pytest.raises(RuntimeError, match="rate limit"):
        provider.universe(date(2022, 12, 31))
