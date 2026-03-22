"""
check_symbol_info.py
Run this ONCE to print the exact volume limits MT5 reports for Crash 500.
Place it next to your other bridge scripts and run:
    python check_symbol_info.py
"""

import MetaTrader5 as mt5

SYMBOL = "Crash 500 Index"

if not mt5.initialize():
    print(f"MT5 init failed: {mt5.last_error()}")
    raise SystemExit(1)

info = mt5.symbol_info(SYMBOL)
if info is None:
    print(f"Symbol '{SYMBOL}' not found. Last error: {mt5.last_error()}")
    print("\nAvailable symbols containing 'Crash':")
    for s in mt5.symbols_get():
        if "crash" in s.name.lower() or "Crash" in s.name:
            print(f"  {s.name}")
    mt5.shutdown()
    raise SystemExit(1)

print(f"\n=== {SYMBOL} volume info ===")
print(f"  volume_min  : {info.volume_min}")
print(f"  volume_max  : {info.volume_max}")
print(f"  volume_step : {info.volume_step}")
print(f"  trade_contract_size : {info.trade_contract_size}")
print(f"  digits      : {info.digits}")
print(f"  point       : {info.point}")
print()
print(">>> Copy these values into crash500_scalper.py as the defaults")
print(f"    _VOL_MIN  = {info.volume_min}")
print(f"    _VOL_MAX  = {info.volume_max}")
print(f"    _VOL_STEP = {info.volume_step}")

mt5.shutdown()