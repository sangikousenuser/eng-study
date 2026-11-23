#!/usr/bin/env python3
"""GoodNotes 用の手書きプリント PDF を生成するスクリプト。

index.html 内の UNITS_DATA / FLASHCARD_DATA を Playwright で読み出し、
ユニットごとに 2 種類の PDF を prints/ に出力する。
  - unitX-translation.pdf : 英文（スラッシュ付き）→ 赤の模範和訳 → 手書き用罫線
  - unitX-vocab.pdf       : 単語カード（英語 → 赤の意味 → 罫線2本）の2列グリッド
  - all.pdf               : 上記6ファイルを1冊にまとめたもの（セクションごとのしおり付き）

使い方:
    pip install playwright pypdf && playwright install chromium
    python3 tools/make_goodnotes_pdfs.py
"""

import html
import json
import math
import re
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"
OUT_DIR = ROOT / "prints"

RED = "#E8382F"
RULE = "#C9C9C9"
LINE_MM = 9
LINE_MM_TR = 10

FONT_STACK = '"Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif'

BASE_CSS = f"""
  * {{ box-sizing: border-box; }}
  body {{
    font-family: {FONT_STACK};
    color: #222;
    margin: 0;
    font-size: 11pt;
    line-height: 1.6;
  }}
  .doc-title {{ margin-bottom: 6mm; padding-bottom: 3mm; border-bottom: 0.5pt solid {RULE}; }}
  .doc-title .badge {{ font-size: 9pt; font-weight: 700; color: #B0662A; }}
  .doc-title h1 {{ font-size: 12pt; margin: 1mm 0 0.5mm; font-weight: 700; }}
  .doc-title .sub {{ font-size: 9pt; color: #888; }}
  .red {{ color: {RED}; font-weight: 500; }}
  .slash {{ color: #BBBBBB; font-weight: 400; padding: 0 0.5mm; }}
"""

SENT_CSS = """
  .sent {
    margin-bottom: 10mm;
    padding-bottom: 2mm;
    border-bottom: 0.7pt solid #CFCFCF;
  }
  .keep { break-inside: avoid; }
  .sent-head { padding: 2mm 0 1.5mm; }
  .sent .label { font-size: 10pt; font-weight: 700; color: #B0662A; margin-right: 2mm; }
  .sent .en { font-size: 11.5pt; line-height: 1.7; margin: 1mm 0 0; }
  .row {
    break-inside: avoid;
    display: grid;
    grid-template-columns: 38% 62%;
    border-top: 0.4pt solid #E3E3E3;
    padding: 3mm 0;
  }
  .row .cen { font-size: 11pt; line-height: 1.6; padding-right: 4mm; }
  .row .cja { font-size: 10.5pt; line-height: 1.6; margin-bottom: 1.5mm; }
  .full {
    break-inside: avoid;
    border-top: 0.4pt solid #E3E3E3;
    padding: 3mm 0 1mm;
  }
  .full .cap { font-size: 8.5pt; color: #999; margin-bottom: 1mm; }
  .full .ja { font-size: 11pt; line-height: 1.6; margin-bottom: 2mm; }
"""

VOCAB_CSS = """
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }
  .card {
    break-inside: avoid;
    border: 0.6pt solid #D5D5D5;
    border-radius: 3mm;
    padding: 3mm 3.5mm 2.5mm;
  }
  .card .num { font-size: 8pt; color: #AAA; margin-right: 1.5mm; }
  .card .en { font-size: 12pt; font-weight: 700; }
  .card .ja { font-size: 10.5pt; margin: 1mm 0 1.5mm; }
"""


def esc(text):
    return html.escape(str(text), quote=False)


def strip_cloze(jp_cloze):
    return re.sub(r"\{\{(.*?)\}\}", r"\1", jp_cloze)


def ruled_lines(count, spacing=LINE_MM):
    line = f'<div style="height: {spacing}mm; border-bottom: 0.6pt solid {RULE};"></div>'
    return f'<div class="lines">{line * count}</div>'


def doc_html(title_badge, title_en, title_ja, body_html, extra_css):
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<style>{BASE_CSS}{extra_css}</style>
</head>
<body>
  <div class="doc-title">
    <span class="badge">{esc(title_badge)}</span>
    <h1>{esc(title_en)}</h1>
    <div class="sub">{esc(title_ja)}</div>
  </div>
  {body_html}
</body>
</html>"""


def build_translation_html(unit):
    blocks = []
    for sent in unit["sentences"]:
        en = ' <span class="slash">/</span> '.join(esc(c["en"]) for c in sent["chunks"])
        ja = strip_cloze(sent["jpCloze"])
        n_lines = max(3, math.ceil(len(ja) / 30) + 1)

        rows = []
        for c in sent["chunks"]:
            rows.append(f"""
          <div class="row">
            <div class="cen">{esc(c["en"])}</div>
            <div>
              <div class="cja red">{esc(c["ja"])}</div>
              {ruled_lines(2, LINE_MM_TR)}
            </div>
          </div>""")

        header = f"""
          <div class="sent-head">
            <div><span class="label">{esc(sent["label"])}</span></div>
            <div class="en">{en}</div>
          </div>"""
        full = f"""
          <div class="full">
            <div class="cap">全文訳</div>
            <div class="ja red">{esc(ja)}</div>
            {ruled_lines(n_lines, LINE_MM_TR)}
          </div>"""

        # 見出し＋最初の行を1ブロックにして、ページ末に見出しだけ残らないようにする
        blocks.append(f"""
        <div class="sent">
          <div class="keep">
            {header}
            {rows[0]}
          </div>
          {"".join(rows[1:])}
          {full}
        </div>""")
    return doc_html(unit["badge"], unit["titleEn"], unit["titleJa"],
                    "\n".join(blocks), SENT_CSS)


def build_vocab_html(unit, cards):
    items = []
    for i, (en, ja) in enumerate(cards, 1):
        items.append(f"""
        <div class="card">
          <div><span class="num">{i}</span><span class="en">{esc(en)}</span></div>
          <div class="ja red">{esc(ja)}</div>
          {ruled_lines(2)}
        </div>""")
    return doc_html(unit["badge"], unit["titleEn"], unit["titleJa"],
                    f'<div class="grid">{"".join(items)}</div>', VOCAB_CSS)


def footer_template(badge, kind):
    return (f'<div style="font-size:8px; width:100%; text-align:center; color:#999; '
            f'font-family:{FONT_STACK};">'
            f'{esc(badge)} {kind}　<span class="pageNumber"></span> / '
            f'<span class="totalPages"></span></div>')


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(INDEX_HTML.as_uri())
        page.wait_for_timeout(500)
        units = page.evaluate("UNITS_DATA")
        cards = page.evaluate("FLASHCARD_DATA")

        OUT_DIR.mkdir(exist_ok=True)
        merged = PdfWriter()
        for key in ("unit7", "unit9", "unit12"):
            unit = units[key]
            jobs = [
                (build_translation_html(unit), f"{key}-translation.pdf", "和訳プリント", "16mm"),
                (build_vocab_html(unit, cards[key]), f"{key}-vocab.pdf", "単語カード", "14mm"),
            ]
            for html_src, filename, kind, side in jobs:
                page.set_content(html_src, wait_until="load")
                page.pdf(
                    path=str(OUT_DIR / filename),
                    format="A4",
                    print_background=True,
                    margin={"top": "14mm", "bottom": "20mm", "left": side, "right": side},
                    display_header_footer=True,
                    header_template="<div></div>",
                    footer_template=footer_template(unit["badge"], kind),
                )
                print(f"wrote {filename}")
                merged.append(str(OUT_DIR / filename), outline_item=f"{unit['badge']} {kind}")
        browser.close()

    with open(OUT_DIR / "all.pdf", "wb") as f:
        merged.write(f)
    print("wrote all.pdf")


if __name__ == "__main__":
    main()
