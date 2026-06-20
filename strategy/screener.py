"""策略分析層(不落地版,可組合規則引擎)。

build_metrics: 把每檔股票需要的指標一次算齊(均線、連買天數、均量、區間高點…)。
apply_filters: 依使用者勾選的條件,用 AND / OR 組合過濾。
新增策略只要在 build_metrics 補欄位、在 _CONDITIONS 補一條規則即可。
"""
import pandas as pd

# pandas 2.2 起月頻代碼由 'M' 改為 'ME',做版本相容
_PV = tuple(int(x) for x in pd.__version__.split(".")[:2])
_MONTH_RULE = "ME" if _PV >= (2, 2) else "M"
_WEEK_RULE = "W"
TIMEFRAMES = {"日": None, "週": _WEEK_RULE, "月": _MONTH_RULE}

# 可選的均線 / 均量 周期(供 UI 與指標預先計算)
MA_PERIODS = [5, 10, 20, 60]
VOL_WINDOWS = [5, 20]
# 突破新高的天數改為使用者自由輸入(預設值與上限)
BREAKOUT_DEFAULT = 20
BREAKOUT_MAX = 250


def _consecutive_buy_days(net_series):
    """從最新往回數,連續買超(net>0)的天數。"""
    days = 0
    for v in net_series.iloc[::-1]:
        if v > 0:
            days += 1
        else:
            break
    return days


def _revenue_metrics(rv):
    """由單檔月營收(已依年月排序)算 年增、連續成長月數、是否創高。"""
    yoy = rv["yoy"].iloc[-1]
    month = f'{int(rv["year"].iloc[-1])}/{int(rv["month"].iloc[-1]):02d}'
    growth = 0
    for v in rv["yoy"].iloc[::-1]:
        if pd.notna(v) and v > 0:
            growth += 1
        else:
            break
    revs = rv["revenue"].dropna()
    is_high = bool(len(revs) > 1 and revs.iloc[-1] >= revs.max())
    return {
        "rev_yoy": round(float(yoy), 2) if pd.notna(yoy) else None,
        "rev_month": month,
        "rev_growth_months": growth,
        "rev_high": is_high,
    }


def build_metrics(bundle, revenue=None):
    """彙整每檔股票的最新指標(以日線為準),回傳 DataFrame(一檔一列)。

    revenue: 選用,datasource.fetch_revenue() 的回傳;提供時計算基本面(營收)指標。
    """
    stocks = bundle["stocks"]
    price = bundle["price"]
    inst = bundle["inst"]
    has_rev = revenue is not None and not revenue.empty
    name_map = dict(zip(stocks["stock_id"], stocks["name"])) if not stocks.empty else {}

    records = []
    for sid in price["stock_id"].unique():
        p = price[price["stock_id"] == sid].sort_values("date")
        if p.empty:
            continue
        close = float(p["close"].iloc[-1])
        volume = int(p["volume"].iloc[-1])

        rec = {
            "stock_id": sid,
            "name": name_map.get(sid, sid),
            "date": p["date"].iloc[-1].date(),
            "close": round(close, 2),
            "volume": volume,
        }
        # N 個交易日前到今天的漲跌幅(%):強勢股排名用
        closes = p["close"].reset_index(drop=True)
        for n in (3, 30):
            if len(closes) > n and closes.iloc[-1 - n]:
                rec[f"ret_{n}d"] = round((closes.iloc[-1] / closes.iloc[-1 - n] - 1) * 100, 2)
            else:
                rec[f"ret_{n}d"] = None
        # 各周期均線、均量、區間最高收盤
        for n in MA_PERIODS:
            v = p["close"].rolling(n).mean().iloc[-1]
            rec[f"ma{n}"] = round(float(v), 2) if pd.notna(v) else None
        for n in VOL_WINDOWS:
            v = p["volume"].rolling(n).mean().iloc[-1]
            rec[f"vol_ma{n}"] = float(v) if pd.notna(v) else None
        # 今天收盤是「最近幾天」以來的新高(從今天往回數,連續幾天收盤比今天低)
        # 支援任意 N:突破 N 日新高 ⟺ new_high_days >= N
        today_close = closes.iloc[-1]
        nh = 1
        for prev in closes.iloc[-2::-1]:
            if prev < today_close:
                nh += 1
            else:
                break
        rec["new_high_days"] = nh

        # 籌碼面:三大法人(外資/投信/自營商)連買天數、最新淨額、合計
        ins = inst[inst["stock_id"] == sid].sort_values("date")
        if not ins.empty:
            last_i = ins.iloc[-1]
            rec["trust_buy_days"] = _consecutive_buy_days(ins["trust_net"])
            rec["foreign_buy_days"] = _consecutive_buy_days(ins["foreign_net"])
            rec["dealer_buy_days"] = _consecutive_buy_days(ins["dealer_net"])
            rec["foreign_net"] = int(last_i["foreign_net"])
            rec["trust_net"] = int(last_i["trust_net"])
            rec["dealer_net"] = int(last_i["dealer_net"])
            rec["total_net"] = int(last_i["foreign_net"] + last_i["trust_net"] + last_i["dealer_net"])
        else:
            rec["trust_buy_days"] = rec["foreign_buy_days"] = rec["dealer_buy_days"] = 0
            rec["foreign_net"] = rec["trust_net"] = rec["dealer_net"] = rec["total_net"] = None

        # 基本面:月營收年增 / 連續成長月數 / 是否創高
        rec.update({"rev_yoy": None, "rev_month": None,
                    "rev_growth_months": 0, "rev_high": False})
        if has_rev:
            rv = revenue[revenue["stock_id"] == sid].sort_values(["year", "month"])
            if not rv.empty:
                rec.update(_revenue_metrics(rv))

        records.append(rec)

    return pd.DataFrame(records)


# --------------------------------------------------------------------------
# 條件規則:每條回傳一個布林 Series(對齊 metrics 的 index)
# --------------------------------------------------------------------------

def _cond_above_ma(m, period=20):
    return m["close"] > m[f"ma{period}"]


def _cond_trust(m, days=3):
    return m["trust_buy_days"] >= days


def _cond_foreign_days(m, days=3):
    return m["foreign_buy_days"] >= days


def _cond_foreign_net(m):
    return m["foreign_net"].fillna(0) > 0


def _cond_dealer_days(m, days=3):
    return m["dealer_buy_days"] >= days


def _cond_dealer_net(m):
    return m["dealer_net"].fillna(0) > 0


def _cond_total_net(m):
    return m["total_net"].fillna(0) > 0


def _cond_volume(m, window=5, ratio=1.5):
    return m["volume"] > ratio * m[f"vol_ma{window}"]


def _cond_breakout(m, window=20):
    return m["new_high_days"].fillna(1) >= window


def _cond_rev_yoy(m, min_yoy=10):
    return m["rev_yoy"].fillna(-9999) >= min_yoy


def _cond_rev_growth(m, months=3):
    return m["rev_growth_months"].fillna(0) >= months


def _cond_rev_high(m):
    return m["rev_high"].fillna(False)


_CONDITIONS = {
    "above_ma": _cond_above_ma,
    "trust": _cond_trust,
    "foreign_days": _cond_foreign_days,
    "foreign_net": _cond_foreign_net,
    "dealer_days": _cond_dealer_days,
    "dealer_net": _cond_dealer_net,
    "total_net": _cond_total_net,
    "volume": _cond_volume,
    "breakout": _cond_breakout,
    "rev_yoy": _cond_rev_yoy,
    "rev_growth": _cond_rev_growth,
    "rev_high": _cond_rev_high,
}


def apply_filters(metrics, conditions, logic="AND"):
    """依條件列表過濾。

    conditions: list[(key, params_dict)]，key 對應 _CONDITIONS。
    logic: "AND"(全部符合) 或 "OR"(任一符合)。
    沒有任何條件時回傳全部。
    """
    if metrics.empty or not conditions:
        return metrics

    masks = []
    for key, params in conditions:
        fn = _CONDITIONS.get(key)
        if fn is None:
            continue
        mask = fn(metrics, **params).fillna(False)
        masks.append(mask)
    if not masks:
        return metrics

    combined = masks[0]
    for mk in masks[1:]:
        combined = (combined & mk) if logic == "AND" else (combined | mk)
    return metrics[combined]


def score_stock(row):
    """對單一檔股票做透明的規則式健檢評分(0~100)。

    row: build_metrics() 回傳 DataFrame 的一列(Series)。
    回傳 dict:score、grade、suggestion、factors(逐項加分明細)。
    註:此為機械式多空訊號量化,僅供參考,非投資建議。
    """
    def val(key):
        v = row.get(key)
        return v if (v is not None and pd.notna(v)) else None

    close = val("close")
    factors = []

    def add(name, got, full, note):
        factors.append({"factor": name, "得分": got, "滿分": full, "評語": note})

    # --- 技術面 ---
    ma20, ma60, ma5 = val("ma20"), val("ma60"), val("ma5")
    ok = close is not None and ma20 is not None and close > ma20
    add("站上月線(MA20)", 15 if ok else 0, 15, "收盤在月線之上" if ok else "收盤跌破月線")

    ok = close is not None and ma60 is not None and close > ma60
    add("站上季線(MA60)", 15 if ok else 0, 15, "中長期偏多" if ok else "中長期偏弱")

    ok = None not in (ma5, ma20, ma60) and ma5 > ma20 > ma60
    add("均線多頭排列", 10 if ok else 0, 10, "MA5>MA20>MA60" if ok else "均線未呈多頭")

    vol, vol_ma5 = val("volume"), val("vol_ma5")
    ok = vol is not None and vol_ma5 is not None and vol > 1.2 * vol_ma5
    add("量能放大", 10 if ok else 0, 10, "量>1.2倍5日均量" if ok else "量能未明顯放大")

    nh = val("new_high_days") or 1
    ok = nh >= 20
    add("突破20日新高", 10 if ok else 0, 10,
        f"創 {nh} 日新高" if ok else f"近高僅 {nh} 日(未達20日)")

    # --- 籌碼面 ---
    td = val("trust_buy_days") or 0
    g = 15 if td >= 3 else (8 if td >= 1 else 0)
    add("投信連續買超", g, 15, f"投信連買 {td} 天")

    fd = val("foreign_buy_days") or 0
    g = 15 if fd >= 3 else (8 if fd >= 1 else 0)
    add("外資連續買超", g, 15, f"外資連買 {fd} 天")

    fn = val("foreign_net")
    ok = fn is not None and fn > 0
    add("外資當日買超", 10 if ok else 0, 10,
        f"外資買超 {fn:,.0f} 股" if ok else "外資未買超")

    dn = val("dealer_net")
    ok = dn is not None and dn > 0
    add("自營商當日買超", 5 if ok else 0, 5,
        f"自營商買超 {dn:,.0f} 股" if ok else "自營商未買超")

    tn = val("total_net")
    ok = tn is not None and tn > 0
    add("三大法人合計買超", 10 if ok else 0, 10,
        f"三大法人合計買超 {tn:,.0f} 股" if ok else "三大法人合計未買超")

    # --- 基本面(僅在有月營收資料時計入)---
    yoy = val("rev_yoy")
    if yoy is not None:
        g = 15 if yoy >= 20 else (8 if yoy >= 0 else 0)
        add("營收年增", g, 15, f"營收年增 {yoy:+.1f}%")

        gm = val("rev_growth_months") or 0
        g = 10 if gm >= 3 else (5 if gm >= 1 else 0)
        add("營收連續成長", g, 10, f"年增連續為正 {gm} 個月")

        hi = bool(val("rev_high"))
        add("營收創高", 10 if hi else 0, 10, "創近一年新高" if hi else "未創近一年新高")

    total = sum(f["得分"] for f in factors)
    full = sum(f["滿分"] for f in factors)
    score = round(total / full * 100) if full else 0

    if score >= 70:
        grade, suggestion = "偏多", "技術與籌碼同步轉強,趨勢偏多,可留意;仍須注意大盤與停損。"
    elif score >= 50:
        grade, suggestion = "中性偏多", "部分指標轉強,留意能否持續;追高需謹慎。"
    elif score >= 30:
        grade, suggestion = "中性偏弱", "多空訊號分歧,建議觀望、等待方向明確。"
    else:
        grade, suggestion = "偏空", "多數指標轉弱,趨勢偏空,不宜貿然進場。"

    return {"score": score, "grade": grade, "suggestion": suggestion, "factors": factors}


def get_price_history(bundle, stock_id, timeframe="日"):
    """取單檔 K 線資料,依 timeframe(日/週/月)重新取樣,並附 MA5/20/60。"""
    p = bundle["price"]
    p = p[p["stock_id"] == stock_id].sort_values("date").copy()
    if p.empty:
        return p

    rule = TIMEFRAMES.get(timeframe)
    if rule is not None:
        p = (p.set_index("date")
               .resample(rule)
               .agg({"open": "first", "high": "max", "low": "min",
                     "close": "last", "volume": "sum"})
               .dropna(subset=["close"])
               .reset_index())

    p["ma5"] = p["close"].rolling(5).mean()
    p["ma20"] = p["close"].rolling(20).mean()
    p["ma60"] = p["close"].rolling(60).mean()
    return p
