"""Streamlit Dashboard(即時抓 + 快取、不落地版)。

資料來源:Yahoo 股市(價量)+ 臺灣證券交易所(上市三大法人)+ 櫃買中心(上櫃三大法人)。
功能:可組合條件選股(AND/OR)、強勢股排名、日/週/月 K 線、單檔健檢評分、相關連結/新聞。
啟動:streamlit run dashboard/app.py
"""
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import config
from datasource import (
    fetch_bundle, fetch_single, fetch_revenue, fetch_universe, fetch_index_returns,
)
from strategy.screener import (
    build_metrics, apply_filters, get_price_history, score_stock,
    MA_PERIODS, VOL_WINDOWS, BREAKOUT_DEFAULT, BREAKOUT_MAX,
)

st.set_page_config(page_title="台股策略選股", layout="wide")
st.title("📈 台股策略選股 Dashboard")
st.caption("資料來源:Yahoo 股市(價量)＋ 臺灣證券交易所/櫃買中心(三大法人)。即時抓取,未儲存。")


@st.cache_data(ttl=config.CACHE_TTL_SECONDS, show_spinner=False)
def load_bundle(stock_ids):
    return fetch_bundle(list(stock_ids))


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def load_universe():
    return fetch_universe()


@st.cache_data(ttl=config.CACHE_TTL_SECONDS, show_spinner=False)
def load_index_returns():
    return fetch_index_returns()


@st.cache_data(ttl=config.CACHE_TTL_SECONDS, show_spinner=False)
def load_single(code):
    return fetch_single(code)


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def load_revenue(market):
    """月營收(MOPS)。月更新,快取 24 小時。market: sii(上市)/ otc(上櫃)。"""
    return fetch_revenue(market)


def quote_url(code, suffix=".TW"):
    return f"https://tw.stock.yahoo.com/quote/{code}{suffix}"


def news_url(code, name=""):
    q = urllib.parse.quote(f"{code} {name} 股票".strip())
    return f"https://news.google.com/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


# 結果/排名表共用的連結欄設定
LINK_COLS = {
    "個股": st.column_config.LinkColumn("個股", display_text="🔗 Yahoo"),
    "新聞": st.column_config.LinkColumn("新聞", display_text="📰 新聞"),
}


def make_kline_fig(hist, timeframe):
    # 依週期決定 x 軸日期格式:月→年/月,日/週→年/月/日
    tickfmt = "%Y/%m" if timeframe == "月" else "%Y/%m/%d"
    unit = {"日": "日", "週": "週", "月": "月"}[timeframe]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3], vertical_spacing=0.05,
        subplot_titles=(f"{timeframe}K 線 + 均線", f"成交量(每{unit})"),
    )
    fig.add_trace(go.Candlestick(
        x=hist["date"], open=hist["open"], high=hist["high"],
        low=hist["low"], close=hist["close"], name=f"{timeframe}K",
        increasing_line_color="red", decreasing_line_color="green",
        xhoverformat=tickfmt,
    ), row=1, col=1)
    for ma, period, color in [("ma5", 5, "orange"), ("ma20", 20, "blue"), ("ma60", 60, "purple")]:
        fig.add_trace(go.Scatter(
            x=hist["date"], y=hist[ma], name=f"MA{period}({period}{unit})",
            line=dict(width=1, color=color),
        ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=hist["date"], y=hist["volume"], name="成交量", marker_color="lightgray",
        hovertemplate="%{x|" + tickfmt + "}　成交量 %{y:,.0f} 股<extra></extra>",
    ), row=2, col=1)

    fig.update_layout(
        height=600, xaxis_rangeslider_visible=False, hovermode="x unified",
        legend=dict(orientation="h", y=1.02, yanchor="bottom"),
    )
    # x 軸日期清楚標示;日線移除週末空檔讓 K 棒連續
    fig.update_xaxes(tickformat=tickfmt, tickangle=-30, nticks=14, row=1, col=1)
    fig.update_xaxes(tickformat=tickfmt, tickangle=-30, nticks=14, row=2, col=1)
    if timeframe == "日":
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


# ==========================================================================
# 側欄:條件面板 + AND/OR
# ==========================================================================
# --- 追蹤股池:依產業選股 ---
st.sidebar.header("追蹤股池")
universe = load_universe()
industries = sorted(universe["industry"].unique()) if not universe.empty else []
sel_inds = st.sidebar.multiselect(
    "選擇產業(可多選)", industries,
    help="選定產業後,系統只抓取並分析該產業的股票。")
max_n = st.sidebar.slider("最多抓取檔數", 10, config.MAX_UNIVERSE, 60, 10,
                          help="即時抓取,檔數越多等待越久(三大法人為固定成本)。")

pool_capped = False
if sel_inds and not universe.empty:
    pool = universe[universe["industry"].isin(sel_inds)]["stock_id"].tolist()
    pool_capped = len(pool) > max_n
    pool = pool[:max_n]
    pool_label = "、".join(sel_inds)
else:
    pool = []
    pool_label = "尚未選擇"
st.sidebar.caption(f"目前股池:{pool_label}（{len(pool)} 檔）"
                   + ("　⚠️ 已截斷至上限" if pool_capped else ""))
st.sidebar.divider()

st.sidebar.header("選股條件")
logic_label = st.sidebar.radio(
    "條件組合邏輯", ["全部符合 (AND)", "任一符合 (OR)"], index=0,
    help="AND:每個勾選的條件都要成立;OR:符合任一個即入選。",
)
logic = "AND" if "AND" in logic_label else "OR"
st.sidebar.divider()

conditions = []

# 條件勾選狀態用 session_state 保存,讓「一鍵範本」能程式化設定
COND_KEYS = [
    "c_above_ma", "c_ma_bull", "c_volume", "c_breakout", "c_rs",
    "c_trust", "c_foreign_days", "c_foreign_net",
    "c_dealer_days", "c_dealer_net", "c_total_net",
    "c_rev_yoy", "c_rev_growth", "c_rev_high",
]
for _k in COND_KEYS:
    st.session_state.setdefault(_k, False)
st.session_state.setdefault("p_rev_yoy", 10)


def apply_preset(on_keys, settings=None):
    for _k in COND_KEYS:
        st.session_state[_k] = _k in on_keys
    for _k, _v in (settings or {}).items():
        st.session_state[_k] = _v


def clear_conditions():
    """清除所有已勾選的條件(用 on_click 回呼,在元件建立前執行才不會報錯)。"""
    for _k in COND_KEYS:
        st.session_state[_k] = False


st.sidebar.markdown("**🎯 一鍵策略範本**")
pc1, pc2 = st.sidebar.columns(2)
if pc1.button("⚡ 強勢股", use_container_width=True,
              help="動能型:站上均線+均線多頭排列+量增+突破新高+三大法人合計買超+贏大盤"):
    apply_preset(["c_above_ma", "c_ma_bull", "c_volume", "c_breakout", "c_total_net", "c_rs"])
    st.rerun()
if pc2.button("🌱 成長股", use_container_width=True,
              help="業績型:營收年增>20%+營收連續成長+營收創新高+站上均線+投信連買"):
    apply_preset(["c_rev_yoy", "c_rev_growth", "c_rev_high", "c_above_ma", "c_trust"],
                 {"p_rev_yoy": 20})
    st.rerun()
st.sidebar.caption("📌 可按範本一鍵套用,或自行勾選。滑鼠移到條件上有說明。")

st.sidebar.markdown("**技術面(股價/量)**")
if st.sidebar.checkbox(
        "站上均線", key="c_above_ma",
        help="收盤價站上均線,代表近期走勢偏多、買方較強。可選天期:5/10日=短線、"
             "20日(月線)=波段、60日(季線)=中長期趨勢。篩出「正在走多頭」的股票。"):
    ma_period = st.sidebar.selectbox("　└ 均線周期(日)", MA_PERIODS, index=MA_PERIODS.index(20))
    conditions.append(("above_ma", {"period": ma_period}))
if st.sidebar.checkbox(
        "均線多頭排列", key="c_ma_bull",
        help="短中長期均線由上而下排好(5MA>20MA>60MA),代表趨勢全面向上、呈攻擊隊形,"
             "是強勢股的典型型態。篩出「趨勢全面轉強」的股票。"):
    conditions.append(("ma_bull", {}))
if st.sidebar.checkbox(
        "成交量放大(量增)", key="c_volume",
        help="當日成交量明顯高於近期均量,代表市場關注度與資金進場增加(量先價行),"
             "常見於發動或轉強初期。篩出「突然爆量、有人在買」的股票。"):
    w = st.sidebar.selectbox("　└ 對比均量(日)", VOL_WINDOWS, index=0)
    r = st.sidebar.slider("　└ 放大倍數 ≥", 1.0, 3.0, 1.5, 0.1)
    conditions.append(("volume", {"window": w, "ratio": r}))
if st.sidebar.checkbox(
        "突破 N 日新高", key="c_breakout",
        help="收盤價創最近 N 日的新高,代表突破前波壓力、強勢表態。"
             "篩出「剛突破、走勢轉強」的股票。"):
    w = st.sidebar.number_input("　└ 區間天數(可自由輸入)", 5, BREAKOUT_MAX, BREAKOUT_DEFAULT,
                                help="例:20=月新高、60=季新高、120=半年新高。"
                                     "受可用歷史約一年(~250 交易日)限制。")
    conditions.append(("breakout", {"window": int(w)}))
if st.sidebar.checkbox(
        "相對強度(贏大盤)", key="c_rs",
        help="個股漲幅扣掉大盤(加權指數)同期漲幅 > 門檻,代表比大盤強(大盤跌它抗跌、"
             "大盤漲它更會噴)。是判斷強勢股的核心指標。篩出「比大盤強」的股票。"):
    rs_p = st.sidebar.radio("　└ 比較區間", [3, 30], index=1, horizontal=True,
                            format_func=lambda x: f"{x}日", key="p_rs_period")
    rs_min = st.sidebar.slider("　└ 至少贏大盤 (%)", -10, 30, 0, key="p_rs_min")
    conditions.append(("rs", {"period": rs_p, "min_rs": rs_min}))

st.sidebar.markdown("**籌碼面(三大法人)**")
if st.sidebar.checkbox(
        "投信連續買超", key="c_trust",
        help="投信=國內基金。它偏好基本面好、做波段,連續買進常被視為「波段轉強」訊號,"
             "且有作帳行情題材。篩出「國內基金正在連續買」的股票。"):
    d = st.sidebar.number_input("　└ 投信連買天數 ≥", 1, 12, 3)
    conditions.append(("trust", {"days": d}))
if st.sidebar.checkbox(
        "外資連續買超", key="c_foreign_days",
        help="外資=國際資金,部位大、影響盤面。連續買進代表國際資金看好,"
             "是大型權值股重要的多方力道。篩出「外資連續買」的股票。"):
    d = st.sidebar.number_input("　└ 外資連買天數 ≥", 1, 12, 3)
    conditions.append(("foreign_days", {"days": d}))
if st.sidebar.checkbox(
        "外資最新買超(當日)", key="c_foreign_net",
        help="只看「最近一個交易日」外資是否買超。比連買寬鬆,觀察當天外資態度。"):
    conditions.append(("foreign_net", {}))
if st.sidebar.checkbox(
        "自營商連續買超", key="c_dealer_days",
        help="自營商=券商自有資金,多為短線/避險操作,訊號參考性低於外資與投信,"
             "適合搭配其他條件一起看。"):
    d = st.sidebar.number_input("　└ 自營商連買天數 ≥", 1, 12, 3)
    conditions.append(("dealer_days", {"days": d}))
if st.sidebar.checkbox(
        "自營商最新買超(當日)", key="c_dealer_net",
        help="只看最近一個交易日自營商是否買超。"):
    conditions.append(("dealer_net", {}))
if st.sidebar.checkbox(
        "三大法人合計買超(當日)", key="c_total_net",
        help="外資+投信+自營商當日合計為買超,代表法人整體偏多,"
             "比單看一隻法人更能反映法人對該股的整體態度。"):
    conditions.append(("total_net", {}))

st.sidebar.markdown("**基本面(月營收)**")
if st.sidebar.checkbox(
        "營收年增達標", key="c_rev_yoy",
        help="當月營收比去年同月成長達到設定的 %(去除淡旺季影響)。代表公司本業在成長,"
             "是基本面轉強的指標。篩出「生意越做越大」的股票。"):
    v = st.sidebar.slider("　└ 營收年增 ≥ (%)", -20, 100, key="p_rev_yoy")
    conditions.append(("rev_yoy", {"min_yoy": v}))
if st.sidebar.checkbox(
        "營收連續成長", key="c_rev_growth",
        help="營收年增「連續多個月」都是正的,代表成長有持續性而非曇花一現。"
             "篩出「成長動能穩定」的股票。"):
    n = st.sidebar.number_input("　└ 連續成長 ≥ (月)", 1, 12, 3)
    conditions.append(("rev_growth", {"months": n}))
if st.sidebar.checkbox(
        "營收創近一年新高", key="c_rev_high",
        help="當月營收是近一年來最高,代表營運動能強勁、可能進入成長爆發期。"):
    conditions.append(("rev_high", {}))

st.sidebar.divider()
if st.sidebar.button("🔄 立即重抓最新資料"):
    load_bundle.clear()
    load_revenue.clear()
    st.rerun()
st.sidebar.button("🧹 清除目前選擇條件", on_click=clear_conditions, use_container_width=True,
                  help="把所有已勾選的選股條件取消,回到全部未勾的狀態。")


# ==========================================================================
# 篩選 + 強勢股排名(需先選產業才會執行)
# ==========================================================================
def render_screening():
    with st.spinner(f"正在抓取 {len(pool)} 檔股票資料(Yahoo / 證交所 / MOPS)…"
                    "(檔數多時首次需數十秒~數分鐘,之後快取秒開)"):
        try:
            bundle = load_bundle(tuple(pool))
            revenue_sii = load_revenue("sii")
            index_ret = load_index_returns()
            metrics = build_metrics(bundle, revenue_sii, index_ret)
        except Exception as e:  # noqa: BLE001
            st.error(f"抓取資料失敗,請稍後再試或檢查網路。\n\n{e}")
            return

    if metrics.empty:
        st.warning("抓不到資料,請稍候再按「立即重抓最新資料」。")
        return

    # 用全市場清單補上正確的名稱與產業別
    metrics = metrics.copy()
    if not universe.empty:
        nm = dict(zip(universe["stock_id"], universe["name"]))
        im = dict(zip(universe["stock_id"], universe["industry"]))
        metrics["name"] = metrics["stock_id"].map(nm).fillna(metrics["name"])
        metrics["industry"] = metrics["stock_id"].map(im).fillna("")
    else:
        metrics["industry"] = ""
    metrics["個股"] = metrics["stock_id"].apply(quote_url)
    metrics["新聞"] = metrics.apply(lambda r: news_url(r["stock_id"], r["name"]), axis=1)

    result = apply_filters(metrics, conditions, logic)

    cond_text = {
        "above_ma": lambda p: f"站上{p['period']}日線",
        "ma_bull": lambda p: "均線多頭排列",
        "rs": lambda p: f"贏大盤≥{p['min_rs']}%({p['period']}日)",
        "trust": lambda p: f"投信連買≥{p['days']}天",
        "foreign_days": lambda p: f"外資連買≥{p['days']}天",
        "foreign_net": lambda p: "外資當日買超",
        "dealer_days": lambda p: f"自營商連買≥{p['days']}天",
        "dealer_net": lambda p: "自營商當日買超",
        "total_net": lambda p: "三大法人合計買超",
        "volume": lambda p: f"量>{p['ratio']}倍{p['window']}日均量",
        "breakout": lambda p: f"突破{p['window']}日新高",
        "rev_yoy": lambda p: f"營收年增≥{p['min_yoy']}%",
        "rev_growth": lambda p: f"營收連續成長≥{p['months']}月",
        "rev_high": lambda p: "營收創近一年新高",
    }
    active_desc = "、".join(cond_text[k](p) for k, p in conditions) or "(未設條件,顯示全部)"
    st.caption(f"資料日期:{metrics['date'].max()}　|　追蹤 {len(metrics)} 檔　|　"
               f"條件【{logic}】:{active_desc}")

    c1, c2 = st.columns(2)
    c1.metric("追蹤股票數", len(metrics))
    c2.metric("符合條件", len(result))

    # --- 篩選結果表(含個股 / 新聞連結)---
    st.subheader("✅ 符合條件的標的")
    display = result.copy()
    if not display.empty:
        display["量比5"] = (display["volume"] / display["vol_ma5"]).round(2)
        display["foreign_net"] = display["foreign_net"].apply(
            lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
        display["三大法人合計"] = display["total_net"].apply(
            lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
        display["營收年增"] = display["rev_yoy"].apply(
            lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")
    display = display.reindex(columns=[
        "stock_id", "name", "date", "close", "ma20",
        "trust_buy_days", "foreign_buy_days", "dealer_buy_days",
        "foreign_net", "三大法人合計",
        "營收年增", "rev_growth_months", "量比5", "個股", "新聞"])
    st.dataframe(
        display.rename(columns={
            "stock_id": "代號", "name": "名稱", "date": "資料日", "close": "收盤",
            "ma20": "月線(MA20)", "trust_buy_days": "投信連買(天)",
            "foreign_buy_days": "外資連買(天)", "dealer_buy_days": "自營商連買(天)",
            "foreign_net": "外資買賣超(股)", "三大法人合計": "三大法人合計(股)",
            "rev_growth_months": "營收連續成長(月)",
        }),
        use_container_width=True, hide_index=True, column_config=LINK_COLS,
    )

    # --- 強勢股排名(3日 / 30日 漲幅)---
    st.subheader("🏆 強勢股排名(依區間漲幅)")
    period = st.radio("排名區間", ["3 日", "30 日"], horizontal=True, index=1,
                      help="以 N 個交易日前到今天的收盤價漲跌幅排序。")
    col = "ret_3d" if period.startswith("3 ") else "ret_30d"
    rank = metrics[["stock_id", "name", "industry", "close", col, "個股", "新聞"]].dropna(subset=[col]).copy()
    rank = rank.sort_values(col, ascending=False).reset_index(drop=True)
    rank.insert(0, "名次", rank.index + 1)
    rank["漲跌幅"] = rank[col].apply(lambda v: f"{v:+.2f}%")
    rank = rank[["名次", "stock_id", "name", "industry", "close", "漲跌幅", "個股", "新聞"]]
    st.dataframe(
        rank.rename(columns={"stock_id": "代號", "name": "名稱",
                             "industry": "產業", "close": "收盤"}),
        use_container_width=True, hide_index=True, column_config=LINK_COLS,
    )

    # --- 清單內個股線圖 ---
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


if pool:
    render_screening()
else:
    st.info("👈 此區為「篩選 + 強勢股排名」:請先從左側「追蹤股池」**選擇產業**並設定條件即可顯示。"
            "(下方「個股健檢」可直接輸入代號查詢,**不需**選產業)")


# ==========================================================================
# 🔎 個股健檢:輸入任意代號 → 資訊 + 評分 + 建議 + 連結 + 新聞
# ==========================================================================
st.divider()
st.subheader("🔎 個股健檢(輸入任意代號)")
code = st.text_input("輸入股票代號(例:2330、2317、6488)", value="").strip()

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
        _ticker = sbundle["stocks"]["ticker"].iloc[0]
        srevenue = load_revenue("otc" if _ticker.endswith(".TWO") else "sii")
        smetrics = build_metrics(sbundle, srevenue)
        if smetrics.empty:
            st.warning("資料不足,無法分析。")
        else:
            row = smetrics.iloc[0]
            sc = score_stock(row)
            name = sbundle["stocks"]["name"].iloc[0]
            ticker = sbundle["stocks"]["ticker"].iloc[0]
            suffix = ".TWO" if ticker.endswith(".TWO") else ".TW"

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

            if row.get("rev_yoy") is not None:
                hi = "、創近一年新高 🔥" if row.get("rev_high") else ""
                st.caption(f'最新月營收({row["rev_month"]}):年增 {row["rev_yoy"]:+.1f}%、'
                           f'年增連續為正 {row["rev_growth_months"]} 個月{hi}')

            if sbundle["inst"].empty:
                st.caption("⚠️ 查無此股三大法人資料,籌碼面項目以 0 計分。")

            st.markdown("**評分明細**")
            st.dataframe(pd.DataFrame(sc["factors"]),
                         use_container_width=True, hide_index=True)

            st.markdown(
                "**相關連結:** "
                + f"[Yahoo 股市]({quote_url(code, suffix)})　｜　"
                + f"[📰 即時新聞]({news_url(code, name)})　｜　"
                + f"[Yahoo 個股新聞](https://tw.stock.yahoo.com/quote/{ticker}/news)　｜　"
                + f"[Goodinfo](https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={code})　｜　"
                + f"[玩股網](https://www.wantgoo.com/stock/{code})　｜　"
                + f"[公開資訊觀測站](https://mops.twse.com.tw/mops/web/t05st01?stockNo={code})"
            )

            # 本健檢各項數據的實際出處(資料來源)
            insti_src = (
                "[證交所 三大法人(T86)](https://www.twse.com.tw/zh/trading/foreign/t86.html)"
                if suffix == ".TW" else
                "[櫃買中心 三大法人](https://www.tpex.org.tw/zh-tw/mainboard/trading/major-institutional/day.html)"
            )
            st.markdown(
                "**📚 資料來源(本健檢數據出處):** "
                + f"價量/K線→[Yahoo 股市]({quote_url(code, suffix)})　｜　"
                + f"三大法人→{insti_src}　｜　"
                + "月營收→[公開資訊觀測站(MOPS)](https://mops.twse.com.tw/mops/#/web/t05st10_ifrs)"
            )

            tf2 = st.radio("時間週期", ["日", "週", "月"], horizontal=True, index=0, key="single_tf")
            h2 = get_price_history(sbundle, code, tf2)
            if not h2.empty:
                st.plotly_chart(make_kline_fig(h2, tf2), use_container_width=True)
