#!/usr/bin/env bash
# Register Genesis for hackathon competition
set -euo pipefail

echo "=== Genesis Competition Registration ==="

# Ensure agent is initialized
genesis init

# Register ERC-8004 identity + competition
genesis register-competition

# Export registration payload for backup
mkdir -p data
echo "Registration complete. Check data/registration.json for payload."