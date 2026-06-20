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
# 時間週期內部代碼(與顯示語言無關):D=日、W=週、M=月
TIMEFRAMES = {"D": None, "W": _WEEK_RULE, "M": _MONTH_RULE}

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


def build_metrics(bundle, revenue=None, index_ret=None):
    """彙整每檔股票的最新指標(以日線為準),回傳 DataFrame(一檔一列)。

    revenue: 選用,datasource.fetch_revenue() 的回傳;提供時計算基本面(營收)指標。
    index_ret: 選用,大盤 N 日漲幅 {3: x, 30: y};提供時計算相對強度 RS。
    """
    index_ret = index_ret or {}
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
        # N 個交易日前到今天的漲跌幅(%):強勢股排名用;並算相對強度 RS(贏大盤幅度)
        closes = p["close"].reset_index(drop=True)
        for n in (3, 30):
            if len(closes) > n and closes.iloc[-1 - n]:
                ret = round((closes.iloc[-1] / closes.iloc[-1 - n] - 1) * 100, 2)
            else:
                ret = None
            rec[f"ret_{n}d"] = ret
            ir = index_ret.get(n)
            rec[f"rs_{n}d"] = round(ret - ir, 2) if (ret is not None and ir is not None) else None
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


def _cond_ma_bullish(m):
    return (m["ma5"] > m["ma20"]) & (m["ma20"] > m["ma60"])


def _cond_rs(m, period=30, min_rs=0.0):
    col = "rs_3d" if period == 3 else "rs_30d"
    return m[col].fillna(-9999) >= min_rs


def _cond_rev_yoy(m, min_yoy=10):
    return m["rev_yoy"].fillna(-9999) >= min_yoy


def _cond_rev_growth(m, months=3):
    return m["rev_growth_months"].fillna(0) >= months


def _cond_rev_high(m):
    return m["rev_high"].fillna(False)


_CONDITIONS = {
    "above_ma": _cond_above_ma,
    "ma_bull": _cond_ma_bullish,
    "rs": _cond_rs,
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

    回傳「結構化、可多語化」的資料(不含寫死文字):
      score、grade_key、factors[{key(因子名鍵), got, full, note_key, val}]。
    顯示文字由前端依語言以 i18n 對照。此為機械式量化,僅供參考,非投資建議。
    """
    def val(key):
        v = row.get(key)
        return v if (v is not None and pd.notna(v)) else None

    close = val("close")
    factors = []

    def add(key, got, full, note_key, v=None):
        factors.append({"key": key, "got": got, "full": full, "note_key": note_key, "val": v})

    # --- 技術面 ---
    ma20, ma60, ma5 = val("ma20"), val("ma60"), val("ma5")
    ok = close is not None and ma20 is not None and close > ma20
    add("f_above_ma20", 15 if ok else 0, 15, "n_above_ma20_y" if ok else "n_above_ma20_n")

    ok = close is not None and ma60 is not None and close > ma60
    add("f_above_ma60", 15 if ok else 0, 15, "n_above_ma60_y" if ok else "n_above_ma60_n")

    ok = None not in (ma5, ma20, ma60) and ma5 > ma20 > ma60
    add("f_ma_bull", 10 if ok else 0, 10, "n_ma_bull_y" if ok else "n_ma_bull_n")

    vol, vol_ma5 = val("volume"), val("vol_ma5")
    ok = vol is not None and vol_ma5 is not None and vol > 1.2 * vol_ma5
    add("f_volume", 10 if ok else 0, 10, "n_volume_y" if ok else "n_volume_n")

    nh = int(val("new_high_days") or 1)
    ok = nh >= 20
    add("f_breakout", 10 if ok else 0, 10, "n_breakout_y" if ok else "n_breakout_n", nh)

    # --- 籌碼面 ---
    td = int(val("trust_buy_days") or 0)
    add("f_trust", 15 if td >= 3 else (8 if td >= 1 else 0), 15, "n_trust", td)

    fd = int(val("foreign_buy_days") or 0)
    add("f_foreign", 15 if fd >= 3 else (8 if fd >= 1 else 0), 15, "n_foreign", fd)

    fn = val("foreign_net")
    ok = fn is not None and fn > 0
    add("f_foreign_net", 10 if ok else 0, 10,
        "n_foreign_net_y" if ok else "n_foreign_net_n", fn if ok else None)

    dn = val("dealer_net")
    ok = dn is not None and dn > 0
    add("f_dealer_net", 5 if ok else 0, 5,
        "n_dealer_net_y" if ok else "n_dealer_net_n", dn if ok else None)

    tn = val("total_net")
    ok = tn is not None and tn > 0
    add("f_total_net", 10 if ok else 0, 10,
        "n_total_net_y" if ok else "n_total_net_n", tn if ok else None)

    # --- 基本面(僅在有月營收資料時計入)---
    yoy = val("rev_yoy")
    if yoy is not None:
        add("f_rev_yoy", 15 if yoy >= 20 else (8 if yoy >= 0 else 0), 15, "n_rev_yoy", yoy)
        gm = int(val("rev_growth_months") or 0)
        add("f_rev_growth", 10 if gm >= 3 else (5 if gm >= 1 else 0), 10, "n_rev_growth", gm)
        hi = bool(val("rev_high"))
        add("f_rev_high", 10 if hi else 0, 10, "n_rev_high_y" if hi else "n_rev_high_n")

    total = sum(f["got"] for f in factors)
    full = sum(f["full"] for f in factors)
    score = round(total / full * 100) if full else 0

    if score >= 70:
        grade_key = "bullish"
    elif score >= 50:
        grade_key = "neutral_bull"
    elif score >= 30:
        grade_key = "neutral_bear"
    else:
        grade_key = "bearish"

    return {"score": score, "grade_key": grade_key, "factors": factors}


def get_price_history(bundle, stock_id, timeframe="D"):
    """取單檔 K 線資料,依 timeframe(D 日 / W 週 / M 月)重新取樣,並附 MA5/20/60。"""
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
