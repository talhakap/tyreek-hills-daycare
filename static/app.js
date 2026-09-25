// Small, dependency-free helpers: theme toggle, mobile menu, local times,
// sortable tables, and the head-to-head picker. The site works without this file.
(function () {
  "use strict";
  var root = document.documentElement;

  // ---- Theme toggle (remembers the choice; follows the system by default) ----
  function currentTheme() {
    var set = root.getAttribute("data-theme");
    if (set) return set;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
    function label() {
      btn.setAttribute("aria-label", currentTheme() === "dark" ? "Switch to light mode" : "Switch to dark mode");
    }
    label();
    btn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (e) { /* private mode: still works for this page */ }
      label();
    });
  });

  // ---- Mobile menu ----
  var menuBtn = document.querySelector("[data-menu-toggle]");
  var nav = document.getElementById("site-nav");
  if (menuBtn && nav) {
    menuBtn.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // ---- "Last updated" in the league's timezone ----
  document.querySelectorAll("[data-local-time]").forEach(function (el) {
    try {
      var d = new Date(el.getAttribute("datetime"));
      el.textContent = new Intl.DateTimeFormat("en-US", {
        timeZone: el.getAttribute("data-tz") || undefined, month: "short", day: "numeric", year: "numeric",
        hour: "numeric", minute: "2-digit", timeZoneName: "short"
      }).format(d);
    } catch (e) { /* keep the server-rendered text */ }
  });

  // ---- Sortable tables ----
  function cellValue(row, i) {
    var cell = row.children[i];
    if (!cell) return "";
    var v = cell.getAttribute("data-v");
    return v !== null ? v : cell.textContent.trim();
  }
  document.querySelectorAll("table.sortable").forEach(function (table) {
    var headers = table.querySelectorAll("thead th");
    headers.forEach(function (th, index) {
      var btn = th.querySelector("button");
      if (!btn) return;
      btn.addEventListener("click", function () {
        var numeric = th.getAttribute("data-sort") === "num";
        var dir = th.getAttribute("aria-sort") === "descending" ? "ascending"
                : th.getAttribute("aria-sort") === "ascending" ? "descending"
                : (numeric ? "descending" : "ascending");
        headers.forEach(function (h) { h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", dir);
        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var x = cellValue(a, index), y = cellValue(b, index), r;
          if (numeric) { r = (parseFloat(x) || 0) - (parseFloat(y) || 0); }
          else { r = x.localeCompare(y, undefined, { sensitivity: "base" }); }
          return dir === "ascending" ? r : -r;
        });
        table.classList.add("is-sorted");
        rows.forEach(function (r) { r.classList.remove("playoff-line"); body.appendChild(r); });
      });
    });
  });

  // ---- Head-to-head picker ----
  var dataEl = document.getElementById("h2h-data");
  var picker = document.querySelector("[data-h2h]");
  if (dataEl && picker) {
    var data = JSON.parse(dataEl.textContent);
    var selA = picker.querySelector("[data-h2h-a]");
    var selB = picker.querySelector("[data-h2h-b]");
    var out = picker.querySelector("[data-h2h-result]");

    function esc(s) {
      return String(s).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }
    function rec(w, l, t) { return t ? w + "-" + l + "-" + t : w + "-" + l; }
    function pts(x) { return Number(x).toFixed(2); }

    function show() {
      var a = selA.value, b = selB.value;
      var an = esc(data.names[a]), bn = esc(data.names[b]);
      if (a === b) { out.innerHTML = '<p class="muted">Pick two different managers.</p>'; return; }
      var r = (data.records[a] || {})[b];
      if (!r) { out.innerHTML = "<p>" + an + " and " + bn + " have never played each other.</p>"; return; }
      var lead = r.w > r.l ? an + " leads" : r.w < r.l ? bn + " leads" : "All square";
      var html = '<p class="h2h-summary">' + an + " " + rec(r.w, r.l, r.t) + " vs " + bn + "</p>" +
        "<p>" + lead + ". Points: " + pts(r.pf) + " to " + pts(r.pa) + "." +
        (r.pw || r.pl || r.pt ? " Playoffs: " + rec(r.pw, r.pl, r.pt) + "." : "") + "</p>";
      html += '<div class="table-wrap"><table class="data"><thead><tr><th scope="col">Game</th>' +
        '<th scope="col" class="num">Result</th><th scope="col" class="num">Score</th></tr></thead><tbody>';
      r.games.forEach(function (g) {
        var cls = g[5] === "W" ? "res-w" : g[5] === "L" ? "res-l" : "res-t";
        html += "<tr><td>" + g[0] + " week " + g[1] + (g[2] === "playoff" ? ' <span class="tag">Playoffs</span>' : "") +
          '</td><td class="num"><span class="res ' + cls + '">' + g[5] + '</span></td><td class="num">' +
          pts(g[3]) + "–" + pts(g[4]) + "</td></tr>";
      });
      out.innerHTML = html + "</tbody></table></div>";
    }
    selA.addEventListener("change", show);
    selB.addEventListener("change", show);
    document.querySelectorAll(".h2h-cell button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        selA.value = btn.getAttribute("data-a");
        selB.value = btn.getAttribute("data-b");
        show();
        picker.scrollIntoView({ block: "start" });
      });
    });
    show();
  }

  // ---- Records: one category group at a time ----
  var tabs = document.querySelector(".rec-tabs");
  var groups = document.querySelectorAll(".rec-group");
  if (tabs && groups.length) {
    var tabLinks = tabs.querySelectorAll("a[href^='#']");
    function openTab(id, scroll) {
      var target = document.getElementById(id);
      if (!target || !target.classList.contains("rec-group")) id = groups[0].id;
      groups.forEach(function (g) { g.hidden = g.id !== id; });
      tabLinks.forEach(function (a) {
        if (a.getAttribute("href") === "#" + id) {
          a.setAttribute("aria-current", "true");
          // Keep the active tab visible in the swipeable row on phones.
          tabs.scrollLeft = a.offsetLeft - (tabs.clientWidth - a.offsetWidth) / 2;
        } else {
          a.removeAttribute("aria-current");
        }
      });
      if (scroll) tabs.scrollIntoView({ block: "start" });
    }
    window.addEventListener("hashchange", function () { openTab(location.hash.slice(1), false); });
    tabLinks.forEach(function (a) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        var id = a.getAttribute("href").slice(1);
        try { history.replaceState(null, "", "#" + id); } catch (e) { /* ignore */ }
        openTab(id, true);
      });
    });
    openTab((location.hash || "").slice(1), false);
  }

  // ---- Team page: roster by season ----
  var rosterSelect = document.querySelector("[data-roster-select]");
  if (rosterSelect) {
    var rosterYears = document.querySelectorAll("[data-roster-year]");
    function showYear() {
      rosterYears.forEach(function (sec) { sec.hidden = sec.getAttribute("data-roster-year") !== rosterSelect.value; });
    }
    rosterSelect.addEventListener("change", showYear);
    showYear();
  }
})();
