#!/usr/bin/env python3
"""Confirm IG CFD epics from the diversified-trend registry.

Hits GET /markets/{epic} for each market (demo keys). Prints instrument name,
expiry, point value, and bid/offer snapshot for an indicative spread.

Usage:
    uv run python scripts/sniff_ig_diversified_epics.py
    uv run python scripts/sniff_ig_diversified_epics.py --only futures_cfd
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from trading_platform.config.settings import Settings
from trading_platform.exchanges.ig.client import DEMO_BASE_URL, IgRestClient


def _load_registry(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        default="config/research/ig-diversified-trend.yaml",
        type=Path,
    )
    parser.add_argument(
        "--only",
        choices=("all", "provisional", "confirmed", "futures_cfd", "cash_cfd"),
        default="all",
    )
    args = parser.parse_args()
    reg = _load_registry(args.registry)
    markets = list(reg.get("markets") or [])
    if args.only in ("provisional", "confirmed"):
        markets = [m for m in markets if m.get("status") == args.only]
    elif args.only in ("futures_cfd", "cash_cfd"):
        markets = [m for m in markets if m.get("product") == args.only]

    settings = Settings()
    key = settings.ig_demo_api_key
    user = settings.ig_demo_username
    password = settings.ig_demo_password
    if not key or not user or not password:
        raise SystemExit("Need IG_DEMO_API_KEY / USERNAME / PASSWORD in .env")

    client = IgRestClient(
        base_url=DEMO_BASE_URL,
        api_key=key,
        username=user,
        password=password,
        account_id=settings.ig_demo_account_id,
    )
    print(f"{'id':12} {'prod':12} {'epic':32} {'ok':3} {'v1pip':>8} {'spr~':>10} {'exp':8} notes")
    print("-" * 120)
    try:
        for m in markets:
            epic = str(m["epic"])
            mid = str(m["id"])
            product = str(m.get("product", "?"))
            try:
                raw = client.request("GET", f"/markets/{epic}", version="3")
                if not isinstance(raw, dict):
                    raise RuntimeError(f"non-object payload: {type(raw)}")
                instrument = raw.get("instrument") or {}
                snapshot = raw.get("snapshot") or {}
                bid = snapshot.get("bid")
                offer = snapshot.get("offer")
                spread_s = "n/a"
                if bid is not None and offer is not None and float(bid) != 0:
                    spread_bps = (float(offer) - float(bid)) / float(bid) * 10_000.0
                    spread_s = f"{spread_bps:.2f}bps"
                expiry = instrument.get("expiry") or ""
                name = instrument.get("name") or ""
                v1 = instrument.get("valueOfOnePip")
                flag = "Y"
                note = f"{name}"
                if str(expiry).upper() == "DFB":
                    flag = "!"
                    note += " ← DFB (avoid for CFD demo)"
                cfg_pv = m.get("point_value")
                if cfg_pv is not None and v1 is not None:
                    try:
                        if abs(float(v1) - float(cfg_pv)) > 1e-6:
                            note += f" ← point_value cfg={cfg_pv} live={v1}"
                    except (TypeError, ValueError):
                        pass
                print(
                    f"{mid:12} {product:12} {epic:32} {flag:3} {str(v1):>8} "
                    f"{spread_s:10} {str(expiry):8} {note}"
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"{mid:12} {product:12} {epic:32} {'N':3} {'FAIL':>8} "
                    f"{'FAIL':10} {'':8} {exc!r}"
                )
    finally:
        client.close()


if __name__ == "__main__":
    main()
