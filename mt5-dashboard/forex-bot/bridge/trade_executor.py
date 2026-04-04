
import logging
import time
from typing import Optional

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

logger = logging.getLogger(__name__)

BOT_MAGIC = 20250101   # Unique identifier for bot orders


# ── Open market order ──────────────────────────────────────────────────────
def send_order(
    symbol:     str,
    direction:  str,      # "buy" or "sell"
    lot_size:   float,
    sl_price:   float,
    tp_price:   float,    # TP2 (full target)
    comment:    str = "BOT",
    magic:      int = BOT_MAGIC,
    max_retry:  int = 3,
) -> dict:
    """
    Send a market order. Retries up to max_retry times on requote/busy.
    Returns result dict with ticket number.
    """
    if not MT5_AVAILABLE:
        logger.warning(f"DEMO: Would send {direction.upper()} {lot_size} {symbol} "
                       f"SL={sl_price} TP={tp_price}")
        return {"ticket": int(time.time()), "retcode": 10009,
                "symbol": symbol, "volume": lot_size,
                "price": 1.10000, "sl": sl_price, "tp": tp_price}

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise ValueError(f"No tick data for {symbol}: {mt5.last_error()}")

    order_type  = mt5.ORDER_TYPE_BUY  if direction == "buy"  else mt5.ORDER_TYPE_SELL
    entry_price = tick.ask             if direction == "buy"  else tick.bid

    filling_modes = [
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_RETURN,
    ]
    info = mt5.symbol_info(symbol)
    filling = filling_modes[0]
    if info and info.filling_mode == 1:
        filling = mt5.ORDER_FILLING_FOK
    elif info and info.filling_mode == 2:
        filling = mt5.ORDER_FILLING_RETURN

    request = {
        "action":      mt5.TRADE_ACTION_DEAL,
        "symbol":      symbol,
        "volume":      round(float(lot_size), 2),
        "type":        order_type,
        "price":       entry_price,
        "sl":          sl_price,
        "tp":          tp_price,
        "deviation":   20,
        "magic":       magic,
        "comment":     comment[:31],
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }

    for attempt in range(1, max_retry + 1):
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"order_send returned None: {mt5.last_error()}")

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                f"Order FILLED | Ticket={result.order} | "
                f"{direction.upper()} {lot_size} {symbol} @ {result.price} | "
                f"SL={sl_price} TP={tp_price}"
            )
            return {
                "ticket":  result.order,
                "retcode": result.retcode,
                "symbol":  symbol,
                "volume":  lot_size,
                "price":   result.price,
                "sl":      sl_price,
                "tp":      tp_price,
            }

        # Requote or busy — update price and retry
        retryable = {mt5.TRADE_RETCODE_REQUOTE,
                     mt5.TRADE_RETCODE_PRICE_OFF,
                     mt5.TRADE_RETCODE_PRICE_CHANGED,
                     mt5.TRADE_RETCODE_TIMEOUT}
        if result.retcode in retryable and attempt < max_retry:
            tick = mt5.symbol_info_tick(symbol)
            request["price"] = tick.ask if direction == "buy" else tick.bid
            logger.warning(f"Retrying order (attempt {attempt}): retcode={result.retcode}")
            time.sleep(0.5)
            continue

        raise RuntimeError(
            f"Order FAILED: retcode={result.retcode} | "
            f"comment={result.comment}"
        )

    raise RuntimeError("Order failed after max retries")


# ── Modify position ────────────────────────────────────────────────────────
def modify_position(ticket: int, sl: float, tp: float) -> bool:
    """Modify SL and/or TP on an open position."""
    if not MT5_AVAILABLE:
        logger.warning(f"DEMO: Would modify ticket {ticket} SL={sl} TP={tp}")
        return True

    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl":       sl,
        "tp":       tp,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"Modified ticket {ticket}: SL={sl} TP={tp}")
        return True
    logger.error(f"Modify failed for {ticket}: {result.retcode if result else 'None'}")
    return False


def move_to_breakeven(ticket: int, open_price: float,
                      tp: float, buffer_pts: float = 0.0) -> bool:
    """Move SL to break-even (open price + small buffer)."""
    be_sl = round(open_price + buffer_pts, 5)
    return modify_position(ticket, be_sl, tp)


# ── Partial close ──────────────────────────────────────────────────────────
def partial_close(ticket: int, close_volume: float,
                  symbol: str, direction: str) -> dict:
    """
    Close a portion of an open position.
    direction: "buy" or "sell" (original trade direction).
    """
    if not MT5_AVAILABLE:
        logger.warning(f"DEMO: Would partially close {close_volume} of ticket {ticket}")
        return {"ticket": ticket, "closed_volume": close_volume}

    tick = mt5.symbol_info_tick(symbol)
    # To close a BUY, we sell; to close a SELL, we buy
    close_type  = mt5.ORDER_TYPE_SELL if direction == "buy" else mt5.ORDER_TYPE_BUY
    close_price = tick.bid            if direction == "buy" else tick.ask

    request = {
        "action":      mt5.TRADE_ACTION_DEAL,
        "position":    ticket,
        "symbol":      symbol,
        "volume":      round(float(close_volume), 2),
        "type":        close_type,
        "price":       close_price,
        "deviation":   20,
        "magic":       BOT_MAGIC,
        "comment":     "BOT_TP1",
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"Partial close: {close_volume} lots of ticket {ticket}")
        return {"ticket": ticket, "closed_volume": close_volume,
                "price": result.price}
    raise RuntimeError(f"Partial close failed: {result.retcode if result else 'None'}")


def close_position(ticket: int, symbol: str, direction: str,
                   volume: float) -> dict:
    """Fully close an open position."""
    return partial_close(ticket, volume, symbol, direction)


# ── Trailing stop logic ────────────────────────────────────────────────────
class TrailingStopManager:
    """
    Manages ATR-based trailing stop for open positions.
    Call update() on every new bar.
    """

    def __init__(self, ticket: int, symbol: str, direction: str,
                 initial_sl: float, atr_multiplier: float = 2.0):
        self.ticket       = ticket
        self.symbol       = symbol
        self.direction    = direction
        self.current_sl   = initial_sl
        self.atr_mult     = atr_multiplier
        self.tp_remaining = None  # set after TP1 close

    def update(self, current_price: float, atr: float, tp: float) -> bool:
        """
        Returns True if SL was modified.
        Trail stop by 2x ATR below/above running high/low.
        """
        if self.direction == "buy":
            new_sl = round(current_price - atr * self.atr_mult, 5)
            if new_sl > self.current_sl:
                success = modify_position(self.ticket, new_sl, tp)
                if success:
                    self.current_sl = new_sl
                    return True
        else:
            new_sl = round(current_price + atr * self.atr_mult, 5)
            if new_sl < self.current_sl:
                success = modify_position(self.ticket, new_sl, tp)
                if success:
                    self.current_sl = new_sl
                    return True
        return False