#!/usr/bin/env python3
"""Đối chiếu tỷ giá trung tâm USD/VND: SBV công bố vs TCB hiển thị.

Chạy trong GitHub Actions (xem .github/workflows/tygia-doichieu.yml).
Đầu ra:
  - in báo cáo ra stdout
  - ghi file theo OUT_DIR (mặc định tygia/): bao-cao.txt, ketqua.json
Biến môi trường:
  OUT_DIR       thư mục ghi kết quả (mặc định "tygia")
Kết quả chỉ một trong ba: KHỚP / LỆCH / Không đối chiếu được.
"""
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone

VN = timezone(timedelta(hours=7))
SBV_URL = "https://sbv.gov.vn/vi/t%E1%BB%B7-gi%C3%A1"
TCB_PAGE = "https://techcombank.com/cong-cu-tien-ich/ty-gia"
TCB_JSON = ("https://techcombank.com/content/techcombank/web/vn/vi/"
            "cong-cu-tien-ich/ty-gia/_jcr_content.exchange-rates.integration.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

MATCH_TEXT = "Tỷ giá trung tâm SBV thông báo đang được TCB áp dụng"
MISMATCH_TEXT = "Kiểm tra lại tỷ giá trung tâm của TCB chưa đúng khớp với tỷ giá trung tâm SBV"


def now_vn():
    return datetime.now(VN)


def vnd(n):
    return f"{n:,}".replace(",", ".")


def http_get(url, accept="text/html,application/xhtml+xml,*/*"):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": accept,
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.status, r.read().decode("utf-8", "replace")


def html_to_text(html):
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&#x2F;", "/"), ("&#47;", "/")):
        html = html.replace(a, b)
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", html))


def browser_text(url, wait_ms=6000):
    """Đọc innerText bằng Chromium (Playwright) — dùng khi đọc HTML thô thất bại."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(user_agent=UA, locale="vi-VN")
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(wait_ms)
        txt = pg.inner_text("body")
        b.close()
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", txt))


# ---------------- SBV ----------------
def parse_sbv(text):
    m = re.search(r"1\s*Đô la Mỹ\s*=\s*([\d.,]+)\s*VND", text)
    if not m:
        return None
    rate = int(re.sub(r"[.,]", "", m.group(1)))
    d = re.search(r"áp dụng cho ngày\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    so = re.search(r"Số văn bản\s*([0-9A-Za-zĐđ/\-.]+)", text)
    return {"central": rate,
            "ngay_ap_dung": d.group(1) if d else None,
            "so_van_ban": so.group(1) if so else None}


def get_sbv(diag):
    errors = []
    try:
        st, html = http_get(SBV_URL)
        diag["sbv_http"] = st
        r = parse_sbv(html_to_text(html))
        if r:
            r["cach_doc"] = "http"
            return r
        errors.append(f"http {st}: không thấy dòng '1 Đô la Mỹ = ...' (len={len(html)})")
    except Exception as e:  # noqa: BLE001
        errors.append(f"http: {type(e).__name__}: {e}")
    try:
        txt = browser_text(SBV_URL)
        r = parse_sbv(txt)
        if r:
            r["cach_doc"] = "browser"
            return r
        errors.append(f"browser: không thấy tỷ giá; đầu trang: {txt[:200]!r}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"browser: {type(e).__name__}: {e}")
    diag["sbv_errors"] = errors
    return None


# ---------------- TCB ----------------
def _find_central(obj):
    if isinstance(obj, dict):
        if "central" in obj and "ceiling" in obj and "floor" in obj:
            return obj
        for v in obj.values():
            f = _find_central(v)
            if f:
                return f
    elif isinstance(obj, list):
        for v in obj:
            f = _find_central(v)
            if f:
                return f
    return None


def _num(x):
    return int(re.sub(r"[^\d]", "", str(x)))


def get_tcb(diag):
    errors = []
    try:
        st, body = http_get(TCB_JSON, accept="application/json")
        diag["tcb_http"] = st
        j = json.loads(body)
        c = _find_central(j)
        if c:
            input_dates = []
            try:
                for x in j["exchangeRate"]["data"]:
                    if "USD" in str(x.get("label", "")) and x.get("inputDate"):
                        input_dates.append(x["inputDate"])
            except Exception:  # noqa: BLE001
                pass
            times = []
            try:
                times = j["exchangeRate"].get("updatedTimes") or []
            except Exception:  # noqa: BLE001
                pass
            # inputDate trên trang TCB là giờ Việt Nam dù có hậu tố "Z"
            upd = max(input_dates) if input_dates else None
            return {"central": _num(c["central"]), "ceiling": _num(c["ceiling"]),
                    "floor": _num(c["floor"]),
                    "ngay_cap_nhat": upd[:10] if upd else None,
                    "gio_cap_nhat": (times[0] if times else (upd[11:19] if upd else None)),
                    "cach_doc": "json"}
        errors.append("json: không thấy khối central/ceiling/floor")
    except Exception as e:  # noqa: BLE001
        errors.append(f"json: {type(e).__name__}: {e}")
    try:
        txt = browser_text(TCB_PAGE, wait_ms=8000)
        g = lambda lbl: re.search(lbl + r"\s*:?\s*([\d.,]+)", txt)  # noqa: E731
        c, s, t = g("TỶ GIÁ TRUNG TÂM NHNN CÔNG BỐ"), g("TỶ GIÁ SÀN USD VND"), g("TỶ GIÁ TRẦN USD VND")
        if c:
            return {"central": _num(c.group(1)),
                    "floor": _num(s.group(1)) if s else None,
                    "ceiling": _num(t.group(1)) if t else None,
                    "ngay_cap_nhat": None, "gio_cap_nhat": None, "cach_doc": "browser"}
        errors.append(f"browser: không thấy nhãn tỷ giá trung tâm; đầu trang: {txt[:200]!r}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"browser: {type(e).__name__}: {e}")
    diag["tcb_errors"] = errors
    return None


# ---------------- Đối chiếu ----------------
def compare(sbv, tcb):
    """Chỉ một kết luận: khớp / lệch / không đối chiếu được."""
    if sbv is None or tcb is None:
        src = " và ".join(n for n, v in (("website SBV", sbv), ("website TCB", tcb)) if v is None)
        return "⚠️", f"Không đối chiếu được: {src} không truy cập được"
    if sbv["central"] == tcb["central"]:
        return "✅", f"KHỚP – {MATCH_TEXT}"
    return "❌", f"LỆCH – {MISMATCH_TEXT}"


def build_report(ts, sbv, tcb, icon, verdict):
    L = [f"📊 Đối chiếu tỷ giá trung tâm – {ts.strftime('%d/%m/%Y %H:%M')}",
         f"• SBV: {vnd(sbv['central']) + ' VND' if sbv else 'không truy cập được'}",
         f"• TCB: {vnd(tcb['central']) + ' VND' if tcb else 'không truy cập được'}"]
    if sbv and tcb and sbv["central"] != tcb["central"]:
        d = tcb["central"] - sbv["central"]
        L.append(f"• Chênh lệch TCB − SBV: {'+' if d > 0 else ''}{vnd(d)} VND")
    L.append(f"{icon} {verdict}")
    return "\n".join(L) + "\n"


def main():
    out_dir = os.environ.get("OUT_DIR", "tygia")
    diag = {}
    sbv, tcb = get_sbv(diag), get_tcb(diag)  # đọc đúng MỘT lần, không thử lại
    icon, verdict = compare(sbv, tcb)
    ts = now_vn()
    report = build_report(ts, sbv, tcb, icon, verdict)
    print(report)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "bao-cao.txt"), "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(out_dir, "ketqua.json"), "w", encoding="utf-8") as f:
        json.dump({"thoi_diem": ts.isoformat(), "ket_luan": icon,
                   "sbv": sbv, "tcb": tcb, "chan_doan": diag},
                  f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
