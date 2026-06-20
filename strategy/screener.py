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

# 可選的均線 / 均量 / 突破周期(供 UI 與指標預先計算)
MA_PERIODS = [5, 10, 20, 60]
VOL_WINDOWS = [5, 20]
BREAKOUT_WINDOWS = [20, 60]


def _consecutive_buy_days(net_series):
    """從最新往回數,連續買超(net>0)的天數。"""
    days = 0
    for v in net_series.iloc[::-1]:
        if v > 0:
            days += 1
        else:
            break
    return days


def build_metrics(bundle):
    """彙整每檔股票的最新指標(以日線為準),回傳 DataFrame(一檔一列)。"""
    stocks = bundle["stocks"]
    price = bundle["price"]
    inst = bundle["inst"]
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
        # 各周期均線、均量、區間最高收盤
        for n in MA_PERIODS:
            v = p["close"].rolling(n).mean().iloc[-1]
            rec[f"ma{n}"] = round(float(v), 2) if pd.notna(v) else None
        for n in VOL_WINDOWS:
            v = p["volume"].rolling(n).mean().iloc[-1]
            rec[f"vol_ma{n}"] = float(v) if pd.notna(v) else None
        for n in BREAKOUT_WINDOWS:
            v = p["close"].rolling(n).max().iloc[-1]
            rec[f"high{n}"] = float(v) if pd.notna(v) else None

        # 籌碼面:投信 / 外資 連買天數 + 外資最新買賣超
        ins = inst[inst["stock_id"] == sid].sort_values("date")
        if not ins.empty:
            rec["trust_buy_days"] = _consecutive_buy_days(ins["trust_net"])
            rec["foreign_buy_days"] = _consecutive_buy_days(ins["foreign_net"])
            rec["foreign_net"] = int(ins["foreign_net"].iloc[-1])
        else:
            rec["trust_buy_days"] = 0
            rec["foreign_buy_days"] = 0
            rec["foreign_net"] = None

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


def _cond_volume(m, window=5, ratio=1.5):
    return m["volume"] > ratio * m[f"vol_ma{window}"]


def _cond_breakout(m, window=20):
    return m["close"] >= m[f"high{window}"]


_CONDITIONS = {
    "above_ma": _cond_above_ma,
    "trust": _cond_trust,
    "foreign_days": _cond_foreign_days,
    "foreign_net": _cond_foreign_net,
    "volume": _cond_volume,
    "breakout": _cond_breakout,
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
