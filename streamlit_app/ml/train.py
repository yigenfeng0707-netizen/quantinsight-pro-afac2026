"""
QuantInsight Pro - ml 模型训练（walk-forward）
=============================================
真实数据（qi_data.db）× 扩张窗口 5 折 × Spearman IC 评估 × 注册落盘。

用法：
  python -m ml.train                  # 默认 5 折
  python -m ml.train --folds 3        # 快速验证
（在 streamlit_app/ 目录下运行）
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ml.features import (FEATURE_COLS, FEATURE_SOURCES, LABEL_COL,
                         build_feature_matrix, data_hash)
from ml.registry import MODELS_DIR, register


def _spearman_ic(pred: np.ndarray, label: np.ndarray) -> float:
    from scipy.stats import spearmanr
    if len(pred) < 10:
        return float("nan")
    r = spearmanr(pred, label)
    return float(r.statistic) if np.isfinite(r.statistic) else float("nan")


def train_walkforward(df: pd.DataFrame, n_folds: int = 5):
    """扩张窗口 walk-forward。返回 (model, fold_ics, overall_ic, metrics)。"""
    import xgboost as xgb
    from sklearn.metrics import r2_score

    df = df.sort_values("date").reset_index(drop=True)
    dates = np.sort(df["date"].unique())
    # 6 段切分：前段起步，每折扩张 +1 段训练、下一段验证
    seg = np.array_split(dates, n_folds + 1)

    fold_ics, all_pred, all_label = [], [], []
    for k in range(n_folds):
        train_dates = np.concatenate(seg[:k + 1])
        valid_dates = seg[k + 1]
        tr = df[df["date"].isin(train_dates)]
        va = df[df["date"].isin(valid_dates)]
        if len(tr) < 500 or len(va) < 100:
            continue

        model = xgb.XGBRegressor(
            n_estimators=300, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42, n_jobs=4,
        )
        X_tr, y_tr = tr[FEATURE_COLS].fillna(0).values, tr[LABEL_COL].values
        X_va, y_va = va[FEATURE_COLS].fillna(0).values, va[LABEL_COL].values
        model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)

        pred = model.predict(X_va)
        ic = _spearman_ic(pred, y_va)
        fold_ics.append({"fold": k + 1, "train_end": str(train_dates[-1]),
                         "valid": f"{valid_dates[0]}~{valid_dates[-1]}",
                         "n_valid": len(va), "ic": round(ic, 4),
                         "r2": round(r2_score(y_va, pred), 4)})
        all_pred.append(pred)
        all_label.append(y_va)
        print(f"  fold{k + 1}/{n_folds}: IC={ic:.4f} "
              f"(train≤{train_dates[-1]}, valid {valid_dates[0]}~{valid_dates[-1]})")

    overall_ic = _spearman_ic(np.concatenate(all_pred), np.concatenate(all_label))
    return model, fold_ics, overall_ic


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--asof", default=None, help="数据截止日（默认全部）")
    ap.add_argument("--min-ic", type=float, default=0.01,
                    help="低于该 IC 不注册（防过拟合垃圾入库）")
    args = ap.parse_args()

    from features.sqlite_data_layer import QIDataDB
    db = QIDataDB()

    t0 = time.time()
    print("[1/3] 构建真实特征矩阵…")
    df = build_feature_matrix(db, asof=args.asof)
    if df.empty:
        print("特征矩阵为空，终止")
        return 1
    dh = data_hash(df)
    print(f"    {df['code'].nunique()} 标的 × {len(df)} 行，"
          f"窗口 {df['date'].min()}~{df['date'].max()}，hash={dh}")

    print(f"[2/3] walk-forward {args.folds} 折训练…")
    model, fold_ics, overall_ic = train_walkforward(df, n_folds=args.folds)
    if not fold_ics:
        print("无有效折，终止")
        return 1
    print(f"    overall IC = {overall_ic:.4f}")

    if abs(overall_ic) < args.min_ic:
        print(f"IC {overall_ic:.4f} 低于 {args.min_ic} 门槛，不注册")
        return 1

    print("[3/3] 注册落盘…")
    model_id = datetime.now().strftime("xgb-%Y%m%d-%H%M")
    mdir = MODELS_DIR / model_id
    mdir.mkdir(parents=True, exist_ok=True)
    import joblib
    joblib.dump(model, mdir / "model.pkl")

    imp = sorted(zip(FEATURE_COLS, model.feature_importances_),
                 key=lambda x: -x[1])[:8]
    metadata = {
        "model_id": model_id,
        "model_type": "XGBRegressor walk-forward",
        "features": FEATURE_COLS,
        "feature_sources": FEATURE_SOURCES,
        "label": "forward 20d return (close.shift(-20)/close-1)",
        "data_hash": dh,
        "window": {"min_date": str(df["date"].min()),
                   "max_date": str(df["date"].max()),
                   "rows": len(df), "codes": int(df["code"].nunique())},
        "fold_ics": fold_ics,
        "mean_ic": round(float(np.mean([f["ic"] for f in fold_ics])), 4),
        "overall_ic": round(overall_ic, 4),
        "top_features": [{"name": n, "importance": round(float(v), 4)}
                         for n, v in imp],
        "train_at": datetime.now().isoformat(timespec="seconds"),
        "train_seconds": round(time.time() - t0, 1),
    }
    (mdir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    entry = register(MODELS_DIR, metadata)

    # 本体实例：MLModel + 因子血缘（qi:usedInModel）→ semantic/ml_models.ttl
    try:
        _write_ontology_instances(metadata)
    except Exception as e:
        print(f"[warn] 本体实例写入失败（不影响注册）: {e}")

    print(f"已注册 {model_id} | mean_ic={entry['mean_ic']} | "
          f"top 特征: {[t['name'] for t in metadata['top_features'][:4]]}")
    return 0


def _load_tbox():
    from pathlib import Path as _P
    from rdflib import Graph
    _g = Graph()
    sem = _P(__file__).resolve().parent.parent / "semantic"
    for f in ("ontology.ttl", "metrics.ttl", "factors.ttl"):
        p = sem / f
        if p.exists():
            _g.parse(str(p), format="turtle")
    return _g


def _write_ontology_instances(metadata: dict) -> None:
    from pathlib import Path as _P
    from rdflib import Graph, Literal, Namespace
    from rdflib.namespace import RDF
    QI = Namespace("http://quantinsight.pro/ontology/")
    tbox = _load_tbox()

    g = Graph()
    g.bind("qi", QI)
    mid = QI["mlm_" + metadata["model_id"]]
    g.add((mid, RDF.type, QI.MLModel))
    g.add((mid, QI.name, Literal(metadata["model_id"])))
    g.add((mid, QI.caliber, Literal(
        f"XGBoost walk-forward；overall IC={metadata.get('overall_ic')}；"
        f"窗口 {metadata['window']['min_date']}~{metadata['window']['max_date']}；"
        f"特征来源：本地库真实行情 + 财报披露对齐（见 metadata）")))
    g.add((mid, QI.sourcedFrom, QI.src_local_sqlite))

    # 特征名 → 本体因子 URI：SHAP 族 shap_ 前缀覆盖全部特征名；技术族为小写
    linked = 0
    for f in metadata["features"]:
        for candidate in (f"shap_{f}", f.lower(), f):
            uri = QI[candidate]
            if (uri, RDF.type, QI.Factor) in tbox:
                g.add((uri, QI.usedInModel, mid))
                linked += 1
                break
    out = _P(__file__).resolve().parent.parent / "semantic" / "ml_models.ttl"
    g.serialize(destination=str(out), format="turtle", encoding="utf-8")
    print(f"  本体血缘：{linked}/{len(metadata['features'])} 因子 → {out.name}")


if __name__ == "__main__":
    raise SystemExit(main())
