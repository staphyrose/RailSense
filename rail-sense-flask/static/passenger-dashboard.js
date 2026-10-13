
/* RailSense passenger dashboard: renders shared backend data. */
(function () {
  "use strict";

  var POLL_MS = 15000;
  var lastSignature = "";

  function at(h, m) {
    var d = new Date();
    d.setHours(h, m, 0, 0);
    return d.toISOString();
  }

  function sampleData(withAlert) {
    var names = ["Mumbai", "Thane", "Kalyan", "Lonavala", "Pune"];

    var stations = names.map(function (n, i) {
      return {
        name: n,
        state: i < 2 ? "passed" : i === 2 ? "current" : "upcoming",
        status: withAlert && n === "Lonavala" ? "caution" : "ok"
      };
    });

    var segments = [
      { caution: false, has_train: false },
      { caution: false, has_train: false },
      { caution: false, has_train: false },
      { caution: false, has_train: false }
    ];

    var updates = [
      {
        time: at(9, 15),
        text: "Journey operating normally.",
        tone: "ok"
      }
    ];

    if (withAlert) {
      updates.unshift({
        time: at(10, 42),
        text: "Maintenance activity near Lonavala.",
        tone: "caution"
      });
    }

    return {
      ok: true,
      source: "sample",
      journey: {
        train_number: "12127",
        train_name: "Mumbai - Pune Express",
        category: "Express",
        from_station: "Mumbai",
        to_station: "Pune",
        current_label: "Kalyan",
        destination: "Pune",
        progress: 0.5,
        status: withAlert
          ? {
              tone: "caution",
              label: "Maintenance Advisory",
              detail: "Maintenance activity near Lonavala."
            }
          : {
              tone: "ok",
              label: "Operating Normally",
              detail: "No active alerts on your route."
            }
      },
      route: {
        stations: stations,
        segments: segments
      },
      alert: withAlert
        ? {
            active: true,
            message: "Maintenance activity has been scheduled near Lonavala.",
            impact: "Your journey may be affected.",
            instructions: [
              "Follow instructions from railway staff.",
              "Check your journey status before travelling."
            ],
            issued_at: at(10, 42)
          }
        : { active: false },
      updates: updates,
      generated_at: new Date().toISOString()
    };
  }

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);

    if (cls) n.className = cls;
    if (text != null) n.textContent = text;

    return n;
  }

  function svgIcon(name, cls) {
    var ns = "http://www.w3.org/2000/svg";
    var s = document.createElementNS(ns, "svg");

    s.setAttribute("class", "ic" + (cls ? " " + cls : ""));
    s.setAttribute("aria-hidden", "true");

    var u = document.createElementNS(ns, "use");
    u.setAttribute("href", "#" + name);
    s.appendChild(u);

    return s;
  }

  /* ALERTS */
  var lastAlertActive = null;

  function renderAlert(alert) {
    var box = $("alert");
    box.innerHTML = "";

    var card = el(
      "div",
      "alert-card " + (alert.active ? "is-active" : "is-clear")
    );

    var icon = el("div", "alert-icon");
    icon.appendChild(svgIcon(alert.active ? "i-warn" : "i-check"));
    card.appendChild(icon);

    var text = el("div", "alert-body");

    var h = el(
      "h2",
      null,
      alert.active ? "Active safety alert" : "No active alerts"
    );

    h.id = "alert-title";
    text.appendChild(h);

    if (alert.active) {
      text.appendChild(el("p", "alert-msg", alert.message));
      text.appendChild(el("p", "alert-impact", alert.impact));

      var steps = el("ul", "alert-steps");

      (alert.instructions || []).forEach(function (s) {
        steps.appendChild(el("li", null, s));
      });

      text.appendChild(steps);
    } else {
      text.appendChild(
        el(
          "p",
          "alert-msg",
          "No active RailSense alerts currently affect your selected journey."
        )
      );
    }

    card.appendChild(text);

    if (alert.active) {
      var btn = el("a", "btn-primary", "View journey");
      btn.href = "/passenger/journey";
      card.appendChild(btn);

      if (lastAlertActive === false) {
        card.classList.add("flash");
      }
    }

    lastAlertActive = !!alert.active;
    box.appendChild(card);
  }

  /* RECENT UPDATES */
  function renderUpdates(updates) {
    var ul = $("update-list");
    ul.innerHTML = "";

    if (!updates.length) {
      ul.appendChild(
        el(
          "li",
          "update-empty",
          "No updates yet. They will appear here when something affects your journey."
        )
      );
      return;
    }

    updates.forEach(function (u) {
      var li = el("li");

      li.appendChild(
        el("span", "update-time", window.RS.formatTime(u.time))
      );

      var tone =
        u.tone === "caution"
          ? "caution"
          : u.tone === "info"
            ? "info"
            : "ok";

      var lamp = el("span", "lamp " + tone);
      lamp.setAttribute("aria-hidden", "true");

      li.appendChild(lamp);
      li.appendChild(el("span", "update-text", u.text));

      ul.appendChild(li);
    });
  }

  /* RENDER DASHBOARD
     Route Preview and Full Route are intentionally removed.
  */
  function render(data, isSample) {
    $("demo-banner").hidden = !isSample;

    var signature = JSON.stringify([
      data.alert,
      data.updates
    ]);

    if (signature !== lastSignature) {
      lastSignature = signature;

      renderAlert(data.alert);
      renderUpdates(data.updates);
      window.RS.renderBell(data);
    }

    $("updated-text").textContent = isSample
      ? "Sample data"
      : "Updated " + window.RS.formatTime(data.generated_at);
  }

  /* LOAD DATA */
  function forcedDemo() {
    var m = /[?&]demo=([^&]*)/.exec(window.location.search);
    return m ? m[1] : null;
  }

  function load() {
    var demo = forcedDemo();

    if (demo) {
      render(sampleData(demo !== "normal"), true);
      return;
    }

    window.RS.fetchDashboard()
      .then(function (data) {
        if (data) render(data, false);
      })
      .catch(function () {
        if (!lastSignature) {
          render(sampleData(true), true);
        }
      });
  }

  load();

  if (!forcedDemo()) {
    setInterval(function () {
      if (!document.hidden) load();
    }, POLL_MS);
  }
})();