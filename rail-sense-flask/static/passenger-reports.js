/* RailSense passenger: My Reports.
   Lists this passenger's own reports and their current status. The status is
   set by the engineer on the Engineer dashboard, so this page just re-reads
   the shared rows every few seconds. */
(function () {
  "use strict";

  var listEl = document.getElementById("rps-list");
  var emptyEl = document.getElementById("rps-empty");
  var errEl = document.getElementById("rps-error");
  if (!listEl) return;

  var STATUS_CLASS = {
    "Submitted": "rp-st-submitted",
    "Under Review": "rp-st-review",
    "Resolved": "rp-st-resolved"
  };

  var STATUS_NOTE = {
    "Submitted": "Sent to the railway engineers.",
    "Under Review": "An engineer is looking into it.",
    "Resolved": "The engineers have dealt with this report."
  };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function when(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  }

  function card(r) {
    var li = el("li", "rp-item");

    var top = el("div", "rp-item-top");
    top.appendChild(el("span", "rp-code", r.code));

    var pill = el("span", "rp-status " + (STATUS_CLASS[r.status] || ""));
    pill.appendChild(el("span", "rp-dot"));
    pill.lastChild.setAttribute("aria-hidden", "true");
    pill.appendChild(el("span", null, r.status));
    top.appendChild(pill);
    li.appendChild(top);

    li.appendChild(el("p", "rp-what", r.issue_type));
    li.appendChild(el("p", "rp-where", "Train " + r.train_number + " \u00b7 " + r.location));
    if (r.description) li.appendChild(el("p", "rp-desc", r.description));
    li.appendChild(el("p", "rp-note", STATUS_NOTE[r.status] || ""));
    li.appendChild(el("p", "rp-time", "Sent " + when(r.created_at) +
      (r.updated_at !== r.created_at ? " \u00b7 Updated " + when(r.updated_at) : "")));
    return li;
  }

  function render(reports) {
    listEl.textContent = "";
    reports.forEach(function (r) { listEl.appendChild(card(r)); });
    emptyEl.hidden = reports.length > 0;
  }

  function load() {
    fetch("/api/passenger/reports", { credentials: "same-origin" })
      .then(function (res) {
        if (res.status === 401) { window.location.href = "/passenger"; return null; }
        return res.json();
      })
      .then(function (data) {
        if (!data || !data.ok) return;
        errEl.textContent = "";
        render(data.reports);
      })
      .catch(function () {
        errEl.textContent = "Could not refresh your reports. Showing the last update.";
      });
  }

  load();
  setInterval(function () {
    if (document.visibilityState === "visible") load();
  }, 8000);
})();
