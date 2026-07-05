#!/usr/bin/env python3
"""Match transactions against inventory files - improved."""
import json
import re
from collections import defaultdict

# Katakana → Kanji/keyword mapping
NAME_MAP = {
    "ムサシノジユウタクハンバイ": ["武蔵野住宅販売", "武蔵野住宅"],
    "トウヨウケンセツホーム": ["東洋建設ホーム", "東洋建設"],
    "カ）エムドツトエム": ["M.M", "ｴﾑﾄﾞｯﾄ", "M．M"],
    "カ）ヨネヤマジシヨ": ["米山地所", "ヨネヤマ"],
    "カ）エービー": ["株式会社ab", "（株）ab", "(株)ab", "株式会社AB", "ab御中", "ab　御中", "株式会社ab", "（株）ab御中"],
    "カ）FGH": ["FGH"],
    "カ）セルフリジエネレーシヨン": ["セルフリジェネレーション", "セルフリジェネレ"],
    "カ）アプラス": ["アプラス"],
    "カ)アプラス": ["アプラス"],
    "オリコ": ["オリコ", "オリエントコーポレーション"],
    "カ）ハセコウライブネツト": ["ハセコウライブ", "長谷工ライブ", "ハセコウ"],
    "カ）ウイナーズ": ["ウィナーズ", "ウイナーズ"],
    "カ）ベルト カイカン": ["ベルト会館", "ベルトカイカン", "Belt"],
    "カ）ユニーク": ["Unique", "ユニーク"],
    "カ）ネクスト": ["ネクスト"],
    "カ）ランドネツト": ["ランドネット"],
    "カ）エヌ クルー": ["N-CREW", "エヌクルー", "Ｎ−ＣＲＥＷ"],
    "カ）エイトフアミリア": ["エイトファミリア"],
    "セキスイハウスシヤーメゾン": ["セキスイハウス", "シャーメゾン"],
    "ダイトウケンタク": ["大東建託"],
    "ニホンハウズイング": ["日本ハウズイング", "ハウズイング"],
    "BTトウキユウコミユニテイー": ["東急コミュニティ"],
    "コガネイフドウサン": ["小金井不動産"],
    # 個人名
    "キムラ シユン": ["木村"],
    "ナガイ コウヘイ": ["永井"],
    "イマイ ノリエ": ["今井", "ノリエ"],
    "イマイダ シヨウ": ["今飯田"],
    "タシロ ヒロミチ": ["田代"],
    "サクマ ジユン": ["佐久間"],
    "ミズマ リヨウジユ": ["水間", "水落", "水真", "ミズマ"],
    "タカハシ シヨウタ": ["髙橋", "高橋", "髙橋昌太", "髙橋勇気"],
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
    "イサカシンヤ": ["井坂"],
    "ナカムラ ヒロシ": ["中村博", "中村　博"],
    "ナカムラヒロシ": ["中村博", "中村　博"],
    "アオキ シユン": ["青木駿", "青木"],
    "イチケ コウタイ": ["市毛"],
    "イヌヅカ アツヒロ": ["犬塚", "戌塚", "イヌヅカ"],
    "コイズミ リユウヘイ": ["小泉"],
    "ミヤザキ サダノリ": ["宮崎"],
    "ヨシナ マサノリ": ["吉名"],
    "ツナカワ ダイゴ": ["綱川", "津名川"],
    "ウエタ アユム": ["上田", "ウエタ"],
    "サトウ リヨウマ": ["佐藤 諒茉", "佐藤諒茉", "諒茉"],
    "カワセユウヤ": ["川瀬", "河瀬"],
    # 出金
    "カ）デイーウイシヨン": ["D-Vision", "ディーウィジョン", "ディヴィジョン"],
    "ヤマサキ シスエ": ["山崎", "山﨑"],
    "イシカワ マサカズ": ["石川"],
    "オキノ ミエコ": ["沖野", "荻野", "オキノ"],
    "ホンダ アヤカ": ["本田"],
    "オキツキミコ": ["興津", "沖津", "オキツ"],
    "カ）エーエフシースマイル": ["AFCスマイル", "ＡＦＣスマイル", "AFC"],
    "カ）エステートリンク": ["エステートリンク"],
    "シマカキ ホクタク": ["島垣", "嶋垣", "シマカキ"],
    "ハウスケ": ["ハウスケ", "HOUSEKE"],
    "カ）リンクエイシ": ["リンクエイジ"],
    "カ）リハーコーホレーシ": ["リハーコーポレーション", "リハーコ"],
    "村田菜津美": ["村田", "菜津美"],
    "カ）コムウェル": ["コムウェル"],
    "ト）スタテツク": ["スタテック", "Statec"],
    "カタオカ ハツミ": ["片岡"],
    "コントウ クニヨ": ["近藤"],
    "コントウ ツネオ": ["近藤"],
    "ヨシモト キヨウヘイ": ["吉本"],
    "カ）フアインレンタカー": ["ファインレンタカー", "ﾌｧｲﾝ"],
    "ニホンハウスインク": ["日本ハウス", "ニホンハウス"],
    "ストライプシヤハン": ["ストライプ"],
    "イトウ スミレ": ["伊藤"],
    "ウチサワ ナチ": ["内沢"],
    "ササモト アマネ": ["笹本"],
    "カ）ハウスコンサルタント": ["ハウスコンサルタント"],
    "カ）イースタイル": ["イースタイル"],
    "ユ）イマカワフドウサンセンター": ["今川不動産"],
    "ハク シンウ": ["白"],
    "スズキ マサアキ": ["鈴木"],
    "シユウタクサーヒスコ": ["住宅サービス"],
    "カ）アナフキレシテンシヤル": ["アナフキレジデンシャル", "アナフキ"],
    "ワタナベ エツコ": ["渡邉", "渡辺"],
    "ケイアイエホツクメイキング": ["KIエポックメイキング", "KAIエポック", "エポックメイキング"],
    "武蔵野銀行狭山西": ["武蔵野銀行"],
}

def reiwa_to_iso(d):
    m = re.match(r'令和(\d+)年(\d+)月(\d+)日', d)
    if not m: return None
    yr = int(m.group(1))
    western = 2018 + yr
    return f"{western:04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"

def date_keys(d):
    iso = reiwa_to_iso(d)
    if not iso: return []
    y, m, dd = iso[:4], iso[4:6], iso[6:]
    keys = [iso, f"{y}.{int(m)}.{int(dd)}", f"{y}.{m}.{dd}", f"{y}-{m}-{dd}", f"{y}/{m}/{dd}",
            f"{y}年{int(m)}月{int(dd)}日", f"{y}{int(m):02d}{int(dd):02d}",
            f"{y[2:]}{int(m):02d}{int(dd):02d}",  # 260328
            f"{int(m)}/{int(dd)}", f"{int(m)}.{int(dd)}",
            f"令和{int(y)-2018}年{int(m)}月{int(dd)}日"]
    return keys

def match_files(payer_name, files):
    keywords = NAME_MAP.get(payer_name, [])
    matches = []
    for f in files:
        t = f.get('title','')
        if not isinstance(t, str): continue
        for kw in keywords:
            if kw in t:
                matches.append(f)
                break
    return matches

def filter_by_kind(matches, kinds):
    if not kinds: return matches
    primary = []
    for f in matches:
        t = f.get('title','')
        for kind in kinds:
            if kind == '請求書' and ('請求書' in t or 'INV' in t.upper() or '請求' in t):
                primary.append(f); break
            elif kind == '契約書' and ('契約書' in t or '売買契約' in t or '請負契約' in t or 'AB間' in t or 'BC間' in t):
                primary.append(f); break
            elif kind == '領収書' and '領収' in t:
                primary.append(f); break
            elif kind == '明細' and ('明細' in t or '精算' in t):
                primary.append(f); break
    return primary if primary else matches

def find_for(t, files, role_key='payer'):
    payer = t.get(role_key) or t.get('payee')
    matches = match_files(payer, files)
    needs = t.get('needs','')
    kinds = []
    if '請求書' in needs: kinds.append('請求書')
    if '契約書' in needs: kinds.append('契約書')
    if '明細' in needs: kinds.append('明細')
    if '領収' in needs: kinds.append('領収書')
    filtered = filter_by_kind(matches, kinds)
    boosted = []
    dks = date_keys(t['date'])
    amt = t.get('amount')
    for f in filtered:
        score = 0
        title = f.get('title','')
        for dk in dks:
            if dk in title:
                score += 5
                break
        if amt is not None:
            abs_a = abs(amt)
            if str(abs_a) in title: score += 10
            if f"{abs_a:,}" in title: score += 10
        boosted.append((score, f))
    boosted.sort(reverse=True, key=lambda x: x[0])
    return boosted[:3]

with open('/home/user/-6780/data/income_all.json') as f: income_files = json.load(f)
with open('/home/user/-6780/data/outcome_all.json') as f: outcome_files = json.load(f)
with open('/home/user/-6780/data/transactions.json') as f: tx = json.load(f)

# Combined search pool
all_files = income_files + outcome_files

def find_anywhere(t, role_key):
    """Search all known files, mark folder origin."""
    payer = t.get(role_key) or t.get('payee')
    matches = match_files(payer, all_files)
    needs = t.get('needs','')
    kinds = []
    if '請求書' in needs: kinds.append('請求書')
    if '契約書' in needs: kinds.append('契約書')
    if '明細' in needs: kinds.append('明細')
    if '領収' in needs: kinds.append('領収書')
    filtered = filter_by_kind(matches, kinds)
    boosted = []
    dks = date_keys(t['date'])
    amt = t.get('amount')
    for f in filtered:
        score = 0
        title = f.get('title','')
        for dk in dks:
            if dk in title:
                score += 5
                break
        if amt is not None:
            abs_a = abs(amt)
            if str(abs_a) in title: score += 10
            if f"{abs_a:,}" in title: score += 10
        boosted.append((score, f))
    boosted.sort(reverse=True, key=lambda x: x[0])
    return boosted[:3]

# Get id->folder mapping
folder_of = {}
for f in income_files: folder_of[f['id']] = '1.入金資料'
for f in outcome_files: folder_of[f['id']] = '2.出金資料'

# Process
results_income = []
for t in tx['income']:
    matched = find_anywhere(t, 'payer')
    results_income.append({'tx': t, 'matches': matched})

results_outcome = []
for t in tx['outcome']:
    matched = find_anywhere(t, 'payee')
    results_outcome.append({'tx': t, 'matches': matched})

# Duplicates: same title + same fileSize
dup_keys = defaultdict(list)
for f in all_files:
    if f.get('fileSize'):
        key = (f.get('title',''), f['fileSize'])
        dup_keys[key].append(f)
duplicates = {k: v for k, v in dup_keys.items() if len(v) > 1}

with open('/home/user/-6780/data/match_results.json', 'w') as f:
    json.dump({
        'income': [{'tx': r['tx'], 'matches': [{'score': s, 'id': m['id'], 'title': m.get('title'), 'fileSize': m.get('fileSize'), 'folder': folder_of.get(m['id'])} for s, m in r['matches']]} for r in results_income],
        'outcome': [{'tx': r['tx'], 'matches': [{'score': s, 'id': m['id'], 'title': m.get('title'), 'fileSize': m.get('fileSize'), 'folder': folder_of.get(m['id'])} for s, m in r['matches']]} for r in results_outcome],
        'duplicates': [{'title': k[0], 'fileSize': k[1], 'ids': [{'id': x['id'], 'folder': folder_of.get(x['id'])} for x in v]} for k, v in duplicates.items()]
    }, f, ensure_ascii=False, indent=1)

income_matched = sum(1 for r in results_income if r['matches'])
outcome_matched = sum(1 for r in results_outcome if r['matches'])
print(f"income matched: {income_matched}/{len(results_income)}")
print(f"outcome matched: {outcome_matched}/{len(results_outcome)}")
print(f"duplicates: {len(duplicates)}")
