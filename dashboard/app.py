"""Streamlit Dashboard(即時抓 + 快取、不落地版)。

資料來源:Yahoo 股市(價量)+ 臺灣證券交易所(三大法人)。
線圖支援 日 / 週 / 月 三種週期切換。
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
from strategy.screener import build_metrics, apply_filters, get_price_history

st.set_page_config(page_title="台股策略選股", layout="wide")
st.title("📈 台股策略選股 Dashboard")
st.caption("資料來源:Yahoo 股市(價量)＋ 臺灣證券交易所(三大法人)。即時抓取,未儲存。")


@st.cache_data(ttl=config.CACHE_TTL_SECONDS, show_spinner=False)
def load_bundle():
    return fetch_bundle()


# --- 側欄:篩選條件 ---
st.sidebar.header("篩選條件")
use_trust = st.sidebar.checkbox("投信連續買超", value=True)
trust_days = st.sidebar.number_input("連買天數 ≥", 1, 20, 3)
use_ma = st.sidebar.checkbox("股價站上月線(MA20)", value=True)
use_foreign = st.sidebar.checkbox("外資最新買超", value=False)
st.sidebar.divider()
if st.sidebar.button("🔄 立即重抓最新資料"):
    load_bundle.clear()
    st.rerun()

# --- 抓資料(首次/快取過期才真的連線)---
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

st.caption(f"資料日期:{metrics['date'].max()}　|　追蹤 {len(metrics)} 檔")

result = apply_filters(
    metrics,
    use_trust=use_trust, trust_days=trust_days,
    use_ma=use_ma, use_foreign=use_foreign,
)

# --- 篩選結果 ---
c1, c2 = st.columns(2)
c1.metric("追蹤股票數", len(metrics))
c2.metric("符合條件", len(result))

st.subheader("✅ 符合條件的標的")
display = result.copy()
if not display.empty:
    display["foreign_net"] = display["foreign_net"].apply(
        lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
st.dataframe(
    display.rename(columns={
        "stock_id": "代號", "name": "名稱", "date": "資料日",
        "close": "收盤", "ma20": "月線", "above_ma20": "站上月線",
        "trust_buy_days": "投信連買(天)", "foreign_net": "外資買賣超(股)",
    }),
    use_container_width=True, hide_index=True,
)

# --- 個股技術線圖(日/週/月切換)---
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
        unit = timeframe  # 均線單位隨週期變動(MA5 = 5日/5週/5月)
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
                x=hist["date"], y=hist[ma], name=f"MA{period}({period}{unit})",
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
