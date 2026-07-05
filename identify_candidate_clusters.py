#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

"""候補族群篩選 - 基於前置跡像框架

根據族群突破前置跡像分析的結果，從 cluster_tw.json 中找出：
1. 已有龍頭股觸發（波浪啟動中）
2. 但尚未全族群點火（還有補漲空間）
3. 符合「進場前 3~5 天」特徵的候補族群

進場準備度評分標準：
- 波浪啟動度 (0~100%)：已觸發股數 / 總股數
- 成交量一致性：高成交量股數 / 已觸發股數
- 未觸發潛力股：尚未觸發但在同族群的股票
"""

import json
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parent
CLUSTER_FILE = REPO / "data" / "results" / "cluster_tw.json"
OUTPUT_DIR = REPO / "data" / "results"


def analyze_candidate_clusters():
    """分析候補族群"""
    print("[分析] 候補族群篩選 - 基於前置跡像框架\n")

    with open(CLUSTER_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    candidates = {
        "date": data.get("date", "2026-07-03"),
        "analysis_date": "2026-07-03",
        "framework": {
            "high_volume_threshold": 2.0,
            "cluster_resonance_threshold": 0.30,  # 30% 股票同日觸發
            "wave_activation_range": "3~7 days",  # 龍頭觸發後的天數
        },
        "candidate_clusters": [],
        "ready_to_watch": [],
    }

    # 評估每個族群的候補程度
    for theme in data["themes"]:
        theme_name = theme["name"]
        total_stocks = theme.get("count", 0)
        fired_today = theme.get("fired_today_count", 0)
        fired_today_pct = fired_today / total_stocks if total_stocks > 0 else 0

        # 篩選條件：
        # 1. 已有龍頭股觸發（fired_today_count > 0）
        # 2. 但還沒全部點火（fired_today_pct < 80%）
        # 3. 族群在「波浪啟動中期」(30% ~ 80% 之間)

        if 0 < fired_today_pct < 0.80:
            # 統計該族群的詳細信息
            high_vol_count = sum(1 for s in theme["stocks"] if s.get("vol_x", 0) >= 2.0)
            unfired_stocks = [s for s in theme["stocks"] if not s.get("fired_today", False)]

            # 計算「進場準備度」
            preparation_score = 0
            preparation_reasons = []

            # 1. 龍頭股確認（已有觸發）
            if fired_today > 0:
                preparation_score += 30
                preparation_reasons.append(f"龍頭股確認（{fired_today} 檔已觸發）")

            # 2. 族群集聚度（30%+ 觸發）
            if fired_today_pct >= 0.30:
                preparation_score += 25
                preparation_reasons.append(f"族群啟動中（觸發率 {fired_today_pct:.1%}）")

            # 3. 成交量一致性（已觸發的都是高成交量）
            if high_vol_count == total_stocks:
                preparation_score += 20
                preparation_reasons.append("成交量全部異常（100% 股票 vol_x >= 2.0）")
            elif high_vol_count / total_stocks >= 0.8:
                preparation_score += 15
                preparation_reasons.append(f"成交量高度一致（{high_vol_count/total_stocks:.0%} 股票異常）")

            # 4. 補漲潛力（未觸發的潛力股數量）
            unfired_count = len(unfired_stocks)
            if unfired_count > 0:
                potential_upside_pct = (100 - fired_today_pct * 100)
                preparation_score += min(25, unfired_count * 5)  # 最多加 25 分
                preparation_reasons.append(f"補漲潛力（{unfired_count} 檔未觸發，空間 {potential_upside_pct:.0f}%）")

            candidate = {
                "theme": theme_name,
                "total_stocks": total_stocks,
                "fired_today": fired_today,
                "fired_today_pct": round(fired_today_pct, 3),
                "preparation_score": preparation_score,
                "preparation_reasons": preparation_reasons,
                "high_vol_count": high_vol_count,
                "unfired_stocks": [
                    {
                        "code": s["stock_id"],
                        "name": s["stock_name"],
                        "current_price": s.get("close"),
                        "prev_fired_dates": s.get("fired_dates", [])[-3:],  # 最近 3 次觸發日期
                    }
                    for s in unfired_stocks
                ],
                "fired_stocks_sample": [
                    {
                        "code": s["stock_id"],
                        "name": s["stock_name"],
                        "close": s.get("close"),
                        "vol_x": s.get("vol_x"),
                        "chg_pct": s.get("chg_pct"),
                    }
                    for s in theme["stocks"]
                    if s.get("fired_today", False)
                ][:3],
            }

            candidates["candidate_clusters"].append(candidate)

    # 按進場準備度排序
    candidates["candidate_clusters"].sort(key=lambda x: x["preparation_score"], reverse=True)

    # 分級
    print("=" * 80)
    print("候補族群篩選結果（按進場準備度排序）\n")

    for i, cluster in enumerate(candidates["candidate_clusters"], 1):
        score = cluster["preparation_score"]
        grade = (
            "🔥 極度熱門（立即進場觀察）" if score >= 80
            else "🌟 高度熱門（明天重點追蹤）" if score >= 60
            else "⭐ 中度關注（列入候補）" if score >= 40
            else "〇 低度關注（持續監控）"
        )

        print(f"{i}. 【{cluster['theme']}】{grade}")
        print(f"   進場準備度：{score}/100 分")
        print(f"   已觸發：{cluster['fired_today']}/{cluster['total_stocks']} 檔（{cluster['fired_today_pct']:.1%}）")
        print(f"   成交量一致性：{cluster['high_vol_count']}/{cluster['total_stocks']} 檔")
        print(f"   未觸發潛力股：{len(cluster['unfired_stocks'])} 檔\n")

        for reason in cluster["preparation_reasons"]:
            print(f"   • {reason}")

        if cluster["fired_stocks_sample"]:
            print(f"\n   【已觸發龍頭（樣本）】")
            for stock in cluster["fired_stocks_sample"]:
                print(f"   • {stock['name']}({stock['code']}) - NT${stock['close']:.2f} " +
                      f"({stock['chg_pct']:+.1f}%) | vol {stock['vol_x']:.1f}x")

        if cluster["unfired_stocks"]:
            print(f"\n   【待補漲潛力股】")
            for stock in cluster["unfired_stocks"][:5]:
                prev_fires = ", ".join(stock["prev_fired_dates"][-2:]) if stock["prev_fired_dates"] else "無"
                print(f"   • {stock['name']}({stock['code']}) - NT${stock['current_price']:.2f} " +
                      f"| 前火日: {prev_fires}")

        print()

    # 生成推薦名單
    print("=" * 80)
    print("【進場觀察推薦名單】\n")

    high_priority = [c for c in candidates["candidate_clusters"] if c["preparation_score"] >= 60]
    medium_priority = [c for c in candidates["candidate_clusters"] if 40 <= c["preparation_score"] < 60]

    if high_priority:
        print(f"🔥 優先級 1 - 極度/高度熱門（{len(high_priority)} 個族群）")
        print("   建議：明天盤中重點追蹤，視成交量和股價變化決定進場\n")
        for c in high_priority:
            candidates["ready_to_watch"].append({
                "priority": 1,
                "theme": c["theme"],
                "score": c["preparation_score"],
                "next_watch_signals": [
                    "同族群再有 1~2 檔點火",
                    "未觸發潛力股出現『小量異常』",
                    "焦點日期周邊（前後 1 天）成交量突增"
                ]
            })
            print(f"   • {c['theme']}")

    if medium_priority:
        print(f"\n⭐ 優先級 2 - 中度關注（{len(medium_priority)} 個族群）")
        print("   建議：列入候補觀察，等待更強的點火訊號\n")
        for c in medium_priority:
            candidates["ready_to_watch"].append({
                "priority": 2,
                "theme": c["theme"],
                "score": c["preparation_score"],
            })
            print(f"   • {c['theme']}")

    # 保存 JSON
    with open(OUTPUT_DIR / "cluster_candidates_watching.json", "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 分析完成，候補名單已存至 data/results/cluster_candidates_watching.json\n")

    return candidates


if __name__ == "__main__":
    candidates = analyze_candidate_clusters()
