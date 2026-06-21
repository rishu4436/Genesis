"""Tests for CMC scan token filtering."""

from genesis.core.models import RulesConfig, TokenConfig
from genesis.core.scan_tokens import tokens_to_scan
from genesis.core.wallet_tokens import is_stablecoin


def test_is_stablecoin_skips_quotes_not_bnb():
    assert is_stablecoin("USDT")
    assert is_stablecoin("USDC")
    assert is_stablecoin("FDUSD")
    assert is_stablecoin("USDe")
    assert is_stablecoin("U")
    assert not is_stablecoin("BNB")
    assert not is_stablecoin("CAKE")


def test_tokens_to_scan_excludes_stables():
    rules = RulesConfig()
    rules.allowed_tokens = [
        TokenConfig(symbol="BNB", address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", cmc_id=1839),
        TokenConfig(symbol="USDT", address="0x55d398326f99059fF775485246999027B3197955", cmc_id=825),
        TokenConfig(symbol="USDC", address="0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", cmc_id=3408),
        TokenConfig(symbol="CAKE", address="0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", cmc_id=7186),
    ]
    scanned = tokens_to_scan(rules)
    symbols = {t.symbol for t in scanned}
    assert symbols == {"BNB", "CAKE"}


def test_tokens_to_scan_demo_limits_priority_symbols():
    rules = RulesConfig()
    rules.loop.demo.token_limit = 2
    rules.loop.demo.priority_symbols = ["CAKE", "BNB"]
    rules.allowed_tokens = [
        TokenConfig(symbol="BNB", address="0x1", cmc_id=1839),
        TokenConfig(symbol="CAKE", address="0x2", cmc_id=7186),
        TokenConfig(symbol="DOGE", address="0x3", cmc_id=74),
        TokenConfig(symbol="ADA", address="0x4", cmc_id=2010),
    ]
    scanned = tokens_to_scan(rules, demo=True)
    assert [t.symbol for t in scanned] == ["CAKE", "BNB"]