#!/usr/bin/env python3
"""Build the Kansai 2027 trip website from the Obsidian itinerary note.

Single source of truth: the Markdown note in Obsidian.
Run:  python3 build_site.py          (one-off rebuild)
      python3 build_site.py --watch  (rebuild whenever the note changes)

Output: index.html + data.json next to this script.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time
from html import escape

ROOT = pathlib.Path(__file__).resolve().parent
NOTE = pathlib.Path(
    "/Users/richard/Library/CloudStorage/SynologyDrive-HermesAgent/Obsidian/Travel/2027關西_5天4夜_完整版.md"
)
SITE_META = ROOT / "site_meta.json"

LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


# ---------------------------------------------------------------- parsing helpers
def strip_md_links(text: str) -> str:
    return LINK_RE.sub(lambda m: m.group(1), text)


def extract_links(text: str) -> list[dict]:
    return [{"text": m.group(1), "url": m.group(2)} for m in LINK_RE.finditer(text)]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    raw = text[3:end]
    body = text[end + 4 :]
    meta: dict = {}
    for line in raw.splitlines():
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta, body


def parse_table(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows[1:] if rows else []


def section(body: str, start_pat: str, stop_pat: str = r"\n## ") -> str:
    m = re.search(start_pat, body)
    if not m:
        return ""
    rest = body[m.end() :]
    s = re.search(stop_pat, rest)
    return rest[: s.start()] if s else rest


def parse_bullet(text: str) -> dict:
    clean = re.sub(r"^[-*]\s+", "", text.strip())
    links = extract_links(clean)
    plain = strip_md_links(clean)
    plain = re.sub(r"\*\*(.+?)\*\*", r"\1", plain)
    plain = re.sub(r"\[[ xX]\]\s*", "", plain)
    return {"text": plain.strip(), "links": links, "raw": clean}


# ---------------------------------------------------------------- day parsing
DAY_RE = re.compile(r"^### Day (\d)｜(\d{4}-\d{2}-\d{2})（(.)）\s*(.+)$", re.M)


def split_outside(text: str, sep: str) -> list[str]:
    """Split on sep only when not inside （）()【】「」."""
    out, buf, depth = [], [], 0
    for ch in text:
        if ch in "（(【「":
            depth += 1
        elif ch in "）)】」":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def parse_meals(block: str, day_no: int) -> list[dict]:
    meals = []
    for line in block.splitlines():
        if not line.startswith("- **Day"):
            continue
        m = re.match(r"- \*\*Day \d+ (\S+?)｜(.+?)\*\*[:：](.*)$", line)
        if not m:
            continue
        slot, area, rest = m.group(1), m.group(2), m.group(3)
        items, extra = [], []
        for part in split_outside(rest, "；"):
            part = part.strip()
            if not part:
                continue
            # a trailing sentence after 。 is a meal-level note, not part of the shop
            head, dot, tail = part.partition("。")
            if dot and tail.strip():
                extra.append(tail.strip())
                part = head
            nm = re.search(r"\*\*(.+?)\*\*", part)
            name = nm.group(1) if nm else strip_md_links(part)[:18]
            detail = strip_md_links(part)
            detail = re.sub(r"\*\*.+?\*\*", "", detail)
            for _ in range(3):
                detail = re.sub(r"[。；;、\s]*(官方|介紹|預約|Map|地圖|官網)[。；;、\s]*$", "", detail)
            detail = detail.strip(" 　。；;、")
            detail = re.sub(r"^[（(]\s*", "", detail)
            if detail.count("）") > detail.count("（"):
                detail = detail.rstrip("）) ")
            detail = detail.strip(" （）()")
            missing = (detail.count("（") + detail.count("(")) - (detail.count("）") + detail.count(")"))
            if missing > 0:
                detail = detail + "）" * missing
            items.append({"name": name, "detail": detail, "links": extract_links(part)})
        meals.append({"slot": slot, "area": area, "items": items, "note": " ".join(extra)})
    return meals


def clean_cell(text: str) -> str:
    text = strip_md_links(text)
    text = text.replace("**", "")
    return text.strip()


def row_of(r: list[str]) -> dict:
    cells = [clean_cell(c) for c in r] + [""] * (4 - len(r))
    time = cells[0]
    optional = "（可選）" in time or "(可選)" in time
    time = time.replace("（可選）", "").replace("(可選)", "").strip()
    return {"time": time, "transport": cells[1], "activity": cells[2], "note": cells[3], "optional": optional}


def parse_days(body: str) -> list[dict]:
    matches = list(DAY_RE.finditer(body))
    days = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else body.find("\n## ", m.end())
        block = body[m.end() : end if end != -1 else len(body)]
        # split off meal lines so they do not pollute notes
        meal_lines = [ln for ln in block.splitlines() if ln.startswith("- **Day")]
        rest_block = "\n".join(ln for ln in block.splitlines() if not ln.startswith("- **Day"))

        plans: list[dict] = []
        decision = []
        plan_headers = list(re.finditer(r"^\*\*(Plan [AB])：(.+?)\*\*$", rest_block, re.M))
        if plan_headers:
            preamble = rest_block[: plan_headers[0].start()]
            decision = [parse_bullet(ln) for ln in preamble.splitlines() if ln.strip().startswith("- ")]
            for j, ph in enumerate(plan_headers):
                p_end = plan_headers[j + 1].start() if j + 1 < len(plan_headers) else len(rest_block)
                chunk = rest_block[ph.end() : p_end]
                tbl_lines = [ln for ln in chunk.splitlines() if ln.strip().startswith("|")]
                rows = parse_table(tbl_lines)
                plans.append({
                    "key": ph.group(1).replace("Plan ", ""),
                    "name": ph.group(2),
                    "rows": [row_of(r) for r in rows],
                    "notes": [],
                })
            # attach following bullets to the plan they belong to
            for j, ph in enumerate(plan_headers):
                p_end = plan_headers[j + 1].start() if j + 1 < len(plan_headers) else len(rest_block)
                chunk = rest_block[ph.end() : p_end]
                for ln in chunk.splitlines():
                    if ln.strip().startswith("- "):
                        plans[j]["notes"].append(parse_bullet(ln))
        else:
            tbl_lines = [ln for ln in rest_block.splitlines() if ln.strip().startswith("|")]
            rows = parse_table(tbl_lines)
            notes = [parse_bullet(ln) for ln in rest_block.splitlines() if ln.strip().startswith("- ")]
            plans.append({"key": None, "name": "", "rows": [row_of(r) for r in rows], "notes": notes})

        days.append({
            "no": int(m.group(1)),
            "date": m.group(2),
            "weekday": m.group(3),
            "title": m.group(4).strip(),
            "plans": plans,
            "decision": decision,
            "meals": parse_meals("\n".join(meal_lines), int(m.group(1))),
        })
    return days


# ---------------------------------------------------------------- other sections
def parse_flights(body: str) -> dict:
    sec = section(body, r"## 1\. 航班與機場交通")
    rows = parse_table([ln for ln in sec.splitlines() if ln.strip().startswith("|")])
    flights = [{"date": r[0], "flight": r[1], "time": r[2], "note": r[3] if len(r) > 3 else ""} for r in rows]
    warnings = []
    for ln in sec.splitlines():
        if "⚠" in ln or "待電子機票確認" in ln:
            warnings.append(parse_bullet(ln))
    return {"rows": flights, "warnings": warnings}


def parse_hotel(body: str) -> dict:
    sec = section(body, r"## 2\. 住宿")
    name = re.search(r"\*\*(.+?)\*\*", sec)
    items = [parse_bullet(ln) for ln in sec.splitlines() if ln.strip().startswith("- ")]
    return {"name": name.group(1) if name else "住宿", "items": items}


def parse_transport(body: str) -> list[dict]:
    sec = section(body, r"## 4\. 交通與票價速查")
    rows = parse_table([ln for ln in sec.splitlines() if ln.strip().startswith("|")])
    out = []
    for r in rows:
        if len(r) < 4:
            continue
        out.append({"from": clean_cell(r[0]), "way": clean_cell(r[1]), "time": clean_cell(r[2]), "fare": clean_cell(r[3])})
    return out


def parse_transfer(body: str) -> list[dict]:
    sec = section(body, r"### 4\.1 大阪站／梅田轉乘小抄", r"\n## ")
    out = []
    for ln in sec.splitlines():
        if not ln.strip().startswith("- "):
            continue
        b = parse_bullet(ln)
        head, sep, tail = b["text"].partition("：")
        out.append({"title": head, "detail": tail if sep else "", "links": b["links"]})
    return out


def parse_practices(body: str) -> list[str]:
    sec = section(body, r"## 6\. 床蝨與住宿實務")
    return [parse_bullet(ln)["text"] for ln in sec.splitlines() if ln.strip().startswith("- ")]


def parse_limits(body: str) -> list[str]:
    sec = section(body, r"## 0\. 先看這裡", r"\n## 1\.")
    keep = ("限制", "尚未", "重查", "待", "⚠", "未公布")
    out = []
    for ln in sec.splitlines():
        if ln.strip().startswith("> -"):
            t = parse_bullet(ln.replace(">", "", 1))["text"]
            if any(k in t for k in keep):
                out.append(t)
    return out


def parse_tickets(body: str) -> list[dict]:
    sec = section(body, r"## 5\. 景點與門票")
    out = []
    for ln in sec.splitlines():
        if ln.strip().startswith("- "):
            raw = ln.strip()
            if "[[" in raw:          # internal vault links never go public
                continue
            b = parse_bullet(ln)
            name, _, rest = b["text"].partition("：")
            out.append({"name": name, "detail": (rest or b["text"]).strip(), "links": b["links"]})
    return out


def parse_checklist(body: str) -> list[str]:
    sec = section(body, r"## 7\. 行前重查清單")
    return [re.sub(r"^\[[ xX]\]\s*", "", parse_bullet(ln)["text"]) for ln in sec.splitlines() if ln.strip().startswith("- [")]


def parse_open(body: str) -> list[dict]:
    sec = section(body, r"## 8\. 待確認")
    out = []
    for ln in sec.splitlines():
        if ln.strip().startswith("- ["):
            done = bool(re.match(r"- \[[xX]\]", ln.strip()))
            out.append({"text": re.sub(r"^\[[ xX]\]\s*", "", parse_bullet(ln)["text"]), "done": done})
    return out


def parse_glossary(body: str) -> list[list[str]]:
    sec = section(body, r"## 附錄：常用日語對照")
    return [r for r in parse_table([ln for ln in sec.splitlines() if ln.strip().startswith("|")]) if len(r) >= 2]


def parse_summary(body: str) -> list[str]:
    sec = section(body, r"## 0\. 先看這裡", r"\n## 1\.")
    out = []
    for ln in sec.splitlines():
        ln = ln.strip()
        if ln.startswith("> -"):
            out.append(parse_bullet(ln.replace(">", "", 1))["text"])
    return out


# ---------------------------------------------------------------- build
def build_data() -> dict:
    text = NOTE.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    meta = json.loads(SITE_META.read_text(encoding="utf-8"))
    credits = json.loads((ROOT / "assets/img/credits.json").read_text(encoding="utf-8"))
    hotel = parse_hotel(body)
    hide = (meta.get("privacy") or {}).get("hide_hotel_rows") or []
    if hide:
        hotel["items"] = [it for it in hotel["items"] if not any(it["text"].startswith(h) for h in hide)]
    return {
        "frontmatter": fm,
        "meta": meta,
        "summary": parse_summary(body),
        "flights": parse_flights(body),
        "limits": parse_limits(body),
        "transfer": parse_transfer(body),
        "practices": parse_practices(body),
        "hotel": hotel,
        "days": parse_days(body),
        "transport": parse_transport(body),
        "tickets": parse_tickets(body),
        "checklist": parse_checklist(body),
        "open": parse_open(body),
        "glossary": parse_glossary(body),
        "credits": credits,
        "built_at": time.strftime("%Y-%m-%d %H:%M"),
        "note": NOTE.name,
    }


HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="description" content="2027 關西 5 天 4 夜家庭行程：大阪梅田基地，京都、神戶、宇治。">
<meta name="robots" content="noindex, nofollow">
<meta property="og:type" content="website">
<meta property="og:title" content="2027 關西 5 天 4 夜｜家庭行程">
<meta property="og:description" content="大阪梅田為基地，一天京都、一天神戶、一天宇治。二大一小，慢慢走、好好吃。">
<meta property="og:image" content="__SITE_URL__assets/img/hero.jpg">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='12' fill='%2317130f'/%3E%3Ctext x='32' y='44' font-family='serif' font-size='34' fill='%23f0c98a' text-anchor='middle'%3E%E9%97%9C%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@500;700;900&family=Noto+Sans+TC:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#f8f4ec; --paper-2:#fffdf8; --ink:#17130f; --ink-soft:#43392f;
  --muted:#5f5347; --line:#e2d8c8; --vermilion:#b23a26; --gold:#8a6526;
  --shadow:0 18px 50px -28px rgba(40,25,10,.55);
  --serif:"Noto Serif TC","Songti TC",Georgia,serif;
  --sans:"Noto Sans TC","PingFang TC",-apple-system,sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
.skip-link{position:absolute;left:-9999px;top:8px;z-index:99;background:var(--ink);color:#fff;padding:10px 16px;border-radius:8px;font-size:13px}
.skip-link:focus{left:16px}
:focus-visible{outline:2px solid var(--vermilion);outline-offset:3px}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-weight:400;line-height:1.75;-webkit-font-smoothing:antialiased}
img{max-width:100%;display:block}
a{color:inherit}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px}
.serif{font-family:var(--serif)}
.eyebrow{font-size:12px;letter-spacing:.28em;text-transform:uppercase;color:var(--muted)}

/* hero */
.hero{position:relative;min-height:92vh;display:flex;align-items:flex-end;color:#fff;overflow:hidden}
.hero__img{position:absolute;inset:0;background:url("assets/img/hero.jpg") center 35%/cover no-repeat;transform:scale(1.04);animation:drift 24s ease-in-out infinite alternate}
@keyframes drift{to{transform:scale(1.12) translateY(-1.5%)}}
.hero::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(20,14,10,.55) 0%,rgba(20,14,10,.15) 35%,rgba(20,14,10,.82) 100%)}
.hero__inner{position:relative;z-index:2;padding:0 0 64px;width:100%}
.hero h1{font-family:var(--serif);font-weight:900;font-size:clamp(40px,7vw,86px);line-height:1.05;margin:14px 0 18px;letter-spacing:.02em}
.hero h1 em{font-style:normal;color:#f0c98a}
.nw{white-space:nowrap}
.hero .lede{max-width:640px;font-weight:300;font-size:clamp(15px,1.6vw,18px);color:#eee3d4}
.hero .meta{display:flex;flex-wrap:wrap;gap:10px;margin-top:26px}
.chip{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border:1px solid rgba(255,255,255,.35);border-radius:999px;font-size:13px;letter-spacing:.04em;backdrop-filter:blur(6px);background:rgba(255,255,255,.08)}
.chip b{font-weight:600}
.countdown{position:absolute;right:24px;top:28px;z-index:3;text-align:right;color:#f4e8d6}
.countdown .n{font-family:var(--serif);font-size:44px;line-height:1;font-weight:700}
.countdown .t{font-size:11px;letter-spacing:.24em}

/* nav */
.nav{position:sticky;top:0;z-index:50;background:rgba(248,244,236,.88);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.nav__in{display:flex;align-items:center;gap:6px;height:56px;overflow-x:auto;scrollbar-width:none}
.nav__in::-webkit-scrollbar{display:none}
.nav a{white-space:nowrap;text-decoration:none;font-size:13px;color:var(--ink-soft);padding:6px 12px;border-radius:999px}
.nav a:hover,.nav a.on{background:var(--ink);color:var(--paper)}
.nav .brand{font-family:var(--serif);font-weight:700;margin-right:8px;letter-spacing:.06em;white-space:nowrap}

/* sections */
section{padding:88px 0;scroll-margin-top:72px}
.sec-head{display:flex;align-items:baseline;gap:18px;margin-bottom:34px;border-bottom:1px solid var(--line);padding-bottom:16px}
.sec-head h2{font-family:var(--serif);font-size:clamp(26px,3.4vw,38px);margin:0;font-weight:700;letter-spacing:.02em}
.sec-head .no{font-family:var(--serif);color:var(--gold);font-size:14px;letter-spacing:.2em}
.sec-head .sub{margin-left:auto;color:var(--muted);font-size:13px}

.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-top:-56px;position:relative;z-index:5}
.stat{background:var(--paper-2);border:1px solid var(--line);border-radius:14px;padding:18px 20px;box-shadow:var(--shadow)}
.stat .k{font-size:11px;letter-spacing:.2em;color:var(--muted)}
.stat .v{font-family:var(--serif);font-size:20px;margin-top:6px;font-weight:700}
.stat .s{font-size:12px;color:var(--muted);margin-top:4px}

.flight-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}
.card{background:var(--paper-2);border:1px solid var(--line);border-radius:16px;overflow:hidden;box-shadow:var(--shadow)}
.card__img{height:200px;background-size:cover;background-position:center}
.card__body{padding:22px 24px}
.card h3{font-family:var(--serif);margin:0 0 10px;font-size:20px;text-wrap:balance;overflow-wrap:anywhere}
.kv{display:flex;gap:10px;font-size:13.5px;color:var(--ink-soft);padding:6px 0;border-bottom:1px dashed var(--line)}
.kv:last-child{border:0}
.kv b{min-width:64px;color:var(--muted);font-weight:500}
.note{font-size:13px;color:var(--muted);margin-top:10px}

/* day */
.day{position:relative;padding:80px 0;border-top:1px solid var(--line);scroll-margin-top:72px}
.day__head{display:grid;grid-template-columns:120px 1fr;gap:28px;align-items:start;margin-bottom:28px}
.day__no{font-family:var(--serif);font-size:76px;line-height:.9;color:var(--vermilion);font-weight:900}
.day__no small{display:block;font-family:var(--sans);font-size:11px;letter-spacing:.24em;color:var(--muted);margin-top:10px}
.day__title h2{text-wrap:balance;font-family:var(--serif);font-size:clamp(22px,2.8vw,32px);margin:0 0 8px;font-weight:700;line-height:1.35}
.day__title .date{font-size:13px;color:var(--muted);letter-spacing:.08em}
.gallery{display:grid;grid-template-columns:1.6fr 1fr 1fr;gap:10px;margin:26px 0 34px}
.gallery figure{margin:0;position:relative;overflow:hidden;border-radius:12px;aspect-ratio:4/3;background:#ddd}
.gallery figure:first-child{aspect-ratio:auto}
.gallery img{width:100%;height:100%;object-fit:cover;transition:transform .8s ease}
.gallery figure:hover img{transform:scale(1.05)}
.gallery figcaption{position:absolute;left:0;right:0;bottom:0;padding:18px 14px 10px;font-size:11px;color:#fff;background:linear-gradient(transparent,rgba(0,0,0,.62))}

.plans{display:flex;gap:8px;margin:0 0 18px}
.plan-tab{border:1px solid var(--line);background:transparent;border-radius:999px;padding:8px 16px;font-family:var(--sans);font-size:13px;cursor:pointer;color:var(--ink-soft)}
.plan-tab.on{background:var(--vermilion);border-color:var(--vermilion);color:#fff}
.plan-name{font-size:13px;color:var(--muted);margin:0 0 14px}

.timeline{list-style:none;margin:0;padding:0;position:relative}
.timeline:before{content:"";position:absolute;left:129px;top:8px;bottom:8px;width:1px;background:var(--line)}
.tl{display:grid;grid-template-columns:104px 1fr;gap:26px;position:relative;padding:0 0 26px}
.tl:last-child{padding-bottom:0}
.tl__time{font-family:var(--serif);font-size:14px;white-space:nowrap;color:var(--vermilion);text-align:right;padding-top:2px;font-weight:700;letter-spacing:.02em}
.tl__body{position:relative;padding-left:26px}
.tl__body:before{content:"";position:absolute;left:-5px;top:9px;width:9px;height:9px;border-radius:50%;background:var(--paper);border:2px solid var(--gold)}
.tl__act{font-size:16px;font-weight:500;line-height:1.6}
.tl__tr{font-size:13px;color:var(--ink-soft);margin-top:4px}
.tl__tr .mode{display:inline-block;background:#e7d9c2;color:#5b4425;font-weight:500;border-radius:6px;padding:1px 8px;margin-right:8px;font-size:12px}
.tl__note{font-size:12.5px;color:var(--muted);margin-top:6px;padding-left:2px}

details.notes{margin-top:20px;border-top:1px dashed var(--line);padding-top:14px}
details.notes summary{cursor:pointer;font-size:13px;color:var(--muted);letter-spacing:.06em}
details.notes ul{margin:12px 0 0;padding-left:18px;font-size:13.5px;color:var(--ink-soft)}
details.notes li{margin-bottom:7px}

.meals{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-top:26px}
.meal{background:var(--paper-2);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
.meal .slot{font-size:11px;letter-spacing:.18em;color:var(--vermilion)}
.meal .area{font-family:var(--serif);font-size:17px;margin:4px 0 10px;font-weight:700}
.meal ol{margin:0;padding-left:0;list-style:none;counter-reset:m}
.meal li{counter-increment:m;font-size:13.5px;padding:7px 0;border-top:1px dashed var(--line);display:flex;gap:10px}
.meal li:before{content:counter(m);font-family:var(--serif);color:var(--gold);font-size:12px;min-width:14px}
.meal .nm{font-weight:600}
.meal .dt{color:var(--muted);font-size:12.5px}
.meal a{color:var(--vermilion);text-decoration:none;border-bottom:1px solid rgba(178,58,38,.3);margin-left:6px;font-size:12px}
.meal li:first-child .nm{color:var(--vermilion)}

/* transport table */
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:14px;background:var(--paper-2)}
table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:640px}
th,td{text-align:left;padding:12px 16px;border-bottom:1px solid var(--line)}
th{font-size:11px;letter-spacing:.16em;color:var(--muted);font-weight:500;background:#f3ecdf}
tr:last-child td{border-bottom:0}
td.fare{font-family:var(--serif);color:var(--vermilion);white-space:nowrap}

/* tickets */
.tix{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.tix .t{background:var(--paper-2);border:1px solid var(--line);border-left:3px solid var(--gold);border-radius:10px;padding:16px 18px}
.tix .n{font-family:var(--serif);font-weight:700;font-size:16px}
.tix .d{font-size:13px;color:var(--muted);margin-top:6px}
.tix a{font-size:12px;color:var(--vermilion)}

/* checklist */
.check{background:var(--paper-2);border:1px solid var(--line);border-radius:16px;padding:26px 28px}
.check .bar{height:6px;background:#eee4d4;border-radius:99px;overflow:hidden;margin:10px 0 22px}
.check .bar i{display:block;height:100%;background:linear-gradient(90deg,var(--gold),var(--vermilion));width:0;transition:width .3s}
.check label{display:flex;gap:12px;align-items:flex-start;padding:9px 0;border-bottom:1px dashed var(--line);font-size:14px;cursor:pointer}
.check input{margin-top:6px;accent-color:var(--vermilion);width:16px;height:16px}
.check input:checked+span{color:var(--muted);text-decoration:line-through}

.open{display:grid;gap:10px}
.open .row{display:flex;gap:12px;align-items:flex-start;padding:12px 16px;background:var(--paper-2);border:1px solid var(--line);border-radius:10px;font-size:14px}
.badge{font-size:11px;padding:2px 8px;border-radius:99px;white-space:nowrap}
.badge.done{background:#e6efe4;color:#3d6b3a}
.badge.todo{background:#f7e6df;color:#9c3a24}

/* glossary */
.gloss{columns:2;column-gap:36px;font-size:13.5px}
.gloss div{break-inside:avoid;display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px dashed var(--line)}
.gloss .jp{font-family:var(--serif);color:var(--ink)}
.gloss .zh{color:var(--muted)}

footer{background:var(--ink);color:#cfc3b1;padding:56px 0 40px;font-size:12.5px}
footer a{color:#e6d3b3}
footer .grid{display:grid;grid-template-columns:1.4fr 1fr;gap:40px}
footer h4{font-family:var(--serif);color:#f4ead8;margin:0 0 10px;font-size:15px}
.credits{max-height:150px;overflow:auto;line-height:1.9;font-size:11px;color:#a89a86}


/* alerts */
.alerts{margin:36px 0 0}
.alert{display:flex;gap:14px;align-items:flex-start;background:#fdf3ef;border:1px solid #ecc6b8;border-left:4px solid var(--vermilion);border-radius:12px;padding:14px 18px;font-size:13.5px;color:#6d2a1c;margin-bottom:10px}
.alert .ico{font-size:16px;line-height:1.4}
.alert b{color:#8f2c19}
.banner{background:#fdf3ef;border:1px dashed #d98f7c;color:#7a2b1a;border-radius:10px;padding:12px 14px;font-size:12.5px;margin:14px 0 0}
.banner strong{color:#8f2c19}

/* day decision + segmented control */
.decision{background:#f3ecdf;border-radius:12px;padding:14px 18px;font-size:13px;color:var(--ink-soft);margin:0 0 18px}
.decision b{font-family:var(--serif)}
.decision ul{margin:6px 0 0;padding-left:18px}
.plans{display:inline-flex;gap:4px;background:#efe7d8;padding:4px;border-radius:999px;margin:0 0 18px}
.plan-tab{border:0;background:transparent;border-radius:999px;padding:8px 18px;font-family:var(--sans);font-size:13px;cursor:pointer;color:var(--ink-soft)}
.plan-tab.on{background:var(--paper-2);color:var(--vermilion);box-shadow:0 2px 8px -4px rgba(60,35,10,.35);font-weight:600}
.tag-opt{display:inline-block;font-size:10.5px;letter-spacing:.06em;color:#8a6526;background:#f6ecda;border:1px solid #e4d2b0;border-radius:4px;padding:1px 6px;margin-left:8px;vertical-align:1px}
.pick{display:inline-block;font-size:10px;letter-spacing:.06em;color:#fff;background:var(--vermilion);border-radius:4px;padding:1px 6px;margin-left:6px;vertical-align:1px}
.meal .maps{font-size:11px;color:var(--muted);border-bottom:1px dotted var(--muted)}

/* transfer + practices */
.transfer-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px;margin-top:18px}
.transfer{background:var(--paper-2);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.transfer .t{font-family:var(--serif);font-weight:700;font-size:14.5px;color:var(--ink)}
.transfer .d{font-size:12.5px;color:var(--muted);margin-top:6px}
.practices{display:grid;gap:8px;margin-top:16px}
.practices div{display:flex;gap:10px;font-size:13.5px;color:var(--ink-soft);background:var(--paper-2);border:1px solid var(--line);border-radius:8px;padding:10px 14px}
.practices span.n{font-family:var(--serif);color:var(--gold);min-width:18px}

@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  .hero__img{animation:none}
  *{transition:none!important}
}

@media (max-width:900px){
  .summary{grid-template-columns:1fr 1fr}
  .flight-grid{grid-template-columns:1fr}
  .day__head{grid-template-columns:1fr;gap:10px}
  .day__no{font-size:52px}
  .gallery{grid-template-columns:1fr 1fr}
  .gallery figure:first-child{grid-column:1/-1}
  .gloss{columns:1}
  footer .grid{grid-template-columns:1fr}
}
@media (max-width:600px){
  .wrap{padding:0 18px}
  .hero{min-height:86vh}
  .hero h1{font-size:clamp(30px,8.6vw,44px);line-height:1.12}
  .hero .lede{font-size:14.5px}
  .hero__inner{padding-bottom:44px}
  .countdown{right:16px;top:14px}
  .countdown .n{font-size:32px}
  .chip{font-size:12px;padding:6px 11px}
  section{padding:56px 0}
  .day{padding:56px 0}
  .gallery{grid-template-columns:1fr 1fr;gap:8px}
  .gallery figure{height:140px}
  .gallery figure:first-child{grid-column:1/-1;height:180px}
  .timeline:before{left:89px}
  .tl{grid-template-columns:76px 1fr;gap:14px}
  .tl__time{font-size:12.5px}
  .tl__body{padding-left:18px}
  .tl__act{font-size:15px}
  .sec-head{flex-wrap:wrap;gap:8px}
  .sec-head .sub{margin-left:0;width:100%}
  .nav{position:sticky}
  .nav__in{mask-image:linear-gradient(90deg,#000 88%,transparent)}
  .table-wrap{border:0;background:transparent;overflow:visible}
  table{min-width:0}
  thead{display:none}
  tbody tr{display:grid;grid-template-columns:1fr;gap:2px;background:var(--paper-2);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:8px}
  tbody td{border:0;padding:2px 0}
  tbody td:nth-child(1){font-weight:600}
  tbody td:nth-child(2):before{content:"方式　";color:var(--muted);font-size:11px}
  tbody td:nth-child(3):before{content:"時間　";color:var(--muted);font-size:11px}
  tbody td.fare{white-space:normal}
  .summary{margin-top:-40px;gap:10px}
  .stat{padding:14px}
  .stat .v{font-size:17px}
}
@media print{
  .nav,.countdown,.skip-link,.plans{display:none}
  body{background:#fff;font-size:11pt}
  .hero{min-height:auto;color:var(--ink);background:#fff;border-bottom:2px solid var(--ink)}
  .hero__img,.hero::after{display:none}
  .hero h1 em{color:var(--ink)}
  .hero .lede{color:#333}
  .chip{border-color:#999;background:#fff;color:#111}
  section{padding:22px 0}
  .day{break-before:page;padding:18px 0}
  .day:first-child{break-before:auto}
  .gallery{display:none}
  .tl,.meal,.card,.transfer,.check{break-inside:avoid}
  details.notes{display:block}
  details.notes[open] summary{display:none}
  a{color:#000;text-decoration:none}
  footer{background:#fff;color:#333}
}
</style>
</head>
<body>
<a class="skip-link" href="#overview">跳至主要內容</a>
<header class="hero">
  <div class="hero__img"></div>
  <div class="countdown"><div class="n" id="cd">—</div><div class="t">DAYS TO GO</div></div>
  <div class="hero__inner wrap">
    <div class="eyebrow" style="color:#e8d6bd">Kansai · Japan · Family Trip</div>
    <h1>2027 關西<em>五天四夜</em></h1>
    <p class="lede">大阪梅田為基地，一天京都、一天神戶、一天宇治。<span class="nw">二大一小</span>，慢慢走、好好吃。</p>
    <div class="meta">
      <span class="chip"><b>2027.02.02</b> — 02.06</span>
      <span class="chip">2 大人 + 1 小孩（7 歲）</span>
      <span class="chip">台北 ⇄ 關西 KIX</span>
      <span class="chip">Hotel Hankyu RESPIRE OSAKA</span>
    </div>
  </div>
</header>

<nav class="nav"><div class="nav__in wrap" id="nav"></div></nav>

<div class="wrap"><div class="summary" id="summary"></div><div class="alerts" id="alerts"></div></div>

<section id="overview"><div class="wrap">
  <div class="sec-head"><span class="no">FLIGHTS</span><h2>航班與住宿</h2><span class="sub">2026-11 查核 · 出發前重查</span></div>
  <div class="flight-grid">
    <div class="card">
      <div class="card__img" style="background-image:url(assets/img/kix.jpg)"></div>
      <div class="card__body"><h3>航班 · 台灣虎航</h3><div id="flight-out"></div></div>
    </div>
    <div class="card">
      <div class="card__img" style="background-image:url(assets/img/umeda.jpg)"></div>
      <div class="card__body"><h3 id="hotel-name">住宿</h3><div id="hotel-body"></div></div>
    </div>
  </div>
</div></section>

<main id="days"></main>

<section id="transport"><div class="wrap">
  <div class="sec-head"><span class="no">TRAVEL</span><h2>交通與票價速查</h2><span class="sub">大人／小孩（日圓）</span></div>
  <div class="table-wrap"><table><thead><tr><th>區間</th><th>方式</th><th>時間</th><th>票價</th></tr></thead><tbody id="transport-body"></tbody></table></div>
  <h3 class="serif" style="margin:34px 0 6px;font-size:19px">大阪站／梅田轉乘小抄</h3>
  <div class="transfer-grid" id="transfer-body"></div>
  <h3 class="serif" style="margin:38px 0 6px;font-size:19px">住宿實務</h3>
  <div class="practices" id="practices-body"></div>
</div></section>

<section id="tickets"><div class="wrap">
  <div class="sec-head"><span class="no">TICKETS</span><h2>門票與場館</h2><span class="sub">價格為 2026-11 查得</span></div>
  <div class="tix" id="tickets-body"></div>
</div></section>

<section id="checklist"><div class="wrap">
  <div class="sec-head"><span class="no">CHECK</span><h2>行前重查清單</h2><span class="sub">勾選狀態存在這台裝置</span></div>
  <div class="check">
    <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--muted)"><span>完成度</span><span id="progress-txt">0 / 0</span></div>
    <div class="bar"><i id="progress-bar"></i></div>
    <div id="checklist-body"></div>
  </div>
  <h3 class="serif" style="margin:40px 0 16px;font-size:20px">待確認事項</h3>
  <div class="open" id="open-body"></div>
</div></section>

<section id="glossary"><div class="wrap">
  <div class="sec-head"><span class="no">WORDS</span><h2>常用日語對照</h2><span class="sub">站名與地名</span></div>
  <div class="gloss" id="gloss-body"></div>
</div></section>

<footer><div class="wrap"><div class="grid">
  <div>
    <h4>資料來源與同步</h4>
    <p>本頁由 Obsidian 筆記 <code>__NOTE__</code> 產生（最後同步 __BUILT__）。修改筆記後執行 <code>python3 build_site.py</code>，或讓 <code>build_site.py --watch</code> 常駐自動重建。</p>
    <p>票價、時刻與營業時間為 2026-11 查得資料，出發前 4–6 週需重新確認。照片來自 Wikimedia Commons，授權與作者如下。</p>
  </div>
  <div><h4>圖片授權</h4><div class="credits" id="credits"></div></div>
</div></div></footer>

<script>
const DATA = __DATA__;
const $ = (s,r=document)=>r.querySelector(s);
const el = (tag, cls, html) => { const n=document.createElement(tag); if(cls)n.className=cls; if(html!=null)n.innerHTML=html; return n; };
const esc = s => String(s ?? "").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const safeUrl = u => /^https?:\/\//i.test(String(u||"")) ? esc(u) : "#";
const bold = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
const maps = q => "https://www.google.com/maps/search/?api=1&query="+encodeURIComponent(q);

/* countdown */
const target = new Date("2027-02-02T00:00:00+08:00");
const days = Math.ceil((target - new Date())/86400000);
$("#cd").textContent = days > 0 ? days : (days===0 ? "TODAY" : "—");

/* nav */
const nav = $("#nav");
nav.appendChild(el("span","brand","關西 2027"));
const navItems = [["overview","總覽"],["transport","交通"],["tickets","門票"],["checklist","清單"],["glossary","日語"]];
DATA.days.forEach(d=>navItems.splice(1+ (d.no-1),0,[`day-${d.no}`,`Day ${d.no}`]));
navItems.forEach(([id,label])=>{ const a=el("a",null,label); a.href="#"+id; nav.appendChild(a); });
let sections=[];
function markNav(){
  sections=[...document.querySelectorAll("section[id], .day[id]")];
  if(!sections.length) return;
  const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 8;
  const y=window.scrollY+130;
  let cur=sections[0];
  if(atBottom){ cur=sections[sections.length-1]; }
  else { for(const s of sections){ if(s.offsetTop<=y) cur=s; } }
  let active=null;
  document.querySelectorAll("#nav a").forEach(a=>{ const on=a.getAttribute("href")==="#"+cur.id; a.classList.toggle("on", on); if(on) active=a; });
  if(active && window.innerWidth<900){ active.scrollIntoView({inline:"center", block:"nearest", behavior:"smooth"}); }
}
window.addEventListener("scroll", markNav, {passive:true});
window.addEventListener("resize", markNav);
markNav();

/* summary stats */
const summary = $("#summary");
[["日期","2027.02.02 — 02.06","5 天 4 夜 · 週二出發"],
 ["航班","IT210 / IT213","TPE ⇄ KIX · 虎航"],
 ["基地","大阪梅田","JR 大阪站徒步 3 分"],
 ["同行","2 大 1 小","7 歲 · 已入小學"]].forEach(([k,v,s])=>{
  const d=el("div","stat"); d.innerHTML=`<div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div>`; summary.appendChild(d);
});

/* flights */
const FL = DATA.flights || {rows:[],warnings:[]};
if(FL.rows.length){
  const f=FL.rows[0], b=FL.rows[1];
  $("#flight-out").innerHTML = `
    <div class="kv"><b>去程</b><span>${esc(f.date)} ${esc(f.flight)}</span></div>
    <div class="kv"><b>時間</b><span>${esc(f.time)}</span></div>
    <div class="kv"><b>備註</b><span>${esc(f.note)}</span></div>
    ${b?`<div class="kv"><b>回程</b><span>${esc(b.date)} ${esc(b.flight)}</span></div>
    <div class="kv"><b>時間</b><span>${esc(b.time)}</span></div>
    <div class="kv"><b>備註</b><span>${esc(b.note)}</span></div>`:""}`;
}
if(FL.warnings && FL.warnings.length){
  $("#flight-out").insertAdjacentHTML("beforeend",
    FL.warnings.map(w=>`<div class="banner"><strong>需確認</strong>　${bold(w.text)} ${w.links.map(l=>`<a href="${safeUrl(l.url)}" target="_blank" rel="noopener">${esc(l.text)}</a>`).join("")}</div>`).join(""));
}
const alerts = $("#alerts");
if(DATA.limits && DATA.limits.length){
  alerts.innerHTML = DATA.limits.map(t=>`<div class="alert"><span class="ico">⚠</span><div>${bold(t)}</div></div>`).join("");
}
const hotel=$("#hotel-name"); if(hotel) hotel.textContent = DATA.hotel.name;
DATA.hotel.items.slice(0,5).forEach(it=>{
  const [k,...rest]=it.text.split("：");
  $("#hotel-body").insertAdjacentHTML("beforeend", `<div class="kv"><b>${esc(k)}</b><span>${esc(rest.join("："))}</span></div>`);
});

/* days */
const dayWrap = $("#days");
DATA.days.forEach(d=>{
  const sec = el("section","day"); sec.id="day-"+d.no;
  const cfg = (DATA.meta.days||{})[String(d.no)] || {};
  const gallery = (cfg.gallery||[]).map(slug=>`<figure><img src="assets/img/${slug}.jpg" alt="${esc((DATA.meta.captions||{})[slug]||"")}" loading="lazy"><figcaption>${esc((DATA.meta.captions||{})[slug] || (DATA.credits[slug]||{}).title || "")}</figcaption></figure>`).join("");
  sec.innerHTML = `
    <div class="wrap">
      <div class="day__head">
        <div class="day__no">0${d.no}<small>DAY ${d.no}</small></div>
        <div class="day__title"><h2>${esc(d.title)}</h2><div class="date">${esc(d.date)}（${esc(d.weekday)}）${cfg.label? " · "+esc(cfg.label):""}</div></div>
      </div>
      ${gallery?`<div class="gallery">${gallery}</div>`:""}
      <div class="plan-host"></div>
      <div class="meals-host"></div>
    </div>`;
  const host = sec.querySelector(".plan-host");
  if(d.decision && d.decision.length){
    host.insertAdjacentHTML("beforebegin", `<div class="decision"><b>決策與提醒</b><ul>${d.decision.map(n=>`<li>${bold(n.text)}${n.links.map(l=>` <a href="${safeUrl(l.url)}" target="_blank" rel="noopener">${esc(l.text)}</a>`).join("")}</li>`).join("")}</ul></div>`);
  }
  const renderPlan = (p, idx) => {
    host.querySelectorAll(".plan-body").forEach(n=>n.remove());
    const body = el("div","plan-body");
    body.innerHTML = `${p.name?`<div class="plan-name">${esc(p.name)}</div>`:""}
      <ul class="timeline">${p.rows.map(r=>`
        <li class="tl">
          <div class="tl__time">${esc(r.time)}</div>
          <div class="tl__body">
            <div class="tl__act">${bold(r.activity)}${r.optional?'<span class="tag-opt">可選</span>':""}</div>
            ${r.transport && r.transport!=="—" ? `<div class="tl__tr"><span class="mode">移動</span>${bold(r.transport)}</div>`:""}
            ${r.note?`<div class="tl__note">${bold(r.note)}</div>`:""}
          </div>
        </li>`).join("")}</ul>
      ${p.notes.length?`<details class="notes"><summary>行程細節與提醒（${p.notes.length}）</summary><ul>${p.notes.map(n=>`<li>${esc(n.text)}${n.links.map(l=>` <a href="${safeUrl(l.url)}" target="_blank" rel="noopener">${esc(l.text)}</a>`).join("")}</li>`).join("")}</ul></details>`:""}`;
    host.appendChild(body);
    host.querySelectorAll(".plan-tab").forEach((b,i)=>{b.classList.toggle("on",i===idx); b.setAttribute("aria-pressed", i===idx?"true":"false");});
  };
  if(!d.plans || !d.plans.length){ dayWrap.appendChild(sec); return; }
  if(d.plans.length>1){
    const tabs = el("div","plans");
    d.plans.forEach((p,i)=>{ const b=el("button","plan-tab","Plan "+p.key); b.type="button"; b.setAttribute("aria-pressed", i===0?"true":"false"); b.onclick=()=>{renderPlan(p,i)}; tabs.appendChild(b); });
    host.appendChild(tabs);
  }
  renderPlan(d.plans[0],0);

  const mealsHost = sec.querySelector(".meals-host");
  if(d.meals.length){
    mealsHost.appendChild(el("div","sec-head","<span class=\"no\">EAT</span><h3 style=\"font-size:20px;margin:0\">用餐安排</h3>"));
    const grid = el("div","meals");
    d.meals.forEach(m=>{
      const box = el("div","meal");
      box.innerHTML = `<div class="slot">${esc(m.slot)}</div><div class="area">${esc(m.area)}</div>
        <ol>${m.items.map((it,i)=>`<li><div><div class="nm">${esc(it.name)}${i===0?'<span class="pick">首選</span>':""}</div><div class="dt">${esc(it.detail)}</div>${it.links.map(l=>`<a href="${safeUrl(l.url)}" target="_blank" rel="noopener">${esc(l.text)}</a>`).join("")}<a class="maps" href="${maps(it.name)}" target="_blank" rel="noopener">地圖</a></div></li>`).join("")}</ol>
        ${m.note?`<div class="note">${esc(m.note)}</div>`:""}`;
      grid.appendChild(box);
    });
    mealsHost.appendChild(grid);
  }
  dayWrap.appendChild(sec);
});

/* transport */
const tb=$("#transport-body");
DATA.transport.forEach(r=>{ const tr=el("tr"); tr.innerHTML=`<td>${esc(r.from)}</td><td>${esc(r.way)}</td><td>${esc(r.time)}</td><td class="fare">${esc(r.fare)}</td>`; tb.appendChild(tr); });

/* tickets */
const tix=$("#tickets-body");
DATA.tickets.forEach(t=>{ const d=el("div","t"); d.innerHTML=`<div class="n">${esc(t.name)}</div><div class="d">${esc(t.detail)}</div>${t.links.map(l=>`<a href="${safeUrl(l.url)}" target="_blank" rel="noopener">${esc(l.text)}</a>`).join(" ")}`; tix.appendChild(d); });

/* checklist */
const key="kansai2027-check";
let saved={};
try{ saved=JSON.parse(localStorage.getItem(key)||"{}"); }catch(e){ saved={}; }
const h=(s)=>{ let x=0; for(let i=0;i<s.length;i++){ x=(x*31+s.charCodeAt(i))|0; } return "c"+(x>>>0).toString(36); };
const cb=$("#checklist-body");
DATA.checklist.forEach((t)=>{
  const id=h(t);
  const l=el("label"); l.innerHTML=`<input type="checkbox" data-id="${id}" ${saved[id]?"checked":""}><span>${esc(t)}</span>`;
  cb.appendChild(l);
});
function syncProgress(){
  const boxes=[...cb.querySelectorAll("input")];
  const done=boxes.filter(b=>b.checked).length;
  $("#progress-txt").textContent=`${done} / ${boxes.length}`;
  $("#progress-bar").style.width=(boxes.length?done/boxes.length*100:0)+"%";
  const state={}; boxes.forEach(b=>state[b.dataset.id]=b.checked);
  try{ localStorage.setItem(key,JSON.stringify(state)); }catch(e){}
}
cb.addEventListener("change",syncProgress); syncProgress();

/* open items */
const ob=$("#open-body");
DATA.open.forEach(o=>{ const d=el("div","row"); d.innerHTML=`<span class="badge ${o.done?"done":"todo"}">${o.done?"已定":"待定"}</span><span>${esc(o.text)}</span>`; ob.appendChild(d); });

/* transfer + practices */
const tb2=$("#transfer-body");
(DATA.transfer||[]).forEach(t=>{ const d=el("div","transfer"); d.innerHTML=`<div class="t">${esc(t.title)}</div>${t.detail?`<div class="d">${esc(t.detail)}</div>`:""}${t.links.map(l=>`<a href="${safeUrl(l.url)}" target="_blank" rel="noopener" style="font-size:11px;color:var(--vermilion)">${esc(l.text)}</a>`).join(" ")}`; tb2.appendChild(d); });
const pb=$("#practices-body");
(DATA.practices||[]).forEach((t,i)=>{ const d=el("div"); d.innerHTML=`<span class="n">${i+1}</span><span>${esc(t)}</span>`; pb.appendChild(d); });

/* glossary */
const gb=$("#gloss-body");
DATA.glossary.forEach(([jp,zh])=>{ const d=el("div"); d.innerHTML=`<span class="jp">${esc(jp)}</span><span class="zh">${esc(zh)}</span>`; gb.appendChild(d); });

/* credits */
const cr=$("#credits");
const used=new Set([...document.querySelectorAll('.gallery img')].map(i=>i.getAttribute('src').split('/').pop().replace('.jpg','')).concat(['hero']));
Object.entries(DATA.credits).filter(([slug])=>used.has(slug)).forEach(([slug,c])=>{
  const d=el("div"); d.innerHTML=`${esc(slug)} · ${esc((c.artist||"").slice(0,60))} · ${esc(c.license||"")} · <a href="${c.page}" target="_blank" rel="noopener">Commons</a>`; cr.appendChild(d);
});
</script>
</body></html>
"""


def render(data: dict) -> str:
    html = HTML
    html = html.replace("__TITLE__", "2027 關西 5 天 4 夜｜家庭行程")
    html = html.replace("__NOTE__", escape(data["note"]))
    html = html.replace("__BUILT__", escape(data["built_at"]))
    html = html.replace("__SITE_URL__", data["meta"].get("site_url", ""))
    public_meta = {k: v for k, v in data["meta"].items() if k not in ("privacy", "site_url")}
    payload = json.dumps({**data, "meta": public_meta}, ensure_ascii=False, separators=(",", ":"))
    for src, dst in ((data["meta"].get("privacy") or {}).get("replacements") or {}).items():
        payload = payload.replace(src, dst)
    payload = payload.replace("</", "<\\/")
    return html.replace("__DATA__", payload)


def build() -> None:
    data = build_data()
    (ROOT / "_shots").mkdir(exist_ok=True)
    (ROOT / "_shots/data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "index.html").write_text(render(data), encoding="utf-8")
    days = len(data["days"])
    stops = sum(len(p["rows"]) for d in data["days"] for p in d["plans"])
    print(f"built index.html · {days} days · {stops} timeline rows · {len(data['checklist'])} checklist items")


if __name__ == "__main__":
    build()
    if "--watch" in sys.argv:
        stamp = NOTE.stat().st_mtime
        print(f"watching {NOTE.name} … Ctrl-C to stop")
        try:
            while True:
                time.sleep(2)
                if NOTE.stat().st_mtime != stamp:
                    stamp = NOTE.stat().st_mtime
                    try:
                        build()
                        print("rebuilt at", time.strftime("%H:%M:%S"))
                    except Exception as exc:  # noqa: BLE001
                        print("build failed:", exc)
        except KeyboardInterrupt:
            pass
