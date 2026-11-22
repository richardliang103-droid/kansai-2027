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
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Noto+Sans+TC:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#F1F2EE; --panel:#FFFFFF; --ink:#101418; --ink-2:#3A4149; --muted:#6B7278;
  --rule:#C8CCC4; --rule-dark:#101418; --accent:#005BAC;
  --d1:#D91E2A; --d2:#E85D04; --d3:#0072BC; --d4:#6F2C91; --d5:#00875A;
  --sans:"IBM Plex Sans","Noto Sans TC","PingFang TC",-apple-system,sans-serif;
  --display:"Archivo Black","Noto Sans TC","PingFang TC",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
  --gut:24px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.65;-webkit-font-smoothing:antialiased}
img{max-width:100%;display:block}
a{color:inherit}
.wrap{max-width:1180px;margin:0 auto;padding:0 var(--gut)}
.skip-link{position:absolute;left:-9999px;top:8px;z-index:99;background:var(--ink);color:#fff;padding:10px 16px;font-size:13px}
.skip-link:focus{left:16px}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.nw{white-space:nowrap}
.serif{font-family:var(--display);font-weight:400;letter-spacing:-.01em}

/* ---------- hero: departure board ---------- */
.hero{position:relative;background:var(--panel);border-bottom:3px solid var(--ink);overflow:hidden}
.hero__bar{background:var(--ink);color:#fff;font-family:var(--mono);font-size:12px;letter-spacing:.08em}
.hero__bar .wrap{display:flex;align-items:center;gap:18px;height:44px}
.hero__bar .tag{background:var(--accent);color:#fff;padding:4px 9px;font-weight:500;line-height:1}
.hero__bar .right{margin-left:auto;color:#B9C0C7}
.hero__stage{position:relative;perspective:1200px;padding:56px 0 0;background:
  linear-gradient(90deg,transparent 0 49.94%,var(--rule) 49.94% 50.06%,transparent 50.06%) 0 0/100% 100%,
  repeating-linear-gradient(0deg,transparent 0 79px,var(--rule) 79px 80px),
  repeating-linear-gradient(90deg,transparent 0 79px,var(--rule) 79px 80px);
  background-color:var(--bg)}
.hero__grid{display:grid;grid-template-columns:1.45fr .95fr;gap:36px;align-items:start;transform-style:preserve-3d}
.hero h1{font-family:var(--display);font-size:clamp(46px,7.2vw,92px);line-height:.94;margin:10px 0 0;letter-spacing:-.03em;font-weight:400}
.hero h1 .cn{display:block;font-family:var(--display);font-size:clamp(30px,4.4vw,58px);letter-spacing:-.02em;margin-top:10px}
.hero .kicker{display:inline-flex;align-items:center;gap:10px;font-family:var(--mono);font-size:11px;letter-spacing:.22em;color:var(--muted)}
.hero .kicker:before{content:"";width:26px;height:3px;background:var(--accent)}
.hero .lede{max-width:34em;margin:20px 0 0;color:var(--ink-2);font-size:15px;text-wrap:pretty;line-height:1.8}
.hero .dest{display:flex;flex-wrap:wrap;gap:8px;margin-top:26px}
.dest span{font-family:var(--mono);font-size:12px;border:1px solid var(--rule-dark);padding:5px 10px;background:var(--panel)}
.dest span b{color:var(--accent)}

/* ticket stub */
.ticket{background:var(--panel);border:2px solid var(--ink);position:relative;padding:26px 28px;transform:translateZ(40px)}
.ticket:before,.ticket:after{content:"";position:absolute;width:18px;height:18px;background:var(--bg);border:2px solid var(--ink);border-radius:50%;top:50%;margin-top:-9px}
.ticket:before{left:-11px;clip-path:inset(0 0 0 50%)}
.ticket:after{right:-11px;clip-path:inset(0 50% 0 0)}
.ticket .row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px dashed var(--rule);font-size:13px}
.ticket .row:last-of-type{border:0}
.ticket .k{color:var(--muted);font-size:11px;letter-spacing:.14em;font-family:var(--mono)}
.ticket .v{font-family:var(--mono);font-weight:500;text-align:right}
.ticket .cd{margin-top:16px;padding-top:14px;border-top:2px solid var(--ink);display:flex;align-items:flex-end;justify-content:space-between}
.ticket .cd .n{font-family:var(--display);font-size:46px;line-height:.85;letter-spacing:-.04em}
.ticket .cd .t{font-family:var(--mono);font-size:10px;letter-spacing:.2em;color:var(--muted);text-align:right}

/* route strip */
.route{border-top:2px solid var(--ink);margin-top:44px;background:var(--panel)}
.route__in{display:grid;grid-template-columns:repeat(5,1fr);position:relative}
.route__in:before{content:"";position:absolute;left:10%;right:10%;top:36px;height:4px;background:linear-gradient(90deg,var(--d1) 0 25%,var(--d2) 25% 50%,var(--d3) 50% 75%,var(--d4) 75% 100%)}
.route a{position:relative;text-decoration:none;padding:29px 14px 14px;border-right:1px solid var(--rule);display:block}
.route a:last-child{border-right:0}
.route .dot{width:14px;height:14px;border-radius:50%;background:var(--panel);border:4px solid var(--c,var(--ink));position:relative;margin:0 0 12px;z-index:1}
.route .code{font-family:var(--mono);font-size:10px;letter-spacing:.18em;color:var(--muted)}
.route .nm{font-weight:700;font-size:13px;margin-top:2px}
.route a:hover{background:#FAFAF8}
.route a:hover .nm{text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:3px}

/* ---------- nav ---------- */
.nav{position:sticky;top:0;z-index:50;background:var(--panel);border-bottom:1px solid var(--ink)}
.nav__in{display:flex;align-items:center;gap:4px;height:52px;overflow-x:auto;scrollbar-width:none}
.nav__in::-webkit-scrollbar{display:none}
.nav .brand{font-family:var(--display);font-size:14px;letter-spacing:.02em;margin-right:16px;white-space:nowrap}
.nav a{white-space:nowrap;text-decoration:none;font-family:var(--mono);font-size:12px;letter-spacing:.06em;color:var(--ink-2);padding:6px 10px;border:1px solid transparent}
.nav a:hover{border-color:var(--rule-dark)}
.nav a.on{background:var(--ink);color:#fff;border-color:var(--ink)}

/* ---------- sections ---------- */
section{padding:74px 0;scroll-margin-top:64px;background:var(--bg)}
.sec-head{display:flex;align-items:flex-end;gap:18px;margin-bottom:30px;border-bottom:2px solid var(--ink);padding-bottom:12px}
.sec-head h2{font-family:var(--display);font-weight:400;font-size:clamp(24px,3vw,34px);margin:0;letter-spacing:-.02em}
.sec-head .no{font-family:var(--mono);font-size:11px;letter-spacing:.22em;color:var(--accent);padding-bottom:6px}
.sec-head .sub{margin-left:auto;color:var(--muted);font-size:12px;font-family:var(--mono);padding-bottom:6px}

.summary{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--ink);background:var(--panel);margin-top:28px}
.stat{padding:18px 20px;border-right:1px solid var(--rule)}
.stat:last-child{border-right:0}
.stat .k{font-family:var(--mono);font-size:10px;letter-spacing:.18em;color:var(--muted)}
.stat .v{font-family:var(--display);font-size:22px;margin-top:6px;letter-spacing:-.02em}
.stat .s{font-size:12px;color:var(--muted);margin-top:2px}

.alerts{margin-top:16px}
.alert{display:flex;gap:14px;align-items:flex-start;border:1px solid var(--ink);border-left:6px solid var(--d1);background:var(--panel);padding:14px 18px;font-size:13px;color:var(--ink-2)}
.alert .ico{font-family:var(--mono);font-weight:700;color:var(--d1)}
.banner{border:1px dashed var(--ink);background:#FFFDF6;color:var(--ink-2);padding:12px 14px;font-size:12.5px;margin-top:14px}
.banner strong{color:var(--d1);font-family:var(--mono);letter-spacing:.08em}

/* flight + hotel */
.flight-grid{display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--ink);background:var(--panel)}
.card{border-right:1px solid var(--ink)}
.card:last-child{border-right:0}
.card__img{height:180px;background-size:cover;background-position:center;border-bottom:1px solid var(--ink);filter:saturate(.9) contrast(1.02)}
.card__body{padding:20px 22px}
.card h3{font-family:var(--display);margin:0 0 12px;font-size:18px;font-weight:400;letter-spacing:-.01em}
.kv{display:grid;grid-template-columns:66px 1fr;gap:10px;font-size:13px;color:var(--ink-2);padding:7px 0;border-bottom:1px dashed var(--rule)}
.kv:last-child{border:0}
.kv b{font-family:var(--mono);font-size:10px;letter-spacing:.14em;color:var(--muted);font-weight:500;padding-top:3px}
.note{font-size:12.5px;color:var(--muted);margin-top:10px}

/* ---------- day ---------- */
.day{position:relative;padding:64px 0 72px;border-top:1px solid var(--ink);scroll-margin-top:64px;background:var(--panel)}
.day:nth-child(even){background:var(--bg)}
.day__head{display:grid;grid-template-columns:150px 1fr;gap:0;border:1px solid var(--ink);margin-bottom:22px}
.day__no{background:var(--c,var(--ink));color:#fff;padding:16px 18px;display:flex;flex-direction:column;justify-content:space-between}
.day__no b{font-family:var(--display);font-size:54px;line-height:.85;letter-spacing:-.04em;font-weight:400}
.day__no small{font-family:var(--mono);font-size:10px;letter-spacing:.2em}
.day__title{padding:16px 22px;background:var(--panel)}
.day__title h2{font-family:var(--display);font-weight:400;font-size:clamp(20px,2.4vw,28px);margin:0 0 6px;line-height:1.25;letter-spacing:-.02em}
.day__title .date{font-family:var(--mono);font-size:11px;letter-spacing:.1em;color:var(--muted)}

.gallery{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 26px}
.gallery:has(figure:first-child:last-child){grid-template-columns:1fr;max-width:720px}
.gallery:has(figure:nth-child(2):last-child){grid-template-columns:1fr 1fr}
.gallery figure{margin:0;position:relative;overflow:hidden;border:1px solid var(--ink);aspect-ratio:4/3;background:#ddd}
.gallery img{width:100%;height:100%;object-fit:cover;filter:saturate(.92)}
.gallery figcaption{position:absolute;left:0;bottom:0;padding:6px 9px;background:var(--ink);color:#fff;font-family:var(--mono);font-size:10px;letter-spacing:.06em}

.decision{border:1px solid var(--ink);border-top:4px solid var(--c,var(--ink));background:#FAFAF7;padding:14px 18px;font-size:13px;margin-bottom:18px}
.decision b{font-family:var(--display);font-weight:400;letter-spacing:.02em}
.decision ul{margin:8px 0 0;padding-left:18px}
.decision li{margin-bottom:4px}

.plans{display:inline-flex;border:1px solid var(--ink);margin-bottom:18px;background:var(--panel)}
.plan-tab{border:0;border-right:1px solid var(--ink);background:transparent;padding:9px 20px;font-family:var(--mono);font-size:12px;letter-spacing:.08em;cursor:pointer;color:var(--ink)}
.plan-tab:last-child{border-right:0}
.plan-tab.on{background:var(--c,var(--ink));color:#fff}
.plan-name{font-family:var(--mono);font-size:11px;letter-spacing:.08em;color:var(--muted);margin:0 0 14px}

.timeline{list-style:none;margin:0;padding:0;position:relative;max-width:860px}
.timeline:before{content:"";position:absolute;left:150px;top:6px;bottom:6px;width:4px;background:var(--c,var(--ink))}
.tl{display:grid;grid-template-columns:150px 1fr;gap:0;position:relative;padding:0 0 24px}
.tl:last-child{padding-bottom:0}
.tl__time{font-family:var(--mono);font-size:13px;color:var(--ink);text-align:right;padding:1px 24px 0 0;font-weight:500;white-space:nowrap}
.tl__body{position:relative;padding-left:22px}
.tl__body:before{content:"";position:absolute;left:-8px;top:6px;width:11px;height:11px;background:var(--panel);border:3px solid var(--c,var(--ink));border-radius:50%}
.tl__act{font-size:15.5px;font-weight:600;line-height:1.55}
.tl__tr{font-size:12.5px;color:var(--ink-2);margin-top:3px}
.tl__tr .mode{display:inline-block;font-family:var(--mono);font-size:10px;letter-spacing:.1em;border:1px solid var(--rule);padding:0 6px;margin-right:8px;color:var(--muted)}
.tl__note{font-size:12px;color:var(--muted);margin-top:5px}
.tag-opt{display:inline-block;font-family:var(--mono);font-size:10px;border:1px solid var(--c,var(--ink));color:var(--c,var(--ink));padding:0 5px;margin-left:8px;vertical-align:2px}

details.notes{margin-top:18px;border-top:1px solid var(--rule);padding-top:12px}
details.notes summary{cursor:pointer;font-family:var(--mono);font-size:11px;letter-spacing:.12em;color:var(--muted)}
details.notes ul{margin:10px 0 0;padding-left:18px;font-size:13px;color:var(--ink-2)}
details.notes li{margin-bottom:6px}

/* meals: ticket stubs */
.meals{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-top:24px}
.meal{background:var(--panel);border:1px solid var(--ink);padding:16px 18px;position:relative}
.meal:before{content:"";position:absolute;right:-1px;top:18px;width:10px;height:10px;background:var(--bg);border:1px solid var(--ink);border-radius:50%;clip-path:inset(0 0 0 50%)}
.meal .slot{font-family:var(--mono);font-size:10px;letter-spacing:.2em;color:var(--c,var(--accent))}
.meal .area{font-family:var(--display);font-size:17px;margin:3px 0 10px;font-weight:400;letter-spacing:-.01em}
.meal ol{margin:0;padding:0;list-style:none;counter-reset:m}
.meal li{counter-increment:m;font-size:13px;padding:8px 0;border-top:1px solid var(--rule);display:flex;gap:10px}
.meal li:before{content:counter(m,decimal-leading-zero);font-family:var(--mono);font-size:10px;color:var(--muted);min-width:18px;padding-top:3px}
.meal .nm{font-weight:600}
.meal .dt{color:var(--muted);font-size:12px}
.meal a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(0,91,172,.35);margin-left:6px;font-family:var(--mono);font-size:10.5px}
.meal .pick{display:inline-block;font-family:var(--mono);font-size:9px;letter-spacing:.1em;background:var(--c,var(--accent));color:#fff;padding:1px 5px;margin-left:6px;vertical-align:1px}
.meal .maps{color:var(--muted);border-bottom:1px dotted var(--muted)}

/* tables */
.table-wrap{border:1px solid var(--ink);background:var(--panel);overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:640px}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--rule)}
th{font-family:var(--mono);font-size:10px;letter-spacing:.16em;color:#fff;background:var(--ink);font-weight:500;border-bottom:0}
tbody tr:nth-child(even){background:#F7F8F5}
td.fare{font-family:var(--mono);white-space:nowrap;color:var(--ink);font-weight:500;text-align:right;font-variant-numeric:tabular-nums}
th.num{text-align:right}
td.note{color:var(--muted);font-size:12px}

/* tickets */
.tix{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:0;border:1px solid var(--ink);background:var(--panel)}
.tix .t{padding:16px 18px;border-right:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.tix .n{font-weight:700;font-size:14.5px}
.tix .d{font-size:12.5px;color:var(--muted);margin-top:4px}
.tix a{font-family:var(--mono);font-size:10.5px;color:var(--accent)}

/* transfer + practices */
.transfer-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin-top:16px}
.transfer{background:var(--panel);border:1px solid var(--ink);border-left:5px solid var(--d3);padding:14px 16px}
.transfer .t{font-weight:700;font-size:13.5px}
.transfer .d{font-size:12.5px;color:var(--muted);margin-top:5px}
.practices{display:grid;gap:0;margin-top:16px;border:1px solid var(--ink);background:var(--panel)}
.practices div{display:flex;gap:12px;font-size:13px;color:var(--ink-2);padding:11px 16px;border-bottom:1px solid var(--rule)}
.practices div:last-child{border-bottom:0}
.practices span.n{font-family:var(--mono);color:var(--accent);min-width:22px;font-size:11px;padding-top:3px}

/* checklist */
.check{background:var(--panel);border:1px solid var(--ink);padding:22px 24px}
.check .bar{height:8px;background:#E6E8E3;margin:10px 0 20px;border:1px solid var(--rule)}
.check .bar i{display:block;height:100%;background:var(--accent);width:0;transition:width .25s}
.check label{display:flex;gap:12px;align-items:flex-start;padding:9px 0;border-bottom:1px solid var(--rule);font-size:13.5px;cursor:pointer}
.check label:last-child{border-bottom:0}
.check input{margin-top:5px;accent-color:var(--accent);width:15px;height:15px}
.check input:checked+span{color:var(--muted);text-decoration:line-through}
.open{display:grid;gap:8px}
.open .row{display:flex;gap:12px;align-items:flex-start;padding:11px 14px;background:var(--panel);border:1px solid var(--ink);font-size:13px}
.badge{font-family:var(--mono);font-size:10px;padding:2px 7px;white-space:nowrap;border:1px solid}
.badge.done{background:var(--d5);color:#fff;border-color:var(--d5)}
.badge.todo{background:#fff;color:var(--d1);border-color:var(--d1)}

/* glossary */
.gloss{columns:2;column-gap:32px;font-size:13px}
.gloss div{break-inside:avoid;display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid var(--rule)}
.gloss .jp{font-weight:600}
.gloss .zh{color:var(--muted);font-family:var(--mono);font-size:11.5px}

footer{background:var(--ink);color:#B9C0C7;padding:48px 0 36px;font-size:12px;font-family:var(--mono)}
footer a{color:#fff}
footer .grid{display:grid;grid-template-columns:1.4fr 1fr;gap:36px}
footer h4{font-family:var(--display);color:#fff;margin:0 0 10px;font-size:14px;font-weight:400}
.credits{max-height:140px;overflow:auto;line-height:1.8;font-size:10.5px;color:#8B939B}


/* ================= MUJI theme overrides ================= */
:root{
  --bg:#F6F4EE; --panel:#FCFBF7; --ink:#3A3833; --ink-2:#5E5A51; --muted:#7E786C;
  --rule:#DFDACF; --rule-dark:#4A4740; --accent:#7C6A52;
  --d1:#A9715C; --d2:#B08C5B; --d3:#7A878B; --d4:#8E8492; --d5:#747C68;
}
body{background:var(--bg);color:var(--ink);letter-spacing:.01em}
.hero{background:var(--panel);border-bottom:1px solid var(--rule)}
.hero__bar{background:var(--panel);color:var(--muted);border-bottom:1px solid var(--rule);font-size:11px;letter-spacing:.12em}
.hero__bar .tag{background:transparent;color:var(--ink);border:1px solid var(--rule);font-weight:400}
.hero__stage{background:var(--panel);padding-top:64px}
.hero .kicker{color:var(--muted);letter-spacing:.28em}
.hero .kicker:before{background:var(--accent);width:22px;height:1px}
.hero h1{font-family:var(--sans);font-weight:300;font-size:clamp(34px,4.6vw,58px);letter-spacing:.16em;line-height:1;margin-top:16px}
.hero h1 .cn{font-weight:300;font-size:clamp(20px,2.4vw,28px);letter-spacing:.30em;margin-top:20px;color:var(--ink-2)}
.hero .lede{color:var(--ink-2);font-size:14px;line-height:2;max-width:32em}
.dest span{background:transparent;border:1px solid var(--rule);color:var(--ink-2);font-size:11px;letter-spacing:.08em}
.dest span b{color:var(--ink);font-weight:500}
.ticket{border:1px solid var(--rule);background:var(--panel);padding:26px 30px;transform:none;box-shadow:none}
.ticket:before,.ticket:after{background:var(--bg);border-color:var(--rule)}
.ticket .row{border-bottom:1px solid var(--rule);padding:10px 0}
.ticket .k{color:var(--muted);font-size:10px;letter-spacing:.18em}
.ticket .v{font-family:var(--sans);font-weight:400;font-size:12.5px;letter-spacing:.02em}
.ticket .cd{border-top:1px solid var(--rule);margin-top:18px;padding-top:16px}
.ticket .cd .n{font-family:var(--sans);font-weight:300;font-size:40px;letter-spacing:.04em;color:var(--ink)}
.ticket .cd .t{font-size:9px;letter-spacing:.22em;color:var(--muted)}
.route{border-top:1px solid var(--rule);background:var(--panel)}
.route__in:before{height:1px;top:38px;opacity:.55}
.route a{padding:32px 16px 16px;border-right:1px solid var(--rule)}
.route .dot{width:9px;height:9px;border:1px solid var(--c);background:var(--panel);margin-bottom:14px}
.route .code{font-size:9.5px;letter-spacing:.2em;color:var(--muted)}
.route .nm{font-weight:400;font-size:12.5px;letter-spacing:.02em}
.route a:hover{background:#F7F5F0}
.nav{border-bottom:1px solid var(--rule);background:rgba(251,250,247,.94);backdrop-filter:saturate(1.1) blur(6px)}
.nav .brand{font-family:var(--sans);font-weight:500;font-size:12px;letter-spacing:.24em}
.nav a{font-family:var(--sans);font-size:12px;letter-spacing:.12em;color:var(--ink-2);padding:7px 11px;border:0;border-bottom:1px solid transparent}
.nav a:hover{border-color:var(--rule);background:transparent}
.nav a.on{background:transparent;color:var(--ink);border-bottom:1px solid var(--ink)}
.sec-head{border-bottom:1px solid var(--rule);padding-bottom:14px;margin-bottom:34px}
.sec-head .no{color:var(--muted);letter-spacing:.26em;font-size:10px}
.sec-head h2{font-family:var(--sans);font-weight:400;font-size:clamp(20px,2.3vw,26px);letter-spacing:.14em}
.sec-head .sub{font-size:10.5px;letter-spacing:.12em;color:var(--muted)}
.summary{border:1px solid var(--rule);background:var(--panel);margin-top:30px}
.stat{padding:22px 24px;border-right:1px solid var(--rule)}
.stat .k{font-size:9.5px;letter-spacing:.22em;color:var(--muted)}
.stat .v{font-family:var(--sans);font-weight:400;font-size:18px;letter-spacing:.04em;margin-top:8px}
.stat .s{font-size:11.5px;color:var(--muted);margin-top:4px}
.alert{border:1px solid var(--rule);border-left:2px solid var(--d1);background:#FAF7F1;padding:16px 20px;font-size:12.5px;color:var(--ink-2)}
.alert .ico{color:var(--d1);font-weight:400}
.banner{border:1px solid var(--rule);background:#FAF7F1}
.banner strong{color:var(--d1);font-weight:500}
.flight-grid{border:1px solid var(--rule);background:var(--panel)}
.card{border-right:1px solid var(--rule)}
.card__img{height:190px;border-bottom:1px solid var(--rule);filter:saturate(.7) contrast(.96)}
.card__body{padding:22px 24px}
.card h3{font-family:var(--sans);font-weight:400;font-size:16px;letter-spacing:.08em}
.kv{border-bottom:1px solid var(--rule);font-size:12.5px}
.kv b{font-size:9.5px;letter-spacing:.18em;color:var(--muted);font-weight:400}
.day{background:var(--panel);border-top:1px solid var(--rule);padding:72px 0 80px}
.day:nth-child(even){background:var(--bg)}
.day__head{border:1px solid var(--rule);grid-template-columns:150px 1fr}
.day__no{background:transparent;color:var(--ink);border-left:2px solid var(--c);padding:18px 20px;justify-content:flex-start;gap:6px}
.day__no b{font-family:var(--sans);font-weight:300;font-size:44px;letter-spacing:.02em;color:var(--ink)}
.day__no small{font-size:9px;letter-spacing:.2em;color:var(--muted)}
.day__title{padding:18px 24px;background:transparent}
.day__title .date{font-size:10px;letter-spacing:.14em;color:var(--muted);margin-bottom:8px}
.day__title h2{font-family:var(--sans);font-weight:400;font-size:clamp(19px,2.1vw,24px);letter-spacing:.06em;line-height:1.5;margin:0}
.gallery{gap:10px}
.gallery figure{border:1px solid var(--rule);background:#EDEAE2}
.gallery img{filter:saturate(.72) contrast(.96)}
.gallery figcaption{background:rgba(251,250,247,.94);color:var(--ink-2);border-top:1px solid var(--rule);font-size:9.5px;letter-spacing:.1em}
.decision{border:1px solid var(--rule);border-top:2px solid var(--c);background:transparent;padding:16px 20px;font-size:12.5px;color:var(--ink-2)}
.decision b{font-family:var(--sans);font-weight:500;letter-spacing:.12em;color:var(--ink)}
.plans{border:1px solid var(--rule);background:transparent}
.plan-tab{border-right:1px solid var(--rule);font-family:var(--sans);font-size:11.5px;letter-spacing:.14em;padding:9px 22px;color:var(--muted)}
.plan-tab.on{background:#EDEAE2;color:var(--ink)}
.plan-name{font-size:10px;letter-spacing:.14em;color:var(--muted)}
.timeline:before{width:1px;background:var(--c);opacity:.42;top:12px;bottom:12px}
.tl{padding-bottom:28px}
.tl__time{font-size:12.5px;font-weight:400;color:var(--ink-2);letter-spacing:.04em;padding:2px 24px 0 0}
.tl__body{padding-left:22px}
.tl__body:before{left:-6px;top:9px;width:5px;height:5px;background:var(--panel);border:1px solid var(--c);border-radius:50%;box-shadow:none}
.tl__act{font-weight:400;font-size:15px;letter-spacing:.02em;color:var(--ink)}
.tl__tr{color:var(--ink-2);font-size:12px}
.tl__tr .mode{border:1px solid var(--rule);color:var(--muted);font-size:9px;padding:1px 6px;letter-spacing:.14em}
.tl__note{color:var(--muted);font-size:11.5px}
.tag-opt{border:1px solid var(--rule);color:var(--muted);font-size:9px;letter-spacing:.12em}
.meals{gap:16px}
.meal{border:1px solid var(--rule);background:var(--panel);padding:20px 22px}
.meal:before{background:var(--bg);border-color:var(--rule);width:9px;height:9px}
.meal .slot{color:var(--muted);font-size:9.5px;letter-spacing:.24em}
.meal .area{font-family:var(--sans);font-weight:400;font-size:16px;letter-spacing:.08em}
.meal li{border-top:1px solid var(--rule)}
.meal .nm{font-weight:400}
.meal .dt{font-size:11.5px}
.meal .pick{background:transparent;border:1px solid var(--c);color:var(--c);font-size:8.5px;letter-spacing:.14em}
.meal a{color:var(--ink-2);border-bottom:1px solid var(--rule);font-size:10px}
.meal .maps{color:var(--muted);border-bottom:1px dotted var(--rule)}
.table-wrap{border:1px solid var(--rule);background:var(--panel)}
th{background:transparent;color:var(--muted);border-bottom:1px solid var(--ink);font-size:9.5px;letter-spacing:.2em;font-weight:400}
td{border-bottom:1px solid var(--rule);padding:12px 16px}
tbody tr:nth-child(even){background:transparent}
td.fare{color:var(--ink);font-weight:400}
td.note{color:var(--muted)}
.tix{border:1px solid var(--rule);background:var(--panel)}
.tix .t{border-right:1px solid var(--rule);border-bottom:1px solid var(--rule);padding:18px 20px}
.tix .n{font-weight:400;font-size:14px;letter-spacing:.04em}
.tix .d{font-size:11.5px}
.tix a{color:var(--ink-2);border-bottom:1px solid var(--rule)}
.transfer{background:var(--panel);border:1px solid var(--rule);border-left:2px solid var(--d3);padding:16px 18px}
.transfer .t{font-weight:500;font-size:13px}
.transfer .d{font-size:11.5px}
.practices{border:1px solid var(--rule);background:var(--panel)}
.practices div{border-bottom:1px solid var(--rule);font-size:12.5px;padding:12px 18px}
.practices span.n{color:var(--muted)}
.check{background:var(--panel);border:1px solid var(--rule);padding:24px 26px}
.check .bar{height:4px;background:#EAE6DC;border:0}
.check .bar i{background:var(--accent)}
.check label{border-bottom:1px solid var(--rule);font-size:13px}
.check input{accent-color:var(--accent)}
.open .row{border:1px solid var(--rule);background:var(--panel);font-size:12.5px}
.badge{border:1px solid var(--rule);font-size:9.5px;letter-spacing:.12em}
.badge.done{background:transparent;color:var(--d5);border-color:var(--d5)}
.badge.todo{background:transparent;color:var(--d1);border-color:var(--d1)}
.gloss div{border-bottom:1px solid var(--rule);font-size:12.5px}
.gloss .zh{font-size:11px;color:var(--muted)}
footer{background:var(--panel);color:var(--muted);border-top:1px solid var(--rule);font-size:11px}
footer a{color:var(--ink-2)}
footer h4{font-family:var(--sans);font-weight:500;color:var(--ink);font-size:12px;letter-spacing:.14em}
.credits{color:var(--muted);font-size:10px}
@media (max-width:600px){
  .hero h1{font-size:clamp(34px,10vw,44px);letter-spacing:.12em}
  .hero h1 .cn{font-size:20px;letter-spacing:.22em}
  .day__no b{font-size:34px}
  .timeline:before{left:104px}
}

@media (max-width:900px){
  .hero__grid{grid-template-columns:1fr;gap:24px}
  .ticket{transform:none}
  .route__in{grid-template-columns:1fr}
  .route__in:before{display:none}
  .route a{display:grid;grid-template-columns:16px 1fr;gap:2px 10px;padding:11px 14px;border-right:0;border-bottom:1px solid var(--rule)}
  .route a:last-child{border-bottom:0}
  .route .dot{grid-row:1/3;margin:3px 0 0}
  .route .code{grid-column:2}
  .route .nm{grid-column:2;margin-top:0}
  .summary{grid-template-columns:1fr 1fr}
  .stat:nth-child(2){border-right:0}
  .stat:nth-child(1),.stat:nth-child(2){border-bottom:1px solid var(--rule)}
  .flight-grid{grid-template-columns:1fr}
  .card{border-right:0;border-bottom:1px solid var(--ink)}
  .card:last-child{border-bottom:0}
  .day__head{grid-template-columns:96px 1fr}
  .day__no b{font-size:38px}
  .gallery{grid-template-columns:1fr 1fr}
  .gallery figure:first-child{grid-column:1/-1;aspect-ratio:16/9}
  .gallery:has(figure:nth-child(2):last-child) figure:first-child{grid-column:auto;aspect-ratio:4/3}
  .gloss{columns:1}
  footer .grid{grid-template-columns:1fr}
}
@media (max-width:600px){
  :root{--gut:16px}
  section{padding:52px 0}
  .day{padding:44px 0 52px}
  .hero__stage{padding-top:34px}
  .hero h1{font-size:clamp(38px,11vw,52px)}
  .hero h1 .cn{font-size:clamp(26px,7.4vw,34px)}
  .hero__bar .right{display:none}
  .timeline:before{left:104px}
  .tl{grid-template-columns:104px 1fr;gap:0}
  .tl__time{font-size:12px;padding-right:20px}
  .tl__body{padding-left:16px}
  .tl__act{font-size:14.5px}
  .table-wrap{border:0;background:transparent;overflow:visible}
  table{min-width:0}
  thead{display:none}
  tbody tr{display:grid;grid-template-columns:1fr;gap:2px;background:var(--panel);border:1px solid var(--ink);padding:11px 13px;margin-bottom:7px}
  tbody td{border:0;padding:1px 0}
  tbody td:nth-child(1){font-weight:700}
  tbody td:not(:first-child):before{content:attr(data-label);color:var(--muted);font-family:var(--mono);font-size:10px;margin-right:8px}
  tbody td.num{text-align:left}
  td.fare{white-space:normal}
  .tix{grid-template-columns:1fr}
  .tix .t{border-right:0}
}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  *{transition:none!important;animation:none!important}
}
@media print{
  body{background:#fff;font-size:10pt}
  .nav,.hero__bar .right,.plans,.skip-link{display:none}
  .hero{background:#fff;border-bottom:2px solid #000}
  .hero__stage{background:#fff;padding:20px 0}
  .hero__grid{grid-template-columns:1.4fr 1fr}
  .ticket{transform:none;border:1px solid #000}
  .route{display:none}
  section{padding:18px 0}
  .day{background:#fff;border-top:1px solid #000;break-before:page;padding:14px 0}
  .day:first-of-type{break-before:auto}
  .gallery{display:none}
  .tl,.meal,.transfer,.check,.card,.tix .t{break-inside:avoid}
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
  <div class="hero__bar"><div class="wrap">
    <span class="tag">KIX → OSAKA</span>
    <span>IT210　TPE 06:55 ／ KIX 10:25</span>
    <span class="right">2027.02.02 TUE　·　2 ADULTS + 1 CHILD</span>
  </div></div>
  <div class="hero__stage"><div class="wrap">
    <div class="hero__grid">
      <div class="hero__main">
        <div class="kicker">KANSAI · JAPAN · FAMILY TRIP</div>
        <h1>KANSAI<span class="cn">關西 2027</span></h1>
        <p class="lede">大阪梅田為基地，一天京都、一天神戶、一天宇治。<span class="nw">二大一小</span>，<span class="nw">慢慢走、好好吃。</span></p>
        <div class="dest">
          <span><b>5</b> DAYS</span>
          <span><b>4</b> NIGHTS</span>
          <span>梅田 <b>⇄</b> 京都</span>
          <span>梅田 <b>⇄</b> 神戶</span>
          <span>梅田 <b>⇄</b> 宇治</span>
        </div>
      </div>
      <div class="ticket">
        <div class="row"><span class="k">DATES</span><span class="v">2027.02.02 — 02.06</span></div>
        <div class="row"><span class="k">PARTY</span><span class="v">2 大 1 小（7 歲）</span></div>
        <div class="row"><span class="k">FLIGHT</span><span class="v">IT210 / IT213</span></div>
        <div class="row"><span class="k">BASE</span><span class="v">大阪梅田</span></div>
        <div class="row"><span class="k">HOTEL</span><span class="v">阪急龍仕柏大阪</span></div>
        <div class="cd"><div class="n" id="cd">—</div><div class="t">DAYS<br>TO GO</div></div>
      </div>
    </div>
    <div class="route"><div class="route__in">
      <a href="#day-1" style="--c:#D91E2A"><div class="dot" style="--c:#D91E2A"></div><div class="code">D1 · 02.02</div><div class="nm">抵達 · 臨空城 · 梅田</div></a>
      <a href="#day-2" style="--c:#E85D04"><div class="dot" style="--c:#E85D04"></div><div class="code">D2 · 02.03</div><div class="nm">大阪 · USJ 或市區</div></a>
      <a href="#day-3" style="--c:#0072BC"><div class="dot" style="--c:#0072BC"></div><div class="code">D3 · 02.04</div><div class="nm">京都 · 宇治</div></a>
      <a href="#day-4" style="--c:#6F2C91"><div class="dot" style="--c:#6F2C91"></div><div class="code">D4 · 02.05</div><div class="nm">神戶 · 光之祭典</div></a>
      <a href="#day-5" style="--c:#00875A"><div class="dot" style="--c:#00875A"></div><div class="code">D5 · 02.06</div><div class="nm">梅田採買 · 返程</div></a>
    </div></div>
  </div></div>
</header>

<nav class="nav"><div class="nav__in wrap" id="nav"></div></nav>

<section id="overview"><div class="wrap">
  <div class="sec-head"><span class="no">OVERVIEW</span><h2>行程總覽</h2><span class="sub">5 天 · 4 夜 · 大阪進出</span></div>
  <div class="summary" id="summary"></div>
  <div class="alerts" id="alerts"></div>
  <div class="sec-head" style="margin-top:46px"><span class="no">FLIGHTS</span><h2>航班與住宿</h2><span class="sub">2026-11 查核 · 出發前重查</span></div>
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
  <div class="table-wrap"><table><thead><tr><th>區間</th><th>方式</th><th>所需時間</th><th class="num">大人</th><th class="num">小孩</th><th>備註</th></tr></thead><tbody id="transport-body"></tbody></table></div>
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
nav.appendChild(el("span","brand","KANSAI 2027"));
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
  if(active && window.innerWidth<900){
    const r=active.getBoundingClientRect(), c=document.querySelector("#nav").getBoundingClientRect();
    if(r.left<c.left+6 || r.right>c.right-6){ active.scrollIntoView({inline:"center", block:"nearest", behavior:"smooth"}); }
  }
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
  const cfg = Object.assign({}, (DATA.meta.days||{})[String(d.no)] || {});
  cfg.code = (DATA.meta.day_codes||{})[String(d.no)];
  sec.style.setProperty("--c", (DATA.meta.day_colors||{})[String(d.no)] || "var(--ink)");
  const gallery = (cfg.gallery||[]).map(slug=>`<figure><img src="assets/img/${slug}.jpg" alt="${esc((DATA.meta.captions||{})[slug]||"")}" loading="lazy"><figcaption>${esc((DATA.meta.captions||{})[slug] || (DATA.credits[slug]||{}).title || "")}</figcaption></figure>`).join("");
  sec.innerHTML = `
    <div class="wrap">
      <div class="day__head">
        <div class="day__no"><b>0${d.no}</b><small>${cfg.code||"D"+d.no} · DAY ${d.no}</small></div>
        <div class="day__title"><div class="date">${esc(d.date)}（${esc(d.weekday)}）${cfg.label? "　/　"+esc(cfg.label):""}</div><h2>${esc(d.title)}</h2></div>
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
    mealsHost.appendChild(el("div","sec-head","<span class=\"no\">EAT</span><h2 style=\"font-size:22px\">用餐安排</h2>"));
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
DATA.transport.forEach(r=>{
  const m=String(r.fare||"").match(/^\s*(¥?[\d,]+)\s*[／/]\s*(¥?[\d,]+)\s*(.*)$/);
  const a=m?m[1]:(r.fare||"—"), c=m?m[2]:"—", note=m?m[3].replace(/^[（(]|[）)]$/g,""):"";
  const tr=el("tr");
  tr.innerHTML=`<td data-label="區間">${esc(r.from)}</td><td data-label="方式">${esc(r.way)}</td><td data-label="所需時間">${esc(r.time)}</td><td class="fare num" data-label="大人">${esc(a)}</td><td class="fare num" data-label="小孩">${esc(c)}</td><td class="note" data-label="備註">${esc(note)}</td>`;
  tb.appendChild(tr);
});

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
