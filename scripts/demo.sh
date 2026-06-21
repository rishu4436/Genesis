#!/usr/bin/env bash
# Demo script for hackathon judges
set -euo pipefail

echo "=== Genesis Demo Script ==="
echo ""

echo "1. Agent Status"
genesis status
echo ""

echo "2. Portfolio Snapshot"
genesis portfolio || echo "(TWAK not configured — skipping)"
echo ""

echo "3. Simulated Trading Cycle (3 cycles)"
genesis backtest --cycles 3
echo ""

echo "4. Recent Decision Logs"
genesis logs --limit 5
echo ""

echo "5. Export Audit Trail"
genesis logs --export data/demo_audit.json
echo ""

echo "=== Demo Complete ==="
echo "Audit trail exported to data/demo_audit.json"
echo "Dashboard: genesis dashboard (http://localhost:8080)"