#!/usr/bin/env bash
set -euo pipefail
PORT="${1:-8000}"
cd "$(dirname "$0")"
echo "Serving dashboard at: http://127.0.0.1:${PORT}/"
echo "Dashboard will auto-load /api/latest, dashboard_data.json or mpls_results/latest.json."
python3 run_dashboard.py "$PORT"
