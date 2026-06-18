"""S&P 500 歷史成分股（point-in-time），用於消除回測的存活者偏誤

回測若只用「今日」的成分股，等於開卷考——今天的權值龍頭當年本就是贏家，
動能策略又專挑贏家，績效會被嚴重灌水。本模組從 Wikipedia 的
「現役成分股」+「歷史變動紀錄」重建任一時點的真實成分股名單，
讓回測在每個調倉日只看「當時實際在指數內」的股票（含後來被剔除者）。

殘留限制（誠實申報）：被剔除/下市的股票，yfinance 不一定抓得到歷史價，
抓不到者只能排除——這仍是「部分」存活者偏誤，但遠小於只用今日權值股。
涵蓋率會在回測時印出。
"""

import datetime as dt
import io
import json
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path(__file__).parent / "cache"
WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
HEADERS = {"User-Agent": "Mozilla/5.0 (sp500-history)"}


def _norm(sym: str) -> str:
    """Wikipedia 代號 → yfinance 代號（class share 的 . 改 -，如 BRK.B→BRK-B）"""
    return str(sym).strip().upper().replace(".", "-")


def _cache_path() -> Path:
    return CACHE_DIR / f"sp500_wiki_{dt.date.today():%Y%m%d}.json"


def _fetch_tables() -> tuple[list[str], list[dict]]:
    """回傳 (現役成分股代號, 變動紀錄)。變動紀錄每筆：{date, added, removed}。"""
    d = _fetch_all()
    return d["current"], d["changes"]


def current_constituents() -> list[dict]:
    """現役 S&P 500 成分股明細：[{code, name, sector}]（含 GICS 產業，供選股池與前端用）"""
    return _fetch_all()["details"]


def _newest_cache() -> dict | None:
    """讀取最近一次的成分股快取（不限當日），供 Wikipedia 抓取失敗時的持久後備。"""
    files = sorted(CACHE_DIR.glob("sp500_wiki_*.json"), reverse=True)
    for f in files:
        try:
            d = json.loads(f.read_text())
            if "details" in d:
                return d
        except Exception:
            continue
    return None


def _fetch_all() -> dict:
    path = _cache_path()
    if path.exists():
        try:
            d = json.loads(path.read_text())
            if "details" in d:  # 舊快取無 details 時重抓
                return d
        except Exception:
            pass

    try:
        return _download_and_parse(path)
    except Exception as e:
        # Wikipedia 抓取/解析失敗 → 退回最近一次成功的快取（避免線上單點故障）
        fallback = _newest_cache()
        if fallback is not None:
            print(f"  ! S&P 500 成分股抓取失敗（{e}），改用最近一次快取（{len(fallback['details'])} 檔）")
            return fallback
        raise


def _download_and_parse(path: Path) -> dict:
    html = requests.get(WIKI_URL, headers=HEADERS, timeout=30).text
    tabs = pd.read_html(io.StringIO(html))
    cur = tabs[0]
    current = [_norm(s) for s in cur["Symbol"].tolist()]
    details = [{"code": _norm(r["Symbol"]),
                "name": str(r["Security"]),
                "sector": str(r.get("GICS Sector", "") or "")}
               for _, r in cur.iterrows()]

    ch = tabs[1].copy()
    ch.columns = ["_".join(str(x) for x in c) if isinstance(c, tuple) else str(c)
                  for c in ch.columns]
    date_col = [c for c in ch.columns if "Effective" in c][0]
    add_col = [c for c in ch.columns if c.startswith("Added") and "Ticker" in c][0]
    rem_col = [c for c in ch.columns if c.startswith("Removed") and "Ticker" in c][0]

    changes = []
    for _, r in ch.iterrows():
        d = pd.to_datetime(r[date_col], errors="coerce")
        if pd.isna(d):
            continue
        added = r[add_col]
        removed = r[rem_col]
        changes.append({
            "date": d.strftime("%Y-%m-%d"),
            "added": _norm(added) if pd.notna(added) else "",
            "removed": _norm(removed) if pd.notna(removed) else "",
        })

    CACHE_DIR.mkdir(exist_ok=True)
    payload = {"current": current, "changes": changes, "details": details}
    path.write_text(json.dumps(payload))
    return payload


def membership_panel(trading_index: pd.DatetimeIndex) -> pd.DataFrame:
    """布林面板 [交易日 × 代號]：該日該股是否為 S&P 500 成分股。

    作法：從今日成分股回推到回測起點，再順著變動紀錄向前重放，
    在每個交易日標記「當時的成分股集合」。
    """
    current, changes = _fetch_tables()
    changes = sorted(changes, key=lambda c: c["date"])
    cdates = [pd.Timestamp(c["date"]) for c in changes]
    t0 = trading_index[0]

    # 1) 從今日集合回推到 t0：把 t0 之後的變動逐一還原（新→舊）
    members = set(current)
    for c in reversed(changes):
        if pd.Timestamp(c["date"]) <= t0:
            break
        if c["added"]:
            members.discard(c["added"])
        if c["removed"]:
            members.add(c["removed"])  # 當時還在，後來才被剔除 → 加回

    # 2) 收集整段期間出現過的所有代號（含後來被剔除者）
    universe = set(members)
    for c in changes:
        if t0 < pd.Timestamp(c["date"]) <= trading_index[-1]:
            if c["added"]:
                universe.add(c["added"])
            if c["removed"]:
                universe.add(c["removed"])

    # 3) 順著時間向前重放，逐日標記成分股集合
    cols = sorted(universe)
    panel = pd.DataFrame(False, index=trading_index, columns=cols)
    ptr = 0
    cur = set(members)
    for i, day in enumerate(trading_index):
        while ptr < len(changes) and cdates[ptr] <= day:
            c = changes[ptr]
            if c["added"]:
                cur.add(c["added"])
            if c["removed"]:
                cur.discard(c["removed"])
            ptr += 1
        present = [s for s in cur if s in panel.columns]
        panel.iloc[i, panel.columns.get_indexer(present)] = True
    return panel
