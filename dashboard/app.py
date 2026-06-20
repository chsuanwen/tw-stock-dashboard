"""Streamlit Dashboard(即時抓 + 快取、不落地版,多語系)。

資料來源:Yahoo 股市(價量)+ 臺灣證券交易所/櫃買中心(三大法人)+ 公開資訊觀測站(月營收)。
介面語言:繁中 / English / 日本語 / 한국어(右上角切換)。
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
import i18n
from datasource import (
    fetch_bundle, fetch_single, fetch_revenue, fetch_universe, fetch_index_returns,
)
from strategy.screener import (
    build_metrics, apply_filters, get_price_history, score_stock,
    MA_PERIODS, VOL_WINDOWS, BREAKOUT_DEFAULT, BREAKOUT_MAX,
)

st.set_page_config(page_title="台股策略選股", layout="wide")

# --- 語言切換(右上角)---
_ct, _cl = st.columns([5, 1])
with _cl:
    _lang_name = st.selectbox("🌐", list(i18n.LANGS.keys()),
                              label_visibility="collapsed", key="lang_sel")
lang = i18n.LANGS[_lang_name]


def t(key, **kw):
    return i18n.tr(lang, key, **kw)


with _ct:
    st.title(t("app_title"))
st.caption(t("src_caption"))

TF_CODES = ["D", "W", "M"]
TF_KEY = {"D": "tf_day", "W": "tf_week", "M": "tf_month"}


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
    return fetch_revenue(market)


def quote_url(code, suffix=".TW"):
    return f"https://tw.stock.yahoo.com/quote/{code}{suffix}"


def news_url(code, name=""):
    q = urllib.parse.quote(f"{code} {name}".strip())
    return f"https://news.google.com/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


def link_cols():
    return {
        "個股": st.column_config.LinkColumn(t("link_stock"), display_text=t("link_stock_text")),
        "新聞": st.column_config.LinkColumn(t("link_news"), display_text=t("link_news_text")),
    }


def make_kline_fig(hist, tf_code):
    tickfmt = "%Y/%m" if tf_code == "M" else "%Y/%m/%d"
    unit = t(TF_KEY[tf_code])
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3], vertical_spacing=0.05,
        subplot_titles=(t("chart_kline", tf=unit), t("chart_vol", u=unit)),
    )
    fig.add_trace(go.Candlestick(
        x=hist["date"], open=hist["open"], high=hist["high"],
        low=hist["low"], close=hist["close"], name=f"{unit}K",
        increasing_line_color="red", decreasing_line_color="green",
        xhoverformat=tickfmt,
    ), row=1, col=1)
    for ma, period, color in [("ma5", 5, "orange"), ("ma20", 20, "blue"), ("ma60", 60, "purple")]:
        fig.add_trace(go.Scatter(
            x=hist["date"], y=hist[ma], name=f"MA{period}({period}{unit})",
            line=dict(width=1, color=color),
        ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=hist["date"], y=hist["volume"], name=t("chart_vol_name"), marker_color="lightgray",
        hovertemplate="%{x|" + tickfmt + "}　" + t("chart_vol_hover") + "<extra></extra>",
    ), row=2, col=1)
    fig.update_layout(
        height=600, xaxis_rangeslider_visible=False, hovermode="x unified",
        legend=dict(orientation="h", y=1.02, yanchor="bottom"),
    )
    fig.update_xaxes(tickformat=tickfmt, tickangle=-30, nticks=14, row=1, col=1)
    fig.update_xaxes(tickformat=tickfmt, tickangle=-30, nticks=14, row=2, col=1)
    if tf_code == "D":
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig


# ==========================================================================
# 側欄:追蹤股池
# ==========================================================================
st.sidebar.header(t("sb_pool_header"))
universe = load_universe()
industries = sorted(universe["industry"].unique()) if not universe.empty else []
sel_inds = st.sidebar.multiselect(t("pool_select"), industries, help=t("pool_select_help"))
max_n = st.sidebar.slider(t("pool_max"), 10, config.MAX_UNIVERSE, 60, 10, help=t("pool_max_help"))

pool_capped = False
if sel_inds and not universe.empty:
    pool = universe[universe["industry"].isin(sel_inds)]["stock_id"].tolist()
    pool_capped = len(pool) > max_n
    pool = pool[:max_n]
    pool_label = "、".join(sel_inds)
else:
    pool = []
    pool_label = t("pool_none")
st.sidebar.caption(t("pool_now", label=pool_label, n=len(pool))
                   + (t("pool_capped") if pool_capped else ""))
st.sidebar.divider()

# ==========================================================================
# 側欄:選股條件
# ==========================================================================
st.sidebar.header(t("cond_header"))
logic_label = st.sidebar.radio(t("logic_label"), [t("logic_and"), t("logic_or")],
                               index=0, help=t("logic_help"))
logic = "OR" if logic_label == t("logic_or") else "AND"
st.sidebar.divider()

conditions = []
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
    for _k in COND_KEYS:
        st.session_state[_k] = False


st.sidebar.markdown("**" + t("preset_header") + "**")
pc1, pc2 = st.sidebar.columns(2)
if pc1.button(t("preset_strong"), use_container_width=True, help=t("preset_strong_help")):
    apply_preset(["c_above_ma", "c_ma_bull", "c_volume", "c_breakout", "c_total_net", "c_rs"])
    st.rerun()
if pc2.button(t("preset_growth"), use_container_width=True, help=t("preset_growth_help")):
    apply_preset(["c_rev_yoy", "c_rev_growth", "c_rev_high", "c_above_ma", "c_trust"],
                 {"p_rev_yoy": 20})
    st.rerun()
st.sidebar.caption(t("preset_caption"))

st.sidebar.markdown("**" + t("grp_tech") + "**")
if st.sidebar.checkbox(t("c_above_ma"), key="c_above_ma", help=t("c_above_ma_help")):
    ma_period = st.sidebar.selectbox(t("lbl_ma_period"), MA_PERIODS, index=MA_PERIODS.index(20))
    conditions.append(("above_ma", {"period": ma_period}))
if st.sidebar.checkbox(t("c_ma_bull"), key="c_ma_bull", help=t("c_ma_bull_help")):
    conditions.append(("ma_bull", {}))
if st.sidebar.checkbox(t("c_volume"), key="c_volume", help=t("c_volume_help")):
    w = st.sidebar.selectbox(t("lbl_vol_window"), VOL_WINDOWS, index=0)
    r = st.sidebar.slider(t("lbl_vol_ratio"), 1.0, 3.0, 1.5, 0.1)
    conditions.append(("volume", {"window": w, "ratio": r}))
if st.sidebar.checkbox(t("c_breakout"), key="c_breakout", help=t("c_breakout_help")):
    w = st.sidebar.number_input(t("lbl_breakout_days"), 5, BREAKOUT_MAX, BREAKOUT_DEFAULT,
                                help=t("lbl_breakout_days_help"))
    conditions.append(("breakout", {"window": int(w)}))
if st.sidebar.checkbox(t("c_rs"), key="c_rs", help=t("c_rs_help")):
    rs_p = st.sidebar.radio(t("lbl_rs_period"), [3, 30], index=1, horizontal=True,
                            format_func=lambda x: f"{x}{t('tf_day')}", key="p_rs_period")
    rs_min = st.sidebar.slider(t("lbl_rs_min"), -10, 30, 0, key="p_rs_min")
    conditions.append(("rs", {"period": rs_p, "min_rs": rs_min}))

st.sidebar.markdown("**" + t("grp_chip") + "**")
if st.sidebar.checkbox(t("c_trust"), key="c_trust", help=t("c_trust_help")):
    d = st.sidebar.number_input(t("lbl_trust_days"), 1, 12, 3)
    conditions.append(("trust", {"days": d}))
if st.sidebar.checkbox(t("c_foreign_days"), key="c_foreign_days", help=t("c_foreign_days_help")):
    d = st.sidebar.number_input(t("lbl_foreign_days"), 1, 12, 3)
    conditions.append(("foreign_days", {"days": d}))
if st.sidebar.checkbox(t("c_foreign_net"), key="c_foreign_net", help=t("c_foreign_net_help")):
    conditions.append(("foreign_net", {}))
if st.sidebar.checkbox(t("c_dealer_days"), key="c_dealer_days", help=t("c_dealer_days_help")):
    d = st.sidebar.number_input(t("lbl_dealer_days"), 1, 12, 3)
    conditions.append(("dealer_days", {"days": d}))
if st.sidebar.checkbox(t("c_dealer_net"), key="c_dealer_net", help=t("c_dealer_net_help")):
    conditions.append(("dealer_net", {}))
if st.sidebar.checkbox(t("c_total_net"), key="c_total_net", help=t("c_total_net_help")):
    conditions.append(("total_net", {}))

st.sidebar.markdown("**" + t("grp_fund") + "**")
if st.sidebar.checkbox(t("c_rev_yoy"), key="c_rev_yoy", help=t("c_rev_yoy_help")):
    v = st.sidebar.slider(t("lbl_rev_yoy"), -20, 100, key="p_rev_yoy")
    conditions.append(("rev_yoy", {"min_yoy": v}))
if st.sidebar.checkbox(t("c_rev_growth"), key="c_rev_growth", help=t("c_rev_growth_help")):
    n = st.sidebar.number_input(t("lbl_rev_growth"), 1, 12, 3)
    conditions.append(("rev_growth", {"months": n}))
if st.sidebar.checkbox(t("c_rev_high"), key="c_rev_high", help=t("c_rev_high_help")):
    conditions.append(("rev_high", {}))

st.sidebar.divider()
bc1, bc2 = st.sidebar.columns(2)
if bc1.button(t("btn_refetch"), use_container_width=True, help=t("btn_refetch_help")):
    load_bundle.clear()
    load_revenue.clear()
    st.rerun()
bc2.button(t("btn_clear"), on_click=clear_conditions, use_container_width=True,
           help=t("btn_clear_help"))


# ==========================================================================
# 篩選 + 強勢股排名(需先選產業才會執行)
# ==========================================================================
def render_screening():
    with st.spinner(t("spinner_fetch", n=len(pool))):
        try:
            bundle = load_bundle(tuple(pool))
            revenue_sii = load_revenue("sii")
            index_ret = load_index_returns()
            metrics = build_metrics(bundle, revenue_sii, index_ret)
        except Exception as e:  # noqa: BLE001
            st.error(t("err_fetch") + f"\n\n{e}")
            return

    if metrics.empty:
        st.warning(t("warn_nodata"))
        return

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

    active_desc = ", ".join(t("cs_" + k, **p) for k, p in conditions) or t("cond_none")
    st.caption(t("status", date=metrics["date"].max(), n=len(metrics),
                 logic=logic, conds=active_desc))

    c1, c2 = st.columns(2)
    c1.metric(t("metric_track"), len(metrics))
    c2.metric(t("metric_match"), len(result))

    # --- 篩選結果表 ---
    st.subheader(t("res_header"))
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
            "stock_id": t("col_code"), "name": t("col_name"), "date": t("col_date"),
            "close": t("col_close"), "ma20": t("col_ma20"),
            "trust_buy_days": t("col_trust_days"), "foreign_buy_days": t("col_foreign_days"),
            "dealer_buy_days": t("col_dealer_days"), "foreign_net": t("col_foreign_net"),
            "三大法人合計": t("col_total_net"), "營收年增": t("col_rev_yoy"),
            "rev_growth_months": t("col_rev_growth"), "量比5": t("col_volratio"),
        }),
        use_container_width=True, hide_index=True, column_config=link_cols(),
    )

    # --- 強勢股排名 ---
    st.subheader(t("rank_header"))
    period = st.radio(t("rank_period"), ["3d", "30d"], horizontal=True, index=1,
                      format_func=lambda x: t("rank_3d") if x == "3d" else t("rank_30d"),
                      help=t("rank_help"))
    col = "ret_3d" if period == "3d" else "ret_30d"
    rank = metrics[["stock_id", "name", "industry", "close", col, "個股", "新聞"]].dropna(subset=[col]).copy()
    rank = rank.sort_values(col, ascending=False).reset_index(drop=True)
    rank.insert(0, "名次", rank.index + 1)
    rank["漲跌幅"] = rank[col].apply(lambda v: f"{v:+.2f}%")
    rank = rank[["名次", "stock_id", "name", "industry", "close", "漲跌幅", "個股", "新聞"]]
    st.dataframe(
        rank.rename(columns={"名次": t("col_rank"), "stock_id": t("col_code"),
                             "name": t("col_name"), "industry": t("col_industry"),
                             "close": t("col_close"), "漲跌幅": t("col_change")}),
        use_container_width=True, hide_index=True, column_config=link_cols(),
    )

    # --- 清單內個股線圖 ---
    st.subheader(t("chart_header"))
    col_a, col_b = st.columns([3, 2])
    options = result["stock_id"].tolist() if not result.empty else metrics["stock_id"].tolist()
    labels = {r["stock_id"]: f'{r["stock_id"]} {r["name"]}' for _, r in metrics.iterrows()}
    with col_a:
        sel = st.selectbox(t("chart_select"), options, format_func=lambda s: labels.get(s, s))
    with col_b:
        tf = st.radio(t("chart_tf"), TF_CODES, horizontal=True, index=0,
                      format_func=lambda c: t(TF_KEY[c]), key="list_tf")
    if sel:
        hist = get_price_history(bundle, sel, tf)
        if not hist.empty:
            st.plotly_chart(make_kline_fig(hist, tf), use_container_width=True)


if pool:
    render_screening()
else:
    st.info(t("prompt_select_pool"))


# ==========================================================================
# 🔎 個股健檢:不需選產業,輸入任意代號即可
# ==========================================================================
st.divider()
st.subheader(t("hc_header"))
code = st.text_input(t("hc_input"), value="").strip()

if code:
    with st.spinner(t("hc_spinner", code=code)):
        try:
            sbundle = load_single(code)
        except Exception as e:  # noqa: BLE001
            st.error(t("err_fetch") + f"\n\n{e}")
            st.stop()

    if sbundle["price"].empty:
        st.warning(t("hc_notfound", code=code))
    else:
        _ticker = sbundle["stocks"]["ticker"].iloc[0]
        srevenue = load_revenue("otc" if _ticker.endswith(".TWO") else "sii")
        smetrics = build_metrics(sbundle, srevenue)
        if smetrics.empty:
            st.warning(t("hc_insufficient"))
        else:
            row = smetrics.iloc[0]
            sc = score_stock(row)
            name = sbundle["stocks"]["name"].iloc[0]
            ticker = sbundle["stocks"]["ticker"].iloc[0]
            suffix = ".TWO" if ticker.endswith(".TWO") else ".TW"

            hp = get_price_history(sbundle, code, "D")
            chg = chgpct = None
            if len(hp) >= 2:
                prev, cur = hp["close"].iloc[-2], hp["close"].iloc[-1]
                chg, chgpct = cur - prev, (cur - prev) / prev * 100

            st.markdown(f"### {code} {name}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric(t("hc_close"), f'{row["close"]}',
                      f'{chg:+.2f} ({chgpct:+.2f}%)' if chg is not None else None)
            m2.metric(t("hc_score"), f'{sc["score"]} / 100')
            m3.metric(t("hc_grade"), t("grade_" + sc["grade_key"]))
            m4.metric(t("hc_date"), str(row["date"]))

            st.info(t("hc_suggest", s=t("sug_" + sc["grade_key"])))

            if row.get("rev_yoy") is not None:
                hi = t("hc_rev_high") if row.get("rev_high") else ""
                st.caption(t("hc_rev_summary", m=row["rev_month"], yoy=row["rev_yoy"],
                             g=row["rev_growth_months"], hi=hi))

            if sbundle["inst"].empty:
                st.caption(t("hc_no_inst"))

            # 評分明細(因子名稱與評語依語言翻譯)
            st.markdown(t("hc_detail"))
            rows = []
            for f in sc["factors"]:
                note = t(f["note_key"], v=f["val"]) if f["val"] is not None else t(f["note_key"])
                rows.append({t("col_factor"): t(f["key"]), t("col_got"): f["got"],
                             t("col_full"): f["full"], t("col_note"): note})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            # 相關連結
            st.markdown(
                t("links_label")
                + f"[Yahoo]({quote_url(code, suffix)})　｜　"
                + f"[{t('link_news_text')}]({news_url(code, name)})　｜　"
                + f"[Yahoo News](https://tw.stock.yahoo.com/quote/{ticker}/news)　｜　"
                + f"[Goodinfo](https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={code})　｜　"
                + f"[Wantgoo](https://www.wantgoo.com/stock/{code})　｜　"
                + f"[MOPS](https://mops.twse.com.tw/mops/web/t05st01?stockNo={code})"
            )

            # 資料來源(數據出處)
            insti_src = (
                "[TWSE T86](https://www.twse.com.tw/zh/trading/foreign/t86.html)"
                if suffix == ".TW" else
                "[TPEx](https://www.tpex.org.tw/zh-tw/mainboard/trading/major-institutional/day.html)"
            )
            st.markdown(
                t("src_label")
                + f"{t('src_price')}→[Yahoo]({quote_url(code, suffix)})　｜　"
                + f"{t('src_insti')}→{insti_src}　｜　"
                + f"{t('src_rev')}→[MOPS](https://mops.twse.com.tw/mops/#/web/t05st10_ifrs)"
            )

            tf2 = st.radio(t("chart_tf"), TF_CODES, horizontal=True, index=0,
                           format_func=lambda c: t(TF_KEY[c]), key="single_tf")
            h2 = get_price_history(sbundle, code, tf2)
            if not h2.empty:
                st.plotly_chart(make_kline_fig(h2, tf2), use_container_width=True)
