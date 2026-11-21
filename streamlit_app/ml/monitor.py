"""
QuantInsight Pro - ml 监控（预测 vs 实现收益）
=============================================
读 predictions_log，对齐 stock_history 实现的 20 日收益，输出：
  - 样本覆盖（预测数 / 标的数 / 日期范围）
  - 命中率（预测方向与实现方向一致占比）
  - IC（Spearman，全体 + 按月衰减序列）

供信号验证中心「模型信号」选项渲染。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def evaluate_predictions(db, horizon: int = 20,
                         min_pred: int = 50) -> Optional[dict]:
    """对齐实现收益并评估。样本不足返回 None。"""
    preds = db.get_predictions()
    if preds is None or len(preds) < min_pred:
        return None

    conn = db._get_conn()
    hist = pd.read_sql(
        "SELECT code, date, close FROM stock_history WHERE close IS NOT NULL "
        "ORDER BY code, date", conn)

    # 实现收益：asof_date 后第 horizon 个交易日 close / 当前 close - 1（逐码）
    h = hist.groupby("code", sort=False)
    hist = hist.copy()
    hist["fwd_close"] = h["close"].shift(-horizon)
    hist["fwd_return"] = hist["fwd_close"] / hist["close"] - 1.0

    preds["asof_date"] = preds["asof_date"].astype(str)
    merged = preds.merge(
        hist[["code", "date", "fwd_return"]],
        left_on=["code", "asof_date"], right_on=["code", "date"], how="left")
    merged = merged.dropna(subset=["fwd_return", "pred_20d"])
    if len(merged) < min_pred:
        return None

    # 最新一次预测快照（同码同日取最新 ts）
    latest_ts = merged["ts"].max()
    snap = merged[merged["ts"] == latest_ts].drop_duplicates(["code", "asof_date"])

    def _ic(df: pd.DataFrame) -> float:
        from scipy.stats import spearmanr
        if len(df) < 10:
            return float("nan")
        r = spearmanr(df["pred_20d"], df["fwd_return"])
        return float(r.statistic) if np.isfinite(r.statistic) else float("nan")

    hit = float((np.sign(snap["pred_20d"]) == np.sign(snap["fwd_return"])).mean())
    ic = _ic(snap)

    # IC 按月衰减（按 asof 月分桶，全历史）
    merged["month"] = merged["asof_date"].str.slice(0, 7)
    monthly = []
    for m, g in merged.groupby("month"):
        monthly.append({"month": m, "ic": _ic(g), "n": len(g)})
    monthly = [m for m in monthly if np.isfinite(m["ic"])]

    # 已实现样本 vs 未实现（前瞻）样本
    n_realized = len(snap)
    n_total = len(preds)

    return {
        "model_id": (preds["model_id"].iloc[-1] if "model_id" in preds else ""),
        "n_predictions": int(n_total),
        "n_realized": int(n_realized),
        "hit_rate": round(hit, 4),
        "ic": round(ic, 4) if np.isfinite(ic) else None,
        "monthly_ic": monthly,
        "asof_range": [str(snap["asof_date"].min()), str(snap["asof_date"].max())],
    }
