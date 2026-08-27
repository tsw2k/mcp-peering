#!/usr/bin/env python3
"""Live smoke test for mcp-peering against the real APIs.

Run from a machine with normal Internet access (and, optionally, access to
your Peering Manager instance):

    python scripts/live_smoke.py

PeeringDB checks always run. Peering Manager checks run only when
PEERING_MANAGER_URL and PEERING_MANAGER_TOKEN are set; only read requests
are issued, so a read-only token is enough. Exits non-zero if any check
fails.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_peering.config import load_config  # noqa: E402
from mcp_peering.peering_manager import PeeringManagerClient  # noqa: E402
from mcp_peering.peeringdb import PeeringDBClient  # noqa: E402

OK = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def report(self, name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{OK if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.failed += 1


async def check_peeringdb(checks: Checks) -> None:
    print("PeeringDB:")
    cfg = load_config()
    client = PeeringDBClient(cfg.peeringdb, timeout=cfg.http_timeout)
    try:
        net = await client.get_network_by_asn(15169)
        checks.report(
            "lookup AS15169",
            bool(net and net.get("asn") == 15169),
            (net or {}).get("name", "not found"),
        )

        t0 = time.monotonic()
        await client.get_network_by_asn(15169)
        cached_ms = (time.monotonic() - t0) * 1000
        checks.report("cache hit on repeat", cached_ms < 50, f"{cached_ms:.1f} ms")

        ix = await client.list("ix", filters={"name__contains": "DE-CIX"}, limit=3)
        checks.report("IX name search", len(ix) > 0, ", ".join(i["name"] for i in ix[:3]))

        # DE-CIX Frankfurt (ix_id=31) has far more than one 250-row page.
        members = await client.list_all("netixlan", filters={"ix_id": 31}, max_results=600)
        checks.report("auto-pagination past 250 rows", len(members) > 250, f"{len(members)} rows")
    finally:
        await client.aclose()


async def check_peering_manager(checks: Checks) -> None:
    cfg = load_config()
    if not cfg.peering_manager.is_configured:
        print("Peering Manager: skipped (set PEERING_MANAGER_URL and PEERING_MANAGER_TOKEN)")
        return
    print(f"Peering Manager ({cfg.peering_manager.base_url}):")
    client = PeeringManagerClient(cfg.peering_manager, timeout=cfg.http_timeout)
    try:
        status = await client.status()
        checks.report(
            "GET /api/status/",
            isinstance(status, dict) and bool(status),
            f"version {status.get('peering-manager-version', '?')}",
        )

        data = await client.list("autonomous-systems", limit=5)
        checks.report(
            "list autonomous systems",
            isinstance(data.get("results"), list),
            f"{data.get('count', 0)} total",
        )

        data = await client.list("internet-exchanges", limit=5)
        checks.report(
            "list internet exchanges",
            isinstance(data.get("results"), list),
            f"{data.get('count', 0)} total",
        )
    finally:
        await client.aclose()


async def main() -> int:
    checks = Checks()
    try:
        await check_peeringdb(checks)
    except Exception as exc:  # noqa: BLE001 - a smoke test must not die mid-run
        checks.report("PeeringDB reachable", False, str(exc))
    try:
        await check_peering_manager(checks)
    except Exception as exc:  # noqa: BLE001
        checks.report("Peering Manager reachable", False, str(exc))
    print()
    if checks.failed:
        print(f"{checks.failed} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
