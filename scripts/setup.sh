#!/usr/bin/env bash
# Genesis setup script — install dependencies and initialize project
set -euo pipefail

echo "=== Genesis Setup ==="

# Check Python version
python3 --version | grep -E "3\.(11|12)" || {
    echo "Error: Python 3.11+ required"
    exit 1
}

# Install TWAK if not present
if ! command -v twak &> /dev/null; then
    echo "Installing Trust Wallet Agent Kit (TWAK)..."
    curl -fsSL https://raw.githubusercontent.com/trustwallet/tw-agent-skills/main/install.sh | bash
else
    echo "TWAK already installed: $(twak --version 2>/dev/null || echo 'ok')"
fi

# Install Genesis package
echo "Installing Genesis..."
pip install -e ".[dev]"

# Initialize project
echo "Initializing Genesis..."
genesis init

# Create .env if needed
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env — edit with your API keys"
fi

echo ""
echo "=== Setup Complete ==="
echo "Next steps:"
echo "  1. Edit .env with XAI_API_KEY, CMC_API_KEY, etc."
echo "  2. genesis setup-wallet"
echo "  3. genesis run --simulate"