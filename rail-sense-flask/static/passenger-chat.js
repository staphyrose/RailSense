/* RailSense passenger: Chat Room. One room per train.
   Renders /api/passenger/chat (polling today; the same payload shape can be
   pushed over a WebSocket later by replacing poll() only). */
(function () {
  "use strict";

  var POLL_MS = 3000;
  var NEAR_BOTTOM_PX = 90;

  var room = null;          // { train_number, ... }
  var msgs = [];            // passenger messages, ascending by id
  var sys = [];             // RailSense System messages (derived, never typed by a passenger)
  var lastId = 0;
  var maxLen = 500;
  var loaded = false;
  var sending = false;
  var nearBottom = true;
  var lastSignature = "";
  var timer = null;

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(name, cls) {
    var ns = "http://www.w3.org/2000/svg";
    var s = document.createElementNS(ns, "svg");
    s.setAttribute("class", "ic" + (cls ? " " + cls : ""));
    s.setAttribute("aria-hidden", "true");
    var u = document.createElementNS(ns, "use");
    u.setAttribute("href", "#c-" + name);
    s.appendChild(u);
    return s;
  }

  var log = $("ch-log");
  var input = $("ch-input");
  var sendBtn = $("ch-send");
  var form = $("ch-form");
  var jump = $("ch-jump");

  var SYS_ICON = { critical: "alert", maintenance: "wrench", completed: "check" };

  /* ---------------- time helpers ---------------- */
  function dayKey(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? "" : d.toDateString();
  }
  function dayLabel(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var now = new Date();
    if (d.toDateString() === now.toDateString()) return "Today";
    var y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return "Yesterday";
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  }
  function clock(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  function stamp(iso) { var t = new Date(iso).getTime(); return isNaN(t) ? 0 : t; }

  /* ---------------- header ---------------- */
  function renderHeader(d) {
    var r = d.room;
    $("ch-train").textContent = r.train_number;
    $("ch-route").textContent = r.from_station + " → " + r.to_station;
    $("ch-name").textContent = r.train_name + " · " + r.category;
    $("ch-room").textContent = "Train " + r.train_number + " Passenger Community";
    $("ch-online").textContent = d.online + (d.online === 1 ? " passenger online" : " passengers online");
    $("ch-note").hidden = !!r.journey_chosen;

    var pin = $("ch-pin");
    if (d.active_alert) {
      pin.hidden = false;
      pin.textContent = "";
      pin.appendChild(icon("shield"));
      pin.appendChild(el("span", null, "RailSense: " + d.active_alert.title + " · " + d.active_alert.location + ". Details are in the chat below."));
    } else {
      pin.hidden = true;
    }
  }

  /* ---------------- timeline ---------------- */
  function sysCard(m) {
    var li = el("li", "chat-sys kind-" + m.kind);
    li.setAttribute("aria-label", "RailSense System message");

    var head = el("div", "chat-sys-head");
    var brand = el("span", "chat-sys-brand");
    brand.appendChild(icon("shield"));
    brand.appendChild(el("span", null, "RailSense System"));
    head.appendChild(brand);
    head.appendChild(el("span", "chat-sys-auto", "Automated"));
    var t = el("time", "chat-time", clock(m.time));
    t.setAttribute("datetime", m.time);
    head.appendChild(t);
    li.appendChild(head);

    var body = el("div", "chat-sys-body");
    var ic = el("span", "chat-sys-icon");
    ic.appendChild(icon(SYS_ICON[m.kind] || "alert"));
    body.appendChild(ic);

    var txt = el("div", "chat-sys-text");
    txt.appendChild(el("p", "chat-sys-title", m.title));
    txt.appendChild(el("p", "chat-sys-msg", m.text));
    txt.appendChild(el("p", "chat-sys-where", m.location));
    if (m.state === "active") txt.appendChild(el("p", "chat-sys-action", m.action));
    body.appendChild(txt);
    li.appendChild(body);
    return li;
  }

  function bubble(m, grouped) {
    var li = el("li", "chat-msg" + (m.mine ? " mine" : "") + (grouped ? " grouped" : ""));
    var wrap = el("div", "chat-bubble");
    if (!grouped) {
      var meta = el("div", "chat-meta");
      meta.appendChild(el("span", "chat-name", m.mine ? "You" : m.name));
      if (!m.mine) meta.appendChild(el("span", "chat-role", "Passenger"));
      var t = el("time", "chat-time", clock(m.time));
      t.setAttribute("datetime", m.time);
      meta.appendChild(t);
      wrap.appendChild(meta);
    }
    wrap.appendChild(el("p", "chat-text", m.text));
    li.appendChild(wrap);
    return li;
  }

  function timeline() {
    var items = [];
    msgs.forEach(function (m) { items.push({ k: "p", m: m, t: stamp(m.time) }); });
    sys.forEach(function (m) { items.push({ k: "s", m: m, t: stamp(m.time) }); });
    items.sort(function (a, b) { return a.t - b.t || (a.k === "p" ? -1 : 1); });
    return items;
  }

  function render(force) {
    var sig = JSON.stringify([msgs.map(function (m) { return m.id; }), sys, loaded]);
    if (!force && sig === lastSignature) return;
    lastSignature = sig;

    var prevCount = log.querySelectorAll(".chat-msg, .chat-sys").length;
    var prevTop = log.scrollTop;
    log.innerHTML = "";

    var items = timeline();
    if (!loaded) {
      log.appendChild(el("li", "chat-empty", "Loading messages…"));
      return;
    }
    if (!items.length) {
      var li = el("li", "chat-empty");
      li.appendChild(icon("chat"));
      li.appendChild(el("p", "chat-empty-title", "No messages yet"));
      li.appendChild(el("p", null, "Be the first to say hello to passengers on Train " + room.train_number + ". RailSense safety messages will also appear here."));
      log.appendChild(li);
      return;
    }

    var lastDay = "", prev = null;
    items.forEach(function (it) {
      var dk = dayKey(it.m.time);
      if (dk && dk !== lastDay) {
        lastDay = dk;
        log.appendChild(el("li", "chat-day", dayLabel(it.m.time)));
        prev = null;
      }
      if (it.k === "s") {
        log.appendChild(sysCard(it.m));
        prev = null;
      } else {
        var grouped = !!(prev && prev.k === "p" && prev.m.name === it.m.name && prev.m.mine === it.m.mine && it.t - prev.t < 180000);
        log.appendChild(bubble(it.m, grouped));
        prev = it;
      }
    });

    var count = log.querySelectorAll(".chat-msg, .chat-sys").length;
    if (force === "bottom" || nearBottom || prevCount === 0) {
      log.scrollTop = log.scrollHeight;
      jump.hidden = true;
    } else {
      log.scrollTop = prevTop;
      if (count > prevCount) jump.hidden = false;
    }
  }

  log.addEventListener("scroll", function () {
    nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < NEAR_BOTTOM_PX;
    if (nearBottom) jump.hidden = true;
  });
  jump.addEventListener("click", function () {
    log.scrollTop = log.scrollHeight;
    jump.hidden = true;
  });

  /* ---------------- network ---------------- */
  function absorb(d) {
    if (room && room.train_number !== d.room.train_number) {
      msgs = []; lastId = 0; lastSignature = "";   // passenger changed journey: new room
      if (d.messages.length === 0) loaded = true;
    }
    room = d.room;
    maxLen = d.max_length || maxLen;
    sys = d.system || [];

    var known = {};
    msgs.forEach(function (m) { known[m.id] = true; });
    d.messages.forEach(function (m) {
      if (!known[m.id]) msgs.push(m);
      if (m.id > lastId) lastId = m.id;
    });
    msgs.sort(function (a, b) { return a.id - b.id; });
    loaded = true;
    renderHeader(d);
    render();
  }

  function poll() {
    return fetch("/api/passenger/chat?since=" + lastId, { credentials: "same-origin" })
      .then(function (res) {
        if (res.status === 401) { window.location.href = "/passenger"; return null; }
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (d) {
        if (!d) return;
        $("ch-error").hidden = true;
        absorb(d);
      })
      .catch(function () { $("ch-error").hidden = false; });
  }

  function schedule() {
    clearInterval(timer);
    timer = setInterval(function () { if (!document.hidden) poll(); }, POLL_MS);
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });

  /* ---------------- composer ---------------- */
  function showSendError(text) {
    var e = $("ch-send-error");
    e.textContent = text || "";
    e.hidden = !text;
  }

  function syncComposer() {
    var len = input.value.trim().length;
    sendBtn.disabled = sending || len === 0 || input.value.length > maxLen;

    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 132) + "px";

    var c = $("ch-count");
    if (input.value.length >= maxLen - 100) {
      c.hidden = false;
      c.textContent = input.value.length + "/" + maxLen;
      c.className = input.value.length > maxLen ? "over" : "";
    } else {
      c.hidden = true;
    }
  }

  function send() {
    var text = input.value.trim();
    if (!text || sending || !room) return;
    sending = true;
    syncComposer();
    showSendError("");

    fetch("/api/passenger/chat/send", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text })
    })
      .then(function (res) {
        if (res.status === 401) { window.location.href = "/passenger"; return null; }
        return res.json().then(function (d) { return { ok: res.ok, d: d }; });
      })
      .then(function (r) {
        sending = false;
        if (!r) return;
        if (!r.ok || !r.d.ok) {
          showSendError((r.d && r.d.error) || "Message could not be sent. Please try again.");
          syncComposer();
          return;
        }
        var m = r.d.message;
        if (!msgs.some(function (x) { return x.id === m.id; })) msgs.push(m);
        if (m.id > lastId) lastId = m.id;
        msgs.sort(function (a, b) { return a.id - b.id; });
        input.value = "";
        syncComposer();
        render("bottom");
        input.focus();
      })
      .catch(function () {
        sending = false;
        showSendError("Message could not be sent. Check your connection and try again.");
        syncComposer();
      });
  }

  form.addEventListener("submit", function (e) { e.preventDefault(); send(); });
  input.addEventListener("input", function () { showSendError(""); syncComposer(); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  });
  // Keep the on-screen keyboard open when tapping Send.
  sendBtn.addEventListener("pointerdown", function (e) { e.preventDefault(); });

  /* ---------------- mobile keyboard ----------------
     The room is a fixed-height column. When the on-screen keyboard opens the
     visual viewport shrinks; follow it so the composer stays above the keys. */
  if (window.visualViewport) {
    var vv = window.visualViewport;
    var fit = function () {
      document.documentElement.style.setProperty("--vvh", vv.height + "px");
      if (document.activeElement === input) {
        window.scrollTo(0, 0);
        if (nearBottom) log.scrollTop = log.scrollHeight;
      }
    };
    vv.addEventListener("resize", fit);
    vv.addEventListener("scroll", fit);
    fit();
  }

  syncComposer();
  render(true);
  poll();
  schedule();
})();
