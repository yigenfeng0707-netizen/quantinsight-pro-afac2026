"""
QuantInsight Pro - 数据质量检查（治理层）
=========================================
对 qi_data.db 做四类检查，产出质量报告并留痕 quality_log：

  1. 新鲜度 SLA：按表 last_updated 年龄 vs 阈值（激活 _STALE_MINUTES 语义）
  2. 完整性：关键列空值率
  3. 合理性：涨跌幅异常值（|z|>6）、负价格
  4. 覆盖率：逐码行数分布、financials 覆盖

用法：
  python scripts/data_quality.py            # 全量报告 → _quality/quality_report.json
  python scripts/data_quality.py --gate     # 门禁模式：stdout 输出含 "gate": PASS/WARN/FAIL
  python scripts/data_quality.py --no-log   # 不写 quality_log
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = ROOT / "streamlit_app"
sys.path.insert(0, str(APP))

from features.sqlite_data_layer import QIDataDB  # noqa: E402

# 新鲜度 SLA（分钟）：数值表 1440（日更），快照类 90，财报 30 天
SLA_MINUTES = {
    "stock_spot": 90,
    "stock_history": 1440,
    "northbound_flow": 1440,
    "sector_flow": 1440,
    "fund_flow": 1440,
    "macro_indices": 1440,
    "market_breadth": 1440,
    "margin_trading": 1440,
    "stock_profile": 43200,
    "financials": 43200,
}


def _age_minutes(last_updated: str) -> float:
    import datetime as dt
    try:
        t = dt.datetime.fromisoformat(last_updated)
    except (ValueError, TypeError):
        return -1.0
    return (dt.datetime.now() - t).total_seconds() / 60


def check_freshness(conn, context: str = "live") -> list[dict]:
    """context: live=生产环境 SLA 硬约束；demo=镜像随构建刷新，超期降级 WARN"""
    rows = conn.execute(
        "SELECT table_name, last_updated, row_count, source FROM data_meta"
    ).fetchall()
    out = []
    for table, last, n, source in rows:
        sla = SLA_MINUTES.get(table, 1440)
        age = _age_minutes(last)
        status = "PASS"
        note = ""
        if age < 0:
            status, note = "WARN", "时间戳不可解析"
        elif age > sla:
            if context == "demo":
                status = "WARN"
                note = f"演示镜像随构建刷新：超 SLA {age/60:.1f}h（SLA {sla/60:.0f}h，live 环境为 FAIL）"
            else:
                status = "WARN" if age < sla * 3 else "FAIL"
                note = f"超 SLA {age:.0f}/{sla} 分钟"
        else:
            note = f"{age:.0f}/{sla} 分钟"
        out.append({"check": f"freshness:{table}", "status": status,
                    "detail": f"rows={n} {note}", "age_min": round(age, 1)})
    # financials 特判（不在 data_meta，用自身 updated_at）
    try:
        r = conn.execute(
            "SELECT COUNT(*), MAX(updated_at) FROM financials").fetchone()
        n_fin, last = (r[0], r[1]) if r else (0, None)
        age = _age_minutes(last) if last else -1
        sla = SLA_MINUTES["financials"]
        if n_fin == 0:
            out.append({"check": "freshness:financials", "status": "WARN",
                        "detail": "表为空（bootstrap 未跑）"})
        elif age > sla:
            out.append({"check": "freshness:financials",
                        "status": "WARN" if context == "demo" else "FAIL",
                        "detail": f"rows={n_fin} 超 SLA {age/60:.1f}h"})
        else:
            out.append({"check": "freshness:financials", "status": "PASS",
                        "detail": f"rows={n_fin} {age:.0f}/{sla} 分钟"})
    except sqlite3.OperationalError:
        pass
    return out


def check_completeness(conn) -> list[dict]:
    out = []
    for col in ("close", "pct_change", "turnover"):
        r = conn.execute(
            f"SELECT COUNT(*), SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) "
            "FROM stock_history").fetchone()
        total, nulls = (r[0] or 0), (r[1] or 0)
        rate = nulls / total if total else 0
        status = "PASS" if rate < 0.05 else ("WARN" if rate < 0.2 else "FAIL")
        out.append({"check": f"null_rate:stock_history.{col}", "status": status,
                    "detail": f"{nulls}/{total} = {rate:.2%}"})
    return out


def check_sanity(conn) -> list[dict]:
    out = []
    # 涨跌幅异常值（全表 |z|>6 记数，正常应极罕见）
    r = conn.execute(
        "SELECT COUNT(*), AVG(pct_change), "
        "MAX(ABS(pct_change - (SELECT AVG(pct_change) FROM stock_history))) "
        "FROM stock_history WHERE pct_change IS NOT NULL").fetchone()
    n, mean = r[0] or 0, r[1] or 0
    if n:
        import statistics
        vals = [row[0] for row in conn.execute(
            "SELECT pct_change FROM stock_history WHERE pct_change IS NOT NULL "
            "ORDER BY RANDOM() LIMIT 20000")]
        std = statistics.pstdev(vals) or 1.0
        outliers = conn.execute(
            "SELECT COUNT(*) FROM stock_history "
            "WHERE ABS(pct_change - ?) > 6 * ?", (mean, std)).fetchone()[0]
        rate = outliers / n
        status = "PASS" if rate < 0.005 else ("WARN" if rate < 0.02 else "FAIL")
        out.append({"check": "outliers:stock_history.pct_change",
                    "status": status,
                    "detail": f"|z|>6: {outliers}/{n} = {rate:.3%} (std={std:.2f})"})
    # 负价格
    r = conn.execute(
        "SELECT COUNT(*) FROM stock_history WHERE close IS NOT NULL AND close <= 0"
    ).fetchone()[0]
    out.append({"check": "negative_close", "status": "PASS" if r == 0 else "FAIL",
                "detail": f"{r} 行"})
    return out


def check_coverage(conn) -> list[dict]:
    out = []
    r = conn.execute(
        "SELECT COUNT(DISTINCT code), COUNT(*), MIN(date), MAX(date) "
        "FROM stock_history").fetchone()
    codes, total, dmin, dmax = r
    out.append({"check": "coverage:stock_history", "status": "PASS" if codes >= 700 else "WARN",
                "detail": f"{codes} 标的 {total} 行 {dmin}~{dmax}"})
    try:
        r = conn.execute(
            "SELECT COUNT(DISTINCT code), COUNT(DISTINCT metric_key), COUNT(*) "
            "FROM financials").fetchone()
        fc, fm, fn = r
        status = "PASS" if fc >= 40 else ("WARN" if fc > 0 else "WARN")
        out.append({"check": "coverage:financials", "status": status,
                    "detail": f"{fc} 标的 × {fm} 指标 = {fn} 行"})
    except sqlite3.OperationalError:
        out.append({"check": "coverage:financials", "status": "WARN",
                    "detail": "表不存在"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", action="store_true", help="门禁模式（供 production_check 调用）")
    ap.add_argument("--context", choices=["live", "demo"], default="live",
                    help="live=SLA 硬约束；demo=镜像随构建刷新，新鲜度超期降级 WARN")
    ap.add_argument("--no-log", action="store_true", help="不写 quality_log")
    args = ap.parse_args()

    db = QIDataDB()
    conn = db._get_conn()

    checks = []
    checks += check_freshness(conn, context=args.context)
    checks += check_completeness(conn)
    checks += check_sanity(conn)
    checks += check_coverage(conn)

    n_fail = sum(1 for c in checks if c["status"] == "FAIL")
    n_warn = sum(1 for c in checks if c["status"] == "WARN")
    gate = "FAIL" if n_fail else ("WARN" if n_warn else "PASS")

    report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "gate": gate,
              "n_fail": n_fail, "n_warn": n_warn, "checks": checks}

    out_path = ROOT / "_quality" / "quality_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    if not args.no_log:
        try:
            db.log_quality(gate, {"n_fail": n_fail, "n_warn": n_warn,
                                  "checks": [c["check"] + ":" + c["status"] for c in checks]})
        except Exception:
            pass

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if (args.gate and gate == "FAIL") else 0


if __name__ == "__main__":
    raise SystemExit(main())
