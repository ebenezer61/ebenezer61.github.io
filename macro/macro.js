/* Shared helpers for macro/, macro/flows/ and macro/ipo/: formatting, data
   loading, and two hand-rolled SVG charts (time-scaled lines with optional
   event markers, and signed bars). No libraries; every chart has a hover
   tooltip and draws from the CSS tokens in macro.css. */
(function () {
	"use strict";
	var DAY = 864e5;
	var M = window.M = {};
	var tip;

	/* Language: Traditional Chinese by default, English with ?lang=en or the
	   toggle (remembered per browser). Static text carries its English in a
	   data-en attribute; page scripts pick strings with M.t(zh, en). */
	var LANG_KEY = "macro-lang";
	M.lang = (function () {
		var q = /[?&]lang=(en|zh)\b/.exec(location.search);
		if (q) return q[1];
		try { return localStorage.getItem(LANG_KEY) === "en" ? "en" : "zh"; } catch (e) { return "zh"; }
	})();
	M.en = M.lang === "en";
	M.t = function (zh, en) { return M.en ? en : zh; };
	function applyLang() {
		var root = document.documentElement;
		if (M.en) {
			root.lang = "en";
			if (root.getAttribute("data-title-en")) document.title = root.getAttribute("data-title-en");
			var md = document.querySelector('meta[name="description"]');
			if (md && md.getAttribute("data-en")) md.setAttribute("content", md.getAttribute("data-en"));
			document.querySelectorAll("body [data-en]").forEach(function (n) { n.innerHTML = n.getAttribute("data-en"); });
		}
		var bc = document.querySelector(".breadcrumb");
		if (bc) {
			var b = document.createElement("button");
			b.className = "btn langbtn"; b.type = "button";
			b.textContent = M.en ? "中文" : "English";
			b.setAttribute("lang", M.en ? "zh-Hant" : "en");
			b.setAttribute("aria-label", M.en ? "切換成中文" : "Switch to English");
			b.addEventListener("click", function () {
				var next = M.en ? "zh" : "en";
				try { localStorage.setItem(LANG_KEY, next); } catch (e) { /* private mode: the URL still carries it */ }
				var u = new URL(location.href);
				u.searchParams.set("lang", next);
				location.href = u.toString();
			});
			bc.appendChild(b);
		}
		// Keep the chosen language on links between the macro pages.
		if (/[?&]lang=/.test(location.search)) {
			document.querySelectorAll("a[data-keep-lang]").forEach(function (a) {
				var u = new URL(a.getAttribute("href"), location.href);
				u.searchParams.set("lang", M.lang);
				a.setAttribute("href", u.pathname + u.search + u.hash);
			});
		}
	}

	M.el = function (id) { return document.getElementById(id); };
	M.esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
	M.sign = function (x) { return x > 0 ? "up" : x < 0 ? "down" : ""; };
	M.pct = function (x, d) {
		if (x == null || isNaN(x)) return "–";
		var v = Math.abs(x * 100).toFixed(d == null ? 1 : d);
		return (x > 0 ? "+" : x < 0 ? "−" : "") + v + "%";
	};
	M.num = function (x, d) {
		if (x == null || isNaN(x)) return "–";
		if (d == null && x === 0) return "0";
		if (d == null) d = Math.abs(x) >= 1000 ? 0 : Math.abs(x) >= 100 ? 1 : Math.abs(x) >= 10 ? 2 : Math.abs(x) >= 1 ? 3 : 4;
		var s = Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
		return (x < 0 ? "−" : "") + s;
	};
	M.signed = function (x, d, unit) {
		if (x == null || isNaN(x)) return "–";
		return (x > 0 ? "+" : "") + M.num(x, d) + (unit || "");
	};
	M.load = function (url) {
		return fetch(url, { cache: "no-cache" }).then(function (r) { if (!r.ok) throw new Error(url + ": HTTP " + r.status); return r.json(); });
	};
	M.iso = function (ms) { return new Date(ms).toISOString().slice(0, 10); };
	M.ms = function (iso) { return Date.parse(iso.slice(0, 10) + "T00:00:00Z"); };

	/* {d0, t: [day offsets], v} -> {x: [ms], v} */
	M.expand = function (s) {
		var base = M.ms(s.d0), x = new Array(s.t.length);
		for (var i = 0; i < s.t.length; i++) x[i] = base + s.t[i] * DAY;
		return { x: x, v: s.v.slice() };
	};
	/* index of the last x <= t (or -1) */
	M.bisect = function (x, t) {
		var lo = 0, hi = x.length - 1, ans = -1;
		while (lo <= hi) { var mid = (lo + hi) >> 1; if (x[mid] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
		return ans;
	};
	/* value at or before t */
	M.at = function (s, t) { var i = M.bisect(s.x, t); return i < 0 ? null : s.v[i]; };

	M.spark = function (v, w, h) {
		w = w || 120; h = h || 26;
		var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v), p = "";
		if (hi === lo) hi = lo + 1;
		for (var i = 0; i < v.length; i++) p += (i ? "L" : "M") + (i / (v.length - 1 || 1) * w).toFixed(1) + " " + (h - 2 - (v[i] - lo) / (hi - lo) * (h - 4)).toFixed(1);
		return '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" aria-hidden="true"><path d="' + p + '" vector-effect="non-scaling-stroke"/></svg>';
	};

	function niceTicks(lo, hi, n) {
		var span = hi - lo || 1, step = Math.pow(10, Math.floor(Math.log10(span / n))), r = span / n / step;
		step *= r > 5 ? 10 : r > 2 ? 5 : r > 1 ? 2 : 1;
		var out = [];
		for (var t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) out.push(Math.abs(t) < step * 1e-9 ? 0 : t);
		return out;
	}
	function timeTicks(x0, x1) {
		var days = (x1 - x0) / DAY, out = [], d = new Date(x0), y = d.getUTCFullYear(), m = d.getUTCMonth();
		var mStep = days > 1500 ? 12 : days > 700 ? 6 : days > 300 ? 3 : days > 120 ? 1 : 0;
		if (!mStep) {
			var step = days > 40 ? 14 : days > 14 ? 7 : 1;
			for (var t = Math.ceil(x0 / DAY) * DAY; t <= x1; t += step * DAY) out.push({ x: t, label: M.iso(t).slice(5) });
			return out;
		}
		m = Math.ceil(m / mStep) * mStep;
		for (;;) {
			var t2 = Date.UTC(y + Math.floor(m / 12), m % 12, 1);
			if (t2 > x1) break;
			if (t2 >= x0) out.push({ x: t2, label: mStep === 12 || m % 12 === 0 ? String(y + Math.floor(m / 12)) : M.iso(t2).slice(0, 7) });
			m += mStep;
		}
		return out;
	}

	/* x-axis labels, skipping any that would overlap the previous one */
	function xLabels(ticks, X, lo, hi, y) {
		var out = "", lastRight = -Infinity;
		ticks.forEach(function (t) {
			var xx = X(t), half = String(t.label).length * 3.3 + 2;
			if (xx - half < lo || xx + half > hi || xx - half < lastRight + 6) return;
			lastRight = xx + half;
			out += '<text class="xlab" x="' + xx.toFixed(1) + '" y="' + y + '">' + t.label + '</text>';
		});
		return out;
	}

	function showTip(ev, html) {
		tip = tip || M.el("tooltip");
		tip.innerHTML = html; tip.hidden = false;
		var x = Math.max(90, Math.min(window.innerWidth - 90, ev.clientX));
		tip.style.left = x + "px"; tip.style.top = ev.clientY + "px";
	}
	function hideTip() { (tip || M.el("tooltip")).hidden = true; }

	/* Lines on a shared time axis.
	   series: [{label, slot, x: [ms], v, step: bool}], opts: {x0, x1, h, fmt, yfmt, baseline,
	   markers: [{x, label, cls}], aria, right: bool (direct labels), zero: bool,
	   xticks: fn(x0, x1) -> [{x, label}] and xlabel: fn(x) for a non-date x axis,
	   legend: false when the page draws its own legend (used on narrow screens)} */
	M.lineChart = function (mount, series, opts) {
		opts = opts || {};
		var W = mount.clientWidth > 0 ? Math.max(320, Math.min(1100, mount.clientWidth)) : 640;
		// Direct labels at the line ends need ~96px; on a phone a legend above the chart is cheaper.
		var narrow = W < 480, lab = opts.right !== false && series.length <= 5 && !narrow, RL = lab ? 96 : 8;
		var H = opts.h || 260, L = 50, R = RL, T = 10, B = 24, pw = W - L - R, ph = H - T - B;
		var x0 = opts.x0, x1 = opts.x1;
		if (x0 == null || x1 == null) {
			var a = Infinity, b = -Infinity;
			series.forEach(function (s) { if (s.x.length) { a = Math.min(a, s.x[0]); b = Math.max(b, s.x[s.x.length - 1]); } });
			if (x0 == null) x0 = a; if (x1 == null) x1 = b;
		}
		if (!(x1 > x0)) x1 = x0 + DAY;
		var lo = Infinity, hi = -Infinity, cut = [];
		series.forEach(function (s) {
			var i0 = Math.max(0, M.bisect(s.x, x0)), i1 = M.bisect(s.x, x1);
			cut.push([i0, i1]);
			for (var i = i0; i <= i1; i++) { var v = s.v[i]; if (v == null) continue; if (v < lo) lo = v; if (v > hi) hi = v; }
		});
		if (opts.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
		if (!isFinite(lo)) { mount.innerHTML = '<p class="muted small">' + M.t("這段期間沒有資料。", "No data in this window.") + '</p>'; return; }
		if (lo === hi) { lo -= 1; hi += 1; }
		var pad = (hi - lo) * 0.05; lo -= pad; hi += pad;
		function X(t) { return L + (t - x0) / (x1 - x0) * pw; }
		function Y(v) { return T + ph - (v - lo) / (hi - lo) * ph; }
		var yf = opts.yfmt || function (v) { return M.num(v); };
		var g = "";
		niceTicks(lo, hi, 5).forEach(function (t) {
			var yy = Y(t).toFixed(1);
			g += '<line class="grid" x1="' + L + '" x2="' + (L + pw) + '" y1="' + yy + '" y2="' + yy + '"/><text class="ylab" x="' + (L - 6) + '" y="' + (+yy + 4) + '">' + yf(t) + '</text>';
		});
		if (opts.baseline != null && opts.baseline > lo && opts.baseline < hi) g += '<line class="base" x1="' + L + '" x2="' + (L + pw) + '" y1="' + Y(opts.baseline).toFixed(1) + '" y2="' + Y(opts.baseline).toFixed(1) + '"/>';
		g += xLabels(opts.xticks ? opts.xticks(x0, x1) : timeTicks(x0, x1), function (t) { return X(t.x); }, L, L + pw, H - 6);
		var mk = "", marks = (opts.markers || []).filter(function (m) { return m.x >= x0 && m.x <= x1; });
		marks.forEach(function (m) {
			var xx = X(m.x).toFixed(1);
			mk += '<line class="ev" x1="' + xx + '" x2="' + xx + '" y1="' + T + '" y2="' + (T + ph) + '"/>' +
				'<path class="evm" style="fill:var(' + (m.color || "--viz-ink-3") + ')" d="M' + xx + ' ' + (T + ph) + 'l-4 6h8z"/>';
		});
		var paths = "", labels = [];
		series.forEach(function (s, k) {
			var p = "", i0 = cut[k][0], i1 = cut[k][1], pen = false, py = null;
			for (var i = i0; i <= i1; i++) {
				if (s.v[i] == null) { pen = false; continue; }
				var xx = X(Math.max(x0, s.x[i])).toFixed(1), yy = Y(s.v[i]).toFixed(1);
				if (s.step && pen) p += "H" + xx + "V" + yy; else p += (pen ? "L" : "M") + xx + " " + yy;
				pen = true; py = yy;
			}
			if (s.step && pen) p += "H" + X(x1).toFixed(1);
			paths += '<path class="ln" style="stroke:var(--s' + s.slot + ')' + (s.dash ? ';stroke-dasharray:4 3' : '') + '" d="' + p + '"/>';
			if (py != null) labels.push({ y: +py, text: s.label });
		});
		var dl = "";
		if (lab) {
			labels.sort(function (p, q) { return p.y - q.y; });
			for (var j = 1; j < labels.length; j++) if (labels[j].y - labels[j - 1].y < 12) labels[j].y = labels[j - 1].y + 12;
			labels.forEach(function (l) { dl += '<text class="dlab" x="' + (L + pw + 5) + '" y="' + (l.y + 4).toFixed(1) + '">' + M.esc(l.text.length > 14 ? l.text.slice(0, 13) + "…" : l.text) + '</text>'; });
		}
		var hov = '<line class="hover" y1="' + T + '" y2="' + (T + ph) + '" visibility="hidden"/>';
		series.forEach(function (s) { hov += '<circle class="hover" r="3.5" style="fill:var(--s' + s.slot + ')" visibility="hidden"/>'; });
		var leg = "";
		if (!lab && opts.legend !== false && series.length > 1)
			leg = '<ul class="legend">' + series.map(function (s) { return '<li><span class="lk" style="--sc:var(--s' + s.slot + ')"></span>' + M.esc(s.label) + '</li>'; }).join("") + '</ul>';
		mount.innerHTML = leg + '<svg class="ts" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + M.esc(opts.aria || "") + '">' + g + mk + paths + dl + hov +
			'<rect x="' + L + '" y="' + T + '" width="' + pw + '" height="' + (ph + 8) + '" fill="transparent"/></svg>';
		var svg = mount.querySelector("svg"), line = svg.querySelector("line.hover"), dots = svg.querySelectorAll("circle.hover");
		var fmt = opts.fmt || function (v) { return M.num(v); };
		function move(ev) {
			var r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) / r.width * W;
			var t = x0 + (sx - L) / pw * (x1 - x0);
			t = Math.max(x0, Math.min(x1, t));
			var xx = X(t);
			line.setAttribute("x1", xx); line.setAttribute("x2", xx); line.setAttribute("visibility", "visible");
			var rows = '<div class="d">' + (opts.xlabel ? opts.xlabel(t) : M.iso(t)) + '</div>';
			series.forEach(function (s, k) {
				var i = M.bisect(s.x, t);
				if (i < 0 || s.v[i] == null) { dots[k].setAttribute("visibility", "hidden"); return; }
				dots[k].setAttribute("cx", X(Math.max(x0, s.x[i]))); dots[k].setAttribute("cy", Y(s.v[i])); dots[k].setAttribute("visibility", "visible");
				rows += '<div class="row"><span><span class="lk" style="--sc:var(--s' + s.slot + ')"></span>' + M.esc(s.label) + '</span><span class="v">' + fmt(s.v[i], s) + '</span></div>';
			});
			var near = marks.filter(function (m) { return Math.abs(X(m.x) - xx) < 5; });
			near.forEach(function (m) { rows += '<div class="ev">◆ ' + M.esc(M.iso(m.x) + " " + m.label) + '</div>'; });
			showTip(ev, rows);
		}
		svg.addEventListener("pointermove", move);
		svg.addEventListener("pointerdown", move);
		svg.addEventListener("pointerleave", function () { line.setAttribute("visibility", "hidden"); for (var k = 0; k < dots.length; k++) dots[k].setAttribute("visibility", "hidden"); hideTip(); });
	};

	/* Signed bars, one per date (blue up, orange down), with an optional cumulative line.
	   d: [iso dates], v: values. opts: {h, fmt, label, cum: bool, aria,
	   clip: number (axis limited to +-clip; longer bars are cut and marked with a dot),
	   barClass: fn(value, i) -> class name in place of bar-up/bar-down} */
	M.barChart = function (mount, d, v, opts) {
		opts = opts || {};
		var W = mount.clientWidth > 0 ? Math.max(320, Math.min(1100, mount.clientWidth)) : 640;
		var H = opts.h || 200, L = 56, R = opts.cum ? 56 : 8, T = 10, B = 24, pw = W - L - R, ph = H - T - B, n = d.length;
		if (!n) { mount.innerHTML = '<p class="muted small">' + M.t("沒有資料。", "No data.") + '</p>'; return; }
		var lo = 0, hi = 0;
		v.forEach(function (x) { if (x == null) return; if (x < lo) lo = x; if (x > hi) hi = x; });
		if (opts.clip) { lo = Math.max(lo, -opts.clip); hi = Math.min(hi, opts.clip); }
		if (lo === hi) hi = 1;
		var cum = [], c = 0, clo = 0, chi = 0;
		v.forEach(function (x) { c += x || 0; cum.push(c); if (c < clo) clo = c; if (c > chi) chi = c; });
		if (clo === chi) chi = clo + 1;
		var bw = pw / n;
		function X(i) { return L + i * bw; }
		function Y(x) { return T + ph - (x - lo) / (hi - lo) * ph; }
		function Yc(x) { return T + ph - (x - clo) / (chi - clo) * ph; }
		var g = "", f = opts.fmt || function (x) { return M.num(x, 0); };
		niceTicks(lo, hi, 4).forEach(function (t) { var yy = Y(t).toFixed(1); g += '<line class="grid" x1="' + L + '" x2="' + (L + pw) + '" y1="' + yy + '" y2="' + yy + '"/><text class="ylab" x="' + (L - 6) + '" y="' + (+yy + 4) + '">' + M.num(t, 0) + '</text>'; });
		if (opts.cum) niceTicks(clo, chi, 4).forEach(function (t) { g += '<text x="' + (L + pw + 6) + '" y="' + (Yc(t) + 4).toFixed(1) + '">' + M.num(t, 0) + '</text>'; });
		g += '<line class="base" x1="' + L + '" x2="' + (L + pw) + '" y1="' + Y(0).toFixed(1) + '" y2="' + Y(0).toFixed(1) + '"/>';
		var x0 = M.ms(d[0]), x1 = M.ms(d[n - 1]);
		g += xLabels(timeTicks(x0, x1), function (t) { return X(Math.round((t.x - x0) / (x1 - x0 || 1) * (n - 1))) + bw / 2; }, L, L + pw, H - 6);
		var bars = "", gap = bw > 4 ? 1 : 0;
		for (var i = 0; i < n; i++) {
			if (v[i] == null) continue;
			var y0 = Y(0), y1 = Y(Math.max(lo, Math.min(hi, v[i])));
			if (v[i] < lo || v[i] > hi) bars += '<circle r="2.5" style="fill:var(--viz-ink-1)" cx="' + (X(i) + bw / 2).toFixed(1) + '" cy="' + (v[i] < lo ? T + ph + 4 : T - 4) + '"/>';
			bars += '<rect class="' + (opts.barClass ? opts.barClass(v[i], i) : v[i] >= 0 ? "bar-up" : "bar-down") + '" x="' + (X(i) + gap / 2).toFixed(2) + '" width="' + Math.max(0.6, bw - gap).toFixed(2) + '" y="' + Math.min(y0, y1).toFixed(1) + '" height="' + Math.max(0.5, Math.abs(y1 - y0)).toFixed(1) + '"/>';
		}
		var cl = "";
		if (opts.cum) {
			var p = "";
			for (var k = 0; k < n; k++) p += (k ? "L" : "M") + (X(k) + bw / 2).toFixed(1) + " " + Yc(cum[k]).toFixed(1);
			cl = '<path class="ln" style="stroke:var(--viz-ink-2)" d="' + p + '"/>';
		}
		mount.innerHTML = '<svg class="ts" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + M.esc(opts.aria || "") + '">' + g + bars + cl +
			'<line class="hover" y1="' + T + '" y2="' + (T + ph) + '" visibility="hidden"/><rect x="' + L + '" y="' + T + '" width="' + pw + '" height="' + ph + '" fill="transparent"/></svg>';
		var svg = mount.firstChild, line = svg.querySelector("line.hover");
		function move(ev) {
			var r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) / r.width * W;
			var i = Math.max(0, Math.min(n - 1, Math.floor((sx - L) / bw))), xx = X(i) + bw / 2;
			line.setAttribute("x1", xx); line.setAttribute("x2", xx); line.setAttribute("visibility", "visible");
			var html = '<div class="d">' + d[i] + '</div><div class="row"><span>' + M.esc(opts.label || M.t("淨額", "Net")) + '</span><span class="v">' + f(v[i]) + '</span></div>';
			if (opts.cum) html += '<div class="row"><span>' + M.t("區間累計", "Cumulative") + '</span><span class="v">' + f(cum[i]) + '</span></div>';
			showTip(ev, html);
		}
		svg.addEventListener("pointermove", move);
		svg.addEventListener("pointerdown", move);
		svg.addEventListener("pointerleave", function () { line.setAttribute("visibility", "hidden"); hideTip(); });
	};

	applyLang();

	/* Redraw registered charts when the width changes enough to matter. */
	var redraws = [], lastW = window.innerWidth;
	M.onResize = function (fn) { redraws.push(fn); };
	window.addEventListener("resize", function () {
		if (Math.abs(window.innerWidth - lastW) < 40) return;
		lastW = window.innerWidth;
		redraws.forEach(function (fn) { fn(); });
	});
})();
