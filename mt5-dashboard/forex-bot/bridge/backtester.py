"""
Backtester — runs the full strategy signal + risk rules over historical OHLCV data.
Uses the same C++ signal engine as the live bot (via signal_bridge).
Outputs trade log, equity curve, and all Section 8 performance metrics.

Usage:
    python bridge/backtester.py --symbol EURUSD --tf H1 --bars 5000
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import List

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.signal_bridge import get_signal
from bridge.mt5_connector  import get_ohlcv, connect, disconnect

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")


# ── Trade record ───────────────────────────────────────────────────────────
class BacktestTrade:
    def __init__(self, bar_idx: int, symbol: str, direction: int,
                 entry: float, sl: float, tp1: float, tp2: float,
                 lot: float, tss: int, atr_zone: str):
        self.bar_idx   = bar_idx
        self.symbol    = symbol
        self.direction = direction   # 1 buy, -1 sell
        self.entry     = entry
        self.sl        = sl
        self.tp1       = tp1
        self.tp2       = tp2
        self.lot       = lot
        self.tss       = tss
        self.atr_zone  = atr_zone
        self.exit_price = None
        self.exit_bar   = None
        self.profit_r   = None
        self.exit_reason = None
        self.tp1_hit    = False

    @property
    def sl_dist(self):
        return abs(self.entry - self.sl)

    def r_multiple(self, price: float) -> float:
        dist = (price - self.entry) * self.direction
        return dist / self.sl_dist if self.sl_dist > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "bar_idx":    self.bar_idx,
            "direction":  "BUY" if self.direction == 1 else "SELL",
            "entry":      round(self.entry, 5),
            "sl":         round(self.sl,    5),
            "tp1":        round(self.tp1,   5),
            "tp2":        round(self.tp2,   5),
            "exit_price": round(self.exit_price, 5) if self.exit_price else None,
            "exit_bar":   self.exit_bar,
            "profit_r":   round(self.profit_r, 3) if self.profit_r else None,
            "exit_reason":self.exit_reason,
            "tss":        self.tss,
            "atr_zone":   self.atr_zone,
            "tp1_hit":    self.tp1_hit,
        }


# ── Backtester ─────────────────────────────────────────────────────────────
class Backtester:
    """
    Walk-forward backtester.
    Signals are generated bar-by-bar using the C++ engine.
    Exits are simulated on subsequent bars using high/low.
    """

    def __init__(self, symbol: str, risk_pct: float = 0.01,
                 sl_atr_mult: float = 1.5, tp1_rr: float = 1.5,
                 tp2_rr: float = 3.0, min_tss: int = 3,
                 spread_pips: float = 1.5, initial_balance: float = 10_000.0):
        self.symbol          = symbol
        self.risk_pct        = risk_pct
        self.sl_atr_mult     = sl_atr_mult
        self.tp1_rr          = tp1_rr
        self.tp2_rr          = tp2_rr
        self.min_tss         = min_tss
        self.spread          = spread_pips * 0.0001
        self.initial_balance = initial_balance
        self.balance         = initial_balance
        self.trades: List[BacktestTrade] = []
        self.equity_curve: List[float]   = []

    def run(self, df: pd.DataFrame, warmup: int = 210) -> dict:
        """
        Run backtest over the DataFrame.
        warmup: number of bars to skip before generating signals (C++ needs history).
        """
        logger.info(f"Backtesting {self.symbol} | {len(df)} bars | warmup={warmup}")

        opens  = df["open"].values
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values

        open_trade: BacktestTrade = None

        for i in range(warmup, len(df)):
            bar_df = df.iloc[:i + 1].copy()

            # ── Manage open trade ─────────────────────────────────────────
            if open_trade is not None:
                h, l = highs[i], lows[i]
                d = open_trade.direction

                # Check SL hit
                sl_hit = (d == 1 and l <= open_trade.sl) or \
                         (d == -1 and h >= open_trade.sl)

                # Check TP1 hit
                tp1_hit = not open_trade.tp1_hit and (
                    (d == 1  and h >= open_trade.tp1) or
                    (d == -1 and l <= open_trade.tp1)
                )

                # Check TP2 hit
                tp2_hit = (d == 1 and h >= open_trade.tp2) or \
                          (d == -1 and l <= open_trade.tp2)

                if sl_hit:
                    exit_price = open_trade.sl
                    r = open_trade.r_multiple(exit_price)
                    if open_trade.tp1_hit:
                        # Already took 50% at TP1, remaining exits at SL (break-even)
                        r = 0.0  # SL moved to break-even
                    self._close_trade(open_trade, exit_price, r, i, "SL")
                    open_trade = None

                elif tp2_hit:
                    exit_price = open_trade.tp2
                    r = open_trade.r_multiple(exit_price)
                    if open_trade.tp1_hit:
                        r = (r * 0.5) + (self.tp1_rr * 0.5)  # blended
                    self._close_trade(open_trade, exit_price, r, i, "TP2")
                    open_trade = None

                elif tp1_hit:
                    open_trade.tp1_hit = True
                    # Partial close: move SL to break-even
                    open_trade.sl = open_trade.entry
                    logger.debug(f"TP1 hit bar {i} — SL moved to BE")

                continue  # don't look for new trades while one is open

            # ── Generate signal ───────────────────────────────────────────
            try:
                sig = get_signal(bar_df)
            except Exception as e:
                logger.debug(f"Signal error at bar {i}: {e}")
                continue

            if sig["direction"] == 0:
                continue
            if sig["tss_score"] < self.min_tss:
                continue
            if sig["atr_zone"] == "extreme":
                continue

            # ── Entry ──────────────────────────────────────────────────────
            direction = sig["direction"]
            entry     = closes[i] + (self.spread if direction == 1 else -self.spread)
            atr       = sig["atr"]
            sl_dist   = atr * self.sl_atr_mult
            tp1_dist  = sl_dist * self.tp1_rr
            tp2_dist  = sl_dist * self.tp2_rr

            if direction == 1:
                sl  = entry - sl_dist
                tp1 = entry + tp1_dist
                tp2 = entry + tp2_dist
            else:
                sl  = entry + sl_dist
                tp1 = entry - tp1_dist
                tp2 = entry - tp2_dist

            # Lot size for stats (fixed fractional)
            lot = (self.balance * self.risk_pct) / (sl_dist * 10_000)
            lot = max(0.01, round(lot, 2))

            open_trade = BacktestTrade(
                bar_idx  = i,
                symbol   = self.symbol,
                direction= direction,
                entry    = entry,
                sl       = sl,
                tp1      = tp1,
                tp2      = tp2,
                lot      = lot,
                tss      = sig["tss_score"],
                atr_zone = sig["atr_zone"],
            )

            self.equity_curve.append(self.balance)

        # Close any trade still open at end of data
        if open_trade is not None:
            last_close = closes[-1]
            r = open_trade.r_multiple(last_close)
            self._close_trade(open_trade, last_close, r, len(df) - 1, "EOD")

        return self._build_report()

    def _close_trade(self, trade: BacktestTrade, price: float,
                     r: float, bar_idx: int, reason: str):
        trade.exit_price  = price
        trade.exit_bar    = bar_idx
        trade.profit_r    = r
        trade.exit_reason = reason

        # Update balance using fixed $ risk
        pnl = self.balance * self.risk_pct * r
        self.balance += pnl
        self.equity_curve.append(self.balance)
        self.trades.append(trade)

        logger.debug(
            f"Trade closed: {reason} | R={r:.2f} | "
            f"Balance=${self.balance:.2f}"
        )

    def _build_report(self) -> dict:
        if not self.trades:
            return {"error": "No trades generated", "symbol": self.symbol}

        rs      = [t.profit_r for t in self.trades if t.profit_r is not None]
        wins    = [r for r in rs if r > 0]
        losses  = [r for r in rs if r <= 0]

        win_rate   = len(wins) / len(rs) if rs else 0
        avg_win    = np.mean(wins)   if wins   else 0
        avg_loss   = np.mean(losses) if losses else 0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * abs(avg_loss))

        # Max drawdown on equity curve
        eq    = np.array(self.equity_curve)
        peak  = np.maximum.accumulate(eq)
        dd    = (peak - eq) / peak
        max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0

        # Profit factor
        gross_profit = sum(r for r in rs if r > 0) * self.initial_balance * self.risk_pct
        gross_loss   = abs(sum(r for r in rs if r < 0)) * self.initial_balance * self.risk_pct
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # SL/TP breakdown
        sl_hits  = sum(1 for t in self.trades if t.exit_reason == "SL")
        tp1_hits = sum(1 for t in self.trades if t.tp1_hit)
        tp2_hits = sum(1 for t in self.trades if t.exit_reason == "TP2")

        report = {
            "symbol":         self.symbol,
            "total_trades":   len(self.trades),
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate_pct":   round(win_rate * 100, 1),
            "avg_win_r":      round(avg_win,  3),
            "avg_loss_r":     round(avg_loss, 3),
            "expectancy_r":   round(expectancy, 3),
            "profit_factor":  round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "final_balance":  round(self.balance, 2),
            "net_return_pct": round((self.balance - self.initial_balance) / self.initial_balance * 100, 2),
            "sl_hits":        sl_hits,
            "tp1_hits":       tp1_hits,
            "tp2_hits":       tp2_hits,
            "trades":         [t.to_dict() for t in self.trades],
            "equity_curve":   [round(e, 2) for e in self.equity_curve],
        }

        # Print summary
        print("\n" + "=" * 55)
        print(f"  BACKTEST RESULTS — {self.symbol}")
        print("=" * 55)
        print(f"  Total trades:    {report['total_trades']}")
        print(f"  Win rate:        {report['win_rate_pct']}%  (target >45%)")
        print(f"  Avg win R:       {report['avg_win_r']}R")
        print(f"  Avg loss R:      {report['avg_loss_r']}R")
        print(f"  Expectancy:      {report['expectancy_r']}R  (must be >0)")
        print(f"  Profit factor:   {report['profit_factor']}  (target >1.5)")
        print(f"  Max drawdown:    {report['max_drawdown_pct']}%  (limit 5%)")
        print(f"  Net return:      {report['net_return_pct']}%")
        print(f"  Final balance:   ${report['final_balance']}")
        print(f"  SL hits:         {sl_hits}")
        print(f"  TP1 hits:        {tp1_hits}")
        print(f"  TP2 hits:        {tp2_hits}")
        print("=" * 55 + "\n")

        return report


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forex Bot Backtester")
    parser.add_argument("--symbol",  default="EURUSD",  help="MT5 symbol")
    parser.add_argument("--tf",      default="H1",      help="Timeframe (H1, H4, D1)")
    parser.add_argument("--bars",    type=int, default=2000, help="Number of bars")
    parser.add_argument("--balance", type=float, default=10000.0, help="Starting balance")
    parser.add_argument("--risk",    type=float, default=0.01, help="Risk per trade (0.01=1%)")
    parser.add_argument("--output",  default=None, help="Save results to CSV path")
    parser.add_argument("--connect", action="store_true", help="Connect to MT5 (Windows only)")
    args = parser.parse_args()

    if args.connect:
        connect()

    logger.info(f"Fetching {args.bars} bars of {args.symbol} {args.tf}...")
    df = get_ohlcv(args.symbol, args.tf, args.bars)
    logger.info(f"Data: {df['time'].iloc[0]} → {df['time'].iloc[-1]}")

    bt     = Backtester(args.symbol, risk_pct=args.risk,
                        initial_balance=args.balance)
    report = bt.run(df)

    if args.output and "trades" in report:
        trades_df = pd.DataFrame(report["trades"])
        trades_df.to_csv(args.output, index=False)
        logger.info(f"Trade log saved to {args.output}")

    if args.connect:
        disconnect()