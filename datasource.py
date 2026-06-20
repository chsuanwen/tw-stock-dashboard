"""即時資料擷取(不落地版)。

- 技術面(日K/價量):Yahoo 股市,透過 yfinance
- 籌碼面(三大法人):臺灣證券交易所 T86 開放資料

全程不寫任何檔案,資料只活在記憶體,算完即丟。
"""
import re
import time
from datetime import date
from io import StringIO

import requests
import pandas as pd
import yfinance as yf

import config

_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _to_float(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


# --------------------------------------------------------------------------
# Yahoo 股市:日 K / 價量
# --------------------------------------------------------------------------

def _strip_tz(series):
    """把日期序列轉成無時區、正規化到當日 0 點(相容 tz-aware / tz-naive)。"""
    s = pd.to_datetime(series)
    try:
        s = s.dt.tz_localize(None)   # tz-aware → naive
    except TypeError:
        pass                          # 本來就是 naive
    return s.dt.normalize()


def _yahoo_prices_batch(stock_ids):
    """批次抓多檔日 K(一次 download,較逐檔快很多)。回傳合併 DataFrame。"""
    tickers = [f"{s}{config.YAHOO_SUFFIX}" for s in stock_ids]
    cols = ["stock_id", "date", "open", "high", "low", "close", "volume"]
    if not tickers:
        return pd.DataFrame(columns=cols)
    try:
        data = yf.download(tickers, start=config.PRICE_START_DATE, auto_adjust=False,
                           group_by="ticker", threads=True, progress=False)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Yahoo 批次抓取失敗: {e}")
        return pd.DataFrame(columns=cols)
    if data is None or data.empty:
        return pd.DataFrame(columns=cols)

    multi = len(tickers) > 1
    frames = []
    for sid in stock_ids:
        t = f"{sid}{config.YAHOO_SUFFIX}"
        try:
            sub = data[t] if multi else data
        except KeyError:
            continue
        sub = sub.dropna(subset=["Close"])
        if sub.empty:
            continue
        sub = sub.reset_index()
        frames.append(pd.DataFrame({
            "stock_id": sid,
            "date": _strip_tz(sub["Date"]),
            "open": sub["Open"].astype(float),
            "high": sub["High"].astype(float),
            "low": sub["Low"].astype(float),
            "close": sub["Close"].astype(float),
            "volume": sub["Volume"].fillna(0).astype("int64"),
        }))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)


def fetch_index_returns():
    """大盤(加權指數 ^TWII)的 3 日 / 30 日漲跌幅(%),供相對強度 RS 計算。"""
    try:
        h = yf.Ticker("^TWII").history(start=config.PRICE_START_DATE, auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 大盤指數抓取失敗: {e}")
        return {}
    if h.empty:
        return {}
    closes = h["Close"].reset_index(drop=True)
    out = {}
    for n in (3, 30):
        if len(closes) > n and closes.iloc[-1 - n]:
            out[n] = round((closes.iloc[-1] / closes.iloc[-1 - n] - 1) * 100, 2)
    return out


def fetch_universe():
    """全上市股票清單(代號 / 名稱 / 產業別),供依產業選股。"""
    cols = ["stock_id", "name", "industry"]
    try:
        j = requests.get(config.TWSE_UNIVERSE_URL, headers=_HEADERS, timeout=30).json()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 取得股票清單失敗: {e}")
        return pd.DataFrame(columns=cols)
    rows = []
    for r in j:
        code = str(r.get("公司代號", "")).strip()
        if not re.fullmatch(r"\d{4}", code):
            continue
        rows.append({
            "stock_id": code,
            "name": str(r.get("公司名稱", "")).strip(),
            "industry": str(r.get("產業別", "")).strip(),
        })
    return pd.DataFrame(rows).sort_values("stock_id").reset_index(drop=True)


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


def _fetch_tpex_insti(date_yyyymmdd, session):
    """抓某交易日「上櫃」三大法人買賣超(櫃買中心),回傳 {stock_id: {...}}。

    欄位固定:0代號 1名稱 / 4外資(不含自營)買賣超 / 13投信買賣超 / 22自營商合計買賣超。
    """
    # TPEx 日期參數格式為西元 YYYY/MM/DD
    d = f"{date_yyyymmdd[:4]}/{date_yyyymmdd[4:6]}/{date_yyyymmdd[6:]}"
    params = {"type": "Daily", "sect": "EW", "date": d, "response": "json"}
    try:
        r = session.get(config.TPEX_INSTI_URL, params=params, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        tables = r.json().get("tables", [])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] TPEx 三大法人抓取失敗 {date_yyyymmdd}: {e}")
        return {}
    if not tables or not tables[0].get("data"):
        return {}

    out = {}
    for row in tables[0]["data"]:
        if len(row) < 23:
            continue
        sid = row[0].strip()
        out[sid] = {
            "name": row[1].strip(),
            "foreign": _to_int(row[4]),
            "trust": _to_int(row[13]),
            "dealer": _to_int(row[22]),
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

    # 1) Yahoo:批次抓日 K(一次 download,適合較大股池)
    price = _yahoo_prices_batch(stock_ids)
    if progress_cb:
        progress_cb(len(stock_ids), total, "Yahoo 價量")

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


def fetch_revenue(market="sii", months=None):
    """抓 MOPS 月營收(含年增率 YoY),回傳全市場 DataFrame。

    market: "sii"(上市)或 "otc"(上櫃)。
    回傳欄位:stock_id, year, month, revenue(當月,仟元), yoy(去年同月增減 %)。
    每個月一個檔(全市場),往前抓 months 個月以利算連續成長/創新高。
    """
    months = months or config.REVENUE_MONTHS
    y, m = date.today().year, date.today().month
    m -= 1  # 從上個月開始(本月通常尚未公布)
    if m == 0:
        y, m = y - 1, 12

    rows, got, tries = [], 0, 0
    session = requests.Session()
    while got < months and tries < months + 4:
        tries += 1
        url = config.MOPS_REVENUE_URL.format(market=market, roc=y - 1911, month=m)
        try:
            r = session.get(url, headers=_HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 3000:
                txt = r.content.decode("big5", "ignore")
                tables = pd.read_html(StringIO(txt), header=None)
                added = 0
                for t in tables:
                    if t.shape[1] != 11:
                        continue
                    for _, row in t.iterrows():
                        code = str(row.iloc[0]).strip()
                        if not re.fullmatch(r"\d{4}", code):
                            continue
                        rows.append({
                            "stock_id": code, "year": y, "month": m,
                            "revenue": _to_int(row.iloc[2]),
                            "yoy": _to_float(row.iloc[6]),
                        })
                        added += 1
                if added:
                    got += 1
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 月營收抓取失敗 {url}: {e}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        time.sleep(0.4)

    return (pd.DataFrame(rows) if rows
            else pd.DataFrame(columns=["stock_id", "year", "month", "revenue", "yoy"]))


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

    # 2) 三大法人:依上市/上櫃路由到 證交所(T86) 或 櫃買中心(TPEx)
    is_otc = used_suffix == ".TWO"
    fetch_insti = _fetch_tpex_insti if is_otc else _fetch_t86
    inst_rows, name = [], stock_id
    dates = sorted(price["date"].dt.strftime("%Y%m%d").unique())[-config.INST_LOOKBACK_DAYS:]
    session = requests.Session()
    for d in dates:
        rec = fetch_insti(d, session).get(stock_id)
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
