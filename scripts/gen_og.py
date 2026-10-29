#!/usr/bin/env python3
"""生成分享卡（1200×630 PNG）并写出 _data/og.yml 映射。

用法：
    python scripts/gen_og.py              # 全量重生成
    python scripts/gen_og.py --only 2026-10-29   # 只生成文件名含该串的文章
    python scripts/gen_og.py --pages      # 顺带生成站点卡 site.png

为什么要预生成而不是运行时拼：OG 协议只认位图，不认 SVG，
GitHub Pages 又没有构建期生成图片的能力，所以卡片只能本地渲染好再提交。

依赖：本机 Chrome（路径见 CHROME）。渲染出的字形是截图像素，
不依赖读者机器上有没有装思源宋体。
"""
import hashlib
import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "_posts"
OUT_DIR = ROOT / "img" / "og"
DATA_FILE = ROOT / "_data" / "og.yml"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

SERIES = {}
for item in yaml.safe_load(io.open(ROOT / "_data" / "series.yml", encoding="utf-8").read()):
    SERIES[item["id"]] = item["name"]

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 1200px; height: 630px; }
  body {
    background: #FCFBF8; color: #16191A; overflow: hidden;
    padding: 62px 76px 54px;
    display: flex; flex-direction: column;
    font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .top { display: flex; justify-content: space-between; align-items: baseline; }
  .mark { font-family: "Noto Serif SC", "Songti SC", "SimSun", serif; font-size: 24px; font-weight: 600; }
  .kicker { font-size: 17px; letter-spacing: .04em; color: #1E5B4E; }
  .mid { flex: 1; display: flex; flex-direction: column; justify-content: center; padding-bottom: 26px; }
  .title {
    font-family: "Noto Serif SC", "Songti SC", "SimSun", serif;
    font-size: {TITLE_SIZE}px; font-weight: 600; line-height: 1.26; letter-spacing: -0.01em;
    max-height: {TITLE_MAXH}px; overflow: hidden;
  }
  .sub {
    margin-top: 24px; font-size: 24px; line-height: 1.55; color: #5C6462;
    max-width: 940px;
  }
  .foot {
    display: flex; justify-content: space-between; align-items: baseline;
    border-top: 1px solid #DCD8CE; padding-top: 16px;
    font-size: 16px; letter-spacing: .03em; color: #5C6462;
  }
  .foot .date { font-family: "JetBrains Mono", Consolas, monospace; font-size: 15px; }
  .foot .tags { color: #1E5B4E; }
</style></head>
<body>
  <div class="top">
    <span class="mark">无意 Blog</span>
    <span class="kicker">{KICKER}</span>
  </div>
  <div class="mid">
    <div class="title">{TITLE}</div>
    <div class="sub">{SUB}</div>
  </div>
  <div class="foot">
    <span class="date">{FOOT_LEFT}</span>
    <span class="tags">{FOOT_RIGHT}</span>
  </div>
</body></html>
"""


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def read_front_matter(path):
    text = io.open(path, encoding="utf-8").read()
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    return yaml.safe_load(parts[1]) or {}


def fit_title(title, max_size=70):
    """按字数估行数与字号：CJK 按 1em 宽，尽量占满 1048px 的三行。"""
    n = sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in title) or 1
    width = 1048.0
    for lines in (1, 2, 3):
        size = min(max_size, int(width / (n / lines)))
        if size * 1.26 * lines <= 246:
            return size, int(size * 1.26 * lines) + 2
    size = int(246 / (1.26 * 3))
    return size, 248


def card_html(fm, kicker, foot_left, foot_right):
    size, maxh = fit_title(fm.get("title", ""))
    return (TEMPLATE
            .replace("{TITLE_SIZE}", str(size))
            .replace("{TITLE_MAXH}", str(maxh))
            .replace("{KICKER}", esc(kicker))
            .replace("{TITLE}", esc(fm.get("title", "")))
            .replace("{SUB}", esc(fm.get("subtitle", "")))
            .replace("{FOOT_LEFT}", esc(foot_left))
            .replace("{FOOT_RIGHT}", esc(foot_right)))


def shoot(html, out_png):
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        tmp = f.name
    subprocess.run([
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--window-size=1200,630",
        "--virtual-time-budget=1500",
        "--screenshot=" + str(out_png), "file:///" + tmp.replace("\\", "/"),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    Path(tmp).unlink(missing_ok=True)


def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mapping = {}
    made = 0

    for post in sorted(POSTS.glob("*.md")):
        rel = "_posts/" + post.name
        if only and only not in post.name:
            continue
        fm = read_front_matter(post)
        if not fm.get("title"):
            continue

        sid = fm.get("series")
        if sid and SERIES.get(sid):
            kicker = SERIES[sid] + " · 第 %s 章" % fm.get("chapter", "")
        else:
            kicker = str(fm.get("date", ""))

        tags = " ".join((fm.get("tags") or [])[:3])
        digest = hashlib.md5(rel.encode("utf-8")).hexdigest()[:10]
        name = "og-%s.png" % digest

        html = card_html(fm, kicker, str(fm.get("date", "")), tags)
        shoot(html, OUT_DIR / name)
        mapping[rel] = "/img/og/" + name
        made += 1
        print("  %-52s -> %s" % (post.name[:52], name))

    if "--pages" in sys.argv or not only:
        site = {"title": "无意 Blog", "subtitle": "AI 数学直觉与后端工程实战"}
        shoot(card_html(site, "Personal Blog", "jimadela.github.io", "数学 · 工程"),
              OUT_DIR / "site.png")
        mapping["index.html"] = "/img/og/site.png"
        for page in ["tags.html", "series.html", "about.html", "search.html"]:
            p = ROOT / page
            if p.exists():
                mapping[page] = "/img/og/site.png"
        print("  site.png")

    if mapping:
        lines = ["# 分享卡映射：由 scripts/gen_og.py 生成，不要手改"]
        for k in sorted(mapping):
            lines.append('"%s": %s' % (k, mapping[k]))
        io.open(DATA_FILE, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    print("生成 %d 张" % made)


if __name__ == "__main__":
    main()
