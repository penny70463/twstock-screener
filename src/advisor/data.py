"""資料層：股票清單（上市+上櫃）、歷史股價（含快取）、三大法人、月營收"""

import datetime as dt
import json
import pickle
import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from . import config

TWSE_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAY_ALL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_INST = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
TWSE_REVENUE = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_REVENUE = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"
TWSE_MI_INDEX = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_QUOTES = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"

HEADERS = {"User-Agent": "Mozilla/5.0 (stock-selector)"}
CACHE_DIR = Path(__file__).parent / "cache"
# code→name 映射快取：離線檢視（無網路時看持倉/show）仍能顯示股票名稱。
# 名稱幾乎不變動，這份快取不設過期，只在每次成功取得清單後覆寫更新。
NAME_MAP_PATH = CACHE_DIR / "name_map.json"


def _get(url: str, retries: int = 3, **kwargs) -> requests.Response:
    """GET with SSL fallback 與重試退避。

    - TPEX 憑證缺 Subject Key Identifier，Python 3.13 的嚴格驗證會間歇性
      失敗，失敗時退回不驗證（皆為公開行情資料）
    - TWSE/TPEX 對連續請求會重置連線，指數退避重試"""
    kwargs.setdefault("headers", HEADERS)
    kwargs.setdefault("timeout", 30)
    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(retries):
        try:
            return requests.get(url, **kwargs)
        except requests.exceptions.SSLError:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            kwargs["verify"] = False
            last_err = requests.exceptions.SSLError("ssl")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            last_err = e
        time.sleep(2 ** attempt * 2)  # 2s, 4s, 8s
    raise last_err


def _to_num(s) -> float:
    try:
        return float(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return float("nan")


def _is_common_stock(code: str) -> bool:
    """放寬限制：4~6碼英數字皆可（涵蓋ETF與ETN），但排除權證（通常為6碼且03~08開頭）"""
    if len(code) == 6 and code[:2] in ("03", "04", "05", "06", "07", "08"):
        return False
    return 4 <= len(code) <= 6 and code.isalnum()


# ── 股票池 ──────────────────────────────────────────────────

def _roc_date(s: str):
    """民國日期字串（1150612）→ date；解析失敗回 None"""
    s = str(s).strip()
    try:
        return dt.date(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:]))
    except (ValueError, IndexError):
        return None


def all_listings(include_otc: bool = True) -> pd.DataFrame:
    """全市場代號表（含 ETF、特別股等所有掛牌證券）。

    回傳 DataFrame：code, name, open, high, low, close, volume,
    trade_value, date, market, yahoo。
    OHLCV 與 date 來自交易所官方報價——除了選股票池，
    也用來補 Yahoo 缺漏的最新 K 線（見 patch_latest_bar）。
    """
    records = []

    resp = _get(TWSE_DAY_ALL)
    resp.raise_for_status()
    for r in resp.json():
        code = r["Code"].strip()
        records.append({
            "code": code, "name": r["Name"].strip(),
            "open": _to_num(r["OpeningPrice"]),
            "high": _to_num(r["HighestPrice"]),
            "low": _to_num(r["LowestPrice"]),
            "close": _to_num(r["ClosingPrice"]),
            "volume": _to_num(r["TradeVolume"]),
            "trade_value": _to_num(r["TradeValue"]),
            "date": _roc_date(r["Date"]),
            "market": "上市", "yahoo": f"{code}.TW",
        })

    if include_otc:
        resp = _get(TPEX_DAY_ALL)
        resp.raise_for_status()
        for r in resp.json():
            code = r["SecuritiesCompanyCode"].strip()
            records.append({
                "code": code, "name": r["CompanyName"].strip(),
                "open": _to_num(r["Open"]),
                "high": _to_num(r["High"]),
                "low": _to_num(r["Low"]),
                "close": _to_num(r["Close"]),
                "volume": _to_num(r["TradingShares"]),
                "trade_value": _to_num(r["TransactionAmount"]),
                "date": _roc_date(r["Date"]),
                "market": "上櫃", "yahoo": f"{code}.TWO",
            })

    df = pd.DataFrame(records)
    _save_name_map(df)  # 成功取得清單後更新名稱快取，供離線檢視使用
    return df


def fetch_day_quotes(day: dt.date) -> pd.DataFrame | None:
    """指定日期的全市場官方 OHLCV（上市 MI_INDEX + 上櫃 dailyQuotes）。
    非交易日回傳 None。"""
    resp = _get(TWSE_MI_INDEX,
                params={"date": day.strftime("%Y%m%d"),
                        "type": "ALLBUT0999", "response": "json"})
    payload = resp.json()
    if payload.get("stat") != "OK":
        return None
    table = next((t for t in payload.get("tables", [])
                  if "證券代號" in (t.get("fields") or [])), None)
    if table is None or not table.get("data"):
        return None

    records = []
    f = table["fields"]
    idx = {k: f.index(k) for k in
           ("證券代號", "開盤價", "最高價", "最低價", "收盤價", "成交股數")}
    for row in table["data"]:
        records.append({
            "code": row[idx["證券代號"]].strip(),
            "open": _to_num(row[idx["開盤價"]]),
            "high": _to_num(row[idx["最高價"]]),
            "low": _to_num(row[idx["最低價"]]),
            "close": _to_num(row[idx["收盤價"]]),
            "volume": _to_num(row[idx["成交股數"]]),
            "date": day,
        })

    try:
        resp = _get(TPEX_QUOTES,
                    params={"date": day.strftime("%Y/%m/%d"),
                            "type": "EW", "response": "json"})
        t = (resp.json().get("tables") or [{}])[0]
        tf = t.get("fields") or []
        if "代號" in tf:
            i = {k: tf.index(k) for k in
                 ("代號", "開盤", "最高", "最低", "收盤", "成交股數")}
            for row in (t.get("data") or []):
                records.append({
                    "code": row[i["代號"]].strip(),
                    "open": _to_num(row[i["開盤"]]),
                    "high": _to_num(row[i["最高"]]),
                    "low": _to_num(row[i["最低"]]),
                    "close": _to_num(row[i["收盤"]]),
                    "volume": _to_num(row[i["成交股數"]]),
                    "date": day,
                })
    except Exception:
        pass  # 上櫃補不到就只補上市
    return pd.DataFrame(records)


def _expected_last_session() -> dt.date:
    """最近一個「已收盤」的工作日（不處理國定假日，由呼叫端查詢確認）"""
    now = dt.datetime.now()
    day = now.date()
    if now.hour < MARKET_CLOSE_HOUR:
        day -= dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


def patch_latest_bar(history: dict[str, pd.DataFrame],
                     listings: pd.DataFrame) -> int:
    """用交易所官方報價補上 Yahoo 缺漏的最新一根 K 線，回傳補丁檔數。

    實際踩過的雷（2026-06-13 週六）：Yahoo 對幾乎全部台股回傳
    「週五 = NaN」整根被 dropna 丟掉，且 TWSE openapi 快照同時間
    也退回前一日——評分全部失真（暴跌的持股仍顯示可加碼）。
    因此：快照日期不夠新時，改打「可指定日期」的 MI_INDEX／dailyQuotes
    往回找到最近真實交易日的全市場 OHLCV，再逐檔補洞。
    （還原權息序列的最新一日調整係數為 1，用原始價補不會造成價差。）
    """
    # 快照「全部列」都已是最後交易日才直接用——openapi 在午夜輪轉時
    # 會出現混合狀態（部分列 6/12、部分列還在 6/11），看最大日期會被騙
    expected = _expected_last_session()
    
    if "date" not in listings.columns:
        return 0 # 美股沒有這個官方報價快照修補邏輯，直接回傳
        
    dates = listings["date"].dropna()
    snapshot_ok = len(dates) > 0 and (dates == expected).all()

    quotes = listings
    if not snapshot_ok:
        day = expected
        for _ in range(7):  # 往回掃，跳過國定假日
            try:
                dq = fetch_day_quotes(day)
            except Exception:
                dq = None
            if dq is not None and len(dq):
                quotes = dq
                break
            day -= dt.timedelta(days=1)
            time.sleep(1.5)
        else:
            return 0  # 按日查詢全失敗時不要用混合快照亂補

    patched = 0
    meta = quotes.set_index("code")
    for code, df in history.items():
        if code not in meta.index or df.empty:
            continue
        row = meta.loc[code]
        official = row["date"]
        if official is None or pd.isna(row["close"]):
            continue
        if df.index[-1].date() >= official:
            continue
        df.loc[pd.Timestamp(official)] = {
            "Open": row["open"], "High": row["high"],
            "Low": row["low"], "Close": row["close"],
            "Volume": row["volume"],
        }
        patched += 1
    return patched


def _save_name_map(listings: pd.DataFrame):
    """把 code→name 映射快取到本機，供無網路時退回顯示名稱。"""
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        name_map = dict(zip(listings["code"], listings["name"]))
        NAME_MAP_PATH.write_text(
            json.dumps(name_map, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # 快取寫入失敗不影響主流程


def load_name_map() -> dict[str, str]:
    """讀取本機的 code→name 映射快取。

    離線檢視（app.py 只顯示持倉、advisor show）拿不到網路清單時使用；
    快取不存在則回空 dict，呼叫端優雅退回只顯示代號。
    """
    try:
        return json.loads(NAME_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def us_listings() -> pd.DataFrame:
    """取得美股清單 (S&P 500 + 熱門 ETF) 作為初始宇宙"""
    records = []
    # 常見大盤與板塊 ETF
    etfs = ["SPY", "QQQ", "DIA", "IWM", "SMH", "SOXX", "XLE", "XLF", "XLV", "XLP", "XLU", "XLI", "XLB", "XLRE", "XLK", "XLY", "ARKK"]
    for sym in etfs:
        records.append({
            "code": sym, "name": sym + " ETF", "yahoo": sym, "market": "US",
            "close": 100, "trade_value": 1e9, "industry": "ETF"
        })
        
    mega_caps = [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "BRK-B", "LLY", "AVGO", "TSLA",
        "JPM", "WMT", "UNH", "XOM", "V", "PG", "MA", "JNJ", "HD", "ORCL",
        "COST", "ABBV", "MRK", "CVX", "CRM", "BAC", "NFLX", "AMD", "KO", "PEP",
        "TMO", "LIN", "ADBE", "DIS", "WFC", "CSCO", "MCD", "QCOM", "ABT", "INTU",
        "IBM", "AMAT", "CAT", "GE", "TXN", "DHR", "VZ", "NOW", "PFE", "PM",
        "MU", "UBER", "INTC", "ISRG", "SYK", "LOW", "SPGI", "HON", "BA", "COP",
        "BKNG", "PLTR", "ARM", "SMCI", "SNOW", "CRWD", "DDOG", "NET", "PANW", "FTNT"
    ]
    for sym in mega_caps:
        records.append({
            "code": sym, "name": sym, "yahoo": sym, "market": "US",
            "close": 100, "trade_value": 1e9, "industry": "Mega Cap / Tech"
        })
        
    df = pd.DataFrame(records)
    df = df.drop_duplicates(subset=["code"])
    _save_name_map(df)
    return df

def get_universe(min_price: float = config.MIN_PRICE,
                 min_value: float = config.MIN_TRADE_VALUE,
                 limit: int = config.UNIVERSE_LIMIT,
                 include_otc: bool = True,
                 listings: pd.DataFrame | None = None,
                 market: str = "TW") -> pd.DataFrame:
    """選股用股票池：區分台股與美股。"""
    if market == "US":
        df = listings if listings is not None else us_listings()
        return df.head(limit).reset_index(drop=True)
        
    df = listings if listings is not None else all_listings(include_otc)
    df = df[df["code"].map(_is_common_stock)]
    df = df[(df["close"] >= min_price) & (df["trade_value"] >= min_value)]
    df = df.sort_values("trade_value", ascending=False)
    return df.head(limit).reset_index(drop=True)


# ── 歷史股價（含每日快取，重跑與回測不必重新下載）──────────────

MARKET_CLOSE_HOUR = 14  # 台股 13:30 收盤，14:00 後行情資料才算完整


def _cache_path(period: str) -> Path:
    return CACHE_DIR / f"hist_{period}_{dt.date.today():%Y%m%d}.pkl"


def _cache_usable(path: Path) -> bool:
    """盤後執行時，「收盤前寫入」的當日快取視為過期（缺今日 K 線），自動重抓。
    盤前/盤中執行則沿用當日快取（檔名已含日期，跨日自然失效）。"""
    if not path.exists():
        return False
    now = dt.datetime.now()
    close_dt = now.replace(hour=MARKET_CLOSE_HOUR, minute=0, second=0, microsecond=0)
    if now < close_dt:
        return True
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
    return mtime >= close_dt


def fetch_history(tickers: dict[str, str],
                  period: str = config.HISTORY_PERIOD) -> dict[str, pd.DataFrame]:
    """下載歷史日線。tickers 為 {股票代號: yahoo代號}。

    當日已下載過的代號直接讀快取，只補抓缺少的部分。
    """
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(period)
    cached: dict[str, pd.DataFrame] = {}
    if _cache_usable(path):
        try:
            cached = pickle.loads(path.read_bytes())
            print(f"  快取命中 {len(cached)} 檔（{path.name}）")
        except Exception:
            cached = {}
    elif path.exists():
        print("  當日快取寫入於收盤前，重新下載以納入今日 K 線…")

    # 防資料倒退：Yahoo 曾在週末把已存在的最新 K 線變回 NaN，
    # 重新下載時以最近一份舊快取墊底合併，已見過的 K 線不會消失
    prior: dict[str, pd.DataFrame] = {}
    if not cached:
        old_files = sorted(CACHE_DIR.glob(f"hist_{period}_*.pkl"))
        if old_files:
            try:
                prior = pickle.loads(old_files[-1].read_bytes())
            except Exception:
                prior = {}

    out = {c: df for c, df in cached.items() if c in tickers}
    missing = {c: y for c, y in tickers.items() if c not in cached}

    chunk = config.DOWNLOAD_CHUNK
    items = list(missing.items())
    for i in range(0, len(items), chunk):
        batch = items[i:i + chunk]
        raw = yf.download([y for _, y in batch], period=period, auto_adjust=True,
                          progress=False, group_by="ticker", threads=True)
        if raw.empty:
            continue
        for code, yahoo in batch:
            try:
                df = raw[yahoo].dropna(subset=["Close"])
            except KeyError:
                continue
            old = prior.get(code)
            if old is not None and len(old) and len(df):
                df = df.combine_first(old).sort_index()  # 新資料優先、舊K線墊底
            if len(df) >= config.MA_SLOW + 5:
                out[code] = df
            cached[code] = out.get(code, pd.DataFrame())  # 空結果也記錄，避免重抓
        print(f"  已下載 {min(i + chunk, len(items))}/{len(items)} 檔歷史股價")

    if missing:
        # 清掉舊日期的快取檔，只留今天的
        for old in CACHE_DIR.glob(f"hist_{period}_*.pkl"):
            if old != path:
                old.unlink()
        path.write_bytes(pickle.dumps(cached))

    return {c: df for c, df in out.items() if len(df)}


# ── 三大法人買賣超（上市 T86 + 上櫃 TPEX）────────────────────

def t86_one_day(datestr: str) -> dict[str, dict]:
    """單日上市法人買賣超：{code: {foreign, trust}}。

    確認是假日（查無資料）回空 dict；限流或異常回應則拋例外，
    呼叫端絕不能把失敗存成假日標記。"""
    resp = _get(
        TWSE_T86,
        params={"date": datestr, "selectType": "ALLBUT0999", "response": "json"})
    payload = resp.json()
    stat = payload.get("stat", "")
    if "沒有符合條件" in stat:   # TWSE 確認的非交易日
        return {}
    if stat != "OK" or not payload.get("data"):
        raise RuntimeError(f"T86 異常回應：{stat or payload}")
    fields = payload["fields"]
    i_code = fields.index("證券代號")
    i_foreign = fields.index("外陸資買賣超股數(不含外資自營商)")
    i_trust = fields.index("投信買賣超股數")
    return {
        row[i_code].strip(): {
            "foreign": _to_num(row[i_foreign]),
            "trust": _to_num(row[i_trust]),
        }
        for row in payload["data"]
    }


def tpex_inst_one_day(day: dt.date) -> dict[str, dict]:
    """單日上櫃法人買賣超。TPEX 表格為位置式欄位：
    外陸資(不含外資自營商)買賣超=idx4、投信買賣超=idx13"""
    resp = _get(
        TPEX_INST,
        params={"type": "Daily", "sect": "EW",
                "date": day.strftime("%Y/%m/%d"), "response": "json"})
    payload = resp.json()
    tables = payload.get("tables") or [{}]
    rows = tables[0].get("data") or []
    out = {}
    for row in rows:
        if len(row) < 14:
            continue
        out[row[0].strip()] = {
            "foreign": _to_num(row[4]),
            "trust": _to_num(row[13]),
        }
    return out


def fetch_institutional(days: int = config.INST_DAYS,
                        lookback: int = config.INST_LOOKBACK) -> pd.DataFrame | None:
    """最近 N 個交易日的法人買賣超（上市+上櫃合併），index=股票代號。

    抓到的每一天同時寫入歷史庫（inst_history），日常使用即自動累積。
    """
    from . import inst_history

    frames = []
    day = dt.date.today()
    tried = 0
    store = inst_history.load_store()
    changed = False
    while len(frames) < days and tried < lookback:
        datestr = day.strftime("%Y%m%d")
        if datestr in store:  # 歷史庫已有就不重抓
            recs = store[datestr]
        else:
            try:
                recs = t86_one_day(datestr)
                if recs:  # 上市有資料才是交易日，再抓上櫃
                    try:
                        recs.update(tpex_inst_one_day(day))
                    except Exception:
                        pass
                store[datestr] = recs  # 確認結果（含真假日）才入庫
                changed = True
            except Exception:
                recs = {}  # 失敗不入庫，下次再試
            time.sleep(1.5)  # TWSE/TPEX 皆有頻率限制
        if recs:
            frames.append(pd.DataFrame.from_dict(recs, orient="index")
                          .add_suffix(f"_{datestr}"))
            print(f"  已取得 {datestr} 法人資料（{len(recs)} 檔）")
        day -= dt.timedelta(days=1)
        tried += 1

    if changed:
        inst_history.save_store(store)
    if len(frames) < days:
        return None
    return pd.concat(frames, axis=1)


# ── 月營收（上市 + 上櫃）────────────────────────────────────

INDUSTRY_MAP_PATH = CACHE_DIR / "industry_map.json"


def fetch_revenue() -> pd.DataFrame | None:
    """最新公布的月營收，index=股票代號，欄位：yoy（去年同月增減%）、
    yoy_cum（累計營收年增%）、ym（資料年月）、industry（產業別）。

    產業別順手快取到本機（industry_map.json），供回測與離線使用。"""
    records = {}
    for url in (TWSE_REVENUE, TPEX_REVENUE):
        try:
            resp = _get(url)
            rows = resp.json()
        except Exception:
            continue
        for r in rows:
            code = str(r.get("公司代號", "")).strip()
            if not _is_common_stock(code):
                continue
            records[code] = {
                "yoy": _to_num(r.get("營業收入-去年同月增減(%)")),
                "yoy_cum": _to_num(r.get("累計營業收入-前期比較增減(%)")),
                "ym": str(r.get("資料年月", "")),
                "industry": str(r.get("產業別", "")).strip() or "未分類",
            }
    if not records:
        return None
    df = pd.DataFrame.from_dict(records, orient="index")
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        INDUSTRY_MAP_PATH.write_text(
            json.dumps(df["industry"].to_dict(), ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass
    return df


def load_industry_map() -> dict[str, str]:
    """讀取本機產業別快取；不存在回空 dict"""
    try:
        return json.loads(INDUSTRY_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
