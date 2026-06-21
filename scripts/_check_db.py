import sqlite3
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "data" / "genesis.db"
print("db_path", p)
print("exists", p.exists())
if not p.exists():
    raise SystemExit(0)
print("size_bytes", p.stat().st_size)
conn = sqlite3.connect(p)
c = conn.cursor()
for t in ["audit_records", "trades", "decisions", "portfolio_snapshots"]:
    c.execute(f"SELECT COUNT(*) FROM {t}")
    print(t, c.fetchone()[0])
c.execute("SELECT created_at, total_value_usd FROM portfolio_snapshots ORDER BY created_at DESC LIMIT 3")
print("snapshots", c.fetchall())
c.execute("SELECT cycle_id, created_at, length(data) FROM audit_records ORDER BY created_at DESC LIMIT 5")
print("audits", c.fetchall())
conn.close()