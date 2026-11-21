"""
QuantInsight Pro - ml 特征工程（真实数据）
==========================================
从 qi_data.db 构建训练特征矩阵：
  - 技术因子：stock_history 逐码真实计算（动量/RSI/MACD/换手/波动/量比）
  - 财务因子：financials 表按"披露时点可见的最新年报"渐进对齐（防前视偏差）
  - 不可得因子：留空由训练侧中位数填充，metadata 如实标注来源

标签：前向 20 日收益（close.shift(-20)/close - 1，逐码）
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 特征 → 数据来源（生产透明度：写进模型 metadata）
FEATURE_SOURCES = {
    "PE_TTM": "imputed", "PB": "imputed", "PS_TTM": "imputed",
    "Revenue_Growth": "financials", "NetProfit_Growth": "financials",
    "ROE": "imputed", "Gross_Margin": "financials",
    "Debt_Ratio": "imputed", "Current_Ratio": "imputed",
    "Momentum_20D": "stock_history", "Momentum_60D": "stock_history",
    "RSI_14": "stock_history", "Turnover_20D": "stock_history",
    "Volume_Ratio": "stock_history", "Float_Cap": "imputed",
    "Volatility_20D": "stock_history", "MACD_Signal": "stock_history",
}

FEATURE_COLS = list(FEATURE_SOURCES.keys())
LABEL_COL = "target_20d"

# 年报披露近似缓冲：period(X-12-31) 之后 90 天才视为可得（防前视）
DISCLOSURE_LAG_DAYS = 90


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _macd_signal(close: pd.Series) -> pd.Series:
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    return (dif - dea) / close  # 归一化，跨标的可比


def _technical_factors(hist: pd.DataFrame) -> pd.DataFrame:
    """逐码向量化技术因子（in-place 新列）。"""
    g = hist.groupby("code", sort=False)
    hist["Momentum_20D"] = g["close"].pct_change(20)
    hist["Momentum_60D"] = g["close"].pct_change(60)
    hist["Turnover_20D"] = g["turnover"].transform(
        lambda s: s.rolling(20, min_periods=10).mean())
    roll_mean = g["close"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    roll_std = g["close"].transform(lambda s: s.rolling(20, min_periods=10).std())
    hist["Volatility_20D"] = roll_std / roll_mean
    vol_ma5 = g["volume"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    hist["Volume_Ratio"] = (hist["volume"] / vol_ma5 - 1.0).clip(-5, 5)
    hist["RSI_14"] = g["close"].transform(_rsi)
    hist["MACD_Signal"] = g["close"].transform(_macd_signal)
    return hist


def _financial_factors(hist: pd.DataFrame, conn) -> pd.DataFrame:
    """财务因子：披露时点对齐（merge_asof 防前视）。缺失列补 NaN。"""
    out_cols = ["Revenue_Growth", "NetProfit_Growth", "Gross_Margin"]
    for c in out_cols:
        hist[c] = np.nan
    try:
        fin = pd.read_sql(
            "SELECT code, period, metric_key, value FROM financials", conn)
    except Exception as e:
        logger.warning("financials 读取失败: %s", e)
        return hist
    if fin.empty:
        return hist

    fin["period_date"] = pd.to_datetime(fin["period"])
    fin["disclosure"] = fin["period_date"] + pd.Timedelta(days=DISCLOSURE_LAG_DAYS)
    annual = fin[fin["period"].str.endswith("-12-31")].sort_values(
        ["code", "period_date"])

    # 逐码标记上一期年报（YoY 分母）
    annual = annual.sort_values(["code", "period_date"])
    annual["prev_value"] = annual.groupby("code")["value"].shift(1)

    metric_map = {
        "revenue": ("Revenue_Growth", "growth"),
        "net_profit_parent": ("NetProfit_Growth", "growth"),
        "gross_margin": ("Gross_Margin", "level"),
    }
    for metric, (col, kind) in metric_map.items():
        sub = annual[annual["metric_key"] == metric].copy()
        if sub.empty:
            continue
        if kind == "growth":
            # 披露日可见的最新年报同比增速
            ok = sub["value"].notna() & sub["prev_value"].notna() \
                & (sub["prev_value"] != 0)
            sub = sub[ok]
            sub[col] = (sub["value"] / sub["prev_value"] - 1.0) * 100
        else:
            sub[col] = sub["value"]
        sub = sub.drop_duplicates(["code", "disclosure"])
        merged = pd.merge_asof(
            hist[["code", "date"]].assign(_d=lambda x: pd.to_datetime(x["date"]))
            .sort_values("_d"),
            sub[["code", "disclosure", col]].rename(columns={"disclosure": "_d"})
            .sort_values("_d"),
            on="_d", by="code", direction="backward")
        hist[col] = merged[col].values
    return hist


def build_feature_matrix(db, min_history: int = 120,
                         asof: Optional[str] = None) -> pd.DataFrame:
    """构建 (code, date) 级特征矩阵 + 前向 20 日收益标签。"""
    conn = db._get_conn()
    hist = pd.read_sql(
        "SELECT code, date, close, volume, turnover FROM stock_history "
        "WHERE close IS NOT NULL ORDER BY code, date", conn)
    if asof:
        hist = hist[hist["date"] <= asof]
    if hist.empty:
        return pd.DataFrame(columns=["code", "date"] + FEATURE_COLS + [LABEL_COL])

    hist = _technical_factors(hist)
    hist = _financial_factors(hist, conn)
    # 不可得因子建空列（训练侧中位数填充），保证 FEATURE_COLS 全在
    for c in FEATURE_COLS:
        if c not in hist.columns:
            hist[c] = np.nan
    hist["target_20d"] = hist.groupby("code")["close"].transform(
        lambda s: s.shift(-20) / s - 1.0)

    hist = hist.dropna(subset=["Momentum_20D", LABEL_COL])
    cnt = hist.groupby("code")["date"].transform("count")
    hist = hist[cnt >= min_history]

    cols = ["code", "date"] + FEATURE_COLS + [LABEL_COL]
    out = hist[[c for c in cols if c in hist.columns]].copy()
    out["date"] = out["date"].astype(str)
    # 名称列（SHAP 页展示用；与实体注册表同源）
    try:
        from semantic.build_entities import CORE_STOCKS
        out["name"] = out["code"].map(CORE_STOCKS).fillna("")
    except Exception:
        out["name"] = ""
    return out.reset_index(drop=True)


def data_hash(df: pd.DataFrame) -> str:
    """数据窗口指纹（写进模型 metadata，用于一致性校验）。"""
    import hashlib
    key = (f"{df['code'].nunique()}|{df['date'].min()}|"
           f"{df['date'].max()}|{len(df)}")
    return hashlib.sha256(key.encode()).hexdigest()[:16]
