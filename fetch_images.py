#!/usr/bin/env python3
"""Fetch landscape photos for the Kansai 2027 site from Wikimedia Commons.

Each entry: slug -> search phrase. Picks the first landscape (w/h >= 1.3) result,
downloads a 1800px thumbnail, and records attribution in assets/img/credits.json.
"""
import json
import pathlib
import urllib.parse
import urllib.request

OUT = pathlib.Path(__file__).parent / "assets" / "img"
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "HermesTravelSite/1.0 (personal itinerary site)"}

TARGETS = {
    "hero": "Kiyomizu-dera Kyoto sunset",
    "kix": "Kansai International Airport terminal interior",
    "rinku": "Rinku Premium Outlets",
    "umeda": "Osaka Station Umeda skyline",
    "usj": "Universal Studios Japan globe",
    "dotonbori": "Dotonbori night Osaka",
    "kinkakuji": "Kinkaku-ji golden pavilion",
    "kiyomizu": "Kiyomizu-dera stage",
    "byodoin": "Byodo-in Phoenix Hall Uji",
    "uji": "Uji river Kyoto",
    "kitano": "Kitano Ijinkan Kobe",
    "luminarie": "Kobe Luminarie",
    "meriken": "Meriken Park Kobe Port Tower",
    "osaka_castle": "Osaka Castle",
    "okonomiyaki": "Okonomiyaki",
    "matcha": "Matcha tea Uji",
    "kobe_beef": "Kobe beef steak",
    "takoyaki": "Takoyaki",
}


def api(params):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    return json.load(urllib.request.urlopen(req, timeout=30))


def pick(query):
    data = api({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": "6", "gsrlimit": "12",
        "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": "1800",
    })
    pages = list((data.get("query") or {}).get("pages", {}).values())
    pages.sort(key=lambda p: p.get("index", 99))
    best = None
    for p in pages:
        ii = p["imageinfo"][0]
        w, h = ii["width"], ii["height"]
        if w / max(h, 1) < 1.3 or w < 1600:
            continue
        meta = ii.get("extmetadata", {})
        artist = meta.get("Artist", {}).get("value", "").replace("\n", " ")
        # strip html tags crudely
        import re
        artist = re.sub("<[^>]+>", "", artist).strip()
        best = {
            "title": p["title"], "url": ii.get("thumburl") or ii["url"],
            "w": w, "h": h,
            "artist": artist[:120],
            "license": meta.get("LicenseShortName", {}).get("value", ""),
            "page": ii.get("descriptionurl", ""),
        }
        break
    return best


def main():
    credits = {}
    for slug, query in TARGETS.items():
        try:
            info = pick(query)
            if not info:
                print(f"[miss] {slug}: no landscape result for {query!r}")
                continue
            dest = OUT / f"{slug}.jpg"
            req = urllib.request.Request(info["url"], headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
                f.write(r.read())
            credits[slug] = info
            print(f"[ok] {slug:14s} {dest.stat().st_size//1024:5d} KB  {info['title'][:60]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[err] {slug}: {exc}")
    (OUT / "credits.json").write_text(json.dumps(credits, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(credits)}/{len(TARGETS)} images -> {OUT}")


if __name__ == "__main__":
    main()
