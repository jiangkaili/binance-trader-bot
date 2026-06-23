"""`python -m trader` entry point — argparse + env loading + Trader.run()."""
from __future__ import annotations

import argparse
import os
import sys

from .config import RuntimeContext, TraderConfig, load_env_file
from .trader import Trader


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m trader")
    p.add_argument("--dry-run", action="store_true",
                   help="don't place real orders, just log signals")
    p.add_argument("--env-file", default=os.getenv("ENV_FILE", ".env.testnet"))
    args = p.parse_args()

    load_env_file(args.env_file)

    try:
        ctx = RuntimeContext.from_env(dry_run=args.dry_run)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    cfg = TraderConfig.from_yaml()

    print(f"Mode : {'DRY-RUN' if ctx.dry_run else 'LIVE'}")
    print(f"Base : {ctx.base_url}")
    print(f"Env  : {args.env_file}")
    print(f"Key  : ...{ctx.api_key[-4:]}  (redacted)")
    print()

    if not ctx.dry_run:
        print("=" * 60)
        print("LIVE MODE — REAL MONEY AT RISK")
        print(f"  - Stop-loss -{cfg.stop_loss_pct*100:.1f}% / Take-profit +{cfg.take_profit_pct*100:.1f}% per trade")
        print(f"  - Daily cap  -{cfg.daily_loss_pct*100:.0f}% of starting equity")
        print(f"  - Weekly cap -{cfg.weekly_loss_pct*100:.0f}% of starting equity")
        print(f"  - Single position, {cfg.symbol} only, {cfg.target_position_usdt} USDT, {cfg.leverage}x leverage")
        print("  - To stop gracefully:  kill -TERM <pid>")
        print("=" * 60)
        print()

    Trader(ctx, cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
