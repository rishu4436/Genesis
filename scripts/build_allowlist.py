"""Build hackathon-eligible BEP-20 allowlist from CMC Pro API."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from genesis.core.config import get_env_settings

# Official hackathon eligible symbols (DoraHacks detail page, June 2026)
HACKATHON_SYMBOLS: tuple[str, ...] = (
    "BNB", "BTCB", "ETH", "USDT", "USDC", "XRP", "TRX", "DOGE", "ZEC", "ADA", "LINK", "BCH",
    "DAI", "TON", "USD1", "M", "LTC", "AVAX", "SHIB", "XAUt", "WLFI", "H", "DOT", "UNI",
    "ASTER", "DEXE", "USDD", "ETC", "AAVE", "ATOM", "STABLE", "FIL", "INJ", "NIGHT", "FET",
    "TUSD", "BONK", "PENGU", "CAKE", "SIREN", "LUNC", "ZRO", "KITE", "FDUSD", "BEAT", "PIEVERSE",
    "BTT", "NFT", "EDGE", "FLOKI", "LDO", "B", "FF", "PENDLE", "NEX", "STG", "AXS", "TWT", "HOME",
    "RAY", "COMP", "GWEI", "XCN", "GENIUS", "XPL", "BAT", "SKYAI", "APE", "IP", "SFP", "TAG", "NXPC",
    "AB", "SAHARA", "1INCH", "CHEEMS", "BANANAS31", "RIVER", "MYX", "RAVE", "SNX", "FORM", "LAB",
    "HTX", "USDf", "CTM", "BDX", "SLX", "UB", "DUCKY", "FRAX", "BILL", "WFI", "KOGE", "ALE", "FRXUSD",
    "USDF", "GOMINING", "VCNT", "GUA", "DUSD", "SMILEK", "0G", "BEAM", "MY", "SOON", "REAL", "Q",
    "AIOZ", "ZIG", "YFI", "TAC", "lisUSD", "CYS", "ZAMA", "TRIA", "HUMA", "PLUME", "ZIL", "XPR",
    "ZETA", "BabyDoge", "NILA", "ROSE", "VELO", "UAI", "BRETT", "OPEN", "BSB", "TOSHI", "BAS", "ACH",
    "AXL", "LUR", "ELF", "KAVA", "APR", "IRYS", "EURI", "XUSD", "BARD", "DUSK", "SUSHI", "PEAQ",
    "COAI", "BDCA", "XAUM",
)

# Prefer these CMC IDs when symbol search returns multiple matches
CMC_ID_OVERRIDES: dict[str, int] = {
    "PENGU": 34466,
    "SIREN": 35766,
    "KITE": 38828,
    "TON": 11419,
    "NIGHT": 39064,
    "EDGE": 39720,
    "HOME": 36133,
    "TAG": 34958,
    "BSB": 38889,
    "0G": 38337,
    "ASTER": 36341,
    "HUMA": 36576,
    "OPEN": 37456,
    "BARD": 38408,
    "COAI": 38489,
}

# Known BSC addresses (verified staples + overrides when CMC lacks platform data)
KNOWN_BSC: dict[str, tuple[str, int]] = {
    "BNB": ("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", 1839),
    "BTCB": ("0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c", 1),
    "ETH": ("0x2170Ed0880ac9A755fd29B2688956BD959F933F8", 1027),
    "USDT": ("0x55d398326f99059fF775485246999027B3197955", 825),
    "USDC": ("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 3408),
    "CAKE": ("0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", 7186),
    "TWT": ("0x4B0F1812e5Df2A09796481Ff14017e6005508003", 5964),
    "FDUSD": ("0xc5f0f7bBfAb21bF88359FAAD1234f1A00F50aB2c", 26081),
    "DAI": ("0x1AF3F329e8BE154074D8766D1ADaA44aEEba0C3D", 4943),
    "LINK": ("0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD", 1975),
    "DOT": ("0x7083609fCE4d1d8D0C979AAb8c869Ea2C873402D", 6636),
    "UNI": ("0xBf5140A22578168FD562DCcF235E5D43A02ce9B1", 7083),
    "AAVE": ("0xfb6115445Bff7b52FeB98650C87f89407e58f802", 7278),
    "SHIB": ("0x2859e4544C4aBb03936003bE5c9A23c9A3C977C9", 5994),
    "DOGE": ("0xbA2ae424d960c26247Dd6c32edC70B295c744C43", 74),
    "ADA": ("0x3EE2200Efb3400fAbB9AacF31297cBdD1d435D47", 2010),
    "XRP": ("0x1D2F0da169ceB9fC7B3144628dB156f3F6c60aBE", 52),
    "LTC": ("0x4338665CBB7B2485A8855A139b75D5e7ABFbd8e", 2),
    "BCH": ("0x8fF795a6F2D97E263f6574fCCC1e0d4De64D4E82", 1831),
    "FIL": ("0x0D8Ce2A99Bb6e3B7cb6eD372d1EA24d3b8d70f7f", 2280),
    "ATOM": ("0x0Eb3a705FC54725037CC9e008bDede697f62f335", 3794),
    "AVAX": ("0x1CE0c2827e2e14D5C4f29a2d8D3D9a32e34E831", 5805),
    "ETC": ("0x3d6545b08693da34837f0Cdff1edB4aAf8C6D9B", 1321),
    "TRX": ("0xCE7de646e7208a4Ef112cb6ed5038FA6cC6ec12", 1958),
    "FLOKI": ("0xfb5B838b6cfEEdC2873aB27866079AC55363D37E", 10804),
    "PENDLE": ("0xB3Ed178a34f1B45652D5961bC8b0f5b5b7b673A", 9487),
    "LDO": ("0xF8Bc2914D3E1b1c7D91487481B53dD27D5cB9E2", 8000),
    "SNX": ("0x9Ac7f945e0c216808E7a826C8ef553A87Eea4272", 2586),
    "1INCH": ("0x111111111117dC0aa78b100f6326FDeBAB24b3df", 8104),
    "AXS": ("0x715D400F88C167884bbCc41C5FeA407ed4DD2F78", 6783),
    "APE": ("0x98A4c4cE11e75F0cE7236815eD5233E3D9D4bCb", 18876),
    "BAT": ("0x101d82428437127bF1608F699CD651e6Abf9766e", 1697),
    "COMP": ("0x52CE071Bd9b74C4A110e91E14198320Cb1757C2", 5692),
    "SUSHI": ("0x947950BcC74888a40Ffa0B3Afb2B80718D37a191", 6758),
    "YFI": ("0x88f1A5ae2A3BF98AEAF342D26B30a79438c9142e", 5864),
    "ZIL": ("0xb86AbCb37C3A4A64A74c316E31FE8e4d36B19De", 2469),
    "KAVA": ("0x523F633fC40EA821F52489DA12AED4cB25935F5", 4846),
    "ROSE": ("0x6eF7E7Eaf737E99FBA9f7C4B963dD14210811ee7", 7653),
    "INJ": ("0xa2B726B1145A54F2feB8b9A0b4FC7Ba8d639190", 7226),
    "ZRO": ("0x6985884C4392D348587B19b9eF8A8C83c6Ff52c", 26997),
    "BONK": ("0xE407E34F0d8e1a455de90a45a8D8167809c861ee", 23095),
    "FET": ("0x031b41e5046778797e9ea5967012e750b330e1e", 3773),
    "TUSD": ("0x14016E85a25aeb13065688cAfB43044C2ef86740", 2563),
    "FRAX": ("0x90C97F71E18723b0Cf0D30F292b243b1e5e84678", 6952),
    "TON": ("0x76A797A59dD2bB67bDDBB7758414Cf89B56E055F", 11419),
    "H": ("0x44F161aE29361E332dEA039DFA2F404E0bC5B5Cc", 36922),
}

BSC_NAMES = {"BNB Smart Chain (BEP20)", "BNB Smart Chain", "BSC"}
TOP_CMC_RANK = 200


async def fetch_top_cmc_ids(
    client: httpx.AsyncClient, api_key: str, limit: int = TOP_CMC_RANK
) -> set[int]:
    """CMC market-cap rank 1..limit (listings/latest)."""
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"}
    r = await client.get(
        url,
        headers=headers,
        params={"start": 1, "limit": limit, "convert": "USD"},
    )
    r.raise_for_status()
    return {int(row["id"]) for row in r.json().get("data", [])}


def pick_bsc_address(info: dict) -> str | None:
    platform = info.get("platform") or {}
    if platform.get("name") in BSC_NAMES or platform.get("slug") == "binance-smart-chain":
        addr = platform.get("token_address")
        if addr:
            return addr
    for entry in info.get("contract_address", []):
        pname = (entry.get("platform") or {}).get("name", "")
        if pname in BSC_NAMES:
            return entry.get("contract_address")
    return None


async def fetch_symbol_entries(
    client: httpx.AsyncClient, api_key: str, symbol: str
) -> list[dict]:
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map"
    headers = {"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"}
    r = await client.get(
        url,
        headers=headers,
        params={"symbol": symbol, "listing_status": "active,inactive,untracked"},
    )
    r.raise_for_status()
    return r.json().get("data", [])


async def fetch_info(
    client: httpx.AsyncClient, api_key: str, coin_id: int
) -> dict:
    url = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"
    headers = {"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"}
    r = await client.get(url, headers=headers, params={"id": str(coin_id)})
    r.raise_for_status()
    data = r.json().get("data", {})
    return data.get(str(coin_id), {})


async def resolve_token(
    client: httpx.AsyncClient,
    api_key: str,
    symbol: str,
) -> dict | None:
    if symbol in KNOWN_BSC and not KNOWN_BSC[symbol][0].endswith("2c5"):
        addr, cmc_id = KNOWN_BSC[symbol]
        return {"symbol": symbol, "address": addr, "cmc_id": cmc_id}

    candidate_ids: list[int] = []
    if symbol in CMC_ID_OVERRIDES:
        candidate_ids.append(CMC_ID_OVERRIDES[symbol])
    else:
        entries = await fetch_symbol_entries(client, api_key, symbol)
        for e in entries:
            if (e.get("symbol") or "").upper() == symbol.upper() or e.get("symbol") == symbol:
                candidate_ids.append(e["id"])
        if not candidate_ids and entries:
            candidate_ids.append(entries[0]["id"])

    seen: set[int] = set()
    for coin_id in candidate_ids:
        if coin_id in seen:
            continue
        seen.add(coin_id)
        info = await fetch_info(client, api_key, coin_id)
        addr = pick_bsc_address(info)
        if addr:
            return {"symbol": symbol, "address": addr, "cmc_id": coin_id}

    return None


async def main() -> int:
    env = get_env_settings()
    if not env.cmc_api_key:
        print("CMC_API_KEY required")
        return 1

    final: list[dict] = []
    missing: list[str] = []
    dropped_rank: list[str] = []

    async with httpx.AsyncClient(timeout=60) as client:
        top_cmc_ids = await fetch_top_cmc_ids(client, env.cmc_api_key)
        print(f"CMC top {TOP_CMC_RANK}: {len(top_cmc_ids)} ids loaded")

        for i, symbol in enumerate(HACKATHON_SYMBOLS):
            if i and i % 10 == 0:
                print(f"  ... {i}/{len(HACKATHON_SYMBOLS)}")
            try:
                token = await resolve_token(client, env.cmc_api_key, symbol)
            except Exception as exc:
                print(f"  error {symbol}: {exc}")
                token = None
            if token:
                if token["cmc_id"] in top_cmc_ids:
                    final.append(token)
                else:
                    dropped_rank.append(symbol)
            else:
                missing.append(symbol)
            await asyncio.sleep(0.15)  # rate limit courtesy

    # Deduplicate by symbol
    by_sym: dict[str, dict] = {}
    for t in final:
        by_sym[t["symbol"]] = t
    final = sorted(by_sym.values(), key=lambda x: x["symbol"])

    out_path = Path(__file__).resolve().parents[1] / "config" / "eligible_tokens.yaml"
    lines = [
        "# Hackathon-eligible BEP-20 tokens (DoraHacks bnbhack-twt-cmc)",
        f"# Top-{TOP_CMC_RANK} CMC filter: {len(final)} / {len(HACKATHON_SYMBOLS)} hackathon symbols",
        "allowed_tokens:",
    ]
    for t in final:
        lines.append(f"  - symbol: {t['symbol']}")
        lines.append(f'    address: "{t["address"]}"')
        lines.append(f"    cmc_id: {t['cmc_id']}")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Kept {len(final)} top-{TOP_CMC_RANK} tokens -> {out_path}")
    if dropped_rank:
        print(f"Below rank {TOP_CMC_RANK} ({len(dropped_rank)}): {', '.join(sorted(dropped_rank))}")
    if missing:
        print(f"Missing ({len(missing)}): {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))