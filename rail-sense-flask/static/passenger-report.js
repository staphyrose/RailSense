/* RailSense passenger: Report Issue.
   Sends the report to /api/passenger/reports. The Engineer dashboard reads
   the same rows, so there is nothing to copy or sync here. */
(function () {
  "use strict";

  var optionsEl = document.getElementById("rp-options");
  if (!optionsEl) return;

  var options = JSON.parse(optionsEl.textContent);

  var form = document.getElementById("rp-form");
  var trainSel = document.getElementById("rp-train");
  var locSel = document.getElementById("rp-location");
  var issueSel = document.getElementById("rp-issue");
  var descEl = document.getElementById("rp-desc");
  var countEl = document.getElementById("rp-count");
  var submitBtn = document.getElementById("rp-submit");
  var formErr = document.getElementById("rp-err-form");
  var done = document.getElementById("rp-done");
  var codeEl = document.getElementById("rp-code");
  var againBtn = document.getElementById("rp-again");

  var FIELDS = ["train", "location", "issue_type", "description"];

  function option(value, label, disabled) {
    var o = document.createElement("option");
    o.value = value;
    o.textContent = label;
    if (disabled) { o.disabled = true; o.selected = true; }
    return o;
  }

  function fillTrains() {
    trainSel.textContent = "";
    options.trains.forEach(function (t) {
      trainSel.appendChild(option(t.train_number, t.train_number + " \u00b7 " + t.name));
    });
    var wanted = options.trains.some(function (t) { return t.train_number === options.selected; });
    if (wanted) trainSel.value = options.selected;
  }

  function fillLocations() {
    var train = options.trains.filter(function (t) { return t.train_number === trainSel.value; })[0];
    locSel.textContent = "";
    locSel.appendChild(option("", "Choose a place", true));
    if (!train) return;
    train.locations.forEach(function (l) { locSel.appendChild(option(l, l)); });
  }

  function fillIssues() {
    issueSel.textContent = "";
    issueSel.appendChild(option("", "Choose one", true));
    options.issue_types.forEach(function (i) { issueSel.appendChild(option(i, i)); });
  }

  function updateCount() {
    var left = options.max_description - descEl.value.length;
    countEl.textContent = left + " characters left";
  }

  function clearErrors() {
    FIELDS.forEach(function (f) {
      var el = document.getElementById("rp-err-" + f);
      if (el) el.textContent = "";
    });
    formErr.textContent = "";
  }

  function showErrors(errors) {
    var first = null;
    FIELDS.forEach(function (f) {
      if (errors[f]) {
        var el = document.getElementById("rp-err-" + f);
        if (el) el.textContent = errors[f];
        if (!first) first = f;
      }
    });
    if (first) {
      var target = { train: trainSel, location: locSel, issue_type: issueSel, description: descEl }[first];
      if (target) target.focus();
    }
  }

  trainSel.addEventListener("change", fillLocations);
  descEl.addEventListener("input", updateCount);

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    clearErrors();

    var local = {};
    if (!trainSel.value) local.train = "Choose the train you are travelling on.";
    if (!locSel.value) local.location = "Choose where on the route you noticed it.";
    if (!issueSel.value) local.issue_type = "Choose what you noticed.";
    if (Object.keys(local).length) { showErrors(local); return; }

    submitBtn.disabled = true;
    submitBtn.textContent = "Sending\u2026";

    fetch("/api/passenger/reports", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        train: trainSel.value,
        location: locSel.value,
        issue_type: issueSel.value,
        description: descEl.value
      })
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        return { status: res.status, body: body };
      });
    }).then(function (r) {
      if (r.status === 401) { window.location.href = "/passenger"; return; }
      if (r.body && r.body.ok) {
        codeEl.textContent = r.body.code;
        form.hidden = true;
        done.hidden = false;
        done.setAttribute("tabindex", "-1");
        done.focus();
        return;
      }
      if (r.body && r.body.errors) { showErrors(r.body.errors); return; }
      formErr.textContent = (r.body && r.body.error) || "Your report could not be sent. Please try again.";
    }).catch(function () {
      formErr.textContent = "Could not reach RailSense. Check your connection and try again.";
    }).then(function () {
      submitBtn.disabled = false;
      submitBtn.textContent = "Send report";
    });
  });

  againBtn.addEventListener("click", function () {
    done.hidden = true;
    form.hidden = false;
    locSel.value = "";
    issueSel.value = "";
    descEl.value = "";
    updateCount();
    clearErrors();
    trainSel.focus();
  });

  fillTrains();
  fillLocations();
  fillIssues();
  updateCount();
})();
