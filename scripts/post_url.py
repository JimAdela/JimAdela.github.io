#!/usr/bin/env python3
"""根据文章文件名推算 GitHub Pages 上的真实 URL（permalink: pretty）。"""
import re
import sys
from pathlib import Path
from urllib.parse import quote

if len(sys.argv) < 2:
    print("用法: python scripts/post_url.py _posts/2026-10-25-foo.md")
    sys.exit(2)

name = Path(sys.argv[1]).name
m = re.match(r"^(\d{4})-(\d{2})-(\d{2})-(.+)\.md$", name)
if not m:
    print("文件名不符合 YYYY-MM-DD-slug.md")
    sys.exit(2)

year, month, day, slug = m.groups()
print(f"https://jimadela.github.io/{year}/{month}/{day}/{quote(slug)}/")
