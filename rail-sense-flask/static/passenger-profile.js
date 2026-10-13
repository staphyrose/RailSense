/* RailSense passenger: Profile page.
   Loads /api/passenger/profile and saves details, SMS preference and password. */
(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }

  var nameIn = $("pf-name");
  var phoneIn = $("pf-phone");
  var sms = $("pf-sms");

  function initials(name) {
    var parts = String(name || "").split(/\s+/).filter(Boolean);
    return (parts.slice(0, 2).map(function (p) { return p[0]; }).join("") || "P").toUpperCase();
  }

  function setStatus(id, text, ok) {
    var e = $(id);
    e.textContent = text || "";
    e.className = "form-status" + (text ? (ok ? " ok" : " err") : "");
  }

  function fieldError(id, text) { $(id).textContent = text || ""; }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (res) {
      if (res.status === 401) { window.location.href = "/passenger"; return null; }
      return res.json().then(function (d) { return { ok: res.ok, d: d }; });
    });
  }

  /* keep the header and sidebar in step with a changed name */
  function applyName(name) {
    $("pf-title").textContent = name;
    $("pf-avatar").textContent = initials(name);
    document.querySelectorAll(".profile-name").forEach(function (n) { n.textContent = name; });
    document.querySelectorAll(".avatar").forEach(function (n) { n.textContent = initials(name); });
  }

  function fill(d) {
    nameIn.value = d.name || "";
    $("pf-ident").value = d.identifier || "";
    phoneIn.value = d.phone || "";
    sms.checked = !!d.sms_alerts;
    $("pf-login").textContent = "Passenger account";
    if (d.member_since) {
      var s = $("pf-since");
      s.textContent = "Member since " + d.member_since;
      s.hidden = false;
    }
    var j = d.journey;
    $("jr-train").textContent = j ? j.train_number + " · " + j.train_name : "No journey chosen yet";
    $("jr-route").textContent = j && j.route ? j.route : "—";
    $("jr-reports").textContent = String(d.reports_count || 0);
    applyName(d.name || "Passenger");
  }

  function load() {
    fetch("/api/passenger/profile", { credentials: "same-origin" })
      .then(function (res) {
        if (res.status === 401) { window.location.href = "/passenger"; return null; }
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (d) { if (d) { $("pf-error").hidden = true; fill(d); } })
      .catch(function () { $("pf-error").hidden = false; });
  }

  /* ---------- account details ---------- */
  phoneIn.addEventListener("input", function () {
    phoneIn.value = phoneIn.value.replace(/\D/g, "").slice(0, 10);
  });

  $("acc-form").addEventListener("submit", function (e) {
    e.preventDefault();
    fieldError("pf-name-err", "");
    fieldError("pf-phone-err", "");
    setStatus("acc-status", "");

    var name = nameIn.value.trim();
    var phone = phoneIn.value.trim();
    var bad = false;
    if (!name) { fieldError("pf-name-err", "Full name is required."); bad = true; }
    if (!/^\d{10}$/.test(phone)) { fieldError("pf-phone-err", "Phone number must be exactly 10 digits."); bad = true; }
    if (bad) return;

    var btn = $("acc-save");
    btn.disabled = true;
    post("/api/passenger/profile", { name: name, phone: phone }).then(function (r) {
      btn.disabled = false;
      if (!r) return;
      if (!r.ok || !r.d.ok) {
        var errs = (r.d && r.d.errors) || {};
        if (errs.name) fieldError("pf-name-err", errs.name);
        if (errs.phone) fieldError("pf-phone-err", errs.phone);
        if (!errs.name && !errs.phone) setStatus("acc-status", (r.d && r.d.error) || "Could not save. Please try again.", false);
        return;
      }
      applyName(r.d.name);
      nameIn.value = r.d.name;
      setStatus("acc-status", "Details saved.", true);
    }).catch(function () {
      btn.disabled = false;
      setStatus("acc-status", "Could not save. Check your connection and try again.", false);
    });
  });

  /* ---------- SMS preference ---------- */
  sms.addEventListener("change", function () {
    var wanted = sms.checked;
    setStatus("sms-status", "");
    post("/api/passenger/profile", { sms_alerts: wanted }).then(function (r) {
      if (!r) return;
      if (!r.ok || !r.d.ok) {
        sms.checked = !wanted;
        setStatus("sms-status", "Could not update. Please try again.", false);
        return;
      }
      setStatus("sms-status", wanted ? "SMS alerts turned on." : "SMS alerts turned off.", true);
    }).catch(function () {
      sms.checked = !wanted;
      setStatus("sms-status", "Could not update. Check your connection.", false);
    });
  });

  /* ---------- password ---------- */
  $("pw-form").addEventListener("submit", function (e) {
    e.preventDefault();
    setStatus("pw-status", "");
    var cur = $("pw-current").value;
    var nw = $("pw-new").value;
    var cf = $("pw-confirm").value;

    if (!cur || !nw) { setStatus("pw-status", "Fill in your current and new password.", false); return; }
    if (nw !== cf) { setStatus("pw-status", "New passwords do not match.", false); return; }

    var btn = $("pw-save");
    btn.disabled = true;
    post("/api/passenger/profile/password", { current: cur, password: nw }).then(function (r) {
      btn.disabled = false;
      if (!r) return;
      if (!r.ok || !r.d.ok) {
        setStatus("pw-status", (r.d && r.d.error) || "Could not update password.", false);
        return;
      }
      $("pw-form").reset();
      setStatus("pw-status", "Password updated.", true);
    }).catch(function () {
      btn.disabled = false;
      setStatus("pw-status", "Could not update. Check your connection and try again.", false);
    });
  });

  load();
})();
