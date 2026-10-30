# -*- coding: utf-8 -*-
"""Generate a static preview of the places page. Not part of the Jekyll site."""
import json
import math
import os

SRC = os.path.join(os.path.dirname(__file__), "_china.json")
OUT = os.path.join(os.path.dirname(__file__), "places.html")

W, H, PAD = 1200, 820, 48


def merc(lng, lat):
    lat = max(min(lat, 85.0), -85.0)
    x = math.radians(lng)
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def perp(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    end = len(pts) - 1
    dmax, idx = 0.0, 0
    for i in range(1, end):
        d = perp(pts[i], pts[0], pts[end])
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        left = rdp(pts[: idx + 1], eps)
        right = rdp(pts[idx:], eps)
        return left[:-1] + right
    return [pts[0], pts[end]]


data = json.load(open(SRC, encoding="utf-8"))
polys = data["features"][0]["geometry"]["coordinates"]

raw = []
for poly in polys:
    ring_ll = poly[0]
    # 海南以南的碎岛不画，避免南海变成一段段短线
    if max(p[1] for p in ring_ll) < 18.05:
        continue
    raw.append([merc(p[0], p[1]) for p in ring_ll])

# 图幅收到大陆、海南和台湾，行程才够大。更南的岛礁留在视口外。
frame = (73.2, 135.2, 17.7, 53.7)
corners = [merc(frame[0], frame[2]), merc(frame[1], frame[3])]
minx, maxx = corners[0][0], corners[1][0]
miny, maxy = corners[0][1], corners[1][1]
spanx, spany = maxx - minx, maxy - miny
scale = min((W - 2 * PAD) / spanx, (H - 2 * PAD) / spany)
ox = (W - spanx * scale) / 2
oy = (H - spany * scale) / 2


def proj_xy(x, y):
    return ox + (x - minx) * scale, oy + (maxy - y) * scale


def proj(lng, lat):
    return proj_xy(*merc(lng, lat))


paths = []
kept = 0
for ring in raw:
    pts = [proj_xy(x, y) for x, y in ring]
    simple = rdp(pts, 0.4)
    if len(simple) < 4:
        continue
    # drop specks smaller than a couple of pixels, keep island chains
    bw = max(p[0] for p in simple) - min(p[0] for p in simple)
    bh = max(p[1] for p in simple) - min(p[1] for p in simple)
    if bw < 1.2 and bh < 1.2:
        continue
    kept += len(simple)
    d = ["M{:.1f},{:.1f}".format(*simple[0])]
    for x, y in simple[1:]:
        d.append("L{:.1f},{:.1f}".format(x, y))
    d.append("Z")
    paths.append("".join(d))

land = "".join(paths)

# Chronological, so the line draws in time order.
# Offsets are label anchors in viewBox units. Tuned after a first projection.
places = [
    {
        "id": "dali",
        "name": "大理",
        "year": 2016,
        "lng": 100.17,
        "lat": 25.59,
        "lx": -14,
        "ly": 5,
        "anchor": "end",
        "note": "示意一句。换成你当时写下的。",
        "home": False,
    },
    {
        "id": "chengdu",
        "name": "成都",
        "year": 2017,
        "lng": 104.07,
        "lat": 30.67,
        "lx": -14,
        "ly": 18,
        "anchor": "end",
        "note": "示意一句。换成你当时写下的。",
        "home": False,
    },
    {
        "id": "xian",
        "name": "西安",
        "year": 2018,
        "lng": 108.94,
        "lat": 34.34,
        "lx": 0,
        "ly": -18,
        "anchor": "middle",
        "note": "示意一句。换成你当时写下的。",
        "home": False,
    },
    {
        "id": "qingdao",
        "name": "青岛",
        "year": 2019,
        "lng": 120.38,
        "lat": 36.07,
        "lx": 34,
        "ly": 22,
        "anchor": "start",
        "note": "示意一句。换成你当时写下的。",
        "home": False,
    },
    {
        "id": "hangzhou",
        "name": "杭州",
        "year": 2021,
        "lng": 120.15,
        "lat": 30.25,
        "lx": 16,
        "ly": 16,
        "anchor": "start",
        "note": "示意一句。换成你当时写下的。",
        "home": False,
    },
    {
        "id": "xiamen",
        "name": "厦门",
        "year": 2023,
        "lng": 118.09,
        "lat": 24.48,
        "lx": 14,
        "ly": 18,
        "anchor": "start",
        "note": "示意一句。换成你当时写下的。",
        "home": False,
    },
    {
        "id": "hefei",
        "name": "合肥",
        "year": 2026,
        "lng": 117.28,
        "lat": 31.86,
        "lx": -14,
        "ly": -16,
        "anchor": "end",
        "note": "现在住在这里。",
        "home": True,
    },
]

for p in places:
    x, y = proj(p["lng"], p["lat"])
    p["x"] = round(x, 1)
    p["y"] = round(y, 1)

route = "M{:.1f},{:.1f}".format(places[0]["x"], places[0]["y"])
for p in places[1:]:
    route += "L{:.1f},{:.1f}".format(p["x"], p["y"])


def scene(kind, seed):
    """Ink-wash stand-in, not a photograph."""
    a = 40 + (seed * 37) % 80
    b = 90 + (seed * 53) % 70
    c = 150 + (seed * 29) % 90
    if kind == 0:
        return (
            '<svg viewBox="0 0 320 200" aria-hidden="true">'
            '<rect width="320" height="200" fill="var(--wash)"/>'
            '<circle cx="{0}" cy="54" r="14" fill="var(--mark)" opacity="0.85"/>'
            '<path d="M0,150 L{1},92 L160,138 L{2},78 L320,146 L320,200 L0,200 Z" fill="var(--ink)" opacity="0.10"/>'
            '<path d="M0,168 L70,120 L150,156 L230,108 L320,160 L320,200 L0,200 Z" fill="var(--ink)" opacity="0.22"/>'
            "</svg>"
        ).format(a + 180, b, c + 40)
    if kind == 1:
        return (
            '<svg viewBox="0 0 320 200" aria-hidden="true">'
            '<rect width="320" height="200" fill="var(--wash)"/>'
            '<rect y="108" width="320" height="92" fill="var(--ink)" opacity="0.06"/>'
            '<path d="M0,108 H320" stroke="var(--ink)" stroke-width="1" opacity="0.45"/>'
            '<path d="M24,132 H{0} M40,150 H200 M18,170 H120" stroke="var(--press)" stroke-width="1" opacity="0.55"/>'
            '<circle cx="{1}" cy="48" r="16" fill="none" stroke="var(--ink)" stroke-width="1" opacity="0.4"/>'
            "</svg>"
        ).format(80 + a, 240 - (seed % 5) * 12)
    return (
        '<svg viewBox="0 0 320 200" aria-hidden="true">'
        '<rect width="320" height="200" fill="var(--wash)"/>'
        '<rect x="18" y="24" width="70" height="176" fill="var(--ink)" opacity="0.08"/>'
        '<rect x="210" y="16" width="92" height="184" fill="var(--ink)" opacity="0.12"/>'
        '<rect x="{0}" y="78" width="22" height="28" fill="var(--mark)" opacity="0.8"/>'
        '<path d="M0,176 H320" stroke="var(--ink)" stroke-width="1" opacity="0.3"/>'
        "</svg>"
    ).format(120 + (seed % 4) * 16)


for i, p in enumerate(places):
    shots = []
    for k in range(3):
        shots.append(
            {
                "svg": scene(k, i + k * 3),
                "caption": "照片位 {}".format(k + 1),
            }
        )
    p["shots"] = shots

# JSON for the client. Drop nothing.
client = []
for p in places:
    client.append(
        {
            "id": p["id"],
            "name": p["name"],
            "year": p["year"],
            "note": p["note"],
            "home": p["home"],
            "shots": p["shots"],
        }
    )

pins = []
for p in places:
    pins.append(
        '<g class="pin" data-id="{id}" transform="translate({x},{y})">'
        '<circle class="pin-hit" r="16"/>'
        '<circle class="pin-ring" r="9"/>'
        '<circle class="pin-dot" r="4.2"/>'
        '<text class="pin-name" x="{lx}" y="{ly}" text-anchor="{anchor}">{name}</text>'
        "</g>".format(**p)
    )

rows = []
for p in sorted(places, key=lambda item: item["year"], reverse=True):
    tag = "现在住在这里" if p["home"] else "示意"
    rows.append(
        '<li><button type="button" data-id="{id}">'
        '<time>{year}</time><strong>{name}</strong>'
        '<span>{tag}</span></button></li>'.format(tag=tag, **p)
    )

html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>预览 · 足迹</title>
<link rel="stylesheet" href="../../css/paper.css">
<style>
  .stage {{
    position: relative;
    height: calc(100vh - 65px);
    min-height: 560px;
    background: var(--paper);
    overflow: hidden;
    scroll-margin-top: 65px;
  }}
  .atlas {{
    display: block;
    width: 100%;
    height: 100%;
  }}
  .land {{
    fill: var(--wash);
    stroke: var(--ink);
    stroke-width: 0.8;
    stroke-linejoin: round;
    opacity: 0.9;
  }}
  .route {{
    fill: none;
    stroke: var(--press);
    stroke-width: 1.7;
    stroke-linecap: round;
    stroke-linejoin: round;
    opacity: 0.9;
  }}
  .pin {{ cursor: pointer; }}
  .pin-hit {{ fill: transparent; }}
  .pin-dot {{ fill: var(--press); }}
  .pin-ring {{
    fill: none;
    stroke: var(--press);
    stroke-width: 1;
    opacity: 0;
  }}
  .pin.is-on .pin-dot {{ fill: var(--mark); }}
  .pin.is-on .pin-ring {{
    stroke: var(--mark);
    opacity: 1;
  }}
  .pin-name {{
    font-family: var(--serif);
    font-size: 15px;
    fill: var(--ink);
    stroke: var(--paper);
    stroke-width: 4px;
    paint-order: stroke;
    pointer-events: none;
  }}
  .pin.is-on .pin-name {{ fill: var(--mark); }}
  .stage-note {{
    position: absolute;
    left: 28px;
    bottom: 22px;
    margin: 0;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--ink-2);
  }}
  .place-panel {{
    position: absolute;
    top: 28px;
    right: 28px;
    width: 340px;
    max-height: calc(100% - 56px);
    overflow: auto;
    background: var(--paper);
    border: 1px solid var(--rule);
    padding: 22px 22px 18px;
    box-shadow: 0 18px 40px rgba(22, 25, 26, 0.06);
  }}
  .place-panel h2 {{
    font-family: var(--serif);
    font-size: 32px;
    font-weight: 600;
    line-height: 1.2;
    margin: 8px 0 12px;
  }}
  .place-panel .note {{
    margin: 0 0 18px;
    color: var(--ink-2);
    font-size: 15px;
    line-height: 1.7;
  }}
  .panel-close {{
    position: absolute;
    top: 16px;
    right: 16px;
    border: 0;
    background: transparent;
    color: var(--ink-2);
    font-size: 12px;
    letter-spacing: 0.08em;
    cursor: pointer;
    font-family: var(--sans);
  }}
  .panel-close:hover {{ color: var(--ink); }}
  .film {{
    display: flex;
    gap: 10px;
    overflow-x: auto;
    scroll-snap-type: x mandatory;
    padding-bottom: 4px;
    margin: 0 -4px;
    scrollbar-width: thin;
    scrollbar-color: var(--rule) transparent;
  }}
  .shot {{
    flex: none;
    width: 210px;
    margin: 0;
    scroll-snap-align: start;
  }}
  .shot svg {{
    display: block;
    width: 100%;
    height: 132px;
    border: 1px solid var(--rule);
  }}
  .shot figcaption {{
    margin-top: 6px;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.06em;
    color: var(--ink-2);
  }}
  .ledger {{
    max-width: var(--measure);
    margin: 0 auto;
    padding: 72px 0 96px;
  }}
  .ledger h2 {{
    font-family: var(--serif);
    font-size: 28px;
    font-weight: 600;
    margin: 8px 0 28px;
  }}
  .ledger ol {{
    list-style: none;
    margin: 0;
    padding: 0;
    border-top: 1px solid var(--rule);
  }}
  .ledger li {{ margin: 0; }}
  .ledger button {{
    width: 100%;
    display: grid;
    grid-template-columns: 4.2em 6em 1fr;
    gap: 12px;
    align-items: baseline;
    text-align: left;
    background: transparent;
    border: 0;
    border-bottom: 1px solid var(--rule-2);
    padding: 14px 0;
    color: var(--ink);
    font: inherit;
    cursor: pointer;
  }}
  .ledger button:hover strong {{ color: var(--press); }}
  .ledger button.is-on strong {{ color: var(--mark); }}
  .ledger time {{
    font-family: var(--mono);
    font-size: 13px;
    color: var(--ink-2);
  }}
  .ledger strong {{
    font-family: var(--serif);
    font-weight: 600;
    font-size: 18px;
  }}
  .ledger span {{
    color: var(--ink-2);
    font-size: 14px;
  }}
  @media (max-width: 720px) {{
    .place-panel {{
      top: auto;
      right: 0;
      left: 0;
      bottom: 0;
      width: auto;
      max-height: 46vh;
      border-left: 0;
      border-right: 0;
      border-bottom: 0;
    }}
    .stage-note {{ left: 16px; bottom: auto; top: 16px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .route {{ stroke-dashoffset: 0 !important; }}
  }}
</style>
</head>
<body>
<header class="masthead">
  <div class="masthead-in">
    <a class="site-mark" href="#">无意 Blog</a>
    <nav class="site-nav" aria-label="主导航">
      <a href="#">首页</a>
      <a href="#">系列</a>
      <a href="#">书影</a>
      <a href="#">诗</a>
      <a href="#" class="is-on" aria-current="page">足迹</a>
      <a href="#">标签</a>
      <a class="nav-about" href="#">关于</a>
      <a href="#">搜索</a>
    </nav>
  </div>
  <div class="readbar"><i style="width:0"></i></div>
</header>

<main id="main">
  <section class="stage" aria-label="足迹地图">
    <svg class="atlas" viewBox="0 0 {W} {H}" role="img" aria-label="示意地图">
      <path class="land" d="{land}"/>
      <path class="route" id="route" d="{route}"/>
      {pins}
    </svg>
    <p class="stage-note">版式预览 · 除合肥外都是示意</p>
    <aside class="place-panel" id="panel" aria-live="polite">
      <button class="panel-close" type="button" id="panel-close">关闭</button>
      <p class="kicker" id="panel-kicker"></p>
      <h2 id="panel-name"></h2>
      <p class="note" id="panel-note"></p>
      <div class="film" id="panel-film"></div>
    </aside>
  </section>

  <div class="wrap">
    <section class="ledger" aria-label="按年份">
      <p class="kicker">Places</p>
      <h2>按年份</h2>
      <ol>
        {rows}
      </ol>
    </section>
  </div>
</main>

<footer class="colophon">
  <div class="colophon-in">
    <span>&copy; 2026 无意 Blog</span>
    <span>预览，尚未上线</span>
  </div>
</footer>

<script>
var PLACES = {client};
var byId = {{}};
PLACES.forEach(function (p) {{ byId[p.id] = p; }});

var route = document.getElementById("route");
var len = route.getTotalLength();
var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
if (!reduce) {{
  route.style.strokeDasharray = len;
  route.style.strokeDashoffset = len;
  route.getBoundingClientRect();
  route.style.transition = "stroke-dashoffset 1.3s ease";
  route.style.strokeDashoffset = "0";
}}

function render(id) {{
  var p = byId[id];
  if (!p) return;
  document.querySelectorAll(".pin").forEach(function (el) {{
    el.classList.toggle("is-on", el.getAttribute("data-id") === id);
  }});
  document.querySelectorAll(".ledger button").forEach(function (el) {{
    el.classList.toggle("is-on", el.getAttribute("data-id") === id);
  }});
  document.getElementById("panel-kicker").textContent = p.home
    ? (p.year + "  ·  现在")
    : (p.year + "  ·  示意");
  document.getElementById("panel-name").textContent = p.name;
  document.getElementById("panel-note").textContent = p.note;
  document.getElementById("panel-film").innerHTML = p.shots.map(function (s) {{
    return '<figure class="shot">' + s.svg + '<figcaption>' + s.caption + '</figcaption></figure>';
  }}).join("");
  document.getElementById("panel").hidden = false;
}}

document.querySelector(".atlas").addEventListener("click", function (e) {{
  var pin = e.target.closest(".pin");
  if (pin) render(pin.getAttribute("data-id"));
}});

document.querySelector(".ledger").addEventListener("click", function (e) {{
  var btn = e.target.closest("button");
  if (!btn) return;
  render(btn.getAttribute("data-id"));
  document.querySelector(".stage").scrollIntoView({{ behavior: reduce ? "auto" : "smooth" }});
}});

document.getElementById("panel-close").addEventListener("click", function () {{
  document.getElementById("panel").hidden = true;
  document.querySelectorAll(".is-on").forEach(function (el) {{ el.classList.remove("is-on"); }});
}});

render("hefei");
</script>
</body>
</html>
""".format(
    W=W,
    H=H,
    land=land,
    route=route,
    pins="\n      ".join(pins),
    rows="\n        ".join(rows),
    client=json.dumps(client, ensure_ascii=False),
)

# The template doubled braces for .format, but the CSS/JS already uses doubled
# braces only where .format would eat them. client/pins are inserted via format.
open(OUT, "w", encoding="utf-8", newline="\n").write(html)
print("wrote", OUT, "bytes", os.path.getsize(OUT), "land pts", kept)
for p in places:
    print(p["name"], p["x"], p["y"])
