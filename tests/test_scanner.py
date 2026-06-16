"""Scanner filter tests: same-day, in-band, spread, and liquidity."""
from datetime import datetime
from typing import Dict, List

from src.marketdata import YesTop
from src.models import Market
from src.scanner import Scanner


def local_time_today(hour: int) -> float:
    return datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()


class FakeDS:
    """A market-data source backed by fixed markets + books."""

    def __init__(self, markets: List[Market], books: Dict[str, YesTop]):
        self._markets = markets
        self._books = books

    def list_candidate_markets(self, now: float, max_close_ts: int) -> List[Market]:
        # Mimic the live source: only return markets inside the close-time window.
        return [m for m in self._markets if now < m.close_ts <= max_close_ts]

    def orderbook_top(self, ticker: str) -> YesTop:
        return self._books.get(ticker, (None, None, 0, 0))

    def settlement_value_cents(self, ticker, now):
        return None


def mk(ticker, close_ts, bid=97, ask=98):
    return Market(ticker, "open", int(close_ts), bid, ask, 0, 0)


STRAT = {
    "max_hours_to_close": 8,
    "entry_min_cents": 96,
    "entry_max_cents": 99,
    "max_entry_spread_cents": 2,
    "min_orderbook_depth_contracts": 50,
}


def test_same_day_filter_excludes_tomorrow():
    now = local_time_today(20)  # 8pm: an 8h window crosses midnight
    same_day = mk("TODAY", now + 1 * 3600)        # 9pm today
    tomorrow = mk("TMRW", now + 6 * 3600)         # 2am next day -- in window, NOT today
    books = {
        "TODAY": (97, 98, 300, 300),
        "TMRW": (97, 98, 300, 300),
    }
    scanner = Scanner(STRAT, now_fn=lambda: now)
    passing = scanner.scan(FakeDS([same_day, tomorrow], books))
    tickers = [m.ticker for m in passing]
    assert "TODAY" in tickers
    assert "TMRW" not in tickers


def test_out_of_band_excluded():
    now = local_time_today(12)
    low = mk("LOW", now + 3600)
    high = mk("HIGH", now + 3600)
    good = mk("GOOD", now + 3600)
    books = {
        "LOW": (90, 91, 300, 300),     # ask 91 < entry_min
        "HIGH": (99, 100, 300, 300),   # ask 100 > entry_max
        "GOOD": (97, 98, 300, 300),
    }
    scanner = Scanner(STRAT, now_fn=lambda: now)
    passing = [m.ticker for m in scanner.scan(FakeDS([low, high, good], books))]
    assert passing == ["GOOD"]


def test_wide_spread_excluded():
    now = local_time_today(12)
    wide = mk("WIDE", now + 3600)
    tight = mk("TIGHT", now + 3600)
    books = {
        "WIDE": (95, 99, 300, 300),    # spread 4 > 2
        "TIGHT": (97, 98, 300, 300),   # spread 1
    }
    scanner = Scanner(STRAT, now_fn=lambda: now)
    passing = [m.ticker for m in scanner.scan(FakeDS([wide, tight], books))]
    assert passing == ["TIGHT"]


def test_thin_depth_excluded():
    now = local_time_today(12)
    thin = mk("THIN", now + 3600)
    deep = mk("DEEP", now + 3600)
    books = {
        "THIN": (97, 98, 300, 10),     # ask depth 10 < 50
        "DEEP": (97, 98, 300, 300),
    }
    scanner = Scanner(STRAT, now_fn=lambda: now)
    passing = [m.ticker for m in scanner.scan(FakeDS([thin, deep], books))]
    assert passing == ["DEEP"]


def test_beyond_window_excluded():
    now = local_time_today(8)
    inside = mk("IN", now + 4 * 3600)
    outside = mk("OUT", now + 10 * 3600)   # beyond max_hours_to_close=8
    books = {"IN": (97, 98, 300, 300), "OUT": (97, 98, 300, 300)}
    scanner = Scanner(STRAT, now_fn=lambda: now)
    passing = [m.ticker for m in scanner.scan(FakeDS([inside, outside], books))]
    assert passing == ["IN"]
