"""添付書類の束ね（PDF 結合）.

各案件の添付書類（登記簿・公図・検査済証・都市計画図 等）の PDF を、指定順に
1 つの PDF へ結合する。BC 書類（本番ワークブックを PDF 化したもの）と添付を
n8n 側で束ねて納品する用途を想定。純 Python（pypdf）で動く。
"""

from __future__ import annotations

import io


def merge_pdfs(pdfs: list[bytes]) -> tuple[bytes, int]:
    """PDF バイト列のリストを順に結合して (結合PDF, 総ページ数) を返す。"""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for data in pdfs:
        reader = PdfReader(io.BytesIO(data))
        for page in reader.pages:
            writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), len(writer.pages)
