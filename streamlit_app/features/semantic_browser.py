"""
QuantInsight Pro - 语义目录页（指标字典 · 因子目录 · 实体查询 · 本体图谱）

路演演示要点：
  - 因子目录：200+ 因子每条带可读公式与数据依赖（回应"因子透明度"）
  - 指标字典：每个指标的统计口径与数据绑定（引用可追溯的概念层）
  - 实体查询：自然语言指称 → 本体实体（"茅台"→600519）
"""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

_PAGE_HINT = (
    "本体与指标注册表：把“问题里的说法”统一映射到可核验的数据口径。"
    "范围：25 个指标 · 64 个因子 · 800+ 标的 · 7 大指数 · 19 个行业。"
)


@st.cache_data(ttl=300, show_spinner=False)
def _get_semantic():
    """安全的装载入口：任何本体异常都不让应用整体崩溃。"""
    from semantic.layer import (
        EntityLinker, MetricParser, SemanticGraph,
    )
    return EntityLinker(), MetricParser(), SemanticGraph()


def _fallback():
    st.error("本体装载失败，请检查 semantic/*.ttl 文件完整性。")
    st.text(_PAGE_HINT)


def render_semantic_browser() -> None:
    st.title("🧭 语义目录")
    st.caption(_PAGE_HINT)

    try:
        linker, parser, sg = _get_semantic()
    except Exception as e:  # 本体缺失/损坏时降级而非全页白屏
        logger.exception("semantic load failed")
        st.warning(f"语义层不可用：{e}")
        _fallback()
        return

    tab_metric, tab_factor, tab_entity, tab_graph, tab_gov = st.tabs(
        ["📐 指标字典", "🧮 因子目录", "🔎 实体查询", "🕸 本体图谱", "🗂 数据治理"]
    )

    # ---------------- Tab 1: 指标字典 ----------------
    with tab_metric:
        try:
            metrics = sg.metrics()
        except Exception:
            metrics = []
        st.subheader("指标注册表")
        st.caption("每条指标带统一口径与数据绑定——问答引用的“这个数怎么算出来的”。")

        kw = st.text_input("搜索指标（名称/同义词）", "", key="sem_metric_kw")
        rows = []
        for m in metrics:
            row = {
                "指标": m["name"],
                "同义词": "、".join(m.get("aliases", [])) or "—",
                "单位": m.get("unit", "—"),
                "时间粒度": "",
                "口径": m.get("caliber", "—"),
                "绑定": (f"{m.get('table', '')}·{m.get('column', '')}"
                         if m.get("table") else m.get("column", "") or "计算型/接口型"),
                "题型": m.get("eval", "—"),
            }
            rows.append(row)
        df = pd.DataFrame(rows)
        if kw:
            mask = df.apply(
                lambda r: kw.lower() in str(r.values).lower(), axis=1)
            df = df[mask]
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={"口径": st.column_config.TextColumn(width="large")})
        st.caption("题型列：benchmark 评测（GroundedFinQA）九类题型的对应标记——"
                   "评测与平台共用同一套指标语义。")

    # ---------------- Tab 2: 因子目录 ----------------
    with tab_factor:
        st.subheader("因子注册表")
        st.caption("每个因子的可读公式、分类与数据依赖——透明度即信任。")
        try:
            factors = sg.factors()
            cats = sg.categories()
        except Exception:
            factors, cats = [], []

        cat_names = ["全部"] + [c["name"] for c in cats]
        sel = st.selectbox("因子大类", cat_names, key="sem_factor_cat")
        shown = [f for f in factors if sel == "全部" or f["category"] == sel]
        st.caption(f"{len(shown)} / {len(factors)} 个因子")

        for f in shown:
            with st.expander(f"**{f['name']}** · {f['category']}"):
                if f.get("formula"):
                    st.code(f["formula"], language="text")
                if f.get("caliber"):
                    st.caption(f["caliber"])
                meta = []
                if f.get("deps"):
                    meta.append("依赖指标：" + "、".join(f["deps"]))
                if f.get("module"):
                    meta.append("来源：" + f["module"])
                if meta:
                    st.markdown("<br>".join(meta), unsafe_allow_html=True)

    # ---------------- Tab 3: 实体查询 ----------------
    with tab_entity:
        st.subheader("实体链接演示")
        st.caption("输入问题里的指称，看本体如何解析为可接地的实体（代码/全称/简称三级匹配）。")

        q = st.text_input(
            "试试自然语言", "茅台最近一年营业收入是多少", key="sem_entity_q")
        if st.button("解析", key="sem_entity_btn") and q:
            try:
                ents = linker.link(q)
                mets = parser.parse(q)
            except Exception as e:
                st.error(f"解析失败：{e}")
                return
            if ents:
                st.markdown("**识别到的实体**")
                st.dataframe(pd.DataFrame([{
                    "实体": e.name or "（未具名）",
                    "类型": e.kind,
                    "代码": e.code,
                    "匹配方式": {"code": "代码", "name": "全称",
                                   "abbr": "简称缩略", "alias": "别名"}.get(e.matched_by, e.matched_by),
                } for e in ents]), use_container_width=True, hide_index=True)

                if mets:
                    st.markdown("**识别到的指标**")
                    st.dataframe(pd.DataFrame([{
                        "指标": m.name, "单位": m.unit or "—",
                        "日期": "、".join(m.dates) or "—",
                        "窗口": m.recent_window or "—",
                    } for m in mets]), use_container_width=True, hide_index=True)
                    cal = next((m.caliber for m in mets if m.caliber), None)
                    if cal:
                        st.info(f"**口径**：{cal}")
                uri = ents[0].uri
                st.markdown("**关系（一跳）**")
                nb = sg.neighbors(uri)
                if nb:
                    st.dataframe(pd.DataFrame([{
                        "关系": r["edge"], "指向": r["target"], "方向": r["direction"],
                    } for r in nb]), use_container_width=True, hide_index=True)
            else:
                st.warning("未识别到实体——可用 6 位代码查询全部 800 只标的。")

    # ---------------- Tab 4: 本体图谱 ----------------
    with tab_graph:
        st.subheader("本体邻接图")
        st.caption("以实体为中心的一跳关系网络（类别-实例-绑定）。")
        _render_entity_graph(sg, linker)

    # ---------------- Tab 5: 数据治理 ----------------
    with tab_gov:
        _render_governance_tab()


def _render_governance_tab() -> None:
    """数据治理：表新鲜度 SLA / 质量门禁留痕 / 指标血缘。失败降级不崩页。"""
    st.subheader("数据治理")
    st.caption("新鲜度 SLA · 质量门禁留痕 · 指标血缘（metric → 表 → 源接口 → 口径）。")

    try:
        from features.sqlite_data_layer import QIDataDB
        import json as _json
        db = QIDataDB()
    except Exception as e:
        st.warning(f"数据层不可用：{e}")
        return

    # 1) 表新鲜度（SLA 分钟）
    st.markdown("**表新鲜度**（SLA：数值表 1440 分钟 / 快照 90 分钟 / 财报 30 天）")
    sla = {"stock_spot": 90, "stock_history": 1440, "northbound_flow": 1440,
           "sector_flow": 1440, "fund_flow": 1440, "macro_indices": 1440,
           "market_breadth": 1440, "margin_trading": 1440, "stock_profile": 43200,
           "financials": 43200}
    rows = []
    try:
        import datetime as _dt
        conn = db._get_conn()
        metas = conn.execute(
            "SELECT table_name, last_updated, row_count FROM data_meta").fetchall()
        fin = conn.execute(
            "SELECT COUNT(*), MAX(updated_at) FROM financials").fetchone()
        metas.append(("financials", fin[1], fin[0]))
        for t, last, n in metas:
            try:
                age = (_dt.datetime.now() - _dt.datetime.fromisoformat(str(last))
                       ).total_seconds() / 60
            except (ValueError, TypeError):
                age = None
            s = sla.get(t, 1440)
            rows.append({
                "表": t, "行数": n or 0,
                "最近更新": str(last) if last else "—",
                "SLA(分)": s,
                "状态": "—" if age is None else ("🟢" if age <= s else
                                                 ("🟡" if age <= s * 3 else "🔴")),
                "年龄(分)": round(age, 1) if age is not None else None,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception as e:
        st.caption(f"新鲜度读取失败：{e}")

    # 2) 质量门禁留痕
    st.markdown("**质量门禁留痕**（production_check / data_quality 每次运行写一条）")
    try:
        qdf = db.get_quality_log(10)
        if qdf is not None:
            qdf["概要"] = qdf["details_json"].apply(
                lambda s: _json.loads(s).get("checks", "") if s else "")
            qdf["概要"] = qdf["概要"].apply(
                lambda s: "、".join(s[:6]) if isinstance(s, list) else str(s)[:80])
            st.dataframe(qdf[["ts", "overall", "概要"]],
                         use_container_width=True, hide_index=True)
        else:
            st.caption("尚无留痕——跑 `python scripts/data_quality.py` 生成。")
    except Exception as e:
        st.caption(f"quality_log 读取失败：{e}")

    st.caption("命令：`python scripts/production_check.py`（六道门禁）· "
               "`python scripts/data_quality.py`（质量报告）")

    # 3) 指标血缘（本体驱动，直接 SPARQL 查含 bindingApi）
    st.markdown("**指标血缘**（来自 semantic/metrics.ttl：绑定表 → 字段 → 源接口 → 口径）")
    try:
        from rdflib.namespace import RDF as _RDF
        q = '''
        SELECT ?m ?name ?tbl ?col ?api ?cal WHERE {
            ?m a qi:Metric ; qi:name ?name .
            ?m qi:bindingTable ?tbl .
            OPTIONAL { ?m qi:bindingColumn ?col }
            OPTIONAL { ?m qi:bindingApi ?api }
            OPTIONAL { ?m qi:caliber ?cal }
        } ORDER BY ?tbl ?name'''
        lineage = []
        from rdflib import Namespace as _NS
        _QI = _NS("http://quantinsight.pro/ontology/")
        for row in sg.g.query(q, initNs={"qi": _QI}):
            m_uri, name, tbl, col, api, cal = row
            lineage.append({
                "指标": str(name),
                "绑定表": str(tbl),
                "字段": str(col) if col else "",
                "源接口": str(api) if api else "",
                "口径": (str(cal) if cal else "")[:60],
            })
        if lineage:
            st.dataframe(pd.DataFrame(lineage), use_container_width=True,
                         hide_index=True,
                         column_config={"口径": st.column_config.TextColumn(width="large")})
        else:
            st.caption("无绑定型指标。")
    except Exception as e:
        st.caption(f"血缘读取失败：{e}")


def _render_entity_graph(sg, linker) -> None:
    """以查询实体为中心的 plotly 网络图；失败时退化为文字邻居表。"""
    try:
        import plotly.graph_objects as go
    except ImportError:
        st.warning("当前环境无 plotly，显示文字邻居表。")
        return

    center_name = st.text_input("中心实体（名称或代码）", "贵州茅台",
                                key="sem_graph_center")
    if not center_name:
        return
    try:
        ents = linker.link(center_name)
        if not ents:
            st.warning("未找到实体。")
            return
        uri = ents[0].uri
        nb = sg.neighbors(uri)
    except Exception as e:
        st.error(f"查询失败：{e}")
        return

    # 节点：中心 + 邻居
    import math
    xs, ys, labels, texts = [], [], [], []
    edges = []
    cx, cy = 0.0, 0.0
    xs.append(cx); ys.append(cy)
    labels.append(sg._label(uri))
    texts.append("中心实体")
    for i, r in enumerate(nb[:60]):
        ang = 2 * math.pi * i / max(len(nb), 1)
        x, y = 2.2 * math.cos(ang), 2.2 * math.sin(ang)
        xs.append(x); ys.append(y)
        labels.append(r["target"])
        texts.append(f"{r['edge']}（{'出' if r['direction'] == 'out' else '入'}）")
        edges.append((0, i + 1))

    fig = go.Figure()
    for a, b in edges:
        fig.add_trace(go.Scatter(
            x=[xs[a], xs[b]], y=[ys[a], ys[b]], mode="lines",
            line=dict(color="#888", width=1), hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="markers+text", text=labels, textposition="top center",
        marker=dict(size=[26] + [14] * (len(xs) - 1),
                    color=["#e63946"] + ["#457b9d"] * (len(xs) - 1)),
        customdata=texts, hovertemplate="%{customdata}<extra></extra>",
        showlegend=False))
    fig.update_layout(margin=dict(l=10, r=10, t=10, b=10),
                      height=560, xaxis=dict(visible=False), yaxis=dict(visible=False))
    st.plotly_chart(fig, use_container_width=True)
