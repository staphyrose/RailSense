/* RailSense passenger: My Journey. Renders /api/passenger/journey (shared Engineer data). */
(function () {
  "use strict";

  var POLL_MS = 10000;
  var applied = null;        // {train, from, to} currently shown
  var catalog = [];
  var lastSignature = "";
  var selectedId = null;
  var userPicked = false;    // true once the passenger clicked a section
  var lastData = null;

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function svgIcon(name) {
    var ns = "http://www.w3.org/2000/svg";
    var s = document.createElementNS(ns, "svg");
    s.setAttribute("class", "ic");
    s.setAttribute("aria-hidden", "true");
    var u = document.createElementNS(ns, "use");
    u.setAttribute("href", "#" + name);
    s.appendChild(u);
    return s;
  }

  var LABEL = { ok: "Normal", warning: "Warning", critical: "Critical" };

  function levelOf(x) { return x.completed ? "done" : x.status; }
  function levelLabel(x) { return x.completed ? "Maintenance completed" : LABEL[x.status]; }

  /* ---------------- picker ---------------- */
  function fillSelect(sel, values, chosen) {
    sel.innerHTML = "";
    values.forEach(function (v) {
      var o = document.createElement("option");
      o.value = v.value; o.textContent = v.label;
      if (v.value === chosen) o.selected = true;
      sel.appendChild(o);
    });
  }

  function trainByNumber(n) {
    for (var i = 0; i < catalog.length; i++) if (catalog[i].train_number === n) return catalog[i];
    return null;
  }

  function fillStations(trainNumber, from, to) {
    var t = trainByNumber(trainNumber);
    var names = t ? t.stations : [];
    var opts = names.map(function (n) { return { value: n, label: n }; });
    fillSelect($("jr-from"), opts, from || names[0]);
    fillSelect($("jr-to"), opts, to || names[names.length - 1]);
  }

  function fillPicker(j) {
    fillSelect($("jr-train"), catalog.map(function (t) {
      return { value: t.train_number, label: t.train_number + " \u2013 " + t.name };
    }), j.train_number);
    fillStations(j.train_number, j.from_station, j.to_station);
  }

  function showFormMsg(text) {
    var m = $("jr-form-msg");
    m.hidden = !text;
    m.textContent = text || "";
  }

  /* ---------------- render ---------------- */
  function renderBoard(j) {
    $("jr-category").textContent = j.category;
    $("jr-number").textContent = j.train_number;
    $("jr-src").textContent = j.from_station;
    $("jr-dst").textContent = j.to_station;
    $("jr-name").textContent = j.train_name;
    $("jr-current").textContent = j.current_label;
    $("jr-next").textContent = j.next_station || "\u2013";
    $("jr-dest").textContent = j.destination;
    $("jr-status").textContent = j.status.label;
    $("jr-lamp").className = "lamp live " + (j.status.tone === "critical" ? "alert" : j.status.tone === "warning" ? "caution" : "ok");

    var pct = Math.round(j.progress * 100);
    $("jr-rail-done").style.width = pct + "%";
    $("jr-rail-train").style.left = pct + "%";
    $("jr-rail").setAttribute("aria-label", "Journey progress: " + pct + " percent from " + j.from_station + " to " + j.to_station);

    var note = $("jr-state-note");
    note.hidden = !j.state_note;
    note.textContent = j.state_note || "";
  }

  function renderAlert(alert, sections) {
    var box = $("jr-alert");
    box.innerHTML = "";
    if (!alert.active) return;

    var card = el("div", "alert-card is-critical jr-alert-card");
    var icon = el("div", "alert-icon");
    icon.appendChild(svgIcon("i-warn"));
    card.appendChild(icon);

    var body = el("div", "alert-body");
    body.appendChild(el("h2", null, alert.title));
    body.appendChild(el("p", "alert-msg", alert.message));
    var m = el("p", "jr-maint");
    m.appendChild(svgIcon("i-wrench"));
    m.appendChild(el("span", null, "Maintenance Scheduled. RailSense has notified the maintenance system for this section."));
    body.appendChild(m);
    body.appendChild(el("p", "alert-impact", alert.impact));
    var ul = el("ul", "alert-steps");
    alert.instructions.forEach(function (s) { ul.appendChild(el("li", null, s)); });
    body.appendChild(ul);
    if (alert.more > 0) {
      body.appendChild(el("p", "jr-more", "+" + alert.more + " more affected section" + (alert.more > 1 ? "s" : "") + " on your route."));
    }
    card.appendChild(body);

    var btn = el("button", "btn-primary", "View section");
    btn.type = "button";
    btn.addEventListener("click", function () {
      select(alert.section_id, true);
      $("jr-detail").focus();
    });
    card.appendChild(btn);
    box.appendChild(card);
  }

  function nodeGlyph(level) {
    if (level === "critical") return "!";
    if (level === "warning") return "!";
    if (level === "done") return "\u2713";
    return "";
  }

  function renderTimeline(route, sections) {
    var ol = $("jr-tl");
    ol.innerHTML = "";

    route.stations.forEach(function (s, i) {
      var sec = sections[s.id];
      var level = levelOf(sec);

      var li = el("li", "stn " + s.state + " lv-" + level);
      var btn = el("button", "node-btn");
      btn.type = "button";
      btn.dataset.id = s.id;
      btn.setAttribute("aria-label", s.name + ", " + levelLabel(sec) + (s.state === "current" ? ", train is here" : s.state === "passed" ? ", passed" : ""));
      btn.setAttribute("aria-pressed", s.id === selectedId ? "true" : "false");
      var dot = el("span", "dot", nodeGlyph(level));
      dot.setAttribute("aria-hidden", "true");
      btn.appendChild(dot);
      btn.addEventListener("click", function () { select(s.id, true); });
      li.appendChild(btn);

      var label = el("span", "stn-name", s.name);
      li.appendChild(label);
      if (s.state === "current") li.appendChild(el("span", "tag-train", "Train is here"));
      ol.appendChild(li);

      if (i < route.stations.length - 1) {
        var g = route.segments[i];
        var gsec = sections[g.id];
        var glevel = levelOf(gsec);
        var sl = el("li", "sg lv-" + glevel + (g.passed ? " passed" : ""));
        var gb = el("button", "sg-btn");
        gb.type = "button";
        gb.dataset.id = g.id;
        gb.setAttribute("aria-label", gsec.title + ", " + levelLabel(gsec) + (g.has_train ? ", train is in this section" : ""));
        gb.setAttribute("aria-pressed", g.id === selectedId ? "true" : "false");
        gb.appendChild(el("span", "sg-line"));
        var tags = el("span", "sg-tags");
        if (g.has_train) {
          var t = el("span", "tag-train moving");
          t.appendChild(svgIcon("i-train"));
          t.appendChild(el("span", null, "Train is here"));
          tags.appendChild(t);
        }
        if (glevel !== "ok") tags.appendChild(el("span", "sg-chip lv-" + glevel, glevel === "done" ? "Completed" : glevel === "critical" ? "Maintenance" : "Warning"));
        gb.appendChild(tags);
        gb.addEventListener("click", function () { select(g.id, true); });
        sl.appendChild(gb);
        ol.appendChild(sl);
      }
    });
  }

  function renderDetail(sec) {
    var box = $("jr-detail");
    box.innerHTML = "";
    if (!sec) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    var level = levelOf(sec);
    box.className = "jr-detail lv-" + level;

    var head = el("div", "jr-detail-head");
    var ic = el("span", "jr-detail-icon");
    ic.appendChild(svgIcon(sec.status === "ok" ? "i-check" : "i-warn"));
    head.appendChild(ic);
    var ht = el("div");
    ht.appendChild(el("h3", null, sec.title));
    ht.appendChild(el("span", "jr-badge lv-" + level, levelLabel(sec)));
    head.appendChild(ht);
    box.appendChild(head);

    var intro = el("p", "jr-detail-msg");
    var strong = el("strong", null, sec.headline);
    intro.appendChild(strong);
    intro.appendChild(document.createElement("br"));
    intro.appendChild(document.createTextNode(sec.message));
    box.appendChild(intro);

    var dl = el("dl", "jr-facts");
    function row(k, v) {
      var d = el("div");
      d.appendChild(el("dt", null, k));
      d.appendChild(el("dd", null, v));
      dl.appendChild(d);
    }
    if (sec.maintenance) row("Maintenance", sec.maintenance);
    row("Journey impact", sec.impact);
    row("Last updated", window.RS.formatTime(sec.updated_at));
    box.appendChild(dl);

    if (sec.maintenance_note) {
      var mn = el("p", "jr-maint");
      mn.appendChild(svgIcon("i-wrench"));
      mn.appendChild(el("span", null, sec.maintenance_note));
      box.appendChild(mn);
    }
    if (sec.instructions && sec.instructions.length) {
      var ul = el("ul", "alert-steps");
      sec.instructions.forEach(function (s) { ul.appendChild(el("li", null, s)); });
      box.appendChild(ul);
    }
  }

  function mostSevere(data) {
    var best = null, rank = { ok: 0, done: 1, warning: 2, critical: 3 };
    Object.keys(data.sections).forEach(function (k) {
      var s = data.sections[k], r = rank[levelOf(s)];
      if (r > 0 && (!best || r > best.r)) best = { id: k, r: r };
    });
    return best ? best.id : null;
  }

  function select(id, byUser) {
    if (byUser) userPicked = true;
    selectedId = id;
    if (lastData) {
      document.querySelectorAll("#jr-tl [data-id]").forEach(function (b) {
        b.setAttribute("aria-pressed", b.dataset.id === id ? "true" : "false");
      });
      renderDetail(lastData.sections[id]);
    }
  }

  function render(data) {
    lastData = data;
    catalog = data.trains;
    $("jr-error").hidden = true;
    $("jr-updated").textContent = "Updated " + window.RS.formatTime(data.generated_at);

    var j = data.journey;
    var sig = JSON.stringify([j, data.route, data.sections, data.alert]);
    if (sig === lastSignature) return;
    lastSignature = sig;

    if (!selectedId || !data.sections[selectedId] || !userPicked) {
      selectedId = mostSevere(data) || (selectedId && data.sections[selectedId] ? selectedId : null);
    }

    renderBoard(j);
    renderAlert(data.alert, data.sections);
    renderTimeline(data.route, data.sections);
    renderDetail(selectedId ? data.sections[selectedId] : null);
  }

  /* ---------------- network ---------------- */
  function query() {
    if (!applied) return "";
    return "?train=" + encodeURIComponent(applied.train) +
           "&from=" + encodeURIComponent(applied.from) +
           "&to=" + encodeURIComponent(applied.to);
  }

  function load(first) {
    return fetch("/api/passenger/journey" + query(), { credentials: "same-origin" })
      .then(function (res) {
        if (res.status === 401) { window.location.href = "/passenger"; return null; }
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data) return;
        catalog = data.trains;
        if (first || !applied) {
          applied = { train: data.journey.train_number, from: data.journey.from_station, to: data.journey.to_station };
          fillPicker(applied2form(data.journey));
        }
        render(data);
      })
      .catch(function () { $("jr-error").hidden = false; });
  }

  function applied2form(j) {
    return { train_number: j.train_number, from_station: j.from_station, to_station: j.to_station };
  }

  $("jr-train").addEventListener("change", function () {
    fillStations(this.value);
    showFormMsg("");
  });
  $("jr-from").addEventListener("change", function () { showFormMsg(""); });
  $("jr-to").addEventListener("change", function () { showFormMsg(""); });

  $("jr-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var body = { train: $("jr-train").value, from: $("jr-from").value, to: $("jr-to").value };
    var t = trainByNumber(body.train);
    if (t && t.stations.indexOf(body.from) >= t.stations.indexOf(body.to)) {
      showFormMsg("Destination must come after the source on this train's route.");
      return;
    }
    showFormMsg("");
    var btn = $("jr-save");
    btn.disabled = true;

    fetch("/api/passenger/journey/select", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
      .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, d: d }; }); })
      .then(function (r) {
        if (!r.ok) { showFormMsg(r.d.error || "Could not save your journey."); return; }
        applied = { train: r.d.journey.train_number, from: r.d.journey.from_station, to: r.d.journey.to_station };
        lastSignature = ""; selectedId = null; userPicked = false;
        render(r.d);
      })
      .catch(function () { showFormMsg("Could not reach RailSense. Please try again."); })
      .then(function () { btn.disabled = false; });
  });

  load(true);
  setInterval(function () { if (!document.hidden) load(false); }, POLL_MS);
})();
