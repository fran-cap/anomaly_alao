/* AALO lab dashboard - vanilla JS, no build step, no CDN. */
"use strict";

var REFRESH_MS = 10000;
var IDEA_STATUSES = ["proposed", "queued", "running", "kept", "pruned"];

var state = {
  view: "corpus",
  ideas: [],
  runs: [],
  summary: null,
  corpus: [],
  selectedRun: null,
  detail: null,
  selectedCorpus: null,
  corpusDetail: null,
  compare: null,
  cmpA: null,
  cmpB: null,
  ideaCategory: "all",
  ideaSort: { key: "id", dir: 1 },
  runSort: { key: "started", dir: -1 },
  corpusSort: { key: "started", dir: -1 }
};

/* ---------- helpers ---------- */

function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

function num(v, digits) {
  if (v === null || v === undefined || v === "" || isNaN(Number(v))) return "-";
  return Number(v).toFixed(digits === undefined ? 1 : digits);
}

function int(v) {
  if (v === null || v === undefined || v === "" || isNaN(Number(v))) return "-";
  return String(Math.round(Number(v)));
}

function txt(v) { return (v === null || v === undefined || v === "") ? "-" : String(v); }

function shortTime(iso) {
  // manifests store UTC ISO timestamps; run ids are stamped in local time, so
  // show local time here too or the two never line up
  if (!iso) return "-";
  var d = new Date(String(iso));
  if (isNaN(d.getTime())) {
    return String(iso).replace("T", " ").replace(/(\.\d+)?(Z|[+-]\d\d:?\d\d)?$/, "");
  }
  var pad = function (n) { return (n < 10 ? "0" : "") + n; };
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
    pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
}

function slug(v) {
  return String(v === null || v === undefined ? "" : v)
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function pill(value, extra) {
  var v = (value === null || value === undefined || value === "") ? "-" : String(value);
  var p = el("span", "pill " + slug(v) + (extra ? " " + extra : ""), v);
  return p;
}

function cmp(a, b) {
  var an = (a === null || a === undefined || a === "");
  var bn = (b === null || b === undefined || b === "");
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "boolean" || typeof b === "boolean") return (a ? 1 : 0) - (b ? 1 : 0);
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

function sortRows(rows, sort) {
  var out = rows.slice();
  out.sort(function (x, y) { return sort.dir * cmp(x[sort.key], y[sort.key]); });
  return out;
}

function wireSortHeaders(table, sort, rerender) {
  var heads = table.querySelectorAll("th[data-sort]");
  for (var i = 0; i < heads.length; i++) {
    (function (th) {
      var key = th.getAttribute("data-sort");
      th.classList.toggle("sorted", sort.key === key);
      th.classList.toggle("desc", sort.key === key && sort.dir < 0);
      if (th.dataset.wired) return;
      th.dataset.wired = "1";
      th.addEventListener("click", function () {
        if (sort.key === key) sort.dir = -sort.dir;
        else { sort.key = key; sort.dir = 1; }
        rerender();
      });
    })(heads[i]);
  }
}

function jget(path) {
  return fetch(path, { headers: { "Accept": "application/json" } }).then(function (r) {
    if (!r.ok) throw new Error(path + " -> " + r.status);
    return r.json();
  });
}

function jpost(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (j) {
      if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
      return j;
    });
  });
}

function tile(label, value, footNode, cls) {
  var t = el("div", "tile" + (cls ? " " + cls : ""));
  t.appendChild(el("div", "label", label));
  t.appendChild(el("div", "value", value));
  if (footNode) {
    t.appendChild(typeof footNode === "string" ? el("div", "foot", footNode) : footNode);
  }
  return t;
}

/* delta span: `good` says whether a positive delta is a good thing. */
function deltaSpan(value, goodWhenPositive, digits) {
  if (value === null || value === undefined || isNaN(Number(value))) return el("span", "dim", "-");
  var v = Number(value);
  var cls = v === 0 ? "delta-zero"
    : ((v > 0) === !!goodWhenPositive ? "delta-pos" : "delta-neg");
  return el("span", cls, (v > 0 ? "+" : "") + num(v, digits === undefined ? 0 : digits));
}

/* ---------- tabs ---------- */

var VIEWS = ["corpus", "ideas", "runs"];

function switchView(name) {
  if (VIEWS.indexOf(name) < 0) name = "corpus";
  state.view = name;
  VIEWS.forEach(function (v) {
    document.getElementById("view-" + v).hidden = (v !== name);
  });
  var tabs = document.querySelectorAll("#tabs .tab");
  for (var i = 0; i < tabs.length; i++) {
    tabs[i].classList.toggle("active", tabs[i].getAttribute("data-view") === name);
  }
  if (location.hash.slice(1) !== name) location.hash = name;
}

/* ---------- corpus: tiles ---------- */

function renderCorpusTiles() {
  var host = document.getElementById("corpus-tiles");
  host.textContent = "";
  var s = (state.summary && state.summary.corpus) || null;
  var latest = s && s.latest;
  if (!s || !latest) {
    host.appendChild(tile("Corpus", "no runs", "data/corpus/ is empty"));
    return;
  }

  var healthFoot = el("div", "foot", latest.status || "");
  if (s.pending) healthFoot.appendChild(el("span", "pill unknown", s.pending + " in flight"));
  host.appendChild(tile("Health", s.health, healthFoot, "health-" + slug(s.health)));

  var idFoot = el("div", "foot");
  idFoot.appendChild(el("span", null, latest.alao_commit_short || "?"));
  if (latest.alao_dirty) idFoot.appendChild(el("span", "pill dirty", "dirty"));
  host.appendChild(tile("Latest run", latest.run_id || "-", idFoot));

  host.appendChild(tile("Files analyzed", int(latest.corpus_files),
    txt(latest.corpus) + " · " + int(latest.files_modified) + " modified"));
  host.appendChild(tile("Findings", int(s.findings_total),
    latest.pattern_count + " patterns · " + int(latest.edits_applied) + " edits"));
  host.appendChild(tile("Parse failures", int(s.parse_failures),
    latest.timeouts ? int(latest.timeouts) + " timeouts" : "",
    s.parse_failures ? "bad-warn" : ""));
  host.appendChild(tile("Compile failures", int(s.compile_failures_after_fix),
    "after --fix", s.compile_failures_after_fix ? "bad-fail" : ""));
  host.appendChild(tile("Idempotence viol.", int(s.idempotence_violations),
    "second --fix pass", s.idempotence_violations ? "bad-fail" : ""));
  host.appendChild(tile("Timing", num(latest.analyze_s, 1) + " s",
    "analyze · fix " + num(latest.fix_s, 1) + " s"));
}

/* ---------- corpus: trend strip ---------- */

function renderTrend() {
  var host = document.getElementById("corpus-trend");
  host.textContent = "";
  var s = (state.summary && state.summary.corpus) || null;
  var pts = (s && s.trend) || [];
  if (pts.length < 1) {
    host.appendChild(el("p", "empty", "no corpus runs to trend yet"));
    return;
  }

  var W = 760, H = 120, PL = 44, PR = 44, PT = 12, PB = 24;
  var svg = svgEl("svg", {
    "class": "trend", viewBox: "0 0 " + W + " " + H,
    preserveAspectRatio: "none", role: "img"
  });

  var maxFind = 1, maxFail = 1;
  pts.forEach(function (p) {
    if (p.findings_total > maxFind) maxFind = p.findings_total;
    var f = (p.parse_failures || 0) + (p.compile_failures_after_fix || 0) +
      (p.idempotence_violations || 0);
    p._fail = f;
    if (f > maxFail) maxFail = f;
  });
  maxFind *= 1.1; maxFail *= 1.3;

  var iw = W - PL - PR, ih = H - PT - PB;
  var n = pts.length;
  function X(i) { return n === 1 ? PL + iw / 2 : PL + (i / (n - 1)) * iw; }
  function YF(v) { return PT + ih - (v / maxFind) * ih; }
  function YE(v) { return PT + ih - (v / maxFail) * ih; }

  svg.appendChild(svgEl("line", {
    x1: PL, y1: PT + ih, x2: W - PR, y2: PT + ih, stroke: "#2b3142", "stroke-width": 1
  }));

  /* failure bars on the right scale */
  var bw = Math.max(4, Math.min(22, iw / (n * 2)));
  pts.forEach(function (p, i) {
    if (!p._fail) return;
    var y = YE(p._fail);
    svg.appendChild(svgEl("rect", {
      x: X(i) - bw / 2, y: y, width: bw, height: (PT + ih) - y,
      fill: p.health === "fail" ? "#e06c6c" : "#e0b64a", opacity: "0.55"
    }));
  });

  /* findings line on the left scale */
  var d = "";
  pts.forEach(function (p, i) {
    d += (i ? "L" : "M") + X(i).toFixed(2) + " " + YF(p.findings_total).toFixed(2) + " ";
  });
  svg.appendChild(svgEl("path", {
    d: d, fill: "none", stroke: "#6aa9ff", "stroke-width": "1.6",
    "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke"
  }));

  pts.forEach(function (p, i) {
    var c = svgEl("circle", {
      cx: X(i), cy: YF(p.findings_total), r: 3.4,
      fill: p.health === "ok" ? "#6fd08c" : (p.health === "warn" ? "#e0b64a" : "#e06c6c"),
      stroke: "#11131a", "stroke-width": "1"
    });
    var title = svgEl("title", {});
    title.textContent = p.run_id + ": " + p.findings_total + " findings, " +
      p._fail + " failures (" + p.health + ")";
    c.appendChild(title);
    svg.appendChild(c);

    var lbl = svgEl("text", {
      x: X(i), y: H - 8, fill: "#8e97ad", "font-size": "9", "text-anchor": "middle"
    });
    lbl.textContent = String(p.run_id || "").slice(9, 15) || String(i + 1);
    svg.appendChild(lbl);
  });

  [[PL - 6, "end", "findings", "#6aa9ff"], [W - PR + 6, "start", "failures", "#e0b64a"]]
    .forEach(function (a) {
      var t = svgEl("text", {
        x: a[0], y: PT + 8, fill: a[3], "font-size": "10", "text-anchor": a[1]
      });
      t.textContent = a[2];
      svg.appendChild(t);
    });

  var maxLbl = svgEl("text", { x: PL - 6, y: PT + 22, fill: "#8e97ad", "font-size": "9", "text-anchor": "end" });
  maxLbl.textContent = Math.round(maxFind / 1.1);
  svg.appendChild(maxLbl);

  host.appendChild(svg);
  host.appendChild(el("p", "chart-note",
    "line: findings total per run (left) · bars: parse + compile + idempotence failures (right) · oldest first"));
}

/* ---------- corpus: runs table ---------- */

function renderCorpus() {
  var table = document.getElementById("corpus-table");
  var body = table.tBodies[0];
  document.getElementById("corpus-count").textContent = state.corpus.length + " runs";
  document.getElementById("corpus-empty").hidden = state.corpus.length > 0;
  wireSortHeaders(table, state.corpusSort, renderCorpus);
  body.textContent = "";

  sortRows(state.corpus, state.corpusSort).forEach(function (run) {
    var tr = el("tr");
    if (run.run_id === state.selectedCorpus) tr.classList.add("selected");
    tr.appendChild(el("td", "mono", shortTime(run.started)));
    tr.appendChild(el("td", "mono", run.run_id));
    var c = el("td", "mono", run.alao_commit_short || "-");
    if (run.alao_dirty) c.appendChild(el("span", "pill dirty", "dirty"));
    c.title = run.alao_commit || "";
    tr.appendChild(c);
    tr.appendChild(el("td", null, txt(run.corpus)));
    tr.appendChild(el("td", "num", int(run.corpus_files)));
    tr.appendChild(el("td", "num", int(run.findings_total)));
    tr.appendChild(countCell(run.parse_failures, "warn"));
    tr.appendChild(countCell(run.compile_failures_after_fix, "bad"));
    tr.appendChild(countCell(run.idempotence_violations, "bad"));
    tr.appendChild(el("td", "num", num(run.analyze_s, 1)));
    tr.appendChild(el("td", "num", num(run.fix_s, 1)));
    var h = el("td"); h.appendChild(pill(run.health)); tr.appendChild(h);
    tr.addEventListener("click", function () { selectCorpusRun(run.run_id); });
    body.appendChild(tr);
  });
}

function countCell(value, badCls) {
  var td = el("td", "num", int(value));
  if (Number(value) > 0) td.classList.add(badCls === "bad" ? "cell-bad" : "cell-warn");
  return td;
}

function selectCorpusRun(runId) {
  state.selectedCorpus = runId;
  renderCorpus();
  jget("/api/corpus/" + encodeURIComponent(runId)).then(function (d) {
    state.corpusDetail = d;
    renderCorpusDetail();
  }).catch(function (err) {
    state.corpusDetail = { run_id: runId, error: String(err.message || err) };
    renderCorpusDetail();
  });
}

function renderCorpusDetail() {
  var panel = document.getElementById("corpus-detail-panel");
  var host = document.getElementById("corpus-detail-body");
  var d = state.corpusDetail;
  host.textContent = "";
  if (!d) { panel.hidden = true; return; }
  panel.hidden = false;
  document.getElementById("corpus-detail-id").textContent = d.run_id;
  if (d.error) { host.appendChild(el("p", "empty", d.error)); return; }

  var grid = el("div", "detail-grid");

  var left = el("div", "detail-col");
  left.appendChild(el("h3", null, "Findings by pattern"));
  left.appendChild(barList(d.patterns || []));
  grid.appendChild(left);

  var right = el("div", "detail-col");
  right.appendChild(el("h3", null, "Severity"));
  var sev = d.findings_by_severity || {};
  var sevOrder = ["RED", "YELLOW", "GREEN", "DEBUG"];
  var sevKeys = sevOrder.filter(function (k) { return k in sev; })
    .concat(Object.keys(sev).filter(function (k) { return sevOrder.indexOf(k) < 0; }));
  if (!sevKeys.length) {
    right.appendChild(el("p", "empty", "no severity breakdown"));
  } else {
    var chips = el("div", "chips");
    sevKeys.forEach(function (k) {
      chips.appendChild(pill(k + " " + sev[k], "sev-" + slug(k)));
    });
    right.appendChild(chips);
  }

  right.appendChild(el("h3", null, "Run"));
  var m = d.manifest || {};
  right.appendChild(kvTable([
    ["commit", txt(d.alao_commit) + (d.alao_dirty ? "  (dirty)" : "")],
    ["corpus", txt(d.corpus) + "  " + int(d.corpus_files) + " files"],
    ["flags", (d.flags || []).join(" ") || "-"],
    ["started", shortTime(d.started)], ["finished", shortTime(d.finished)],
    ["status / health", txt(d.status) + " / " + txt(d.health)],
    ["analyze_s / fix_s", num(d.analyze_s, 1) + " / " + num(d.fix_s, 1)],
    ["files_modified", int(d.files_modified)],
    ["edits_applied", int(d.edits_applied)],
    ["edits_dropped_overlap", int(d.edits_dropped_overlap)],
    ["notes", txt(m.notes)]
  ]));
  grid.appendChild(right);
  host.appendChild(grid);

  host.appendChild(failureBlock("Parse failures", d.parse_failure_list, "warn"));
  host.appendChild(failureBlock("Compile failures after --fix", d.compile_failure_list, "bad"));
  host.appendChild(failureBlock("Idempotence violations", d.idempotence_violation_list, "bad"));
  if ((d.timeout_list || []).length) {
    host.appendChild(failureBlock("Timeouts", d.timeout_list, "warn"));
  }
  if ((d.crash_list || []).length) {
    host.appendChild(failureBlock("Crashes", d.crash_list, "bad"));
  }
  if ((d.differential_failure_list || []).length) {
    host.appendChild(failureBlock("Differential failures", d.differential_failure_list, "bad"));
  }
}

function barList(patterns) {
  if (!patterns.length) return el("p", "empty", "no findings recorded");
  var max = 0;
  patterns.forEach(function (p) { if (p.count > max) max = p.count; });
  if (max <= 0) max = 1;
  var box = el("div", "bars");
  patterns.forEach(function (p) {
    var row = el("div", "bar-row");
    row.appendChild(el("div", "bar-label mono", p.pattern));
    var track = el("div", "bar-track");
    var fill = el("div", "bar-fill");
    fill.style.width = (100 * p.count / max).toFixed(1) + "%";
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el("div", "bar-num", int(p.count)));
    box.appendChild(row);
  });
  return box;
}

function failureBlock(title, items, kind) {
  items = items || [];
  var det = el("details", "failures " + (kind === "bad" ? "bad" : "warn"));
  var sum = el("summary", null, title + " (" + items.length + ")");
  if (!items.length) det.classList.add("clean");
  det.appendChild(sum);
  if (!items.length) {
    det.appendChild(el("p", "empty", "none"));
    return det;
  }
  var list = el("ul", "failure-list");
  items.forEach(function (item) {
    var li = el("li");
    if (typeof item === "string") {
      li.appendChild(el("span", "mono file", item));
    } else {
      li.appendChild(el("span", "mono file", txt(item.file)));
      var msg = item.error || item.detail || item.traceback;
      if (msg) li.appendChild(el("span", "err", String(msg).split("\n")[0]));
    }
    list.appendChild(li);
  });
  det.appendChild(list);
  return det;
}

/* ---------- corpus: compare ---------- */

function fillCompareSelects() {
  ["cmp-a", "cmp-b"].forEach(function (id) {
    var sel = document.getElementById(id);
    var want = id === "cmp-a" ? state.cmpA : state.cmpB;
    var current = sel.value;
    sel.textContent = "";
    state.corpus.forEach(function (r) {
      var o = document.createElement("option");
      o.value = r.run_id;
      o.textContent = r.run_id + "  (" + r.health + ")";
      sel.appendChild(o);
    });
    if (want && state.corpus.some(function (r) { return r.run_id === want; })) sel.value = want;
    else if (current) sel.value = current;
    if (!sel.value && state.corpus.length) {
      sel.value = state.corpus[id === "cmp-a" ? 0 : Math.min(1, state.corpus.length - 1)].run_id;
    }
  });
}

function loadCompare() {
  var q = "";
  if (state.cmpA && state.cmpB) {
    q = "?a=" + encodeURIComponent(state.cmpA) + "&b=" + encodeURIComponent(state.cmpB);
  }
  return jget("/api/corpus/compare" + q).then(function (d) {
    state.compare = d;
    if (d && d.a && d.b) { state.cmpA = d.a.run_id; state.cmpB = d.b.run_id; }
    fillCompareSelects();
    renderCompare();
  }).catch(function (err) {
    state.compare = { error: String(err.message || err) };
    renderCompare();
  });
}

function renderCompare() {
  var host = document.getElementById("cmp-body");
  var note = document.getElementById("cmp-note");
  host.textContent = "";
  note.textContent = "";
  var c = state.compare;
  if (!c) { host.appendChild(el("p", "empty", "loading...")); return; }
  if (c.error) { host.appendChild(el("p", "empty", c.error)); return; }
  if (!c.a || !c.b) {
    host.appendChild(el("p", "empty", c.note || "need two corpus runs to compare"));
    return;
  }

  note.textContent = c.same_commit ? "same commit" :
    (c.a.alao_commit_short + " vs " + c.b.alao_commit_short);

  var head = el("div", "cmp-head");
  var ft = c.findings_total || {};
  head.appendChild(cmpStat("Findings", ft.a, ft.b, ft.delta, true, 0));
  head.appendChild(cmpStat("Parse failures", c.a.parse_failures, c.b.parse_failures,
    c.a.parse_failures - c.b.parse_failures, false, 0));
  head.appendChild(cmpStat("Compile failures", c.a.compile_failures_after_fix,
    c.b.compile_failures_after_fix,
    c.a.compile_failures_after_fix - c.b.compile_failures_after_fix, false, 0));
  head.appendChild(cmpStat("Idempotence viol.", c.a.idempotence_violations,
    c.b.idempotence_violations,
    c.a.idempotence_violations - c.b.idempotence_violations, false, 0));
  var tm = c.timing || {};
  if (tm.analyze_s) {
    head.appendChild(cmpStat("analyze_s", tm.analyze_s.a, tm.analyze_s.b, tm.analyze_s.delta, false, 1));
  }
  if (tm.fix_s) {
    head.appendChild(cmpStat("fix_s", tm.fix_s.a, tm.fix_s.b, tm.fix_s.delta, false, 1));
  }
  host.appendChild(head);

  var grid = el("div", "detail-grid");

  var left = el("div", "detail-col");
  left.appendChild(el("h3", null, "Per-pattern delta (A minus B)"));
  var rows = c.patterns || [];
  if (!rows.length) {
    left.appendChild(el("p", "empty", "no patterns in either run"));
  } else {
    var t = el("table", "cmp-table");
    var th = document.createElement("thead");
    var htr = el("tr");
    ["Pattern", "A", "B", "Δ"].forEach(function (h, i) {
      htr.appendChild(el("th", i ? "num" : null, h));
    });
    th.appendChild(htr); t.appendChild(th);
    var tb = document.createElement("tbody");
    rows.forEach(function (p) {
      var tr = el("tr");
      var name = el("td", "mono", p.pattern);
      if (p["new"]) name.appendChild(el("span", "pill new", "new"));
      if (p.gone) name.appendChild(el("span", "pill gone", "gone"));
      tr.appendChild(name);
      tr.appendChild(el("td", "num", int(p.a)));
      tr.appendChild(el("td", "num", int(p.b)));
      var d = el("td", "num");
      d.appendChild(deltaSpan(p.delta, true, 0));
      tr.appendChild(d);
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    left.appendChild(t);
  }
  grid.appendChild(left);

  var right = el("div", "detail-col");
  right.appendChild(el("h3", null, "Failure-list diff"));
  var f = c.failures || {};
  [["parse_failures", "Parse failures"],
   ["compile_failures_after_fix", "Compile failures"],
   ["idempotence_violations", "Idempotence violations"],
   ["timeouts", "Timeouts"]].forEach(function (pair) {
    var diff = f[pair[0]];
    if (!diff) return;
    var block = el("div", "diff-block");
    block.appendChild(el("h4", null, pair[1] + "  " + diff.b_count + " → " + diff.a_count));
    if (!diff.added.length && !diff.removed.length) {
      block.appendChild(el("p", "empty", diff.a_count ? "unchanged" : "clean in both"));
    }
    diff.removed.forEach(function (file) {
      var li = el("div", "diff-line fixed");
      li.appendChild(el("span", "sign", "−"));
      li.appendChild(el("span", "mono file", file));
      block.appendChild(li);
    });
    diff.added.forEach(function (file) {
      var li = el("div", "diff-line regressed");
      li.appendChild(el("span", "sign", "+"));
      li.appendChild(el("span", "mono file", file));
      block.appendChild(li);
    });
    right.appendChild(block);
  });
  var sev = c.severity || [];
  if (sev.length) {
    right.appendChild(el("h3", null, "Severity delta"));
    var chips = el("div", "chips");
    sev.forEach(function (s) {
      var c2 = el("span", "pill sev-" + slug(s.severity));
      c2.appendChild(document.createTextNode(s.severity + " " + s.a + " "));
      c2.appendChild(deltaSpan(s.delta, s.severity !== "RED", 0));
      chips.appendChild(c2);
    });
    right.appendChild(chips);
  }
  grid.appendChild(right);
  host.appendChild(grid);
}

function cmpStat(label, a, b, delta, goodWhenPositive, digits) {
  var foot = el("div", "foot");
  foot.appendChild(document.createTextNode(num(b, digits) + " → " + num(a, digits) + "  "));
  foot.appendChild(deltaSpan(delta, goodWhenPositive, digits));
  return tile(label, num(a, digits), foot);
}

/* ---------- summary tiles (ideas view) ---------- */

function renderTiles() {
  var host = document.getElementById("tiles");
  host.textContent = "";
  var s = state.summary;
  if (!s) return;

  var chips = el("div", "chips");
  var byStatus = s.ideas_by_status || {};
  IDEA_STATUSES.concat(Object.keys(byStatus).filter(function (k) {
    return IDEA_STATUSES.indexOf(k) < 0;
  })).forEach(function (k) {
    if (!byStatus[k]) return;
    chips.appendChild(pill(k + " " + byStatus[k], k));
  });
  host.appendChild(tile("Ideas", s.ideas_total || 0, chips));

  host.appendChild(tile("FPS runs", s.runs_total || 0,
    "done " + (s.runs_done || 0) + "  failed " + (s.runs_failed || 0) +
    "  crashed " + (s.runs_crashed || 0)));

  var best = s.best_run;
  host.appendChild(tile("Best FPS avg", best ? num(best.fps_avg, 1) : "-",
    best ? best.run_id : "no completed runs"));

  if (s.baseline) {
    host.appendChild(tile("Baseline FPS", num(s.baseline.fps_avg, 1), s.baseline.run_id));
  }

  var deltas = s.deltas || [];
  if (deltas.length) {
    var box = el("div", "tile");
    box.appendChild(el("div", "label", "Baseline vs variant"));
    var list = el("div", "chips");
    deltas.forEach(function (d) {
      var sign = d.fps_avg_delta >= 0 ? "+" : "";
      var c = el("span", "pill " + (d.fps_avg_delta >= 0 ? "kept" : "pruned"),
        d.idea_id + " " + sign + num(d.fps_avg_delta, 1) + " fps" +
        (d.fps_avg_pct === undefined ? "" : " (" + sign + num(d.fps_avg_pct, 1) + "%)"));
      c.title = d.variant_run + " vs " + d.baseline_run;
      list.appendChild(c);
    });
    box.appendChild(list);
    host.appendChild(box);
  }
}

/* ---------- ideas ---------- */

function editingIdeas() {
  var a = document.activeElement;
  return !!(a && a.closest && a.closest("#ideas-table"));
}

function ideaCategories() {
  var seen = {};
  state.ideas.forEach(function (i) {
    var c = i.category;
    if (c !== null && c !== undefined && c !== "") seen[String(c)] = 1;
  });
  return Object.keys(seen).sort();
}

function renderCategoryFilter() {
  var sel = document.getElementById("ideas-category");
  var cats = ideaCategories();
  var want = state.ideaCategory;
  if (want !== "all" && cats.indexOf(want) < 0) { want = "all"; state.ideaCategory = "all"; }
  var signature = cats.join("|");
  if (sel.dataset.signature !== signature) {
    sel.dataset.signature = signature;
    sel.textContent = "";
    ["all"].concat(cats).forEach(function (c) {
      var o = document.createElement("option");
      o.value = c;
      o.textContent = c === "all" ? "all (" + state.ideas.length + ")" : c;
      sel.appendChild(o);
    });
  }
  sel.value = want;
  if (!sel.dataset.wired) {
    sel.dataset.wired = "1";
    sel.addEventListener("change", function () {
      state.ideaCategory = sel.value;
      renderIdeas();
    });
  }
}

function visibleIdeas() {
  if (state.ideaCategory === "all") return state.ideas;
  return state.ideas.filter(function (i) {
    return String(i.category) === state.ideaCategory;
  });
}

function renderIdeas() {
  var table = document.getElementById("ideas-table");
  var body = table.tBodies[0];
  renderCategoryFilter();
  var rows = visibleIdeas();
  document.getElementById("ideas-count").textContent =
    rows.length === state.ideas.length
      ? state.ideas.length + " total"
      : rows.length + " of " + state.ideas.length;
  document.getElementById("ideas-empty").hidden = rows.length > 0;
  wireSortHeaders(table, state.ideaSort, renderIdeas);
  body.textContent = "";

  sortRows(rows, state.ideaSort).forEach(function (idea) {
    var tr = el("tr");
    tr.appendChild(el("td", "num", txt(idea.generation)));
    tr.appendChild(el("td", "mono", txt(idea.id)));
    var title = el("td", "title", txt(idea.title));
    if (idea.hypothesis) title.title = idea.hypothesis;
    tr.appendChild(title);
    var cat = el("td");
    cat.appendChild(pill(idea.category, "cat"));
    tr.appendChild(cat);
    var g = el("td"); g.appendChild(pill(idea.expected_gain)); tr.appendChild(g);
    var r = el("td"); r.appendChild(pill(idea.risk)); tr.appendChild(r);

    var statusCell = el("td");
    var sel = document.createElement("select");
    IDEA_STATUSES.forEach(function (s) {
      var o = document.createElement("option");
      o.value = s; o.textContent = s;
      if (idea.status === s) o.selected = true;
      sel.appendChild(o);
    });
    if (IDEA_STATUSES.indexOf(idea.status) < 0 && idea.status) {
      var o2 = document.createElement("option");
      o2.value = idea.status; o2.textContent = idea.status; o2.selected = true;
      sel.appendChild(o2);
    }
    sel.addEventListener("change", function () {
      saveField(sel, "/api/ideas/" + encodeURIComponent(idea.id) + "/status",
        { status: sel.value }, function () { idea.status = sel.value; });
    });
    statusCell.appendChild(sel);
    tr.appendChild(statusCell);

    var scoreCell = el("td", "num");
    var inp = document.createElement("input");
    inp.type = "number"; inp.step = "0.01";
    inp.value = (idea.score === null || idea.score === undefined) ? "" : idea.score;
    inp.addEventListener("change", function () {
      var v = inp.value.trim() === "" ? null : Number(inp.value);
      saveField(inp, "/api/ideas/" + encodeURIComponent(idea.id) + "/score",
        { score: v }, function () { idea.score = v; });
    });
    scoreCell.appendChild(inp);
    tr.appendChild(scoreCell);

    body.appendChild(tr);
  });
}

function saveField(node, url, body, onOk) {
  node.classList.remove("saved", "savefail");
  jpost(url, body).then(function () {
    onOk();
    node.classList.add("saved");
    setTimeout(function () { node.classList.remove("saved"); }, 1200);
    refresh();
  }).catch(function (err) {
    node.classList.add("savefail");
    node.title = String(err.message || err);
  });
}

/* ---------- fps runs ---------- */

function renderRuns() {
  var table = document.getElementById("runs-table");
  var body = table.tBodies[0];
  document.getElementById("runs-count").textContent = state.runs.length + " total";
  document.getElementById("runs-empty").hidden = state.runs.length > 0;
  wireSortHeaders(table, state.runSort, renderRuns);
  body.textContent = "";

  sortRows(state.runs, state.runSort).forEach(function (run) {
    var tr = el("tr");
    if (run.run_id === state.selectedRun) tr.classList.add("selected");
    tr.appendChild(el("td", "mono", run.run_id));
    tr.appendChild(el("td", "mono", txt(run.idea_id)));
    var armCell = el("td");
    armCell.appendChild(pill(run.arm || "-", run.arm === "variant" ? "running" : "done"));
    if (run.mods_enabled && run.mods_enabled.length) {
      var overlay = el("span", "mono muted", " " + run.mods_enabled.map(function (m) {
        return m.replace(/^aalo-rewrite-\d{8}-\d{6}-/, "");
      }).join(" "));
      overlay.title = run.mods_enabled.join("\n");
      armCell.appendChild(overlay);
    }
    if (run.capped) {
      var cap = el("span", "pill failed", "capped");
      cap.title = "flat on a refresh rate / limiter in every window; excluded from A/B means";
      armCell.appendChild(cap);
    }
    tr.appendChild(armCell);
    var st = el("td"); st.appendChild(pill(run.status)); tr.appendChild(st);
    tr.appendChild(el("td", "mono", shortTime(run.started)));
    tr.appendChild(el("td", "num", num(run.duration_s, 0)));
    tr.appendChild(el("td", "num", num(run.fps_avg, 1)));
    tr.appendChild(el("td", "num", num(run.fps_1pct_low, 1)));
    tr.appendChild(el("td", "num", num(run.frametime_p99_ms, 2)));
    var cr = el("td");
    cr.appendChild(run.crashed === null || run.crashed === undefined
      ? el("span", "pill", "-")
      : pill(run.crashed ? "yes" : "no", run.crashed ? "failed" : "done"));
    tr.appendChild(cr);
    tr.addEventListener("click", function () { selectRun(run.run_id); });
    body.appendChild(tr);
  });
}

function selectRun(runId) {
  state.selectedRun = runId;
  renderRuns();
  jget("/api/runs/" + encodeURIComponent(runId)).then(function (d) {
    state.detail = d;
    renderDetail();
  }).catch(function (err) {
    state.detail = { run_id: runId, error: String(err.message || err) };
    renderDetail();
  });
}

function renderDetail() {
  var panel = document.getElementById("detail-panel");
  var host = document.getElementById("detail-body");
  var d = state.detail;
  host.textContent = "";
  if (!d) { panel.hidden = true; return; }
  panel.hidden = false;
  document.getElementById("detail-id").textContent = d.run_id;

  if (d.error) { host.appendChild(el("p", "empty", d.error)); return; }

  var grid = el("div", "detail-grid");

  var left = el("div", "detail-col");
  left.appendChild(el("h3", null, "Frametime"));
  left.appendChild(buildChart(d.samples || []));
  var note = "samples: " + (d.sample_count || 0);
  if (d.downsampled) note += " (downsampled to " + (d.samples || []).length + " points, peak-preserving)";
  left.appendChild(el("p", "chart-note", note));
  grid.appendChild(left);

  var right = el("div", "detail-col");
  right.appendChild(el("h3", null, "Manifest"));
  var m = d.manifest || {};
  right.appendChild(kvTable([
    ["idea_id", txt(m.idea_id)], ["status", txt(m.status)],
    ["started", shortTime(m.started)], ["finished", shortTime(m.finished)],
    ["exe", txt(m.exe)], ["mo2_profile", txt(m.mo2_profile)],
    ["arm", txt(m.arm)], ["experiment", txt(m.experiment)],
    ["mods toggled", Object.keys(m.config_diff || {}).filter(function (k) { return k.indexOf("mod/") === 0; })
      .map(function (k) { return k.slice(4) + " -> " + (m.config_diff[k] || []).slice(-1)[0]; }).join("; ") || "-"],
    ["notes", txt(m.notes)]
  ]));

  right.appendChild(el("h3", null, "Metrics"));
  var mt = d.metrics || {};
  right.appendChild(kvTable([
    ["fps_avg", num(mt.fps_avg, 2)], ["fps_1pct_low", num(mt.fps_1pct_low, 2)],
    ["frametime_p99_ms", num(mt.frametime_p99_ms, 2)], ["duration_s", num(mt.duration_s, 1)],
    ["load_time_s", num(mt.load_time_s, 1)], ["ram_peak_mb", num(mt.ram_peak_mb, 0)],
    ["vram_peak_mb", num(mt.vram_peak_mb, 0)], ["crashed", mt.crashed === undefined ? "-" : String(!!mt.crashed)]
  ]));

  right.appendChild(el("h3", null, "config_diff"));
  var diff = m.config_diff || {};
  var keys = Object.keys(diff);
  if (!keys.length) {
    right.appendChild(el("p", "empty", "no config changes recorded"));
  } else {
    right.appendChild(kvTable(keys.map(function (k) {
      var pair = diff[k];
      var old = Array.isArray(pair) ? pair[0] : "";
      var neu = Array.isArray(pair) ? pair[1] : pair;
      return [k, String(old) + "  →  " + String(neu)];
    })));
  }
  grid.appendChild(right);
  host.appendChild(grid);
}

function kvTable(pairs) {
  var t = el("table", "kv");
  var tb = document.createElement("tbody");
  pairs.forEach(function (p) {
    var tr = el("tr");
    tr.appendChild(el("td", null, p[0]));
    tr.appendChild(el("td", "mono", p[1]));
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  return t;
}

/* ---------- inline SVG chart ---------- */

var SVGNS = "http://www.w3.org/2000/svg";

function svgEl(name, attrs) {
  var n = document.createElementNS(SVGNS, name);
  for (var k in attrs) if (Object.prototype.hasOwnProperty.call(attrs, k)) {
    n.setAttribute(k, attrs[k]);
  }
  return n;
}

function buildChart(samples) {
  var W = 720, H = 260, PL = 46, PR = 12, PT = 12, PB = 26;
  var svg = svgEl("svg", {
    "class": "chart", viewBox: "0 0 " + W + " " + H,
    preserveAspectRatio: "none", role: "img"
  });
  if (!samples.length) {
    var t = svgEl("text", { x: W / 2, y: H / 2, fill: "#8e97ad", "text-anchor": "middle", "font-size": "13" });
    t.textContent = "no samples.csv for this run";
    svg.appendChild(t);
    return svg;
  }

  var minT = samples[0].t_s, maxT = samples[samples.length - 1].t_s;
  var maxF = 0;
  samples.forEach(function (s) { if (s.frametime_ms > maxF) maxF = s.frametime_ms; });
  if (maxT <= minT) maxT = minT + 1;
  if (maxF <= 0) maxF = 1;
  maxF = maxF * 1.08;

  var iw = W - PL - PR, ih = H - PT - PB;
  function X(t) { return PL + (t - minT) / (maxT - minT) * iw; }
  function Y(f) { return PT + ih - (f / maxF) * ih; }

  var ticks = 4;
  for (var i = 0; i <= ticks; i++) {
    var v = maxF * i / ticks;
    var y = Y(v);
    svg.appendChild(svgEl("line", {
      x1: PL, y1: y, x2: W - PR, y2: y, stroke: "#2b3142", "stroke-width": 1
    }));
    var lbl = svgEl("text", { x: PL - 6, y: y + 4, fill: "#8e97ad", "font-size": "10", "text-anchor": "end" });
    lbl.textContent = v.toFixed(1);
    svg.appendChild(lbl);
  }

  var d = "";
  samples.forEach(function (s, idx) {
    d += (idx ? "L" : "M") + X(s.t_s).toFixed(2) + " " + Y(s.frametime_ms).toFixed(2) + " ";
  });
  svg.appendChild(svgEl("path", {
    d: d, fill: "none", stroke: "#6aa9ff", "stroke-width": "1.1",
    "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke"
  }));

  [[minT, "start"], [maxT, "end"]].forEach(function (p) {
    var tl = svgEl("text", {
      x: p[1] === "start" ? PL : W - PR, y: H - 8,
      fill: "#8e97ad", "font-size": "10", "text-anchor": p[1]
    });
    tl.textContent = p[0].toFixed(1) + " s";
    svg.appendChild(tl);
  });

  var yl = svgEl("text", { x: PL - 6, y: PT + 2, fill: "#8e97ad", "font-size": "10", "text-anchor": "end" });
  yl.textContent = "ms";
  svg.appendChild(yl);
  return svg;
}

/* ---------- refresh loop ---------- */

function setDot(cls) {
  var dot = document.getElementById("refresh-state");
  dot.className = "dot" + (cls ? " " + cls : "");
}

function refresh() {
  setDot("busy");
  return Promise.all([jget("/api/summary"), jget("/api/ideas"), jget("/api/runs"),
                      jget("/api/corpus"), jget("/api/health")])
    .then(function (res) {
      state.summary = res[0];
      state.ideas = res[1].ideas || [];
      state.runs = res[2].runs || [];
      state.corpus = res[3].runs || [];
      document.getElementById("data-dir").textContent = res[4].data_dir || "?";
      renderCorpusTiles();
      renderTrend();
      renderCorpus();
      renderTiles();
      if (!editingIdeas()) renderIdeas();
      renderRuns();
      document.getElementById("last-updated").textContent =
        "updated " + new Date().toLocaleTimeString();
      setDot("");
      return loadCompare();
    })
    .catch(function (err) {
      setDot("error");
      document.getElementById("last-updated").textContent = "error: " + (err.message || err);
    });
}

/* ---------- wiring ---------- */

(function wire() {
  var tabs = document.querySelectorAll("#tabs .tab");
  for (var i = 0; i < tabs.length; i++) {
    (function (btn) {
      btn.addEventListener("click", function () {
        switchView(btn.getAttribute("data-view"));
      });
    })(tabs[i]);
  }
  window.addEventListener("hashchange", function () {
    switchView(location.hash.slice(1) || "corpus");
  });

  document.getElementById("refresh-now").addEventListener("click", function () { refresh(); });
  document.getElementById("detail-close").addEventListener("click", function () {
    state.detail = null;
    state.selectedRun = null;
    renderDetail();
    renderRuns();
  });
  document.getElementById("corpus-detail-close").addEventListener("click", function () {
    state.corpusDetail = null;
    state.selectedCorpus = null;
    renderCorpusDetail();
    renderCorpus();
  });
  ["cmp-a", "cmp-b"].forEach(function (id) {
    document.getElementById(id).addEventListener("change", function () {
      state.cmpA = document.getElementById("cmp-a").value;
      state.cmpB = document.getElementById("cmp-b").value;
      loadCompare();
    });
  });

  switchView(location.hash.slice(1) || "corpus");
})();

refresh();
setInterval(function () {
  if (document.hidden) return;
  refresh().then(function () {
    if (state.selectedRun) selectRun(state.selectedRun);
    if (state.selectedCorpus) selectCorpusRun(state.selectedCorpus);
  });
}, REFRESH_MS);
