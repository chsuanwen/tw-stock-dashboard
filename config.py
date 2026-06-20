"""全域設定:股票池、抓取區間、快取時間、資料來源端點。

資料來源:
  - 技術面(日K/價量)→ Yahoo 股市(透過 yfinance)
  - 籌碼面(三大法人買賣超)→ 臺灣證券交易所 T86 開放資料
本版本為「即時抓 + 快取、不落地」架構,不使用資料庫。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- 股票池(MVP 先用小籃子權值股,皆為上市) ---
STOCK_LIST = [
    "2330",  # 台積電
    "2317",  # 鴻海
    "2454",  # 聯發科
    "2308",  # 台達電
    "2382",  # 廣達
    "2412",  # 中華電
    "2603",  # 長榮
    "3008",  # 大立光
    "2881",  # 富邦金
    "1301",  # 台塑
]

# Yahoo 股市代號後綴:上市 .TW、上櫃 .TWO(本清單皆為上市)
YAHOO_SUFFIX = ".TW"

# 全上市股票清單(含產業別)來源:TWSE OpenAPI 月營收彙總(有中文產業別)
TWSE_UNIVERSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
# 全上櫃股票清單(含產業別)來源:TPEx OpenAPI 月營收彙總(欄位同上市,含中文產業別)
TPEX_UNIVERSE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"

# Yahoo 股市代號後綴對照:上市 sii→.TW、上櫃 otc→.TWO
MARKET_SUFFIX = {"sii": ".TW", "otc": ".TWO"}

# 依產業選股時,單次最多抓取的檔數上限(保護即時抓取的反應速度)
MAX_UNIVERSE = 1000

# 日 K 抓取起始日(約一年,足夠算月線與週/月線圖)
PRICE_START_DATE = "2024-06-01"

# 三大法人:回抓最近幾個交易日即可(夠算投信連買與最新外資買超)
INST_LOOKBACK_DAYS = 12

# 證交所「三大法人買賣超日報(T86)」端點。單一日期會回傳全市場所有股票(上市)。
TWSE_T86_URL = "https://www.twse.com.tw/fund/T86"

# 櫃買中心「三大法人買賣明細」端點(上櫃)。單一日期回傳全上櫃股。
TPEX_INSTI_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"

# 公開資訊觀測站(MOPS)月營收彙總靜態檔。market: sii(上市)/ otc(上櫃)。
# 單一檔=某月份全市場;以 ROC 年份與月份組成。big5 編碼。
MOPS_REVENUE_URL = "https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc}_{month}_0.html"

# 月營收往前抓幾個月(供 年增、連續成長月數、創新高 計算)
REVENUE_MONTHS = 13

# 快取時間(秒)。在這段時間內重複開啟/操作網頁都用暫存結果,不重抓。
CACHE_TTL_SECONDS = 8 * 60 * 60
