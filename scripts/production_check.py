"""
QuantInsight Pro - 生产就绪质量门禁
====================================
一键六道门禁，任何一道 FAIL 即退出非零（可作 CI/发布闸门）：

  G1 语义层自测      复用 semantic/selftest.py（18 项）
  G2 评测回归        benchmark S3 离线复评，指标不回退
  G3 接地冒烟        实体链接/点查/计算模板/财务/超纲 5 问
  G4 数据质量        data_quality.py（T2 落地后自动启用）
  G5 本体一致性      SPARQL 抽查 10 条
  G6 模型注册表      registry.json 与工件一致性（T4 落地后自动启用）

用法：
  python scripts/production_check.py                # 全部门禁
  python scripts/production_check.py --json out.json
  python scripts/production_check.py --only G2,G5   # 只跑指定门禁
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # quantinsight-deploy/scripts
ROOT = HERE.parent                              # quantinsight-deploy/
APP = ROOT / "streamlit_app"
BENCH = ROOT.parent / "grounded-finqa-benchmark"

# 评测回归门槛（601 版在 T3 后更新为 items_v1）
GATE_NA, GATE_F1, GATE_CP, GATE_HR = 0.96, 0.92, 0.99, 0.001


class Gate:
    def __init__(self, gid: str, name: str):
        self.gid, self.name = gid, name
        self.status = "SKIP"     # PASS / FAIL / WARN / SKIP
        self.detail = ""
        self.duration = 0.0

    def finish(self, status: str, detail: str, t0: float):
        self.status, self.detail = status, detail
        self.duration = round(time.time() - t0, 1)


def _run(cmd: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------- G1 语义层
def g1_semantic(g: Gate) -> None:
    t0 = time.time()
    if not (APP / "semantic" / "selftest.py").exists():
        g.finish("SKIP", "semantic/selftest.py 不存在", t0); return
    code, out = _run([sys.executable, "-X", "utf8", "semantic/selftest.py"],
                     cwd=APP, timeout=180)
    m = re.search(r"(全部通过|\d+ 项失败)（共 (\d+) 项）", out)
    if code == 0:
        g.finish("PASS", m.group(0) if m else "exit=0", t0)
    else:
        fails = [l for l in out.splitlines() if "❌" in l]
        g.finish("FAIL", "; ".join(fails[:3]) or out[-300:], t0)


# ---------------------------------------------------------------- G2 评测回归
def g2_benchmark(g: Gate) -> None:
    t0 = time.time()
    pred = BENCH / "runs_real" / "pred_real_s3.jsonl"
    if not pred.exists():
        g.finish("SKIP", "缺 runs_real/pred_real_s3.jsonl", t0); return
    code, out = _run([sys.executable, "-X", "utf8", "evaluate.py",
                      "--items", "data_real/items_v0.jsonl",
                      "--pred", "runs_real/pred_real_s3.jsonl",
                      "--out", "runs_real/_gatecheck"],
                     cwd=BENCH, timeout=600)
    if code != 0:
        g.finish("FAIL", f"evaluate.py exit={code}: {out[-200:]}", t0); return
    metrics = dict(re.findall(r"(NA|F1|RC|OR|CP|CR|HR|TA)_\S+\s+([\d.]+)", out))
    if not metrics:
        g.finish("FAIL", "无法解析指标输出", t0); return
    checks = [
        ("HR", float(metrics.get("HR", 1)) <= GATE_HR),
        ("NA", float(metrics.get("NA", 0)) >= GATE_NA),
        ("CP", float(metrics.get("CP", 0)) >= GATE_CP),
        ("F1", float(metrics.get("F1", 0)) >= GATE_F1),
    ]
    bad = [f"{k}={metrics.get(k, '?')}（门槛{'≤' if k == 'HR' else '≥'}"
           f"{GATE_HR if k == 'HR' else dict(NA=GATE_NA, CP=GATE_CP, F1=GATE_F1)[k]}）"
           for k, ok in checks if not ok]
    if bad:
        g.finish("FAIL", "回退: " + ", ".join(bad), t0)
    else:
        g.finish("PASS", f"NA={metrics.get('NA')} F1={metrics.get('F1')} "
                         f"CP={metrics.get('CP')} HR={metrics.get('HR')}", t0)


# ---------------------------------------------------------------- G3 接地冒烟
def g3_grounding(g: Gate) -> None:
    t0 = time.time()
    sys.path.insert(0, str(APP))
    try:
        from features.sqlite_data_layer import QIDataDB
        from ai.data_grounder import DataGrounder
        db = QIDataDB()
        grounder = DataGrounder(qi_db=db)
    except Exception as e:
        g.finish("FAIL", f"模块加载失败: {type(e).__name__}: {e}", t0); return

    fails, warns = [], []
    fin_has_rows = False
    try:
        import sqlite3
        conn = db._get_conn()
        fin_has_rows = conn.execute(
            "SELECT COUNT(*) FROM financials").fetchone()[0] > 0
    except Exception:
        pass

    def hits(q: str):
        r = grounder.ground(q)
        return (len(r.get("point_hits") or []),
                len(r.get("computed") or []),
                len(r.get("financial_hits") or []))

    # 1) 实体链接 + 点查
    p, c, f = hits("茅台最近收盘价是多少")
    if p < 1: warns.append(f"实体链接点查: point={p}")
    # 2) 区间计算
    p, c, f = hits("600519从2026-01-01到2026-06-30的区间涨跌幅")
    if c < 1: warns.append(f"区间计算: computed={c}")
    # 3) 多跳模板
    p, c, f = hits("在2026-01-01至2026-06-30区间内，区间涨幅排名前 5 的标的中，"
                   "平均换手率最低的那只，其区间涨幅是多少")
    if c < 1: warns.append(f"多跳模板: computed={c}")
    # 4) 财务接地
    p, c, f = hits("贵州茅台2025年年报的营业收入是多少")
    if fin_has_rows and f < 1: fails.append(f"财务接地: financial={f}（表有数据却未命中）")
    if not fin_has_rows: warns.append("financials 表为空（T2 bootstrap 未跑）")
    # 5) 超纲不乱接
    p, c, f = hits("美联储下月加息概率是多少")
    if p + c + f > 0: fails.append(f"超纲问题被错误接地: point={p} computed={c} financial={f}")

    if fails:
        g.finish("FAIL", "; ".join(fails), t0)
    elif warns:
        g.finish("WARN", "; ".join(warns), t0)
    else:
        g.finish("PASS", "5 问全过（实体/点查/计算/多跳/财务/超纲）", t0)


# ---------------------------------------------------------------- G4 数据质量
def g4_data_quality(g: Gate) -> None:
    t0 = time.time()
    dq = HERE / "data_quality.py"
    if not dq.exists():
        g.finish("SKIP", "T2 data_quality.py 未落地", t0); return
    code, out = _run([sys.executable, "-X", "utf8", str(dq), "--gate", "--context", "demo"],
                     cwd=ROOT, timeout=180)
    try:
        # stdout 是完整 JSON 报告
        m = re.search(r'\{.*"gate"\s*:\s*"(PASS|WARN|FAIL)".*\}', out, re.S)
        rep = json.loads(m.group(0)) if m else json.loads(out[out.index("{"):])
        status = rep.get("gate", "FAIL" if code else "PASS")
        bad = [f"{c['check']}({c['status']})" for c in rep.get("checks", [])
               if c["status"] != "PASS"]
        detail = (f"fail={rep.get('n_fail')} warn={rep.get('n_warn')}; "
                  + "; ".join(bad[:4])) if bad else "全部 PASS"
        g.finish(status, detail, t0); return
    except Exception:
        pass
    g.finish("FAIL" if code else "WARN", out[-200:], t0)


# ---------------------------------------------------------------- G5 本体一致性
G5_ASKS = [
    ("茅台是 Stock", 'ASK { qi:stk_600519 a qi:Stock }'),
    ("茅台 ∈ 中证800", 'ASK { qi:stk_600519 qi:memberOf qi:idx_sh000906 }'),
    ("rsi_14 ∈ 技术指标类", 'ASK { qi:rsi_14 qi:inCategory qi:cat_technical }'),
    ("营业收入绑定 financials", 'ASK { qi:m_revenue qi:bindingTable "financials" }'),
    ("营业收入为年报口径", 'ASK { qi:m_revenue qi:reportedIn qi:rp_annual }'),
    ("价值因子依赖 PE", 'ASK { qi:f_value qi:computedFrom qi:m_pe_ttm }'),
    ("SHAP 特征已注册", 'ASK { qi:shap_ROE a qi:Factor }'),
    ("SHAP 大类存在", 'ASK { qi:cat_shap a qi:FactorCategory }'),
    ("本地库数据源", 'ASK { qi:src_local_sqlite a qi:DataSource }'),
    ("区间涨跌幅对应 T2/T5", 'ASK { qi:m_interval_return qi:evalType "T2,T5" }'),
    ("注册模型为本体实例", 'ASK { ?m a qi:MLModel }'),
    ("因子-模型血缘存在", 'ASK { ?f qi:usedInModel ?m }'),
]

def g5_ontology(g: Gate) -> None:
    t0 = time.time()
    sys.path.insert(0, str(APP))
    try:
        from semantic.layer import load_graph, _sparql
        gph = load_graph()
    except Exception as e:
        g.finish("FAIL", f"本体装载失败: {e}", t0); return
    bad = []
    for label, q in G5_ASKS:
        try:
            if not bool(gph.query(q).askAnswer):
                bad.append(label)
        except Exception as e:
            bad.append(f"{label}（查询异常 {type(e).__name__}）")
    if bad:
        g.finish("FAIL", f"{len(bad)} 条不一致: " + "; ".join(bad[:4]), t0)
    else:
        g.finish("PASS", f"{len(G5_ASKS)} 条 ASK 全部成立", t0)


# ---------------------------------------------------------------- G6 模型注册表
def g6_registry(g: Gate) -> None:
    t0 = time.time()
    reg = APP / "models" / "registry.json"
    if not reg.exists():
        g.finish("SKIP", "T4 模型注册表未落地", t0); return
    try:
        entries = json.loads(reg.read_text(encoding="utf-8"))
        latest = entries[-1] if isinstance(entries, list) else None
        mdir = APP / "models" / latest["model_id"]
        ok = (mdir / "model.pkl").exists() and (mdir / "metadata.json").exists()
        ic = latest.get("mean_ic")
        if not ok:
            g.finish("FAIL", f"最新模型 {latest.get('model_id')} 工件缺失", t0)
        elif ic is None or abs(float(ic)) < 0.01:
            g.finish("WARN", f"最新模型 IC={ic} 过弱，建议重训", t0)
        else:
            g.finish("PASS", f"model_id={latest['model_id']} mean_ic={ic}", t0)
    except Exception as e:
        g.finish("FAIL", f"registry.json 解析失败: {e}", t0)


GATES = {"G1": g1_semantic, "G2": g2_benchmark, "G3": g3_grounding,
         "G4": g4_data_quality, "G5": g5_ontology, "G6": g6_registry}
GATE_NAMES = {
    "G1": "语义层自测", "G2": "评测回归", "G3": "接地冒烟",
    "G4": "数据质量", "G5": "本体一致性", "G6": "模型注册表",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None, help="报告输出路径")
    ap.add_argument("--only", default="", help="逗号分隔门禁 id（如 G2,G5）")
    args = ap.parse_args()

    only = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    results = []
    for gid, fn in GATES.items():
        if only and gid not in only:
            g = Gate(gid, GATE_NAMES[gid]); g.status = "SKIP"; g.detail = "未选中"
        else:
            g = Gate(gid, GATE_NAMES[gid])
            try:
                fn(g)
            except Exception as e:
                g.finish("FAIL", f"门禁异常 {type(e).__name__}: {e}", time.time())
        results.append(g)

    # 汇总
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "➖"}
    print("\n===== 生产就绪质量门禁 =====")
    for g in results:
        print(f"{icon[g.status]} {g.gid} {g.name:<10s} [{g.status}] "
              f"({g.duration}s) {g.detail[:100]}")
    n_fail = sum(1 for g in results if g.status == "FAIL")
    n_warn = sum(1 for g in results if g.status == "WARN")
    print(f"===== FAIL={n_fail} WARN={n_warn} "
          f"PASS={sum(1 for g in results if g.status == 'PASS')} =====")

    if args.json:
        report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "overall": "FAIL" if n_fail else ("WARN" if n_warn else "PASS"),
                  "gates": [{"id": g.gid, "name": g.name, "status": g.status,
                             "detail": g.detail, "duration_s": g.duration}
                            for g in results]}
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"报告已写: {args.json}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
