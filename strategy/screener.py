"""策略分析層(不落地版)。

吃 datasource.fetch_bundle() 回傳的記憶體 DataFrame,計算指標、套用篩選。
價量(Yahoo)支援日/週/月 K 線切換;籌碼(證交所)支援投信連買、外資買超。
"""
import pandas as pd

# pandas 2.2 起月頻代碼由 'M' 改為 'ME',做版本相容
_PV = tuple(int(x) for x in pd.__version__.split(".")[:2])
_MONTH_RULE = "ME" if _PV >= (2, 2) else "M"
_WEEK_RULE = "W"

# 時間週期 → resample 頻率代碼
TIMEFRAMES = {"日": None, "週": _WEEK_RULE, "月": _MONTH_RULE}


def build_metrics(bundle):
    """彙整每檔股票的最新指標(以日線為準),回傳 DataFrame(一檔一列)。

    欄位:stock_id, name, date, close, ma20, above_ma20, trust_buy_days, foreign_net
    """
    stocks = bundle["stocks"]
    price = bundle["price"]
    inst = bundle["inst"]
    name_map = dict(zip(stocks["stock_id"], stocks["name"])) if not stocks.empty else {}

    records = []
    for sid in price["stock_id"].unique():
        # --- 技術面:收盤 vs 20 日均線(月線) ---
        p = price[price["stock_id"] == sid].sort_values("date")
        if p.empty:
            continue
        p = p.copy()
        p["ma20"] = p["close"].rolling(20).mean()
        last = p.iloc[-1]
        close = float(last["close"])
        ma20 = float(last["ma20"]) if pd.notna(last["ma20"]) else None
        above_ma20 = ma20 is not None and close > ma20

        # --- 籌碼面:投信連買天數 + 外資最新買賣超 ---
        ins = inst[inst["stock_id"] == sid].sort_values("date")
        trust_buy_days = 0
        foreign_net = None
        if not ins.empty:
            foreign_net = int(ins.iloc[-1]["foreign_net"])
            for v in ins["trust_net"].iloc[::-1]:
                if v > 0:
                    trust_buy_days += 1
                else:
                    break

        records.append({
            "stock_id": sid,
            "name": name_map.get(sid, sid),
            "date": last["date"].date(),
            "close": round(close, 2),
            "ma20": round(ma20, 2) if ma20 else None,
            "above_ma20": above_ma20,
            "trust_buy_days": trust_buy_days,
            "foreign_net": foreign_net,
        })

    return pd.DataFrame(records)


def apply_filters(metrics, *, use_trust=False, trust_days=3,
                  use_ma=False, use_foreign=False):
    """依條件過濾 metrics(各條件為 AND 關係)。"""
    if metrics.empty:
        return metrics
    mask = pd.Series(True, index=metrics.index)
    if use_trust:
        mask &= metrics["trust_buy_days"] >= trust_days
    if use_ma:
        mask &= metrics["above_ma20"]
    if use_foreign:
        mask &= metrics["foreign_net"].fillna(0) > 0
    return metrics[mask]


def get_price_history(bundle, stock_id, timeframe="日"):
    """取單檔 K 線資料,依 timeframe(日/週/月)重新取樣,並附 MA5/20/60。"""
    p = bundle["price"]
    p = p[p["stock_id"] == stock_id].sort_values("date").copy()
    if p.empty:
        return p

    rule = TIMEFRAMES.get(timeframe)
    if rule is not None:
        # 週/月:OHLC 重新取樣(開=首、高=最高、低=最低、收=末、量=加總)
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
