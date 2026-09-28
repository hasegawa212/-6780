#!/usr/bin/env python3
"""Match transactions against inventory files."""
import json
import re
from collections import defaultdict

# Katakana → Kanji/keyword mapping for transaction-counterpart names
NAME_MAP = {
    # 入金
    "ムサシノジユウタクハンバイ": ["武蔵野住宅販売", "武蔵野住宅"],
    "トウヨウケンセツホーム": ["東洋建設ホーム", "東洋建設"],
    "カ）エムドツトエム": ["M.M", "エムドット", "ｴﾑﾄﾞｯﾄ"],
    "カ）ヨネヤマジシヨ": ["米山地所", "ヨネヤマ"],
    "カ）エービー": ["株式会社ab", "（株）ab", "(株)ab", "株式会社AB", "ab御中"],
    "カ）FGH": ["FGH"],
    "カ）セルフリジエネレーシヨン": ["セルフリジェネレーション", "セルフリジェネレ"],
    "カ）アプラス": ["アプラス"],
    "カ)アプラス": ["アプラス"],
    "オリコ": ["オリコ"],
    "カ）ハセコウライブネツト": ["ハセコウライブ", "長谷工ライブ"],
    "カ）ウイナーズ": ["ウィナーズ", "ウイナーズ"],
    "カ）ベルト カイカン": ["ベルト会館", "ベルトカイカン"],
    "カ）ユニーク": ["Unique", "ユニーク"],
    "カ）ネクスト": ["ネクスト"],
    "カ）ランドネツト": ["ランドネット"],
    "カ）エヌ クルー": ["N-CREW", "エヌクルー"],
    "カ）エイトフアミリア": ["エイトファミリア"],
    "セキスイハウスシヤーメゾン": ["セキスイハウス", "シャーメゾン"],
    "ダイトウケンタク": ["大東建託"],
    "ニホンハウズイング": ["日本ハウズイング", "ハウズイング"],
    "BTトウキユウコミユニテイー": ["東急コミュニティ"],
    "コガネイフドウサン": ["小金井不動産"],
    # 個人名
    "キムラ シユン": ["木村"],
    "ナガイ コウヘイ": ["永井"],
    "イマイ ノリエ": ["今井"],
    "イマイダ シヨウ": ["今飯田"],
    "タシロ ヒロミチ": ["田代"],
    "サクマ ジユン": ["佐久間"],
    "ミズマ リヨウジユ": ["水間", "水落", "水真"],
    "タカハシ シヨウタ": ["髙橋", "高橋"],
    "マツシタ マサル": ["松下", "松島"],
    "マツシタ シヨウ": ["松下", "松島"],
    "ヒラマツ フミヤ": ["平松"],
    "コイケ マサヒロ": ["小池"],
    "オウミ ヤスアキ": ["近江", "大海"],
    "キマヅカ マサヒロ": ["木間塚"],
    "オオワダケイタ": ["大和田"],
    "オオサキヒデヤ": ["大崎"],
    "ZHUANG HAORAN": ["ZHUANG", "荘", "庄"],
    "シヨウ コウゼン": ["庄", "祥"],
    "ミヤシタ ナオト": ["宮下"],
    "タナカ サダハル": ["田中"],
    "サトウキヨウスケ": ["佐藤"],
    "イサカシンヤ": ["井坂", "井阪"],
    "ナカムラ ヒロシ": ["中村博", "中村　博"],
    "ナカムラヒロシ": ["中村博", "中村　博"],
    "アオキ シユン": ["青木"],
    "イチケ コウタイ": ["市毛"],
    "イヌヅカ アツヒロ": ["犬塚", "戌塚"],
    "コイズミ リユウヘイ": ["小泉"],
    "ミヤザキ サダノリ": ["宮崎"],
    "ヨシナ マサノリ": ["吉名", "吉永"],
    "ツナカワ ダイゴ": ["綱川", "津名川"],
    "ウエタ アユム": ["上田"],
    "サトウ リヨウマ": ["佐藤", "諒茉", "リョウマ"],
    "カワセユウヤ": ["川瀬", "河瀬"],
    # 出金
    "カ）デイーウイシヨン": ["D-Vision", "ディーウィジョン", "ディヴィジョン"],
    "ヤマサキ シスエ": ["山崎"],
    "イシカワ マサカズ": ["石川"],
    "オキノ ミエコ": ["沖野", "荻野"],
    "ホンダ アヤカ": ["本田"],
    "オキツキミコ": ["興津", "沖津", "オキツ"],
    "カ）エーエフシースマイル": ["AFCスマイル", "ＡＦＣスマイル"],
    "カ）エステートリンク": ["エステートリンク"],
    "シマカキ ホクタク": ["島垣", "嶋垣", "シマカキ"],
    "ハウスケ": ["ハウスケ", "HOUSEKE"],
    "カ）リンクエイシ": ["リンクエイジ"],
    "カ）リハーコーホレーシ": ["リハーコーポレーション", "リハーコポレ"],
    "村田菜津美": ["村田", "菜津美"],
    "カ）コムウェル": ["コムウェル"],
    "ト）スタテツク": ["スタテック", "Statec"],
    "カタオカ ハツミ": ["片岡"],
    "コントウ クニヨ": ["近藤"],
    "コントウ ツネオ": ["近藤"],
    "ヨシモト キヨウヘイ": ["吉本"],
    "カ）フアインレンタカー": ["ファインレンタカー"],
    "ニホンハウスインク": ["日本ハウス", "ニホンハウス"],
    "ストライプシヤハン": ["ストライプ"],
    "イトウ スミレ": ["伊藤"],
    "ウチサワ ナチ": ["内沢"],
    "ササモト アマネ": ["笹本"],
    "カ）ハウスコンサルタント": ["ハウスコンサルタント"],
    "カ）イースタイル": ["イースタイル"],
    "ユ）イマカワフドウサンセンター": ["今川不動産"],
    "ハク シンウ": ["白", "パク"],
    "スズキ マサアキ": ["鈴木"],
    "シユウタクサーヒスコ": ["住宅サービス"],
    "カ）アナフキレシテンシヤル": ["アナフキレジデンシャル", "アナフキ"],
    "ワタナベ エツコ": ["渡邉", "渡辺"],
    "ケイアイエホツクメイキング": ["KIエポックメイキング", "KAIエポック"],
    "武蔵野銀行狭山西": ["武蔵野銀行"],
}

def match_files(payer_name, file_titles_indexed, amount=None, kind=None):
    """Return list of (id, title) matched."""
    keywords = NAME_MAP.get(payer_name, [])
    matches = []
    for f in file_titles_indexed:
        t = f['title']
        if not isinstance(t, str): continue
        # Skip folders/shortcuts unless useful
        for kw in keywords:
            if kw in t:
                matches.append(f)
                break
    return matches

def filter_by_kind(matches, kind):
    """Prefer files that match the document type."""
    if not kind: return matches
    primary = []
    for f in matches:
        t = f['title']
        if kind == '請求書' and ('請求書' in t or 'INV' in t.upper() or 'invoice' in t.lower() or '請求' in t):
            primary.append(f)
        elif kind == '契約書' and ('契約書' in t or '売買契約' in t or '請負契約' in t):
            primary.append(f)
        elif kind == '領収書' and '領収' in t:
            primary.append(f)
        elif kind == '明細' and '明細' in t:
            primary.append(f)
    if primary:
        return primary
    return matches

# Load
with open('/home/user/-6780/data/income_all.json') as f: income_files = json.load(f)
with open('/home/user/-6780/data/outcome_all.json') as f: outcome_files = json.load(f)
with open('/home/user/-6780/data/transactions.json') as f: tx = json.load(f)

# Date string parse for filtering
def reiwa_to_iso(d):
    # 令和7年=2025, 令和8年=2026
    m = re.match(r'令和(\d+)年(\d+)月(\d+)日', d)
    if not m: return None
    yr = int(m.group(1))
    western = 2018 + yr
    return f"{western:04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"

def date_keys(d):
    iso = reiwa_to_iso(d)
    if not iso: return []
    y, m, dd = iso[:4], iso[4:6], iso[6:]
    return [iso, f"{y}{m}{dd}", f"{y}.{int(m)}.{int(dd)}", f"{y}.{m}.{dd}", f"{y}-{m}-{dd}", f"{y}/{m}/{dd}", f"{y}年{int(m)}月{int(dd)}日", f"{int(m)}/{int(dd)}", f"{int(m)}.{int(dd)}"]

def match_amount(amount, title):
    if amount is None: return False
    abs_a = abs(amount)
    if str(abs_a) in title: return True
    # comma formats
    if f"{abs_a:,}" in title: return True
    return False

def find_for(t, files, role='payer'):
    payer = t.get(role) or t.get('payee')
    matches = match_files(payer, files)
    primary_kind = None
    needs = t.get('needs','')
    if '請求書' in needs: primary_kind = '請求書'
    elif '契約書' in needs: primary_kind = '契約書'
    elif '明細' in needs: primary_kind = '明細'
    elif '領収' in needs: primary_kind = '領収書'
    filtered = filter_by_kind(matches, primary_kind)
    # boost by date or amount
    boosted = []
    dks = date_keys(t['date'])
    for f in filtered:
        score = 0
        for dk in dks:
            if dk in f['title']: score += 5
        if match_amount(t.get('amount'), f['title']): score += 10
        boosted.append((score, f))
    boosted.sort(reverse=True, key=lambda x: x[0])
    return boosted[:5]  # top 5

# Process income
results_income = []
for t in tx['income']:
    matched = find_for(t, income_files, 'payer')
    results_income.append({'tx': t, 'matches': matched})

results_outcome = []
for t in tx['outcome']:
    matched = find_for(t, outcome_files, 'payee')
    results_outcome.append({'tx': t, 'matches': matched})

# Also search across both folders for cross-references
all_files = income_files + outcome_files

# Find duplicates (same title + same fileSize)
dup_keys = defaultdict(list)
for f in income_files + outcome_files:
    if f.get('fileSize'):
        key = (f['title'], f['fileSize'])
        dup_keys[key].append(f)
duplicates = {k: v for k, v in dup_keys.items() if len(v) > 1}

# Save results
with open('/home/user/-6780/data/match_results.json', 'w') as f:
    json.dump({
        'income': [{'tx': r['tx'], 'matches': [{'score': s, 'id': m['id'], 'title': m['title'], 'fileSize': m.get('fileSize')} for s, m in r['matches']]} for r in results_income],
        'outcome': [{'tx': r['tx'], 'matches': [{'score': s, 'id': m['id'], 'title': m['title'], 'fileSize': m.get('fileSize')} for s, m in r['matches']]} for r in results_outcome],
        'duplicates': [{'title': k[0], 'fileSize': k[1], 'ids': [x['id'] for x in v]} for k, v in duplicates.items()]
    }, f, ensure_ascii=False, indent=1)

income_matched = sum(1 for r in results_income if r['matches'])
outcome_matched = sum(1 for r in results_outcome if r['matches'])
print(f"income matched: {income_matched}/{len(results_income)}")
print(f"outcome matched: {outcome_matched}/{len(results_outcome)}")
print(f"duplicates (same title+size): {len(duplicates)}")
