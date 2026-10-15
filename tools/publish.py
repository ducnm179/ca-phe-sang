#!/usr/bin/env python3
"""Lưu trữ bản tin Cà phê sáng.

Chạy SAU khi index.html của ngày đã được viết xong:
    python3 tools/publish.py

Việc làm:
  1. Đọc ngày trong <title> của index.html (định dạng D/M/YYYY).
  2. Lưu bản sao vĩnh viễn: archive/YYYY-MM-DD.html
  3. Gắn vào index.html đoạn chuyển hướng: link cũ dạng ?d=YYYYMMDD
     sẽ tự mở đúng số của ngày đó trong archive/.
  4. Dựng lại trang mục lục archive/index.html.

Dùng thêm --backfill để khôi phục các số cũ từ lịch sử git.
"""
import html
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "archive"
MARK_START = "<!-- cps:archive-start -->"
MARK_END = "<!-- cps:archive-end -->"
TITLE_RE = re.compile(r"<title>[^<]*?(\d{1,2})/(\d{1,2})/(\d{4})[^<]*</title>", re.I)


def brief_date(text):
    m = TITLE_RE.search(text)
    if not m:
        raise ValueError("Không tìm thấy ngày D/M/YYYY trong <title>")
    d, mth, y = (int(x) for x in m.groups())
    return f"{y:04d}-{mth:02d}-{d:02d}"


def strip_block(text):
    return re.sub(re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?", "", text, flags=re.S)


def insert_before_body_end(text, block):
    idx = text.lower().rfind("</body>")
    return text + block if idx == -1 else text[:idx] + block + text[idx:]


def nav_block(archive_href, today=None):
    """Đoạn thêm vào cuối trang: link mục lục + (với index) chuyển hướng ?d= cũ."""
    redirect = ""
    if today:
        redirect = (
            "<script>(function(){var m=location.search.match(/[?&]d=(\\d{8})/);if(!m)return;"
            "var d=m[1],f=d.slice(0,4)+'-'+d.slice(4,6)+'-'+d.slice(6);"
            f"if(f==='{today}')return;var u='archive/'+f+'.html';"
            "fetch(u,{method:'HEAD'}).then(function(r){if(r.ok)location.replace(u);}).catch(function(){});})();</script>\n"
        )
    return (
        f"{MARK_START}\n"
        '<div style="max-width:720px;margin:24px auto 32px;padding:0 16px;text-align:center;'
        'font:14px/1.5 system-ui,sans-serif;">'
        f'<a href="{archive_href}" style="color:inherit;opacity:.75;">Xem các số trước →</a></div>\n'
        f"{redirect}{MARK_END}\n"
    )


def save_archive_copy(text):
    clean = strip_block(text)
    date = brief_date(clean)
    ARCHIVE.mkdir(exist_ok=True)
    (ARCHIVE / f"{date}.html").write_text(insert_before_body_end(clean, nav_block("./")), encoding="utf-8")
    return date


def meta(text, name):
    m = re.search(rf'<meta[^>]+property="{name}"[^>]+content="([^"]*)"', text) or \
        re.search(rf'<meta[^>]+name="{name}"[^>]+content="([^"]*)"', text)
    return html.unescape(m.group(1)) if m else ""


def build_index():
    items = []
    for f in sorted(ARCHIVE.glob("????-??-??.html"), reverse=True):
        t = f.read_text(encoding="utf-8")
        y, m, d = f.stem.split("-")
        items.append((f.name, f"{int(d)}/{int(m)}/{y}", meta(t, "og:description") or meta(t, "description")))
    rows = "\n".join(
        f'<li><a href="{n}"><span class="d">{html.escape(dt)}</span>'
        f'<span class="s">{html.escape(desc)}</span></a></li>' for n, dt, desc in items)
    page = f"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Các số trước – Cà phê sáng</title>
<meta property="og:title" content="Điểm tin thị trường – Cà phê sáng: các số trước">
<style>
:root{{--bg:#f6f4f1;--fg:#1d2128;--mu:#6a6f78;--ln:#e2ddd6;--ac:#b8460e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#15171b;--fg:#e8e6e3;--mu:#9a9fa8;--ln:#2a2d33;--ac:#f08a4b}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,"Segoe UI",sans-serif}}
main{{max-width:720px;margin:0 auto;padding:28px 16px 48px}}
h1{{font-size:24px;margin:0 0 4px}} p.sub{{color:var(--mu);margin:0 0 20px}}
a.home{{color:var(--ac)}} ul{{list-style:none;margin:0;padding:0}}
li a{{display:grid;gap:2px;padding:12px 0;border-bottom:1px solid var(--ln);color:inherit;text-decoration:none}}
.d{{font-weight:600;color:var(--ac);font-variant-numeric:tabular-nums}} .s{{color:var(--mu);font-size:14.5px}}
</style></head><body><main>
<h1>Điểm tin thị trường – Cà phê sáng</h1>
<p class="sub">Các số đã phát hành ({len(items)} số) · <a class="home" href="../">Số mới nhất</a></p>
<ul>
{rows}
</ul></main></body></html>
"""
    (ARCHIVE / "index.html").write_text(page, encoding="utf-8")


def publish():
    idx = ROOT / "index.html"
    text = strip_block(idx.read_text(encoding="utf-8"))
    date = save_archive_copy(text)
    idx.write_text(insert_before_body_end(text, nav_block("archive/", today=date)), encoding="utf-8")
    build_index()
    print(f"OK: archive/{date}.html")


def backfill():
    revs = subprocess.run(["git", "-C", str(ROOT), "log", "--reverse", "--format=%H", "--", "index.html"],
                          capture_output=True, text=True, check=True).stdout.split()
    for rev in revs:  # cũ → mới: số sau cùng của mỗi ngày được giữ lại
        text = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:index.html"],
                              capture_output=True, text=True, check=True).stdout
        try:
            print(f"{rev[:7]} → archive/{save_archive_copy(text)}.html")
        except ValueError as e:
            print(f"{rev[:7]} bỏ qua: {e}")


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        backfill()
    publish()
