# 台股策略選股系統(即時抓 + 快取、不落地版)

純 Python 的台股分析工具:開啟網頁 → 即時抓 FinMind 資料(記憶體)→ pandas 算策略 → Streamlit 顯示。**全程不寫任何檔案、不用資料庫。**

## 架構特點
- **不落地**:資料只活在記憶體,算完即丟,沒有資料庫、沒有檔案膨脹問題。
- **快取秒開**:當天第一次開會即時抓最新資料(約數十秒),之後 8 小時內再開/操作都用暫存結果秒開;過期後下次開啟才重抓。
- **資料永遠最新**:不需要排程器,更新發生在「你開啟、且快取過期」的當下。

## 技術堆疊
- 資料來源:
  - 技術面(日K/價量)→ **Yahoo 股市**(透過 yfinance)
  - 籌碼面(三大法人買賣超)→ **臺灣證券交易所 T86 開放資料**
- 分析:pandas
- 前端:Streamlit + Plotly
- 線圖支援 **日 / 週 / 月** 三種週期切換

## 專案結構
```
.
├── config.py            # 設定:股票池、抓取區間、快取時間、Token
├── datasource.py        # 即時向 FinMind 抓資料,回傳記憶體 DataFrame(不落地)
├── strategy/screener.py # 指標計算 + 條件篩選
├── dashboard/app.py     # Streamlit Dashboard(含快取)
└── requirements.txt
```

## 安裝與執行
```bash
pip install -r requirements.txt

# (選用)設定 FinMind token 提高 API 額度
#   PowerShell:  $env:FINMIND_TOKEN="你的token"   取得:https://finmindtrade.com

streamlit run dashboard/app.py     # 開啟後瀏覽 http://localhost:8501
```
不需要先灌資料,開啟網頁即會自動抓取。

## 內建策略條件(側欄勾選,AND 關係)
- 投信連續買超 N 天(籌碼面,證交所)
- 股價站上月線 MA20(技術面,Yahoo)
- 外資最新買超(籌碼面,證交所)

> 註:原「月營收年增」屬基本面,證交所交易 API 與 Yahoo 皆不提供,
> 已暫時移除。如需此條件,可再串接公開資訊觀測站(MOPS)補回。

## 限制與後續
- **股票池不宜過大**:每次抓取是即時連線,幾十檔內體驗良好;要追蹤全市場需改回「有資料庫」的架構(避免每次重抓上千檔)。
- **無歷史紀錄**:不存資料,故無法回測「過去某天選了哪些股、後來漲跌」。要回測時,再加「只存精簡選股結果」即可。
- 金融股(如富邦金)的月營收年增率不具參考意義,建議篩選時排除或改用其他基本面指標。

## 部署到雲端(隨時隨地可開)
因為不落地,GitHub 只會放程式碼、永遠不會變大:
1. 程式碼推上 GitHub
2. 到 https://share.streamlit.io 連結 repo 一鍵部署,取得公開網址
3. (選用)在 Streamlit Cloud 的 Secrets 設定 `FINMIND_TOKEN` 與登入密碼
