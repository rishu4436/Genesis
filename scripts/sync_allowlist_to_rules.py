"""Merge config/eligible_tokens.yaml allowlist into config/rules.yaml."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "config" / "rules.yaml"
TOKENS = ROOT / "config" / "eligible_tokens.yaml"


def main() -> None:
    token_block = TOKENS.read_text(encoding="utf-8")
    # Strip header comments, keep from allowed_tokens:
    if "allowed_tokens:" not in token_block:
        raise SystemExit("eligible_tokens.yaml missing allowed_tokens")
    allowlist = token_block[token_block.index("allowed_tokens:") :]

    rules = RULES.read_text(encoding="utf-8")
    pattern = re.compile(
        r"# Token allowlist.*?\nallowed_tokens:.*?(?=\n# Preferred trading pairs)",
        re.DOTALL,
    )
    count = allowlist.count("- symbol:")
    replacement = (
        f"# Token allowlist — {count} hackathon-eligible BEP-20 tokens (CMC top-200 filter)\n"
        "# Regenerate: python scripts/build_allowlist.py && python scripts/sync_allowlist_to_rules.py\n"
        "# Or trim only: python scripts/trim_allowlist_top200.py && python scripts/sync_allowlist_to_rules.py\n"
        + allowlist.rstrip()
        + "\n"
    )
    if not pattern.search(rules):
        raise SystemExit("Could not find allowlist section in rules.yaml")
    updated = pattern.sub(replacement, rules)
    RULES.write_text(updated, encoding="utf-8")
    count = allowlist.count("- symbol:")
    print(f"Synced {count} tokens into {RULES}")


if __name__ == "__main__":
    main()