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

# 日 K 抓取起始日(約一年,足夠算月線與週/月線圖)
PRICE_START_DATE = "2024-06-01"

# 三大法人:回抓最近幾個交易日即可(夠算投信連買與最新外資買超)
INST_LOOKBACK_DAYS = 12

# 證交所「三大法人買賣超日報(T86)」端點。單一日期會回傳全市場所有股票(上市)。
TWSE_T86_URL = "https://www.twse.com.tw/fund/T86"

# 櫃買中心「三大法人買賣明細」端點(上櫃)。單一日期回傳全上櫃股。
TPEX_INSTI_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"

# 快取時間(秒)。在這段時間內重複開啟/操作網頁都用暫存結果,不重抓。
CACHE_TTL_SECONDS = 8 * 60 * 60
