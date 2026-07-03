# -*- coding: utf-8 -*-
import argparse
import base64
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen, Request


def read_input(source):
    if source.startswith("http://") or source.startswith("https://"):
        req = Request(source, headers={"User-Agent": "Mozilla/5.0 atcoder-html-to-pdf/0.1"})
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    return Path(source).read_text(encoding="utf-8")


def image_data_uri(path):
    p = Path(path)
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def build_watermark_css(args, data_uri):
    if args.watermark_mode == "center":
        placement = f"""
        left: 50%;
        top: 50%;
        width: {args.watermark_width};
        transform: translate(-50%, -50%);
        """
    else:
        placement = f"""
        right: {args.watermark_margin};
        top: {args.watermark_margin};
        width: {args.watermark_width};
        transform: none;
        """

    return f"""
<style id="pdf-watermark-style">
  @media print {{
    * {{
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
    }}
    .pdf-watermark {{
      position: fixed;
      {placement}
      height: auto;
      opacity: {args.watermark_opacity};
      z-index: 9999;
      pointer-events: none;
      user-select: none;
    }}
  }}
  @media screen {{
    .pdf-watermark {{
      position: fixed;
      {placement}
      height: auto;
      opacity: {args.watermark_opacity};
      z-index: 9999;
      pointer-events: none;
      user-select: none;
    }}
  }}
</style>
<img class="pdf-watermark" src="{data_uri}" alt="">
"""


def inject_watermark(html_text, watermark_html):
    marker = "</body>"
    idx = html_text.lower().rfind(marker)
    if idx >= 0:
        return html_text[:idx] + watermark_html + html_text[idx:]
    return html_text + watermark_html


def output_path_for(source, out):
    if out:
        return Path(out)
    if source.startswith("http://") or source.startswith("https://"):
        name = Path(urlparse(source).path).name or "output"
        return Path(name).with_suffix(".pdf")
    return Path(source).with_suffix(".pdf")


def main():
    parser = argparse.ArgumentParser(description="Render translated AtCoder HTML to PDF with image watermark.")
    parser.add_argument("source", help="Input HTML file path or URL")
    parser.add_argument("-o", "--out", help="Output PDF path")
    parser.add_argument("--watermark", default="watermark.png", help="Watermark image path")
    parser.add_argument("--watermark-mode", choices=["corner", "center"], default="center")
    parser.add_argument("--watermark-width", default="180mm", help="CSS width, e.g. 180mm, 150px")
    parser.add_argument("--watermark-margin", default="14mm", help="CSS margin for corner mode")
    parser.add_argument("--watermark-opacity", default="0.075")
    parser.add_argument("--format", default="A4")
    parser.add_argument("--margin-top", default="14mm")
    parser.add_argument("--margin-right", default="13mm")
    parser.add_argument("--margin-bottom", default="15mm")
    parser.add_argument("--margin-left", default="13mm")
    parser.add_argument("--preview-html", help="Optional path to save the watermarked intermediate HTML")
    args = parser.parse_args()

    source_html = read_input(args.source)
    watermark_uri = image_data_uri(args.watermark)
    watermarked = inject_watermark(source_html, build_watermark_css(args, watermark_uri))

    if args.preview_html:
        Path(args.preview_html).write_text(watermarked, encoding="utf-8")

    out = output_path_for(args.source, args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1600})
        page.set_content(watermarked, wait_until="networkidle")
        page.emulate_media(media="print")
        page.pdf(
            path=str(out),
            format=args.format,
            print_background=True,
            prefer_css_page_size=False,
            margin={
                "top": args.margin_top,
                "right": args.margin_right,
                "bottom": args.margin_bottom,
                "left": args.margin_left,
            },
        )
        browser.close()

    print(str(out.resolve()))


if __name__ == "__main__":
    main()
