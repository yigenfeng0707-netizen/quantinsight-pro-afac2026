"""
QuantInsight Pro - ml 模型注册表
================================
models/registry.json 追加登记；模型工件落 models/{model_id}/（model.pkl + metadata.json）。
加载接口：load_latest() → (XGBRegressor, metadata) 或 None（调用方降级现场训练）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def register(models_dir: Path, metadata: dict) -> dict:
    """登记一条模型记录（metadata 已含 model_id，工件已写好）。"""
    models_dir.mkdir(parents=True, exist_ok=True)
    reg_path = models_dir / "registry.json"
    entries = []
    if reg_path.exists():
        try:
            entries = json.loads(reg_path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    entries.append({
        "model_id": metadata["model_id"],
        "mean_ic": metadata.get("mean_ic"),
        "fold_ics": metadata.get("fold_ics", []),
        "features_n": len(metadata.get("features", [])),
        "feature_sources": metadata.get("feature_sources", {}),
        "data_hash": metadata.get("data_hash"),
        "train_at": metadata.get("train_at"),
    })
    reg_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return entries[-1]


def load_latest(models_dir: Optional[Path] = None) -> Optional[Tuple[object, dict]]:
    """加载最新注册模型（joblib 完整 sklearn 对象）。无注册表/损坏时返回 None。"""
    import joblib
    models_dir = models_dir or MODELS_DIR
    reg_path = models_dir / "registry.json"
    if not reg_path.exists():
        return None
    try:
        entries = json.loads(reg_path.read_text(encoding="utf-8"))
        if not entries:
            return None
        latest = entries[-1]
        mdir = models_dir / latest["model_id"]
        meta = json.loads((mdir / "metadata.json").read_text(encoding="utf-8"))
        model = joblib.load(mdir / "model.pkl")
        return model, meta
    except FileNotFoundError:
        logger.warning("registry 最新条目工件缺失")
        return None
    except Exception as e:
        logger.warning("registry 加载失败: %s", e)
        return None
