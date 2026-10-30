# -*- coding: utf-8 -*-
"""Turn the places preview into a scroll-driven story. Keeps the map paths."""
import pathlib

path = pathlib.Path(__file__).with_name("places.html")
html = path.read_text(encoding="utf-8")

style = r"""<style>
  .stage {
    position: fixed;
    top: 65px;
    left: 0;
    right: 0;
    height: calc(100vh - 65px);
    background: var(--paper);
    overflow: hidden;
    z-index: 1;
  }
  .atlas { display: block; width: 100%; height: 100%; }
  .land {
    fill: var(--wash);
    stroke: var(--ink);
    stroke-width: 0.8;
    stroke-linejoin: round;
  }
  .route-ghost {
    fill: none;
    stroke: var(--rule);
    stroke-width: 1.4;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-dasharray: 3 7;
  }
  .route {
    fill: none;
    stroke: var(--press);
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .pin { cursor: pointer; opacity: 0.28; }
  .pin.is-past, .pin.is-on { opacity: 1; }
  .pin-hit { fill: transparent; }
  .pin-dot { fill: var(--press); }
  .pin-ring {
    fill: none;
    stroke: var(--press);
    stroke-width: 1;
    opacity: 0;
  }
  .pin.is-on .pin-dot { fill: var(--mark); }
  .pin.is-on .pin-ring { stroke: var(--mark); opacity: 1; }
  .pin-name {
    font-family: var(--serif);
    font-size: 15px;
    fill: var(--ink);
    stroke: var(--paper);
    stroke-width: 4px;
    paint-order: stroke;
    pointer-events: none;
  }
  .pin.is-on .pin-name { fill: var(--mark); }
  .stage-note, .stage-index {
    position: absolute;
    margin: 0;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--ink-2);
  }
  .stage-note { left: 28px; bottom: 22px; }
  .stage-index { left: 28px; top: 22px; }
  .rail {
    position: absolute;
    left: 28px;
    top: 50%;
    transform: translateY(-50%);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .rail button {
    border: 0;
    background: transparent;
    padding: 0;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.06em;
    color: var(--ink-2);
    cursor: pointer;
    text-align: left;
  }
  .rail button.is-on { color: var(--mark); }
  .story {
    position: relative;
    z-index: 2;
    pointer-events: none;
  }
  .stop {
    height: calc(100vh - 65px);
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 32px 36px 32px 42%;
    box-sizing: border-box;
    scroll-snap-align: start;
    scroll-margin-top: 65px;
  }
  .stop-card {
    pointer-events: auto;
    width: min(360px, 100%);
    background: var(--paper);
    border: 1px solid var(--rule);
    padding: 22px 22px 18px;
    opacity: 0.4;
    transition: opacity 0.35s ease;
  }
  .stop.is-on .stop-card { opacity: 1; }
  .stop-card h2 {
    font-family: var(--serif);
    font-size: 32px;
    font-weight: 600;
    line-height: 1.2;
    margin: 8px 0 12px;
  }
  .stop-card .note {
    margin: 0 0 18px;
    color: var(--ink-2);
    font-size: 15px;
    line-height: 1.7;
  }
  .film {
    display: flex;
    gap: 10px;
    overflow-x: auto;
    scroll-snap-type: x mandatory;
    padding-bottom: 4px;
    scrollbar-width: thin;
    scrollbar-color: var(--rule) transparent;
  }
  .shot {
    flex: none;
    width: 210px;
    margin: 0;
    scroll-snap-align: start;
  }
  .shot svg {
    display: block;
    width: 100%;
    height: 132px;
    border: 1px solid var(--rule);
  }
  .shot figcaption {
    margin-top: 6px;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.06em;
    color: var(--ink-2);
  }
  .colophon { position: relative; z-index: 3; background: var(--paper); }
  html { scroll-snap-type: y proximity; }
  @media (max-width: 720px) {
    .stage { height: 42vh; }
    .story { padding-top: 42vh; }
    .stop {
      height: auto;
      min-height: 62vh;
      align-items: flex-end;
      padding: 16px;
    }
    .rail { display: none; }
    .stage-note { left: 16px; bottom: 12px; }
    .stage-index { left: 16px; top: 12px; }
  }
  @media (prefers-reduced-motion: reduce) {
    .stop-card, .route { transition: none; }
    html { scroll-snap-type: none; }
  }
</style>"""

tail = r"""    <p class="stage-index" id="stage-index"></p>
    <p class="stage-note">往下滚动 · 除合肥外都是示意</p>
    <nav class="rail" id="rail" aria-label="站点"></nav>
  </section>

  <div class="story" id="story"></div>
</main>

<footer class="colophon">
  <div class="colophon-in">
    <span>&copy; 2026 无意 Blog</span>
    <span>预览，尚未上线</span>
  </div>
</footer>

<script>
var PLACES = __PLACES__;
var byId = {};
PLACES.forEach(function (p, i) { p.index = i; byId[p.id] = p; });

var story = document.getElementById("story");
var rail = document.getElementById("rail");
story.innerHTML = PLACES.map(function (p) {
  var shots = p.shots.map(function (s) {
    return '<figure class="shot">' + s.svg + '<figcaption>' + s.caption + '</figcaption></figure>';
  }).join("");
  var kicker = p.home ? (p.year + "  ·  现在") : (p.year + "  ·  示意");
  return '<article class="stop" id="stop-' + p.id + '" data-id="' + p.id + '">'
    + '<div class="stop-card"><p class="kicker">' + kicker + '</p><h2>' + p.name + '</h2>'
    + '<p class="note">' + p.note + '</p><div class="film">' + shots + '</div></div></article>';
}).join("");
rail.innerHTML = PLACES.map(function (p) {
  return '<button type="button" data-id="' + p.id + '">' + p.year + '</button>';
}).join("");

var route = document.getElementById("route");
var ghost = route.cloneNode();
ghost.removeAttribute("id");
ghost.setAttribute("class", "route-ghost");
route.parentNode.insertBefore(ghost, route);

var len = route.getTotalLength();
var cmds = route.getAttribute("d").match(/[ML][-0-9.]+,[-0-9.]+/g) || [];
var pts = cmds.map(function (s) {
  var xy = s.slice(1).split(",");
  return { x: +xy[0], y: +xy[1] };
});
var walked = [0];
for (var i = 1; i < pts.length; i++) {
  walked.push(walked[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y));
}
var walkedMax = walked[walked.length - 1] || 1;
var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
route.style.strokeDasharray = String(len);
if (!reduce) route.style.transition = "stroke-dashoffset 0.55s ease";

function drawTo(index) {
  var portion = walked[index] / walkedMax;
  route.style.strokeDashoffset = String(len * (1 - portion));
}

function activate(id, scroll) {
  var p = byId[id];
  if (!p) return;
  document.querySelectorAll(".pin").forEach(function (el) {
    var n = byId[el.getAttribute("data-id")].index;
    el.classList.toggle("is-on", n === p.index);
    el.classList.toggle("is-past", n < p.index);
  });
  document.querySelectorAll(".stop").forEach(function (el) {
    el.classList.toggle("is-on", el.getAttribute("data-id") === id);
  });
  document.querySelectorAll(".rail button").forEach(function (el) {
    el.classList.toggle("is-on", el.getAttribute("data-id") === id);
  });
  document.getElementById("stage-index").textContent = (p.index + 1) + " / " + PLACES.length;
  drawTo(p.index);
  if (scroll) {
    var stop = document.getElementById("stop-" + id);
    stop.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }
}

document.querySelector(".atlas").addEventListener("click", function (e) {
  var pin = e.target.closest(".pin");
  if (pin) activate(pin.getAttribute("data-id"), true);
});
rail.addEventListener("click", function (e) {
  var btn = e.target.closest("button");
  if (btn) activate(btn.getAttribute("data-id"), true);
});

var current = PLACES[0].id;
var observer = new IntersectionObserver(function (entries) {
  var hit = entries.filter(function (en) { return en.isIntersecting; })
    .sort(function (a, b) { return b.intersectionRatio - a.intersectionRatio; })[0];
  if (!hit) return;
  var id = hit.target.getAttribute("data-id");
  if (id === current) return;
  current = id;
  activate(id, false);
}, { rootMargin: "-35% 0px -45% 0px", threshold: [0, 0.25, 0.6] });
document.querySelectorAll(".stop").forEach(function (el) { observer.observe(el); });

activate(PLACES[0].id, false);
</script>
</body>
</html>
"""

start = html.index("<style>")
end = html.index("</style>") + len("</style>")
html = html[:start] + style + html[end:]

marker = "</svg>"
at = html.index(marker) + len(marker)
# Keep PLACES json from the old script
p0 = html.index("var PLACES = ")
p1 = html.index(";", p0)
places = html[p0 + len("var PLACES = "):p1]
tail = tail.replace("__PLACES__", places)
html = html[:at] + "\n" + tail
path.write_text(html, encoding="utf-8", newline="\n")
print("wrote", path, "bytes", path.stat().st_size)
