"""Configuration loading: environment variables + rules.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from genesis.core.models import RulesConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "rules.yaml"


class EnvSettings(BaseSettings):
    """Environment-backed settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Network
    genesis_network: Literal["bsc-testnet", "bsc-mainnet"] = "bsc-mainnet"
    genesis_loop_interval_seconds: int = 300

    # LLM
    xai_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    llm_enabled: bool = True
    llm_provider: Literal["grok", "openai", "anthropic"] = "grok"
    llm_model: str = "grok-3-fast"

    # CMC
    cmc_mcp_url: str = "https://mcp.coinmarketcap.com/mcp"
    cmc_api_key: str = ""
    cmc_x402_enabled: bool = False
    cmc_x402_mode: Literal["fallback", "only"] = "fallback"
    cmc_x402_max_payment: str = "10000"  # 0.01 USDC on Base (6 dp); use 10000000000000000 on BSC
    cmc_x402_prefer_network: str = "base"  # match network where x402 USDC is funded

    # BSC RPC (on-chain balance supplement when TWAK portfolio omits tokens)
    bsc_rpc_url: str = "https://bsc-dataseed.binance.org/"

    # TWAK (@trustwallet/cli — npm install -g @trustwallet/cli)
    twak_cli_path: str = "twak"
    twak_wallet_mode: Literal["autonomous", "interactive"] = "autonomous"
    twak_network: str = "bsc-mainnet"
    twak_chain: str = "bsc"  # TWAK --chain key (bsc for mainnet, bsctestnet for testnet)
    twak_wallet_password: str = ""  # Falls back to WALLET_PASSWORD if empty
    twak_use_wsl: bool = False  # Set true if running Genesis on Windows but TWAK is in WSL

    # BNB SDK
    bnb_agent_network: str = "bsc-mainnet"
    wallet_password: str = ""
    private_key: str = ""

    # Agent identity
    genesis_agent_name: str = "Genesis"
    genesis_agent_description: str = "Self-custody autonomous AI trader on BSC"
    genesis_agent_endpoint: str = "http://localhost:8080"

    # Storage
    genesis_db_path: str = "./data/genesis.db"

    # Dashboard
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080
    # When false, POST /api/agent/* is blocked (public landing is view-only).
    dashboard_controls_enabled: bool = True

    # Demo mode — faster cycles (limited token scan, parallel CMC fetches)
    genesis_demo_mode: bool = False

    # Competition (comma-separated: 1=autonomous agent, 2=strategy skill)
    hackathon_tracks: str = "1,2"
    hackathon_track: int = 1
    competition_agent_id: str = ""
    genesis_wallet_address: str = ""  # TWAK agent wallet — instant dashboard display

    @field_validator("genesis_db_path")
    @classmethod
    def resolve_db_path(cls, v: str) -> str:
        path = Path(v)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)

    def get_llm_api_key(self) -> str:
        """Return API key for configured LLM provider."""
        keys = {
            "grok": self.xai_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
        }
        key = keys.get(self.llm_provider, "")
        if not key:
            raise ValueError(
                f"No API key configured for LLM provider '{self.llm_provider}'. "
                f"Set {self.llm_provider.upper()}_API_KEY in .env"
            )
        return key


def load_rules(path: Path | str | None = None) -> RulesConfig:
    """Load and validate rules.yaml."""
    rules_path = Path(path) if path else DEFAULT_RULES_PATH
    if not rules_path.exists():
        raise FileNotFoundError(f"Rules file not found: {rules_path}")

    with open(rules_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return RulesConfig.model_validate(data)


@lru_cache
def get_env_settings() -> EnvSettings:
    """Cached environment settings singleton."""
    return EnvSettings()


@lru_cache
def get_rules() -> RulesConfig:
    """Cached rules config singleton."""
    return load_rules()


def demo_mode_active(env: EnvSettings | None = None, rules: RulesConfig | None = None) -> bool:
    """True when demo optimizations (fast scan, optional rule-only decisions) apply."""
    env = env or get_env_settings()
    rules = rules or get_rules()
    return bool(env.genesis_demo_mode or rules.loop.demo.enabled)


def clear_config_cache() -> None:
    """Clear cached config (useful in tests)."""
    get_env_settings.cache_clear()
    get_rules.cache_clear()


def update_env_file(key: str, value: str, env_path: Path | None = None) -> None:
    """Update or append a single KEY=value line in .env."""
    path = env_path or (PROJECT_ROOT / ".env")
    if not path.exists():
        raise FileNotFoundError(f".env not found: {path}")

    prefix = f"{key}="
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            out.append(prefix + value)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(prefix + value)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")