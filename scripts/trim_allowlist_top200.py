"""Filter config/eligible_tokens.yaml to CMC top-200 market-cap rank."""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genesis.core.config import get_env_settings
from scripts.build_allowlist import TOP_CMC_RANK, fetch_top_cmc_ids

ROOT = Path(__file__).resolve().parents[1]
TOKENS = ROOT / "config" / "eligible_tokens.yaml"


def load_allowlist(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return list(data.get("allowed_tokens", []))


def write_allowlist(path: Path, tokens: list[dict]) -> None:
    lines = [
        "# Hackathon-eligible BEP-20 tokens (DoraHacks bnbhack-twt-cmc)",
        f"# Top-{TOP_CMC_RANK} CMC filter: {len(tokens)} tokens",
        "allowed_tokens:",
    ]
    for t in sorted(tokens, key=lambda x: x["symbol"]):
        lines.append(f"  - symbol: {t['symbol']}")
        lines.append(f'    address: "{t["address"]}"')
        lines.append(f"    cmc_id: {t['cmc_id']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> int:
    env = get_env_settings()
    if not env.cmc_api_key:
        print("CMC_API_KEY required")
        return 1
    if not TOKENS.exists():
        print(f"Missing {TOKENS} — run scripts/build_allowlist.py first")
        return 1

    tokens = load_allowlist(TOKENS)
    async with httpx.AsyncClient(timeout=60) as client:
        top_ids = await fetch_top_cmc_ids(client, env.cmc_api_key)

    kept = [t for t in tokens if int(t["cmc_id"]) in top_ids]
    dropped = [t["symbol"] for t in tokens if int(t["cmc_id"]) not in top_ids]

    write_allowlist(TOKENS, kept)
    print(f"Trimmed {len(tokens)} -> {len(kept)} (top {TOP_CMC_RANK} CMC)")
    if dropped:
        print(f"Dropped ({len(dropped)}): {', '.join(sorted(dropped))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))