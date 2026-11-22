#!/usr/bin/env python3
"""Downscale site photos for the web (keeps originals in assets/_orig)."""
import pathlib
import shutil

from PIL import Image

IMG = pathlib.Path(__file__).parent / "assets" / "img"
ORIG = pathlib.Path(__file__).parent / "assets" / "_orig"
ORIG.mkdir(parents=True, exist_ok=True)

MAX_W = {"hero.jpg": 2000}
DEFAULT_W = 1500
QUALITY = 82

total_before = total_after = 0
for src in sorted(IMG.glob("*.jpg")):
    before = src.stat().st_size
    total_before += before
    backup = ORIG / src.name
    if not backup.exists():
        shutil.copy2(src, backup)
    with Image.open(backup) as im:
        im = im.convert("RGB")
        max_w = MAX_W.get(src.name, DEFAULT_W)
        if im.width > max_w:
            h = round(im.height * max_w / im.width)
            im = im.resize((max_w, h), Image.LANCZOS)
        im.save(src, "JPEG", quality=QUALITY, optimize=True, progressive=True)
    after = src.stat().st_size
    total_after += after
    print(f"{src.name:16s} {before//1024:5d} -> {after//1024:4d} KB")

print(f"\nTOTAL {total_before//1024//1024} MB -> {total_after//1024//1024} MB")
