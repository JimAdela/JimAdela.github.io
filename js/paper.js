/* ==========================================================================
   paper.js — 顶栏进度、文章目录、宽表滚动
   原生实现，不依赖 jQuery / Bootstrap JS。
   ========================================================================== */
(function () {
  'use strict';

  /* ---------- 阅读进度 ---------- */
  var bar = document.getElementById('js-progress');
  if (bar) {
    var tick = function () {
      var doc = document.documentElement;
      var max = doc.scrollHeight - window.innerHeight;
      var y = window.pageYOffset || doc.scrollTop;
      var p = max > 0 ? Math.min(1, Math.max(0, y / max)) : 0;
      bar.style.width = (p * 100).toFixed(2) + '%';
    };
    window.addEventListener('scroll', tick, { passive: true });
    window.addEventListener('resize', tick);
    tick();
  }

  /* ---------- 文章目录 ---------- */
  var toc = document.getElementById('js-toc');
  var sheet = document.querySelector('.sheet');
  if (toc && sheet) {
    var list = toc.querySelector('ol');
    var heads = sheet.querySelectorAll('h2, h3');
    var n = 0;

    Array.prototype.forEach.call(heads, function (h) {
      if (!h.id) return;
      var li = document.createElement('li');
      if (h.tagName === 'H3') li.className = 'lv3';
      var a = document.createElement('a');
      a.href = '#' + h.id;
      a.textContent = h.textContent.replace(/^#+\s*/, '');
      li.appendChild(a);
      list.appendChild(li);
      n++;
    });

    if (n < 3) {
      toc.parentNode.removeChild(toc);
    } else {
      var label = toc.querySelector('span');
      if (label) label.textContent = n + ' 节';
      if (window.innerWidth < 900) toc.removeAttribute('open');
    }
  }

  /* ---------- 宽表横向滚动 ---------- */
  var tables = document.querySelectorAll('.sheet table');
  Array.prototype.forEach.call(tables, function (t) {
    var p = t.parentNode;
    if (p && p.classList && p.classList.contains('table-responsive')) return;
    var w = document.createElement('div');
    w.className = 'table-responsive';
    p.insertBefore(w, t);
    w.appendChild(t);
  });

  /* ---------- 图片：懒加载 + 失效外链降级 ---------- */
  Array.prototype.forEach.call(document.querySelectorAll('.sheet img'), function (img) {
    img.loading = 'lazy';

    var drop = function () {
      var p = img.parentNode;
      img.parentNode && img.parentNode.removeChild(img);
      // 图片独占一段时，把空段落一并收掉，避免留下一条空白
      if (p && p.tagName === 'P' && !p.textContent.trim() && !p.querySelector('img')) {
        p.parentNode && p.parentNode.removeChild(p);
      }
    };

    if (img.complete && img.naturalWidth === 0) {
      drop();
    } else {
      img.addEventListener('error', drop);
    }
  });
  /* ---------- 代码块：语言标签与复制 ---------- */
  Array.prototype.forEach.call(document.querySelectorAll('.sheet pre'), function (pre) {
    var parent = pre.parentNode;
    if (parent && parent.classList && parent.classList.contains('codeblock')) return;

    var host = pre.closest('.highlight') || pre;
    var outer = host.parentNode;

    var lang = '';
    var m = (outer.className || '').match(/language-([A-Za-z0-9+#-]+)/);
    if (!m) m = (host.className || '').match(/language-([A-Za-z0-9+#-]+)/);
    if (m) lang = m[1];

    var box = document.createElement('div');
    box.className = 'codeblock';
    outer.insertBefore(box, host);
    box.appendChild(host);

    if (lang) {
      var tag = document.createElement('span');
      tag.className = 'code-lang';
      tag.textContent = lang;
      box.appendChild(tag);
    }

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'code-copy';
    btn.textContent = '复制';
    btn.setAttribute('aria-label', '复制这段代码');
    box.appendChild(btn);

    btn.addEventListener('click', function () {
      var text = pre.innerText.replace(/\s+$/, '');

      var done = function () {
        btn.textContent = '已复制';
        btn.classList.add('is-done');
        setTimeout(function () {
          btn.textContent = '复制';
          btn.classList.remove('is-done');
        }, 1400);
      };

      var fallback = function () {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.top = '-1000px';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); done(); } catch (e) { /* 忽略 */ }
        document.body.removeChild(ta);
      };

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, fallback);
      } else {
        fallback();
      }
    });
  });
})();
