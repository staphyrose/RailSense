/* RailSense passenger: shared behaviour (drawer, bell, logout). */
(function () {
  "use strict";

  var body = document.body;
  var menuBtn = document.getElementById("menu-btn");
  var scrim = document.getElementById("scrim");
  var bellBtn = document.getElementById("bell-btn");
  var bellPanel = document.getElementById("bell-panel");

  /* ---------- drawer ---------- */
  function setNav(open) {
    body.classList.toggle("nav-open", open);
    scrim.hidden = !open;
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    menuBtn.querySelector("use").setAttribute("href", open ? "#i-x" : "#i-menu");
  }
  menuBtn.addEventListener("click", function () { setNav(!body.classList.contains("nav-open")); });
  scrim.addEventListener("click", function () { setNav(false); });

  /* ---------- notifications ---------- */
  function setBell(open) {
    bellPanel.hidden = !open;
    bellBtn.setAttribute("aria-expanded", open ? "true" : "false");
  }
  bellBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    setBell(bellPanel.hidden);
  });
  document.addEventListener("click", function (e) {
    if (!bellPanel.hidden && !bellPanel.contains(e.target)) setBell(false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (!bellPanel.hidden) { setBell(false); bellBtn.focus(); }
    if (body.classList.contains("nav-open")) { setNav(false); menuBtn.focus(); }
  });
  window.addEventListener("resize", function () {
    if (window.innerWidth >= 1100 && body.classList.contains("nav-open")) setNav(false);
  });

  /* ---------- logout ---------- */
  document.querySelectorAll("[data-logout]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      fetch("/api/passenger/logout", { method: "POST" })
        .catch(function () {})
        .then(function () {
          window.location.href = "/passenger";
        });
    });
  });

  /* ---------- shared helpers ---------- */
  function formatTime(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    var now = new Date();
    var sameDay = d.toDateString() === now.toDateString();
    return sameDay ? time : d.toLocaleDateString([], { day: "numeric", month: "short" }) + ", " + time;
  }

  function renderBell(data) {
    var list = document.getElementById("bell-list");
    var dot = document.getElementById("bell-dot");
    var navDot = document.getElementById("nav-alert-dot");
    var alertOn = !!(data && data.alert && data.alert.active);

    dot.hidden = !alertOn;
    navDot.hidden = !alertOn;
    list.innerHTML = "";

    var items = (data && data.updates) ? data.updates.slice(0, 4) : [];
    if (!items.length) {
      var empty = document.createElement("li");
      empty.className = "bell-empty";
      empty.textContent = "No new updates for your journey.";
      list.appendChild(empty);
      return;
    }
    items.forEach(function (u) {
      var li = document.createElement("li");
      var lamp = document.createElement("span");
      lamp.className = "lamp " + (u.tone === "caution" ? "caution" : u.tone === "info" ? "info" : "ok");
      var wrap = document.createElement("div");
      wrap.appendChild(document.createTextNode(u.text));
      var t = document.createElement("time");
      t.textContent = formatTime(u.time);
      wrap.appendChild(t);
      li.appendChild(lamp);
      li.appendChild(wrap);
      list.appendChild(li);
    });
  }

  window.RS = {
    formatTime: formatTime,
    renderBell: renderBell,
    fetchDashboard: function () {
      return fetch("/api/passenger/dashboard" + window.location.search, { credentials: "same-origin" })
        .then(function (res) {
          if (res.status === 401) { window.location.href = "/passenger"; return null; }
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.json();
        });
    }
  };

  /* Pages other than the dashboard only need the bell. */
  if (body.dataset.page !== "dashboard") {
    window.RS.fetchDashboard().then(function (d) { if (d) renderBell(d); })
      .catch(function () { renderBell(null); });
  }
})();
