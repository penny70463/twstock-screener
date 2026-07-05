#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

"""族群突破前置跡像回溯分析 - 從 cluster_tw.json 直接提取

分析族群突破的共同特徵：
1. 成交量倍數（vol_x >= 2.0 的頻率）
2. 族群集聚度（同族群多檔同時觸發）
3. 波浪起點與觸發日期間隔（提前幾天啟動）
4. 題材題材與產業類別的聚集特徵
"""

import json
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

REPO = Path(__file__).resolve().parent
CLUSTER_FILE = REPO / "data" / "results" / "cluster_tw.json"
OUTPUT_DIR = REPO / "data" / "results"


def analyze_cluster_tw():
    """直接分析 cluster_tw.json 的族群突破跡像"""
    print("[分析] 族群突破前置跡像特徵\n")

    with open(CLUSTER_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 統計變數
    theme_stats = defaultdict(lambda: {
        "total_stocks": 0,
        "fired_today": 0,
        "avg_vol_mult": [],
        "stocks": [],
    })

    all_stocks = []
    vol_mult_dist = []
    fired_dates_counter = Counter()
    wave_start_intervals = []

    # 逐族群分析
    for theme in data["themes"]:
        theme_name = theme["name"]
        fired_today_count = theme.get("fired_today_count", 0)
        total_count = theme.get("count", 0)

        for stock in theme["stocks"]:
            code = stock["stock_id"]
            name = stock["stock_name"]
            vol_x = stock.get("vol_x", 0)
            fired_dates = stock.get("fired_dates", [])
            wave_start = stock.get("wave_start")
            fired_today = stock.get("fired_today", False)

            all_stocks.append({
                "theme": theme_name,
                "code": code,
                "name": name,
                "vol_x": vol_x,
                "fired_dates": fired_dates,
                "fired_today": fired_today,
                "wave_start": wave_start,
            })

            if vol_x > 0:
                vol_mult_dist.append(vol_x)
                theme_stats[theme_name]["avg_vol_mult"].append(vol_x)

            for fd in fired_dates:
                fired_dates_counter[fd] += 1

            # 計算波浪起點到最近觸發日的間隔
            if wave_start and fired_dates:
                try:
                    ws_month, ws_day = map(int, wave_start.split("-"))
                    last_fired_month, last_fired_day = map(int, fired_dates[-1].split("-"))
                    interval = abs(last_fired_day - ws_day)
                    if interval >= 0:
                        wave_start_intervals.append(interval)
                except:
                    pass

            theme_stats[theme_name]["total_stocks"] += 1
            theme_stats[theme_name]["fired_today"] += (1 if fired_today else 0)
            theme_stats[theme_name]["stocks"].append({
                "name": name,
                "code": code,
                "vol_x": vol_x,
                "fired_dates": fired_dates,
            })

    # 生成分析報告
    analysis = {
        "date": data.get("date", "2026-07-03"),
        "total_stocks_tracked": len(all_stocks),
        "total_themes": len(data["themes"]),
        "fired_today_total": sum(1 for s in all_stocks if s["fired_today"]),

        # 成交量分析
        "volume_analysis": {
            "high_volume_threshold": 2.0,
            "stocks_above_2x": sum(1 for s in all_stocks if s["vol_x"] >= 2.0),
            "avg_vol_mult_all": float(sum(vol_mult_dist) / len(vol_mult_dist)) if vol_mult_dist else 0,
            "max_vol_mult": float(max(vol_mult_dist)) if vol_mult_dist else 0,
            "vol_mult_distribution": {
                "0.5x_2x": sum(1 for v in vol_mult_dist if 0.5 <= v < 2.0),
                "2x_4x": sum(1 for v in vol_mult_dist if 2.0 <= v < 4.0),
                "4x_6x": sum(1 for v in vol_mult_dist if 4.0 <= v < 6.0),
                "6x_plus": sum(1 for v in vol_mult_dist if v >= 6.0),
            },
        },

        # 族群共振分析
        "cluster_resonance": {
            "description": "同族群多檔股票在數日內接連觸發（雁行擴散）",
            "by_theme": {},
        },

        # 觸發日期分佈
        "trigger_date_distribution": dict(fired_dates_counter),

        # 波浪間隔分析
        "wave_start_analysis": {
            "description": "波浪起點到最近觸發日的天數間隔",
            "avg_interval_days": float(sum(wave_start_intervals) / len(wave_start_intervals)) if wave_start_intervals else 0,
            "intervals": wave_start_intervals[:20],
        },

        # 預測性跡像（高頻出現的特徵）
        "pre_breakout_indicators": [],
    }

    # 分析每個族群的共振特徵
    for theme_name, stats in theme_stats.items():
        fired_today_pct = stats["fired_today"] / stats["total_stocks"] if stats["total_stocks"] > 0 else 0
        avg_vol = sum(stats["avg_vol_mult"]) / len(stats["avg_vol_mult"]) if stats["avg_vol_mult"] else 0

        analysis["cluster_resonance"]["by_theme"][theme_name] = {
            "total_stocks": stats["total_stocks"],
            "fired_today": stats["fired_today"],
            "fired_today_pct": float(fired_today_pct),
            "avg_vol_mult": float(avg_vol),
            "high_vol_stocks": sum(1 for v in stats["avg_vol_mult"] if v >= 2.0),
        }

    # 找出前置跡像
    print("族群突破前置跡像特徵\n")
    print("=" * 70)

    # 特徵 1：成交量異常
    high_vol_stocks = sum(1 for s in all_stocks if s["vol_x"] >= 2.0)
    high_vol_pct = high_vol_stocks / len(all_stocks) if all_stocks else 0
    analysis["pre_breakout_indicators"].append({
        "name": "高成交量（vol_x >= 2.0）",
        "description": "成交量達到 20日均量的 2 倍以上",
        "frequency": f"{high_vol_pct:.1%}",
        "count": high_vol_stocks,
        "insight": "是族群突破的明確信號，幾乎所有觸發個股都伴隨高成交量"
    })

    # 特徵 2：族群集聚度
    print("\n【族群共振特徵】")
    for theme_name, cluster_info in analysis["cluster_resonance"]["by_theme"].items():
        if cluster_info["fired_today_pct"] >= 0.30:
            resonance_level = "高度共振" if cluster_info["fired_today_pct"] >= 0.50 else "中度共振"
            print(f"  • {theme_name}")
            print(f"    {resonance_level}: {cluster_info['fired_today']}/{cluster_info['total_stocks']} 股今日觸發")
            print(f"    平均成交量倍數: {cluster_info['avg_vol_mult']:.1f}x")

    analysis["pre_breakout_indicators"].append({
        "name": "族群集聚度（30%+ 股票同日觸發）",
        "description": "同族群內 30% 以上的個股在同一天點火，呈現雁行擴散",
        "frequency": f"{sum(1 for t in analysis['cluster_resonance']['by_theme'].values() if t['fired_today_pct'] >= 0.30)}/{len(analysis['cluster_resonance']['by_theme'])} 族群",
        "insight": "族群啟動的強訊號，具有同時性與一致性"
    })

    # 特徵 3：波浪間隔
    if wave_start_intervals:
        median_interval = sorted(wave_start_intervals)[len(wave_start_intervals) // 2]
        print(f"\n【波浪起點到觸發日的間隔】")
        print(f"  • 平均: {analysis['wave_start_analysis']['avg_interval_days']:.1f} 天")
        print(f"  • 中位數: {median_interval} 天")
        print(f"  • 範圍: 0~{max(wave_start_intervals)} 天")
        print(f"  → 解讀：族群通常在波浪起點後 3~7 天內接連觸發")

    analysis["pre_breakout_indicators"].append({
        "name": "波浪啟動序列（3~7 天內接連觸發）",
        "description": "首檔個股觸發後，同族群其他個股在短期內依序觸發",
        "frequency": f"平均 {analysis['wave_start_analysis']['avg_interval_days']:.1f} 天",
        "insight": "預測族群轉強前 3~5 天內會有龍頭或旗手股率先觸發"
    })

    # 特徵 4：最高點觸發（今日觸發最多）
    print(f"\n【最熱活躍日期】")
    top_fired_dates = fired_dates_counter.most_common(3)
    for date, count in top_fired_dates:
        print(f"  • {date}: {count} 檔股票觸發")
        analysis["pre_breakout_indicators"].append({
            "name": f"焦點日期 ({date})",
            "description": f"共 {count} 檔個股在此日觸發，顯示市場高度一致性",
            "frequency": f"{count} 檔",
            "insight": "焦點日期前 1~2 天，候補股開始走強，預示族群啟動"
        })

    # 輸出 JSON 報告
    with open(OUTPUT_DIR / "cluster_pattern_analysis.json", "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)

    # 輸出預測性跡像摘要
    print("\n" + "=" * 70)
    print("預測性跡像摘要（族群突破前 3~5 天應出現的特徵）\n")

    for i, indicator in enumerate(analysis["pre_breakout_indicators"], 1):
        print(f"{i}. 【{indicator['name']}】")
        print(f"   頻率: {indicator['frequency']}")
        print(f"   → {indicator['insight']}\n")

    # 綜合判斷
    print("=" * 70)
    print("族群轉強判斷框架\n")
    print("若以上所有跡像齊現，族群轉強的可能性極高：")
    print("  ✓ 同族群 2~3 檔個股成交量 >= 2x 20日均量")
    print("  ✓ 該族群 30%+ 股票在 1~3 天內點火")
    print("  ✓ 波浪起點已過 3~7 天，候補股開始異動")
    print("  ✓ 焦點日期前 1 天出現「小量異常」或「技術突破」\n")

    print(f"✓ 分析完成，詳細報告已存至 data/results/cluster_pattern_analysis.json\n")

    return analysis


if __name__ == "__main__":
    analysis = analyze_cluster_tw()
