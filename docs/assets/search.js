/* HEADING OS docs — dependency-free client-side search.
 * Loads assets/search-index.json (one record per doc section) on first use and
 * matches the query against section headings + body text, entirely in the
 * browser. No server, no external library, no CDN — fits a static GitHub Pages
 * site. Generated index is built by scripts/regenerate-docs-html.py. */
(function () {
  "use strict";

  var INDEX_URL = "assets/search-index.json";
  var MAX_RESULTS = 8;

  var input = document.getElementById("doc-search");
  var box = document.getElementById("search-results");
  if (!input || !box) return;

  var index = null;
  var loading = false;
  var active = -1;

  function load() {
    if (index || loading) return;
    loading = true;
    fetch(INDEX_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        index = data;
        loading = false;
        if (input.value.trim()) run(input.value);
      })
      .catch(function () {
        loading = false;
        box.innerHTML = '<div class="search-empty">Search index unavailable.</div>';
        box.hidden = false;
      });
  }

  function tokenize(q) {
    return q.toLowerCase().split(/\s+/).filter(Boolean);
  }

  function count(hay, needle) {
    var n = 0, i = hay.indexOf(needle);
    while (i !== -1) { n++; i = hay.indexOf(needle, i + needle.length); }
    return n;
  }

  function score(rec, toks) {
    var head = (rec.h + " " + rec.p).toLowerCase();
    var hay = (head + " " + rec.t).toLowerCase();
    var s = 0;
    for (var i = 0; i < toks.length; i++) {
      var t = toks[i];
      if (hay.indexOf(t) === -1) return 0;     // every token must appear (AND)
      s += count(hay, t);
      if (head.indexOf(t) !== -1) s += 8;       // heading / page-title hits weigh more
      if (rec.h.toLowerCase().indexOf(t) === 0) s += 4;
    }
    return s;
  }

  function esc(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function highlight(s, toks) {
    s = esc(s);
    for (var i = 0; i < toks.length; i++) {
      var re = new RegExp("(" + toks[i].replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig");
      s = s.replace(re, "<mark>$1</mark>");
    }
    return s;
  }

  function snippet(text, toks) {
    var low = text.toLowerCase();
    var pos = -1;
    for (var i = 0; i < toks.length; i++) {
      var p = low.indexOf(toks[i]);
      if (p !== -1 && (pos === -1 || p < pos)) pos = p;
    }
    if (pos === -1) pos = 0;
    var start = Math.max(0, pos - 40);
    var end = Math.min(text.length, pos + 130);
    return (start > 0 ? "..." : "") + text.slice(start, end) + (end < text.length ? "..." : "");
  }

  function run(q) {
    var toks = tokenize(q);
    if (!toks.length || !index) { close(); return; }

    var hits = [];
    for (var i = 0; i < index.length; i++) {
      var sc = score(index[i], toks);
      if (sc > 0) hits.push([sc, index[i]]);
    }
    hits.sort(function (a, b) { return b[0] - a[0]; });
    hits = hits.slice(0, MAX_RESULTS);
    active = -1;

    if (!hits.length) {
      box.innerHTML = '<div class="search-empty">No matches</div>';
      box.hidden = false;
      return;
    }

    box.innerHTML = hits.map(function (pair, i) {
      var rec = pair[1];
      // rec.u/rec.a are our own slugified filenames + heading ids (build output);
      // esc() on the href is defensive only. The index is first-party same-origin
      // data and the query is only ever used as a regex over esc()'d text, so no
      // untrusted markup reaches innerHTML.
      var url = esc(rec.u + (rec.a ? "#" + rec.a : ""));
      var crumb = esc(rec.p);
      if (rec.h && rec.h !== rec.p) {
        crumb += ' <span class="crumb-sep">&rsaquo;</span> ' + highlight(rec.h, toks);
      }
      return '<a class="search-hit" role="option" data-i="' + i + '" href="' + url + '">'
        + '<div class="search-hit-title">' + crumb + "</div>"
        + '<div class="search-hit-snip">' + highlight(snippet(rec.t, toks), toks) + "</div>"
        + "</a>";
    }).join("");
    box.hidden = false;
  }

  function close() {
    box.hidden = true;
    box.innerHTML = "";
    active = -1;
  }

  function move(delta) {
    var hits = box.querySelectorAll(".search-hit");
    if (!hits.length) return;
    active = (active + delta + hits.length) % hits.length;
    for (var i = 0; i < hits.length; i++) hits[i].classList.toggle("active", i === active);
    hits[active].scrollIntoView({ block: "nearest" });
  }

  input.addEventListener("focus", load);
  input.addEventListener("input", function () {
    if (input.value.trim()) run(input.value); else close();
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") {
      var hits = box.querySelectorAll(".search-hit");
      if (active >= 0 && hits[active]) { e.preventDefault(); window.location.href = hits[active].getAttribute("href"); }
    } else if (e.key === "Escape") { close(); input.blur(); }
  });

  // Global "/" focuses the search box (unless already typing in a field).
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/") return;
    var el = document.activeElement;
    var tag = (el && el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || (el && el.isContentEditable)) return;
    e.preventDefault();
    input.focus();
  });

  // Click outside closes the dropdown.
  document.addEventListener("click", function (e) {
    if (e.target !== input && !box.contains(e.target)) close();
  });
})();
