"""Streamlit Dashboard(即時抓 + 快取、不落地版)。

資料來源:Yahoo 股市(價量)+ 臺灣證券交易所(三大法人)。
條件面板:可自由勾選/調參、AND/OR 切換,結果即時更新。線圖支援 日/週/月 切換。
啟動:streamlit run dashboard/app.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import config
from datasource import fetch_bundle
from strategy.screener import (
    build_metrics, apply_filters, get_price_history,
    MA_PERIODS, VOL_WINDOWS, BREAKOUT_WINDOWS,
)

st.set_page_config(page_title="台股策略選股", layout="wide")
st.title("📈 台股策略選股 Dashboard")
st.caption("資料來源:Yahoo 股市(價量)＋ 臺灣證券交易所(三大法人)。即時抓取,未儲存。")


@st.cache_data(ttl=config.CACHE_TTL_SECONDS, show_spinner=False)
def load_bundle():
    return fetch_bundle()


# ==========================================================================
# 側欄:條件面板(勾選 → 展開參數),AND/OR 切換
# ==========================================================================
st.sidebar.header("選股條件")
logic_label = st.sidebar.radio(
    "條件組合邏輯", ["全部符合 (AND)", "任一符合 (OR)"], index=0,
    help="AND:每個勾選的條件都要成立;OR:符合任一個即入選。",
)
logic = "AND" if "AND" in logic_label else "OR"
st.sidebar.divider()

conditions = []  # list[(key, params)]

# 1) 站上均線(周期可選)
if st.sidebar.checkbox("站上均線", value=True):
    ma_period = st.sidebar.selectbox("　└ 均線周期(日)", MA_PERIODS, index=MA_PERIODS.index(20))
    conditions.append(("above_ma", {"period": ma_period}))

# 2) 投信連續買超
if st.sidebar.checkbox("投信連續買超", value=True):
    d = st.sidebar.number_input("　└ 投信連買天數 ≥", 1, 12, 3)
    conditions.append(("trust", {"days": d}))

# 3) 外資連續買超
if st.sidebar.checkbox("外資連續買超", value=False):
    d = st.sidebar.number_input("　└ 外資連買天數 ≥", 1, 12, 3)
    conditions.append(("foreign_days", {"days": d}))

# 4) 外資最新買超
if st.sidebar.checkbox("外資最新買超(當日)", value=False):
    conditions.append(("foreign_net", {}))

# 5) 成交量放大(量增)
if st.sidebar.checkbox("成交量放大(量增)", value=False):
    w = st.sidebar.selectbox("　└ 對比均量(日)", VOL_WINDOWS, index=0)
    r = st.sidebar.slider("　└ 放大倍數 ≥", 1.0, 3.0, 1.5, 0.1)
    conditions.append(("volume", {"window": w, "ratio": r}))

# 6) 突破 N 日新高
if st.sidebar.checkbox("突破 N 日新高", value=False):
    w = st.sidebar.selectbox("　└ 區間天數", BREAKOUT_WINDOWS, index=0)
    conditions.append(("breakout", {"window": w}))

st.sidebar.divider()
if st.sidebar.button("🔄 立即重抓最新資料"):
    load_bundle.clear()
    st.rerun()


# ==========================================================================
# 抓資料 + 算指標 + 套用條件
# ==========================================================================
with st.spinner("正在向 Yahoo 與證交所抓取最新資料…(首次約需數十秒,之後快取秒開)"):
    try:
        bundle = load_bundle()
        metrics = build_metrics(bundle)
    except Exception as e:  # noqa: BLE001
        st.error(f"抓取資料失敗,請稍後再試或檢查網路。\n\n{e}")
        st.stop()

if metrics.empty:
    st.warning("抓不到資料,請稍候再按「立即重抓最新資料」。")
    st.stop()

result = apply_filters(metrics, conditions, logic)

# 目前生效的條件,顯示成一行說明
cond_text = {
    "above_ma": lambda p: f"站上{p['period']}日線",
    "trust": lambda p: f"投信連買≥{p['days']}天",
    "foreign_days": lambda p: f"外資連買≥{p['days']}天",
    "foreign_net": lambda p: "外資當日買超",
    "volume": lambda p: f"量>{p['ratio']}倍{p['window']}日均量",
    "breakout": lambda p: f"突破{p['window']}日新高",
}
active_desc = "、".join(cond_text[k](p) for k, p in conditions) or "(未設條件,顯示全部)"
st.caption(f"資料日期:{metrics['date'].max()}　|　追蹤 {len(metrics)} 檔　|　"
           f"條件【{logic}】:{active_desc}")

c1, c2 = st.columns(2)
c1.metric("追蹤股票數", len(metrics))
c2.metric("符合條件", len(result))

# ==========================================================================
# 篩選結果表
# ==========================================================================
st.subheader("✅ 符合條件的標的")
display = result.copy()
if not display.empty:
    display["量比5"] = (display["volume"] / display["vol_ma5"]).round(2)
    display["foreign_net"] = display["foreign_net"].apply(
        lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
    cols = ["stock_id", "name", "date", "close", "ma20",
            "trust_buy_days", "foreign_buy_days", "foreign_net", "量比5"]
    display = display[cols]
st.dataframe(
    display.rename(columns={
        "stock_id": "代號", "name": "名稱", "date": "資料日", "close": "收盤",
        "ma20": "月線(MA20)", "trust_buy_days": "投信連買(天)",
        "foreign_buy_days": "外資連買(天)", "foreign_net": "外資買賣超(股)",
    }),
    use_container_width=True, hide_index=True,
)

# ==========================================================================
# 個股技術線圖(日/週/月切換)
# ==========================================================================
st.subheader("📊 個股技術線圖")
col_a, col_b = st.columns([3, 2])
options = result["stock_id"].tolist() if not result.empty else metrics["stock_id"].tolist()
labels = {r["stock_id"]: f'{r["stock_id"]} {r["name"]}' for _, r in metrics.iterrows()}
with col_a:
    sel = st.selectbox("選擇股票", options, format_func=lambda s: labels.get(s, s))
with col_b:
    timeframe = st.radio("時間週期", ["日", "週", "月"], horizontal=True, index=0)

if sel:
    hist = get_price_history(bundle, sel, timeframe)
    if hist.empty:
        st.info("該股無價格資料。")
    else:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.7, 0.3], vertical_spacing=0.03,
            subplot_titles=(f"{timeframe}K 線 + 均線", "成交量"),
        )
        fig.add_trace(go.Candlestick(
            x=hist["date"], open=hist["open"], high=hist["high"],
            low=hist["low"], close=hist["close"], name=f"{timeframe}K",
            increasing_line_color="red", decreasing_line_color="green",
        ), row=1, col=1)
        for ma, period, color in [("ma5", 5, "orange"), ("ma20", 20, "blue"), ("ma60", 60, "purple")]:
            fig.add_trace(go.Scatter(
                x=hist["date"], y=hist[ma], name=f"MA{period}({period}{timeframe})",
                line=dict(width=1, color=color),
            ), row=1, col=1)
        fig.add_trace(go.Bar(
            x=hist["date"], y=hist["volume"], name="成交量",
            marker_color="lightgray",
        ), row=2, col=1)
        fig.update_layout(
            height=600, xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", y=1.02, yanchor="bottom"),
        )
        st.plotly_chart(fig, use_container_width=True)
