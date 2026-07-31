# TradingAgents/agents/daily/daily_tools.py
"""Purpose-built tools for the daily trading graph.

These complement the repo's vendor-routed tools (get_stock_data,
get_indicators, get_news, ...) with fast, small-account-oriented views:
a live quote and a one-shot watchlist scan table.
"""

from typing import Annotated

from langchain_core.tools import tool


@tool
def get_realtime_quote(
    symbol: Annotated[str, "ticker symbol, e.g. AAPL"],
) -> str:
    """Get the latest price plus day range, previous close, gap % and
    volume for a symbol. Use this for entry/stop placement — daily bars
    are too stale for intraday decisions."""
    import yfinance as yf

    t = yf.Ticker(symbol)
    try:
        fi = t.fast_info
        last = fi.last_price
        prev = fi.previous_close
        day_high = fi.day_high
        day_low = fi.day_low
        volume = fi.last_volume
    except Exception as exc:
        return f"Quote unavailable for {symbol}: {exc}"

    gap = ((last - prev) / prev * 100) if prev else 0.0
    return (
        f"{symbol}: last=${last:.2f} | prev_close=${prev:.2f} | "
        f"change={gap:+.2f}% | day_range=${day_low:.2f}-${day_high:.2f} | "
        f"volume={volume:,}"
    )


@tool
def get_watchlist_snapshot(
    symbols: Annotated[str, "comma-separated tickers, e.g. 'TSLA,AMD,SOFI'"],
    trade_date: Annotated[str, "session date in yyyy-mm-dd format"],
) -> str:
    """Scan a watchlist in one call. Returns a table per symbol with:
    last close, gap % vs prior close, relative volume (vs 20-day avg),
    ATR as % of price, distance from 20-day high, and 5-day trend.
    Use this to rank candidates before doing deep analysis on one."""
    import pandas as pd
    import yfinance as yf

    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not tickers:
        return "No symbols provided."
    if len(tickers) > 30:
        tickers = tickers[:30]

    end = pd.Timestamp(trade_date) + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=60)

    rows = []
    for sym in tickers:
        try:
            df = yf.download(
                sym, start=start.date(), end=end.date(),
                progress=False, auto_adjust=True, multi_level_index=False,
            )
            if df is None or len(df) < 21:
                rows.append(f"| {sym} | insufficient history |")
                continue
            close = df["Close"]
            vol = df["Volume"]
            high = df["High"]
            low = df["Low"]

            last_close = float(close.iloc[-1])
            prior_close = float(close.iloc[-2])
            gap_pct = (last_close - prior_close) / prior_close * 100

            avg_vol_20 = float(vol.iloc[-21:-1].mean())
            rel_vol = float(vol.iloc[-1]) / avg_vol_20 if avg_vol_20 else 0.0

            tr = pd.concat(
                [
                    high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr14 = float(tr.rolling(14).mean().iloc[-1])
            atr_pct = atr14 / last_close * 100

            high_20 = float(close.iloc[-20:].max())
            dist_high = (last_close - high_20) / high_20 * 100

            trend_5d = (last_close - float(close.iloc[-6])) / float(close.iloc[-6]) * 100

            rows.append(
                f"| {sym} | ${last_close:.2f} | {gap_pct:+.2f}% | {rel_vol:.2f}x | "
                f"{atr_pct:.2f}% | {dist_high:+.2f}% | {trend_5d:+.2f}% |"
            )
        except Exception as exc:
            rows.append(f"| {sym} | error: {exc} |")

    header = (
        f"Watchlist snapshot as of {trade_date} (daily bars):\n\n"
        "| Symbol | Close | Gap | RelVol(20d) | ATR% | vs 20d High | 5d Trend |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    return header + "\n".join(rows)
