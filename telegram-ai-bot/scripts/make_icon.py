"""さくら発信 ホーム画面アイコンを生成する（ワンショット・開発用）。

cairosvg で炎マークの SVG を 1024px にレンダリングし、PIL で各サイズへ縮小して
tac/assets/ に PNG を書き出す。生成物はリポジトリに同梱し、本番コンテナでは
Flask が静的ファイルとして配信する（コンテナに PIL/cairosvg は不要）。

再生成したいときだけ手元で:
    cd telegram-ai-bot && python3 scripts/make_icon.py
"""

from __future__ import annotations

import io
import os

import cairosvg
from PIL import Image

# 背景=チャコール黒(#0c0a09)、炎=オレンジ→赤のグラデ(#fb923c→#ea580c→#c2410c)。
# 1024 の正方形いっぱいに背景を敷き、中央に炎。iOS が角丸マスクをかける。
_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1c1917"/>
      <stop offset="1" stop-color="#0c0a09"/>
    </linearGradient>
    <linearGradient id="flame" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0"    stop-color="#fb923c"/>
      <stop offset="0.45" stop-color="#ea580c"/>
      <stop offset="1"    stop-color="#c2410c"/>
    </linearGradient>
    <linearGradient id="inner" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0"   stop-color="#fde68a"/>
      <stop offset="0.6" stop-color="#fbbf24"/>
      <stop offset="1"   stop-color="#f97316"/>
    </linearGradient>
  </defs>

  <rect width="1024" height="1024" fill="url(#bg)"/>

  <!-- 外炎 -->
  <path fill="url(#flame)" d="
    M512 176
    C 560 300, 648 352, 684 452
    C 726 568, 686 700, 582 766
    C 636 712, 648 636, 612 580
    C 596 652, 548 690, 512 690
    C 560 626, 540 556, 496 520
    C 480 584, 452 616, 420 648
    C 356 596, 332 496, 372 412
    C 404 344, 464 320, 468 236
    C 486 288, 500 300, 512 176 Z"/>

  <!-- 内炎（明るいコア） -->
  <path fill="url(#inner)" d="
    M512 388
    C 548 448, 588 492, 584 560
    C 580 636, 528 684, 476 668
    C 512 636, 516 592, 492 560
    C 480 604, 452 624, 436 636
    C 404 596, 408 528, 448 484
    C 476 452, 500 440, 512 388 Z"/>
</svg>"""

_SIZES = {
    "icon-1024.png": 1024,
    "icon-512.png": 512,
    "icon-192.png": 192,
    "icon-180.png": 180,
}


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    assets = os.path.join(os.path.dirname(here), "tac", "assets")
    os.makedirs(assets, exist_ok=True)

    png1024 = cairosvg.svg2png(bytestring=_SVG.encode("utf-8"),
                               output_width=1024, output_height=1024)
    master = Image.open(io.BytesIO(png1024)).convert("RGBA")

    for name, size in _SIZES.items():
        img = master.resize((size, size), Image.LANCZOS)
        out = os.path.join(assets, name)
        img.save(out, "PNG", optimize=True)
        print("wrote", out, f"({size}x{size})")


if __name__ == "__main__":
    main()
