/* RailSense passenger: Emergency Help.
   The cards and tel: links are rendered by the server (so the page works
   even if this script fails). This script only adds:
   - a "Copy number" button on devices that cannot place calls (desktop)
   - the passenger's train details to quote to the operator */
(function () {
  "use strict";

  /* ---------- copy number (desktop only) ---------- */
  var canHover = window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy") ? resolve() : reject(); } catch (e) { reject(e); }
      document.body.removeChild(ta);
    });
  }

  document.querySelectorAll(".em-copy").forEach(function (btn) {
    if (canHover) btn.hidden = false;
    var label = btn.querySelector(".em-copy-label");
    btn.addEventListener("click", function () {
      copyText(btn.getAttribute("data-copy")).then(function () {
        label.textContent = "Copied";
      }, function () {
        label.textContent = "Copy failed";
      }).then(function () {
        setTimeout(function () { label.textContent = "Copy number"; }, 2000);
      });
    });
  });

  /* ---------- journey details to quote ---------- */
  function row(dl, term, value) {
    var d = document.createElement("div");
    var dt = document.createElement("dt");
    var dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = value;
    d.appendChild(dt);
    d.appendChild(dd);
    dl.appendChild(d);
  }

  if (window.RS && window.RS.fetchDashboard) {
    window.RS.fetchDashboard().then(function (d) {
      if (!d || !d.journey) return;
      var j = d.journey;
      var dl = document.getElementById("em-facts");
      dl.textContent = "";
      row(dl, "Your train", j.train_number + " · " + j.train_name);
      row(dl, "Train is now", j.current_label);
      row(dl, "Heading to", j.destination);
      dl.hidden = false;
    }).catch(function () { /* details are optional */ });
  }
})();
