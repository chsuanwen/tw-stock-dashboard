"""即時資料擷取(不落地版)。

- 技術面(日K/價量):Yahoo 股市,透過 yfinance
- 籌碼面(三大法人):臺灣證券交易所 T86 開放資料

全程不寫任何檔案,資料只活在記憶體,算完即丟。
"""
import time

import requests
import pandas as pd
import yfinance as yf

import config

_HEADERS = {"User-Agent": "Mozilla/5.0"}


# --------------------------------------------------------------------------
# Yahoo 股市:日 K / 價量
# --------------------------------------------------------------------------

def _yahoo_price(stock_id, suffix=None):
    """抓單檔日 K,回傳統一格式 DataFrame(失敗回空)。suffix 預設用設定值。"""
    suffix = suffix if suffix is not None else config.YAHOO_SUFFIX
    ticker = f"{stock_id}{suffix}"
    try:
        h = yf.Ticker(ticker).history(start=config.PRICE_START_DATE, auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Yahoo 抓取失敗 {ticker}: {e}")
        return pd.DataFrame()
    if h.empty:
        return pd.DataFrame()
    h = h.reset_index()
    return pd.DataFrame({
        "stock_id": stock_id,
        # 去掉時區並正規化到當日 0 點,方便後續週/月重新取樣
        "date": pd.to_datetime(h["Date"]).dt.tz_localize(None).dt.normalize(),
        "open": h["Open"].astype(float),
        "high": h["High"].astype(float),
        "low": h["Low"].astype(float),
        "close": h["Close"].astype(float),
        "volume": h["Volume"].astype("int64"),
    })


# --------------------------------------------------------------------------
# 證交所 T86:三大法人買賣超
# --------------------------------------------------------------------------

def _field_idx(fields, *names):
    """在 T86 fields 中找欄位索引:先比對完全相同,再比對包含關係。"""
    for n in names:
        if n in fields:
            return fields.index(n)
    for n in names:
        for i, f in enumerate(fields):
            if n in f:
                return i
    return None


def _to_int(x):
    try:
        return int(str(x).replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


def _fetch_t86(date_yyyymmdd, session):
    """抓某交易日的三大法人買賣超,回傳 {stock_id: {name, foreign, trust, dealer}}。"""
    params = {"response": "json", "date": date_yyyymmdd, "selectType": "ALL"}
    try:
        r = session.get(config.TWSE_T86_URL, params=params, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        j = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] T86 抓取失敗 {date_yyyymmdd}: {e}")
        return {}
    if j.get("stat") != "OK":
        return {}

    fields = j.get("fields", [])
    i_id = _field_idx(fields, "證券代號")
    i_name = _field_idx(fields, "證券名稱")
    i_foreign = _field_idx(fields, "外陸資買賣超股數(不含外資自營商)", "外資買賣超股數")
    i_trust = _field_idx(fields, "投信買賣超股數")
    i_dealer = _field_idx(fields, "自營商買賣超股數")
    if None in (i_id, i_foreign, i_trust, i_dealer):
        return {}

    out = {}
    for row in j.get("data", []):
        sid = row[i_id].strip()
        out[sid] = {
            "name": row[i_name].strip() if i_name is not None else sid,
            "foreign": _to_int(row[i_foreign]),
            "trust": _to_int(row[i_trust]),
            "dealer": _to_int(row[i_dealer]),
        }
    return out


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def fetch_bundle(stock_ids=None, progress_cb=None):
    """抓取所有股票的價量(Yahoo)+ 三大法人(證交所),回傳 dict[str, DataFrame]。

    progress_cb(done, total, label): 選用的進度回呼。
    """
    stock_ids = stock_ids or config.STOCK_LIST
    total = len(stock_ids) + 1  # +1 給三大法人階段

    # 1) Yahoo:逐檔抓日 K
    prices = []
    for i, sid in enumerate(stock_ids, 1):
        df = _yahoo_price(sid)
        if not df.empty:
            prices.append(df)
        if progress_cb:
            progress_cb(i, total, f"Yahoo 價量 {sid}")
    price = (pd.concat(prices, ignore_index=True) if prices
             else pd.DataFrame(columns=["stock_id", "date", "open", "high", "low", "close", "volume"]))

    # 2) 證交所:用實際交易日(取自價量資料)回抓最近 N 天三大法人
    inst_rows = []
    name_map = {}
    if not price.empty:
        dates = sorted(price["date"].dt.strftime("%Y%m%d").unique())[-config.INST_LOOKBACK_DAYS:]
        session = requests.Session()
        wanted = set(stock_ids)
        for d in dates:
            t86 = _fetch_t86(d, session)
            for sid in wanted:
                rec = t86.get(sid)
                if rec:
                    name_map.setdefault(sid, rec["name"])
                    inst_rows.append({
                        "stock_id": sid,
                        "date": pd.to_datetime(d),
                        "foreign_net": rec["foreign"],
                        "trust_net": rec["trust"],
                        "dealer_net": rec["dealer"],
                    })
            time.sleep(0.8)  # 禮貌性間隔,避免被證交所限流
        if progress_cb:
            progress_cb(total, total, "證交所 三大法人")

    inst = (pd.DataFrame(inst_rows) if inst_rows
            else pd.DataFrame(columns=["stock_id", "date", "foreign_net", "trust_net", "dealer_net"]))
    stocks = pd.DataFrame(
        [{"stock_id": sid, "name": name_map.get(sid, sid)} for sid in stock_ids]
    )

    return {"stocks": stocks, "price": price, "inst": inst}


def fetch_single(stock_id):
    """抓「任意單一代號」的價量 + 三大法人,回傳 bundle 格式。

    價量會自動嘗試上市(.TW)與上櫃(.TWO);三大法人(證交所 T86)僅含上市股。
    """
    stock_id = str(stock_id).strip()

    # 1) 價量:先試上市 .TW,失敗再試上櫃 .TWO
    price = pd.DataFrame()
    used_suffix = ".TW"
    for suffix in (".TW", ".TWO"):
        price = _yahoo_price(stock_id, suffix)
        if not price.empty:
            used_suffix = suffix
            break
    if price.empty:
        return {"stocks": pd.DataFrame(), "price": price, "inst": pd.DataFrame()}

    # 2) 三大法人:用實際交易日回抓 T86 並過濾此代號(僅上市有)
    inst_rows, name = [], stock_id
    dates = sorted(price["date"].dt.strftime("%Y%m%d").unique())[-config.INST_LOOKBACK_DAYS:]
    session = requests.Session()
    for d in dates:
        rec = _fetch_t86(d, session).get(stock_id)
        if rec:
            name = rec["name"]
            inst_rows.append({
                "stock_id": stock_id, "date": pd.to_datetime(d),
                "foreign_net": rec["foreign"], "trust_net": rec["trust"],
                "dealer_net": rec["dealer"],
            })
        time.sleep(0.8)

    inst = (pd.DataFrame(inst_rows) if inst_rows
            else pd.DataFrame(columns=["stock_id", "date", "foreign_net", "trust_net", "dealer_net"]))
    stocks = pd.DataFrame([{
        "stock_id": stock_id, "name": name,
        "ticker": f"{stock_id}{used_suffix}",
    }])
    return {"stocks": stocks, "price": price, "inst": inst}
