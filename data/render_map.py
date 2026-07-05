#!/usr/bin/env python3
"""Render final location map."""
import json

with open('/home/user/-6780/data/match_results.json') as f:
    results = json.load(f)

lines = []
lines.append("# マーシャルアーツ様 不足資料 所在マップ（2026/06/02時点）\n")

income_total = len(results['income'])
outcome_total = len(results['outcome'])
income_matched = sum(1 for r in results['income'] if r['matches'])
outcome_matched = sum(1 for r in results['outcome'] if r['matches'])
dup_count = len(results['duplicates'])

lines.append("## サマリー\n")
lines.append(f"- 入金資料不足: 全{income_total}件 → マッチ{income_matched}件 / 未マッチ{income_total-income_matched}件")
lines.append(f"- 出金資料不足: 全{outcome_total}件 → マッチ{outcome_matched}件 / 未マッチ{outcome_total-outcome_matched}件")
lines.append(f"- 完全一致重複候補: {dup_count}件\n")
lines.append("注: マッチは「取引先名キーワード一致」＋「ファイル種別フィルタ」での候補抽出。スコアは日付/金額一致でブースト。候補は最大3件まで表示。最終確認はHIKARU様判断で。\n")

# Income table
lines.append("## 1.入金資料 マッチング結果\n")
lines.append("| # | 日付 | 取引先 | 金額 | 不足 | 所在 | 候補ファイル / FileID |")
lines.append("| - | --- | ----- | --- | ---- | ---- | ------------------- |")
for r in results['income']:
    t = r['tx']
    no = t['no']
    if r['matches']:
        for i, m in enumerate(r['matches']):
            tag = f"⭐{m['score']}" if m['score'] > 0 else "△"
            folder = m['folder'] or '?'
            line1 = f"{no}" if i == 0 else ""
            d = t['date'] if i == 0 else ""
            p = t['payer'] if i == 0 else ""
            amt = f"{t['amount']:,}" if i == 0 else ""
            n = t['needs'] if i == 0 else ""
            lines.append(f"| {line1} | {d} | {p} | {amt} | {n} | {folder} {tag} | {m['title']} / `{m['id']}` |")
    else:
        lines.append(f"| {no} | {t['date']} | {t['payer']} | {t['amount']:,} | {t['needs']} | **要確認** | （該当なし） |")

lines.append("")

# Outcome table
lines.append("## 2.出金資料 マッチング結果\n")
lines.append("| # | 日付 | 取引先 | 金額 | 不足 | 所在 | 候補ファイル / FileID |")
lines.append("| - | --- | ----- | --- | ---- | ---- | ------------------- |")
for r in results['outcome']:
    t = r['tx']
    no = t['no']
    if r['matches']:
        for i, m in enumerate(r['matches']):
            tag = f"⭐{m['score']}" if m['score'] > 0 else "△"
            folder = m['folder'] or '?'
            line1 = f"{no}" if i == 0 else ""
            d = t['date'] if i == 0 else ""
            p = t['payee'] if i == 0 else ""
            amt = f"{t['amount']:,}" if i == 0 else ""
            n = t['needs'] if i == 0 else ""
            lines.append(f"| {line1} | {d} | {p} | {amt} | {n} | {folder} {tag} | {m['title']} / `{m['id']}` |")
    else:
        lines.append(f"| {no} | {t['date']} | {t['payee']} | {t['amount']:,} | {t['needs']} | **要確認** | （該当なし） |")

lines.append("")

# Duplicates
lines.append("## 重複ファイル候補（title+fileSize 完全一致）\n")
lines.append("以下は一方を削除候補。中身を必ず1件目だけ開き、内容確認後に削除推奨。\n")
for d in results['duplicates']:
    title = d['title']
    sz = d['fileSize']
    lines.append(f"- **{title}** (size={sz} bytes)")
    for x in d['ids']:
        lines.append(f"  - `{x['id']}` ({x['folder']})")

lines.append("")

# Unmatched lists for clarity
lines.append("## 未マッチ取引（要追加調査）\n")
lines.append("### 入金資料 未マッチ\n")
for r in results['income']:
    if not r['matches']:
        t = r['tx']
        lines.append(f"- #{t['no']} {t['date']} | {t['payer']} | {t['amount']:,} | {t['needs']}")

lines.append("\n### 出金資料 未マッチ\n")
for r in results['outcome']:
    if not r['matches']:
        t = r['tx']
        lines.append(f"- #{t['no']} {t['date']} | {t['payee']} | {t['amount']:,} | {t['needs']}")

lines.append("")
lines.append("## 凡例\n")
lines.append("- ⭐**N**: マッチスコア（日付一致+5、金額一致+10）— 数字が高いほど精度が高い。")
lines.append("- △: 取引先名のみで候補化（スコア0）。複数候補がある場合は中身で要確認。")
lines.append("- **要確認**: 自動マッチできず。同フォルダ内で別名で保管されているか、未取得の可能性あり。")
lines.append("- カナ→漢字推測は機械的なため誤マッチ可能性あり。最終確認はExcelの「補助列」やオリジナル明細と突合してください。")

with open('/home/user/-6780/location_map.md', 'w') as f:
    f.write('\n'.join(lines))

print(f"Wrote /home/user/-6780/location_map.md ({len(lines)} lines)")
