/* ============================================================
   fx.js — threeui 风格的交互与动效层（零依赖、渐进增强）

   设计约束（务必保持）：
   1. 所有内容在禁用 JS / 加载失败时仍完整可见：动效类一律由本文件注入，
      且每个模块单独 try/catch，任一模块抛错不影响其余模块与页面内容。
   2. 只用 CSS 自定义属性（--fx-*）向样式层传值，绝不写行内 transform，
      避免与页面既有 hover/深色主题规则打架。
   3. prefers-reduced-motion: reduce 时全部降级为静态终态。
   4. 对外暴露 window.FX：decode / countUp / pop / field / chartMotion。
   ============================================================ */
(function () {
  'use strict';

  var doc = document;
  var html = doc.documentElement;
  var reduceQuery = null;
  var fineQuery = null;
  try { reduceQuery = window.matchMedia('(prefers-reduced-motion: reduce)'); } catch (e) {}
  try { fineQuery = window.matchMedia('(hover: hover) and (pointer: fine)'); } catch (e) {}

  function reduced() { return !!(reduceQuery && reduceQuery.matches); }
  function fine() { return !!(fineQuery && fineQuery.matches); }
  function isDark() { return html.classList.contains('theme-dark'); }

  /* ============================================================
     1. 滚动入场（IntersectionObserver + 同容器交错延迟）
     ============================================================ */
  var REVEAL_GROUPS = [
    { sel: '.home-hero', step: 0, cap: 1 },
    { sel: '.home-feature-card', step: 110, cap: 8 },
    { sel: '.card, .well, .warm-card, .answer-card', step: 70, cap: 6 },
    { sel: '.data-row', step: 22, cap: 18 }
  ];

  function initReveal() {
    if (reduced() || !('IntersectionObserver' in window)) return;

    var targets = [];
    REVEAL_GROUPS.forEach(function (group) {
      var nodes = doc.querySelectorAll(group.sel);
      var perParent = new Map();
      for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i];
        // 未渲染的元素（如隐藏 tab 面板内）不做入场：观察器可能长期不触发，会把内容留在透明态
        if (!el.getClientRects().length) continue;
        var key = el.parentNode || doc.body;
        var seen = perParent.get(key) || 0;
        perParent.set(key, seen + 1);
        var idx = Math.min(seen, group.cap);
        el.style.transitionDelay = (idx * group.step) + 'ms';
        el.classList.add('fx-anim');
        targets.push(el);
      }
    });

    function reveal(el) {
      if (el.classList.contains('fx-in')) return;
      el.classList.add('fx-in');
      // 入场完成后必须清掉交错延迟，否则同一元素的 hover 过渡会被拖慢
      setTimeout(function () { el.style.transitionDelay = ''; }, 900);
    }

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        reveal(entry.target);
        io.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.04 });

    targets.forEach(function (el) { io.observe(el); });

    // 兜底：只要元素已在视口内就必须可见。观察器在后台标签页等场景可能不触发，
    // 演示现场绝不能出现「内容在屏上却是透明」的情况。
    function sweep() {
      var vh = window.innerHeight || doc.documentElement.clientHeight;
      for (var i = 0; i < targets.length; i++) {
        var el = targets[i];
        if (!el.getClientRects().length) continue;
        var r = el.getBoundingClientRect();
        if (r.top < vh * 0.98 && r.bottom > 0) reveal(el);
      }
    }
    window.addEventListener('focus', sweep);
    window.addEventListener('pageshow', sweep);
    window.addEventListener('resize', sweep);
    window.addEventListener('scroll', sweep, { passive: true });
    setTimeout(sweep, 1200);
    setTimeout(sweep, 3000);
  }

  /* ============================================================
     2. 解码式文字入场（移植 threeui articleHeadingDecode）
     ============================================================ */
  var POOL_ASCII = '#%&@$/\\<>*+=~ABCDEFGHKMNPRSTUVWXYZ0123456789';
  var POOL_CJK = '数据薪职位城市招聘分析技能学历经验算法模型趋势';
  var CJK = /[　-〿一-龥＀-￯]/;
  var MAX_DECODE_CHARS = 1400;

  function poolFor(ch) { return CJK.test(ch) ? POOL_CJK : POOL_ASCII; }
  function rand(arr) { return arr.charAt((Math.random() * arr.length) | 0); }
  function easeOut(t) { return 1 - Math.pow(1 - t, 2); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function collectTextNodes(el, out) {
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3) {
        if (n.textContent && n.textContent.trim()) out.push(n);
      } else if (n.nodeType === 1 && n.tagName !== 'SCRIPT' && n.tagName !== 'STYLE') {
        collectTextNodes(n, out);
      }
    }
    return out;
  }

  /**
   * 逐字解码显示 root 内文字；返回 cleanup。reduced-motion 时直接返回 noop。
   */
  function decode(rootEl, opts) {
    if (!rootEl || reduced()) return function () {};
    opts = opts || {};
    var duration = opts.duration || 620;
    var scramble = opts.scrambleLength == null ? 10 : opts.scrambleLength;
    var tailChance = opts.tailChance == null ? 0.05 : opts.tailChance;

    var nodes = collectTextNodes(rootEl, []);
    var originals = [];
    var total = 0;
    for (var i = 0; i < nodes.length; i++) {
      var text = nodes[i].textContent;
      originals.push(text);
      total += text.length;
    }
    if (!nodes.length || total > MAX_DECODE_CHARS || total < 2) return function () {};

    var token = { raf: 0, done: false };
    var owner = rootEl.__fxDecode;
    if (owner && owner.token) owner.token.done = true;
    rootEl.__fxDecode = token;

    var start = 0;
    function frame(now) {
      if (token.done) return;
      if (!start) start = now;
      var progress = clamp((now - start) / duration, 0, 1);
      var budget = Math.floor(easeOut(progress) * total);
      var cancelled = false;

      for (var j = 0; j < nodes.length; j++) {
        var node = nodes[j];
        if (!node.isConnected) { cancelled = true; break; }
        var src = originals[j];
        var revealed = clamp(budget, 0, src.length);
        budget -= revealed;
        if (revealed >= src.length) { node.textContent = src; continue; }

        var out = src.slice(0, revealed);
        var win = Math.min(src.length - revealed, scramble);
        for (var k = 0; k < win; k++) {
          var ch = src.charAt(revealed + k);
          out += (ch === ' ' || Math.random() < 0.12) ? ch : rand(poolFor(ch));
        }
        out += src.slice(revealed + win).replace(/\S/g, function (c) {
          return Math.random() < tailChance ? rand(poolFor(c)) : c;
        });
        node.textContent = out;
      }

      if (cancelled) { token.done = true; return; }
      if (progress < 1) { token.raf = requestAnimationFrame(frame); }
      else {
        token.done = true;
        for (var m = 0; m < nodes.length; m++) {
          if (nodes[m].isConnected) nodes[m].textContent = originals[m];
        }
      }
    }
    token.raf = requestAnimationFrame(frame);

    return function cleanup() {
      token.done = true;
      cancelAnimationFrame(token.raf);
      for (var m = 0; m < nodes.length; m++) {
        if (nodes[m].isConnected) nodes[m].textContent = originals[m];
      }
    };
  }

  function initDecode() {
    var nodes = doc.querySelectorAll('[data-fx-decode]');
    for (var i = 0; i < nodes.length; i++) {
      (function (el, delay) {
        setTimeout(function () { try { decode(el, { duration: 760, scrambleLength: 6 }); } catch (e) {} }, delay);
      })(nodes[i], 120 + i * 90);
    }
  }

  /* ============================================================
     3. 点击涟漪
     ============================================================ */
  var RIPPLE_SEL = '.btn, .warm-btn, .pill-btn, .interest-btn, .theme-toggle, .add-city-btn, .fx-press';

  function spawnRipple(host, ev) {
    var rect = host.getBoundingClientRect();
    var size = Math.max(rect.width, rect.height) * 1.7;
    var x = (ev.clientX || (rect.left + rect.width / 2)) - rect.left - size / 2;
    var y = (ev.clientY || (rect.top + rect.height / 2)) - rect.top - size / 2;
    var ink = doc.createElement('span');
    ink.className = 'fx-ripple';
    ink.style.width = ink.style.height = size + 'px';
    ink.style.left = x + 'px';
    ink.style.top = y + 'px';
    host.appendChild(ink);
    setTimeout(function () { if (ink.parentNode) ink.parentNode.removeChild(ink); }, 620);
  }

  function initRipple() {
    if (reduced()) return;
    doc.body.addEventListener('pointerdown', function (ev) {
      try {
        if (ev.button !== 0) return;
        var host = ev.target.closest ? ev.target.closest(RIPPLE_SEL) : null;
        if (!host) return;
        spawnRipple(host, ev);
      } catch (e) {}
    }, { passive: true });
  }

  /* ============================================================
     4. 指针磁吸 + 卡片追随光斑（只写 --fx-* 变量）
     ============================================================ */
  var MAGNET_SEL = '.btn-primary, .btn-info, .btn-default, .warm-btn, .add-city-btn, .theme-toggle';
  var SPOT_SEL = '.home-feature-card, [data-fx-spot]';
  var MAX_PULL = 5;
  var MAX_TILT = 4.5;

  function initPointerFx() {
    if (!fine() || reduced()) return;

    // 由 JS 打标，样式层只认 .fx-magnet / .fx-spot，避免选择器两处维护
    doc.querySelectorAll(MAGNET_SEL).forEach(function (el) { el.classList.add('fx-magnet'); });
    doc.querySelectorAll(SPOT_SEL).forEach(function (el) { el.classList.add('fx-spot'); });

    var pending = new Map();
    var frame = 0;

    function flush() {
      frame = 0;
      pending.forEach(function (state, el) {
        if (state.type === 'magnet') {
          el.style.setProperty('--fx-tx', state.x.toFixed(2) + 'px');
          el.style.setProperty('--fx-ty', state.y.toFixed(2) + 'px');
        } else {
          var rect = el.getBoundingClientRect();
          el.style.setProperty('--fx-mx', state.x.toFixed(1) + 'px');
          el.style.setProperty('--fx-my', state.y.toFixed(1) + 'px');
          el.style.setProperty('--fx-roty', (state.x / rect.width * 2 - 1) * MAX_TILT + 'deg');
          el.style.setProperty('--fx-rotx', (0.5 - state.y / rect.height * 2) * MAX_TILT + 'deg');
        }
      });
      pending.clear();
    }

    function queue(el, type, x, y) {
      pending.set(el, { type: type, x: x, y: y });
      if (!frame) frame = requestAnimationFrame(flush);
    }

    function reset(el, type) {
      if (type === 'magnet') {
        el.style.setProperty('--fx-tx', '0px');
        el.style.setProperty('--fx-ty', '0px');
      } else {
        el.style.setProperty('--fx-rotx', '0deg');
        el.style.setProperty('--fx-roty', '0deg');
      }
    }

    doc.body.addEventListener('pointermove', function (ev) {
      try {
        var magnet = ev.target.closest ? ev.target.closest('.fx-magnet') : null;
        if (magnet && !magnet.disabled) {
          var r = magnet.getBoundingClientRect();
          queue(magnet, 'magnet',
            clamp((ev.clientX - r.left - r.width / 2) * 0.28, -MAX_PULL, MAX_PULL),
            clamp((ev.clientY - r.top - r.height / 2) * 0.34, -MAX_PULL, MAX_PULL));
        }
        var spot = ev.target.closest ? ev.target.closest('.fx-spot') : null;
        if (spot) {
          var sr = spot.getBoundingClientRect();
          queue(spot, 'spot', ev.clientX - sr.left, ev.clientY - sr.top);
        }
      } catch (e) {}
    }, { passive: true });

    doc.body.addEventListener('pointerout', function (ev) {
      try {
        var el = ev.target;
        if (!el.matches) return;
        if (el.matches('.fx-magnet')) reset(el, 'magnet');
        else if (el.matches('.fx-spot')) reset(el, 'spot');
      } catch (e) {}
    }, { passive: true });
  }

  /* ============================================================
     5. 数字滚动
     ============================================================ */
  function countUp(el) {
    if (!el) return;
    var target = parseFloat((el.textContent || '').replace(/[^\d.\-]/g, ''));
    if (!isFinite(target) || reduced()) return;
    var decimals = (el.getAttribute('data-fx-decimals') | 0);
    var suffix = el.getAttribute('data-fx-suffix') || '';
    var duration = 900;
    var start = 0;

    function frame(now) {
      if (!start) start = now;
      var p = clamp((now - start) / duration, 0, 1);
      var v = target * (1 - Math.pow(1 - p, 3));
      el.textContent = (decimals ? v.toFixed(decimals) : Math.round(v).toLocaleString('en-US')) + suffix;
      if (p < 1) requestAnimationFrame(frame);
      else el.textContent = (decimals ? target.toFixed(decimals) : Math.round(target).toLocaleString('en-US')) + suffix;
    }
    requestAnimationFrame(frame);
  }

  function initCountUp() {
    var nodes = doc.querySelectorAll('[data-fx-count]');
    if (!nodes.length) return;
    if (!('IntersectionObserver' in window) || reduced()) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        countUp(entry.target);
        io.unobserve(entry.target);
      });
    }, { threshold: 0.3 });
    for (var i = 0; i < nodes.length; i++) io.observe(nodes[i]);
  }

  /* ============================================================
     6. 角标弹跳（收藏数变化反馈）
     ============================================================ */
  function pop(el) {
    if (!el || reduced()) return;
    el.classList.remove('fx-pop');
    void el.offsetWidth;
    el.classList.add('fx-pop');
    setTimeout(function () { el.classList.remove('fx-pop'); }, 520);
  }

  function initBadgePop() {
    var badge = doc.getElementById('interested-badge');
    if (!badge || !('MutationObserver' in window)) return;
    var last = badge.textContent;
    new MutationObserver(function () {
      if (badge.textContent !== last) { last = badge.textContent; pop(badge); }
    }).observe(badge, { childList: true, characterData: true, subtree: true });
  }

  /* ============================================================
     7. 导航抬升 + 顶部阅读进度 + 固定导航高度同步
     ============================================================ */
  function initScrollChrome() {
    var nav = doc.querySelector('.navbar');
    var bar = doc.getElementById('fx-progress');
    if (!bar) {
      bar = doc.createElement('div');
      bar.id = 'fx-progress';
      doc.body.appendChild(bar);
    }

    // 窄窗口下导航会换行成多排，写死的 body padding-top 会让首屏内容被遮住
    var basePad = parseFloat(getComputedStyle(doc.body).paddingTop) || 0;
    function syncNavHeight() {
      if (!nav) return;
      var h = nav.getBoundingClientRect().height;
      doc.body.style.paddingTop = Math.max(basePad, Math.round(h) + 16) + 'px';
    }

    var ticking = false;
    function update() {
      ticking = false;
      if (nav) nav.classList.toggle('fx-scrolled', window.pageYOffset > 8);
      var max = doc.documentElement.scrollHeight - window.innerHeight;
      var p = max > 80 ? clamp(window.pageYOffset / max, 0, 1) : 0;
      bar.style.transform = 'scaleX(' + p.toFixed(4) + ')';
      bar.classList.toggle('fx-visible', p > 0.005);
    }
    function onScroll() { if (!ticking) { ticking = true; requestAnimationFrame(update); } }
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', function () { syncNavHeight(); onScroll(); }, { passive: true });
    syncNavHeight();
    update();
  }

  /* ============================================================
     8. Hero 数据粒子场（Canvas 2D，threeui ParticleNetwork 移植）
     ============================================================ */
  var PALETTE_LIGHT = {
    dot: 'rgba(31,35,32,0.055)',
    near: 'rgba(46,110,94,0.55)',
    far: 'rgba(46,110,94,0.16)',
    line: '46,110,94',
    accent: '196,79,58',
    glow: 'rgba(196,79,58,0.10)'
  };
  var PALETTE_DARK = {
    dot: 'rgba(180,140,255,0.09)',
    near: 'rgba(93,224,230,0.72)',
    far: 'rgba(93,224,230,0.18)',
    line: '93,224,230',
    accent: '255,110,199',
    glow: 'rgba(180,140,255,0.16)'
  };

  function field(canvas) {
    if (!canvas || canvas.__fxField) return null;
    var ctx = canvas.getContext && canvas.getContext('2d');
    if (!ctx) return null;

    var api = { stop: stop, start: function () { start(); }, setTheme: function () { useTheme(); } };
    var raf = 0, dpr = 1, w = 0, h = 0;
    var parts = [], pointer = { x: -9999, y: -9999, on: false };
    var host = canvas.parentElement || canvas;
    var pal = isDark() ? PALETTE_DARK : PALETTE_LIGHT;
    var linkDist = 108;

    function useTheme() {
      pal = isDark() ? PALETTE_DARK : PALETTE_LIGHT;
      if (reduced()) draw();
    }

    function resize() {
      var rect = host.getBoundingClientRect();
      w = Math.max(160, Math.round(rect.width));
      h = Math.max(120, Math.round(rect.height));
      dpr = Math.min(window.devicePixelRatio || 1, 1.6);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = w + 'px';
      canvas.style.height = h + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
      if (reduced()) draw();
    }

    function seed() {
      var count = clamp(Math.round(w * h / 12500), 26, 92);
      parts.length = 0;
      for (var i = 0; i < count; i++) {
        parts.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.24,
          vy: (Math.random() - 0.5) * 0.24,
          r: 0.9 + Math.random() * 1.9,
          hot: Math.random() < 0.16,
          ph: Math.random() * Math.PI * 2
        });
      }
    }

    function step(t) {
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        p.x += p.vx; p.y += p.vy;
        if (pointer.on) {
          var dx = p.x - pointer.x, dy = p.y - pointer.y;
          var d2 = dx * dx + dy * dy;
          if (d2 < 15000 && d2 > 1) {
            var f = (1 - d2 / 15000) * 0.05;
            p.x += dx * f * 0.4; p.y += dy * f * 0.4;
          }
        }
        if (p.x < -20) p.x = w + 20; if (p.x > w + 20) p.x = -20;
        if (p.y < -20) p.y = h + 20; if (p.y > h + 20) p.y = -20;
      }
    }

    function draw(t) {
      var time = t || 0;
      ctx.clearRect(0, 0, w, h);

      // 点阵底纹（DotMatrixBackground 质感）
      var gap = 26;
      ctx.fillStyle = pal.dot;
      for (var gx = gap / 2; gx < w; gx += gap) {
        for (var gy = gap / 2; gy < h; gy += gap) {
          var wob = reduced() ? 0 : Math.sin(time / 1600 + gx * 0.012 + gy * 0.017) * 0.5 + 0.5;
          var rr = 0.7 + wob * 0.75;
          ctx.beginPath();
          ctx.arc(gx, gy, rr, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // 指针柔光
      if (pointer.on) {
        var g = ctx.createRadialGradient(pointer.x, pointer.y, 0, pointer.x, pointer.y, 190);
        g.addColorStop(0, pal.glow);
        g.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = g;
        ctx.fillRect(pointer.x - 200, pointer.y - 200, 400, 400);
      }

      // 连线
      ctx.lineWidth = 1;
      for (var a = 0; a < parts.length; a++) {
        for (var b = a + 1; b < parts.length; b++) {
          var pa = parts[a], pb = parts[b];
          var ddx = pa.x - pb.x, ddy = pa.y - pb.y;
          var dd = ddx * ddx + ddy * ddy;
          if (dd > linkDist * linkDist) continue;
          var alpha = (1 - Math.sqrt(dd) / linkDist) * 0.3;
          ctx.strokeStyle = 'rgba(' + pal.line + ',' + alpha.toFixed(3) + ')';
          ctx.beginPath();
          ctx.moveTo(pa.x, pa.y);
          ctx.lineTo(pb.x, pb.y);
          ctx.stroke();
        }
      }

      // 节点
      for (var i = 0; i < parts.length; i++) {
        var p2 = parts[i];
        var pulse = reduced() ? 1 : 0.75 + Math.sin(time / 700 + p2.ph) * 0.25;
        ctx.fillStyle = p2.hot ? 'rgba(' + pal.accent + ',0.85)' : pal.near;
        ctx.beginPath();
        ctx.arc(p2.x, p2.y, p2.r * pulse, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    function loop(t) {
      raf = requestAnimationFrame(loop);
      step(t);
      draw(t);
    }

    function start() {
      if (api.running || reduced()) return;
      api.running = true;
      raf = requestAnimationFrame(loop);
    }
    function stop() {
      api.running = false;
      cancelAnimationFrame(raf);
    }

    host.addEventListener('pointermove', function (ev) {
      var rect = host.getBoundingClientRect();
      pointer.x = ev.clientX - rect.left;
      pointer.y = ev.clientY - rect.top;
      pointer.on = true;
    }, { passive: true });
    host.addEventListener('pointerleave', function () { pointer.on = false; pointer.x = pointer.y = -9999; }, { passive: true });

    doc.addEventListener('visibilitychange', function () { if (doc.hidden) stop(); else start(); });

    if ('ResizeObserver' in window) { new ResizeObserver(resize).observe(host); }
    else { window.addEventListener('resize', resize); }

    canvas.__fxField = api;
    resize();

    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) { if (entry.isIntersecting) start(); else stop(); });
      }, { threshold: 0.02 }).observe(canvas);
    } else {
      start();
    }
    return api;
  }

  function initFields() {
    var nodes = doc.querySelectorAll('canvas[data-fx-field]');
    for (var i = 0; i < nodes.length; i++) {
      try { field(nodes[i]); } catch (e) { nodes[i].style.display = 'none'; }
    }
  }

  /* ============================================================
     9. ECharts 入场动效统一注入（包装 echarts.init）
     ============================================================ */
  function chartMotion(echartsLib) {
    var lib = echartsLib || window.echarts;
    if (!lib || lib.__fxMotionPatched) return;
    var origInit = lib.init;
    lib.init = function () {
      var chart = origInit.apply(this, arguments);
      if (!chart || chart.__fxMotion) return chart;
      chart.__fxMotion = true;
      var origSet = chart.setOption.bind(chart);
      chart.setOption = function (option, cfg) {
        try {
          if (option && typeof option === 'object' && !reduced()) {
            if (option.animationDuration == null) option.animationDuration = 880;
            if (option.animationEasing == null) option.animationEasing = 'quinticOut';
            if (option.animationDelay == null) {
              option.animationDelay = function (idx) { return 90 + idx * 28; };
            }
            if (option.animationDurationUpdate == null) option.animationDurationUpdate = 420;
            if (option.animationEasingUpdate == null) option.animationEasingUpdate = 'cubicOut';
          }
        } catch (e) {}
        return origSet(option, cfg);
      };
      return chart;
    };
    lib.__fxMotionPatched = true;
  }

  /** 重放入场动画：类名先摘再挂，强制浏览器重新开始 animation */
  function replay(el, cls) {
    if (!el) return;
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
  }

  /* ============================================================
     10. 面板内容写入 + 解码（供 AI 弹层复用）
     ============================================================ */
  function setPanel(el, htmlText, opts) {
    if (!el) return;
    el.innerHTML = htmlText;
    try { decode(el, opts || { duration: 560, scrambleLength: 7 }); } catch (e) {}
  }

  /* ============================================================
     启动
     ============================================================ */
  var FX = {
    decode: decode,
    setPanel: setPanel,
    replay: replay,
    countUp: countUp,
    pop: pop,
    field: field,
    chartMotion: chartMotion
  };
  window.FX = FX;

  function boot() {
    try { chartMotion(window.echarts); } catch (e) {}
    [initReveal, initDecode, initRipple, initPointerFx, initCountUp, initBadgePop, initScrollChrome, initFields]
      .forEach(function (fn) { try { fn(); } catch (e) { /* 单模块失败不影响页面 */ } });
  }

  // 本文件放在 <head> 里（早于页面内联脚本），故解析期就要把 FX 暴露出来，
  // 让图表页在引入 echarts CDN 后能立刻调用 FX.chartMotion() 打补丁。
  try { chartMotion(window.echarts); } catch (e) {}

  window.addEventListener('theme:changed', function () {
    try {
      var nodes = doc.querySelectorAll('canvas[data-fx-field]');
      for (var i = 0; i < nodes.length; i++) {
        if (nodes[i].__fxField) nodes[i].__fxField.setTheme();
      }
    } catch (e) {}
  });

  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
