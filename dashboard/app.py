"""Streamlit Dashboard(即時抓 + 快取、不落地版)。

資料來源:Yahoo 股市(價量)+ 臺灣證券交易所(三大法人)。
功能:可組合條件選股(AND/OR、即時更新)、日/週/月 K 線、單檔健檢評分。
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
from datasource import fetch_bundle, fetch_single
from strategy.screener import (
    build_metrics, apply_filters, get_price_history, score_stock,
    MA_PERIODS, VOL_WINDOWS, BREAKOUT_WINDOWS,
)

st.set_page_config(page_title="台股策略選股", layout="wide")
st.title("📈 台股策略選股 Dashboard")
st.caption("資料來源:Yahoo 股市(價量)＋ 臺灣證券交易所(三大法人)。即時抓取,未儲存。")


@st.cache_data(ttl=config.CACHE_TTL_SECONDS, show_spinner=False)
def load_bundle():
    return fetch_bundle()


@st.cache_data(ttl=config.CACHE_TTL_SECONDS, show_spinner=False)
def load_single(code):
    return fetch_single(code)


def make_kline_fig(hist, timeframe):
    """以 hist(含 ma5/20/60)畫 K 線 + 均線 + 成交量。"""
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
        x=hist["date"], y=hist["volume"], name="成交量", marker_color="lightgray",
    ), row=2, col=1)
    fig.update_layout(
        height=600, xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.02, yanchor="bottom"),
    )
    return fig


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

conditions = []
if st.sidebar.checkbox("站上均線", value=True):
    ma_period = st.sidebar.selectbox("　└ 均線周期(日)", MA_PERIODS, index=MA_PERIODS.index(20))
    conditions.append(("above_ma", {"period": ma_period}))
if st.sidebar.checkbox("投信連續買超", value=True):
    d = st.sidebar.number_input("　└ 投信連買天數 ≥", 1, 12, 3)
    conditions.append(("trust", {"days": d}))
if st.sidebar.checkbox("外資連續買超", value=False):
    d = st.sidebar.number_input("　└ 外資連買天數 ≥", 1, 12, 3)
    conditions.append(("foreign_days", {"days": d}))
if st.sidebar.checkbox("外資最新買超(當日)", value=False):
    conditions.append(("foreign_net", {}))
if st.sidebar.checkbox("成交量放大(量增)", value=False):
    w = st.sidebar.selectbox("　└ 對比均量(日)", VOL_WINDOWS, index=0)
    r = st.sidebar.slider("　└ 放大倍數 ≥", 1.0, 3.0, 1.5, 0.1)
    conditions.append(("volume", {"window": w, "ratio": r}))
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

# 清單內個股線圖
st.subheader("📊 清單內個股技術線圖")
col_a, col_b = st.columns([3, 2])
options = result["stock_id"].tolist() if not result.empty else metrics["stock_id"].tolist()
labels = {r["stock_id"]: f'{r["stock_id"]} {r["name"]}' for _, r in metrics.iterrows()}
with col_a:
    sel = st.selectbox("選擇股票", options, format_func=lambda s: labels.get(s, s))
with col_b:
    tf = st.radio("時間週期", ["日", "週", "月"], horizontal=True, index=0, key="list_tf")
if sel:
    hist = get_price_history(bundle, sel, tf)
    if not hist.empty:
        st.plotly_chart(make_kline_fig(hist, tf), use_container_width=True)


# ==========================================================================
# 🔎 個股健檢:輸入任意代號 → 資訊 + 評分 + 建議 + 連結
# ==========================================================================
st.divider()
st.subheader("🔎 個股健檢(輸入任意代號)")
code = st.text_input("輸入股票代號(例:2330、2317、6446)", value="").strip()

if code:
    with st.spinner(f"抓取 {code} 的資料中…(約需 10~20 秒)"):
        try:
            sbundle = load_single(code)
        except Exception as e:  # noqa: BLE001
            st.error(f"抓取失敗:{e}")
            st.stop()

    if sbundle["price"].empty:
        st.warning(f"找不到代號「{code}」的資料,請確認代號是否正確(支援上市/上櫃)。")
    else:
        smetrics = build_metrics(sbundle)
        if smetrics.empty:
            st.warning("資料不足,無法分析。")
        else:
            row = smetrics.iloc[0]
            sc = score_stock(row)
            name = sbundle["stocks"]["name"].iloc[0]
            ticker = sbundle["stocks"]["ticker"].iloc[0]

            # 漲跌
            hp = get_price_history(sbundle, code, "日")
            chg = chgpct = None
            if len(hp) >= 2:
                prev, cur = hp["close"].iloc[-2], hp["close"].iloc[-1]
                chg, chgpct = cur - prev, (cur - prev) / prev * 100

            st.markdown(f"### {code} {name}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("收盤", f'{row["close"]}',
                      f'{chg:+.2f} ({chgpct:+.2f}%)' if chg is not None else None)
            m2.metric("健檢評分", f'{sc["score"]} / 100')
            m3.metric("綜合研判", sc["grade"])
            m4.metric("資料日", str(row["date"]))

            st.info(f'**建議:** {sc["suggestion"]}　_（此為機械式指標量化,僅供參考,非投資建議）_')

            if sbundle["inst"].empty:
                st.caption("⚠️ 此股無證交所三大法人資料(可能為上櫃股),籌碼面項目以 0 計分。")

            # 評分明細
            st.markdown("**評分明細**")
            st.dataframe(pd.DataFrame(sc["factors"]),
                         use_container_width=True, hide_index=True)

            # 外部連結
            st.markdown(
                "**外部資源:** "
                + f"[Yahoo 股市](https://tw.stock.yahoo.com/quote/{ticker})　｜　"
                + f"[Goodinfo](https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={code})　｜　"
                + f"[玩股網](https://www.wantgoo.com/stock/{code})　｜　"
                + f"[公開資訊觀測站](https://mops.twse.com.tw/mops/web/t05st01?stockNo={code})"
            )

            # 線圖(日/週/月)
            tf2 = st.radio("時間週期", ["日", "週", "月"], horizontal=True, index=0, key="single_tf")
            h2 = get_price_history(sbundle, code, tf2)
            if not h2.empty:
                st.plotly_chart(make_kline_fig(h2, tf2), use_container_width=True)
