"""運用ダッシュボード（HTML Console）。

架電記録（Call Log）・サマリー・DNC を1枚の HTML にまとめて描画する。curl を
使わずブラウザで運用状況を目で確認できるようにするための、依存なしの純関数。
値はすべて html.escape でエスケープしてから埋め込む（インジェクション防止）。

電話番号を含むため、表示するルート側は必ずトークン認証を掛けること。
"""

from __future__ import annotations

import html


def _e(v: object) -> str:
    return html.escape(str(v))


def render(summary: dict, calls: list[dict], dnc_numbers: list[str],
           screenings: list[dict] | None = None) -> str:
    """集計・架電記録・DNC（・電話5問の判定）を HTML ページ文字列にして返す。

    screenings が None なら判定の欄は出さない（機能 OFF 時は従来どおり）。
    """
    total = summary.get("total", 0)
    unique = summary.get("unique_numbers", 0)
    by_status = summary.get("by_status", {}) or {}

    status_cells = "".join(
        f'<span class="pill">{_e(k)}: <b>{_e(v)}</b></span>'
        for k, v in sorted(by_status.items())
    ) or '<span class="muted">記録なし</span>'

    call_rows = "".join(
        "<tr>"
        f"<td>{_e(c.get('ts', ''))}</td>"
        f"<td>{_e(c.get('direction', ''))}</td>"
        f"<td>{_e(c.get('to', ''))}</td>"
        f"<td>{_e(c.get('status', ''))}</td>"
        f"<td>{_e(c.get('reason', ''))}</td>"
        "</tr>"
        for c in calls
    ) or '<tr><td colspan="5" class="muted">架電記録はまだありません</td></tr>'

    screening_section = ""
    if screenings is not None:
        screening_rows = "".join(
            "<tr>"
            f"<td>{_e(s.get('ts', ''))}</td>"
            f"<td>{_e(s.get('caller', ''))}</td>"
            f"<td><b>{_e(s.get('rank', ''))}</b></td>"
            f"<td>{_e(' / '.join(map(str, s.get('reasons') or [])))}</td>"
            f"<td>{_e(', '.join(map(str, s.get('missing') or [])))}</td>"
            "</tr>"
            for s in screenings
        ) or '<tr><td colspan="5" class="muted">判定はまだありません</td></tr>'
        screening_section = (
            "<h2>電話5問の仮ランク（新しい順・相手には伝えない）</h2>\n<table>\n"
            "  <thead><tr><th>時刻</th><th>相手</th><th>仮ランク</th><th>理由</th><th>未確認</th></tr></thead>\n"
            f"  <tbody>{screening_rows}</tbody>\n</table>\n"
        )

    dnc_items = "".join(f"<li>{_e(n)}</li>" for n in dnc_numbers) or (
        '<li class="muted">登録なし</li>'
    )

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TAC 運用ダッシュボード</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, -apple-system, "Hiragino Sans", sans-serif;
         margin: 0; padding: 16px; line-height: 1.6; }}
  h1 {{ font-size: 1.3rem; margin: 0 0 12px; }}
  h2 {{ font-size: 1.05rem; margin: 20px 0 8px; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 12px; }}
  .card {{ border: 1px solid rgba(128,128,128,.35); border-radius: 10px;
          padding: 12px 16px; min-width: 120px; }}
  .card .n {{ font-size: 1.8rem; font-weight: 700; }}
  .pill {{ display: inline-block; border: 1px solid rgba(128,128,128,.35);
          border-radius: 999px; padding: 2px 10px; margin: 2px 4px 2px 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid rgba(128,128,128,.25);
           white-space: nowrap; }}
  .muted {{ opacity: .6; }}
  ul {{ padding-left: 20px; }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
</style>
</head>
<body>
<div class="wrap">
<h1>TAC 運用ダッシュボード</h1>

<h2>サマリー</h2>
<div class="cards">
  <div class="card"><div>総件数</div><div class="n">{_e(total)}</div></div>
  <div class="card"><div>ユニーク番号</div><div class="n">{_e(unique)}</div></div>
</div>
<div style="margin-top:10px">{status_cells}</div>

<h2>架電記録（新しい順）</h2>
<table>
  <thead><tr><th>時刻</th><th>方向</th><th>相手</th><th>結果</th><th>理由</th></tr></thead>
  <tbody>{call_rows}</tbody>
</table>

{screening_section}
<h2>DNC（発信禁止リスト）</h2>
<ul>{dnc_items}</ul>
</div>
</body>
</html>"""
