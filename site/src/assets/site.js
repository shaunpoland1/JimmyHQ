// The site's only script. The pages work without it; it adds three things:
// the drawn Bench answers its buttons and writes each decision to the drawn Tape,
// the Tape's times follow the visitor's clock and a few events arrive while it is open,
// and a code block gets a Copy button.
(function () {
  "use strict";

  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function clock(ms) {
    try {
      return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch (e) {
      return new Date(ms).toTimeString().slice(0, 8);
    }
  }

  // --- the Tape --------------------------------------------------------------------------

  var events = document.querySelector("[data-events]");
  var said = document.querySelector("[data-said]");

  function chevron() {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS(ns, "use");
    use.setAttribute("href", "#i-chev");
    svg.appendChild(use);
    return svg;
  }

  function print(kind, actor) {
    if (!events) return;
    var row = el("li", "ev" + (reduce ? "" : " new"));
    var now = Date.now();
    var time = el("time", "", clock(now));
    time.setAttribute("datetime", new Date(now).toISOString());
    row.appendChild(chevron());
    row.appendChild(time);
    row.appendChild(el("span", "chip", kind));
    row.appendChild(el("span", "who", actor));
    events.insertBefore(row, events.firstChild);
    while (events.children.length > 12) events.removeChild(events.lastChild);
  }

  if (events) {
    var now = Date.now();
    Array.prototype.forEach.call(events.querySelectorAll("time[data-ago]"), function (t) {
      var at = now - Number(t.getAttribute("data-ago")) * 1000;
      t.textContent = clock(at);
      t.setAttribute("datetime", new Date(at).toISOString());
    });
    // A few events arrive while the page is open, then it goes quiet.
    var script = [
      ["model.called", "app:reader"], ["lookup.fetched", "app:reader"], ["task.filed", "agent:moss"],
      ["model.called", "agent:moss"], ["village.said", "agent:scout"], ["memory.recalled", "agent:scout"],
    ];
    if (!reduce) {
      var timer = window.setInterval(function () {
        if (document.hidden) return;
        var next = script.shift();
        if (!next) return window.clearInterval(timer);
        print(next[0], next[1]);
      }, 4200);
    }
  }

  // --- the Bench -------------------------------------------------------------------------

  var demo = document.querySelector("[data-demo]");
  if (demo) {
    var rows = Array.prototype.slice.call(demo.querySelectorAll("[data-card]"));
    var panes = Array.prototype.slice.call(demo.querySelectorAll("[data-pane]"));
    var empty = demo.querySelector("[data-empty]");
    var hint = demo.querySelector("[data-hint]");
    var needs = demo.querySelectorAll('[data-count="needs"]');
    var waiting = demo.querySelector('[data-count="waiting"]');
    var sum = demo.querySelector("[data-sum]");
    var open = rows.map(function () { return true; });
    var parked = 0;
    var selected = 0;
    if (hint) hint.hidden = false;

    var count = function () {
      var left = open.filter(Boolean).length;
      Array.prototype.forEach.call(needs, function (n) { n.textContent = String(left); });
      if (waiting) waiting.textContent = String(parked);
      if (sum) {
        sum.hidden = left === 0;
        var tally = {};
        rows.forEach(function (r, j) {
          if (!open[j]) return;
          var who = r.querySelector("b").textContent;
          tally[who] = (tally[who] || 0) + 1;
        });
        sum.textContent = "";
        sum.appendChild(el("b", "", String(left)));
        sum.appendChild(document.createTextNode(" in this lane" + Object.keys(tally).map(function (k) {
          return " · " + k + " " + tally[k];
        }).join("")));
      }
      return left;
    };

    var select = function (i) {
      selected = i;
      rows.forEach(function (r, j) {
        if (j === i) r.setAttribute("aria-current", "true");
        else r.removeAttribute("aria-current");
      });
      panes.forEach(function (p, j) { p.hidden = j !== i; });
      if (empty) empty.hidden = i !== -1;
    };

    var settle = function (i, label, moves) {
      var pane = panes[i];
      var actions = pane.querySelector("[data-actions]");
      var reason = pane.querySelector(".reason");
      if (reason) reason.remove();
      actions.hidden = true;
      var box = el("div", "decided");
      box.setAttribute("role", "status");
      box.appendChild(el("b", "", label));
      box.appendChild(el("span", "muted", " · decided just now"));
      pane.insertBefore(box, pane.querySelector(".label"));
      print("bench.decided", "owner");
      if (said) said.textContent = label + ". The decision is on the Tape.";
      open[i] = false;
      if (moves) parked += 1;
      window.setTimeout(function () {
        rows[i].parentNode.hidden = true;
        var next = open.indexOf(true);
        count();
        select(next);
      }, reduce ? 300 : 1100);
    };

    var askReason = function (i, button) {
      var pane = panes[i];
      var box = pane.querySelector(".reason");
      if (box) {
        var input = box.querySelector("input");
        if (input.value.trim()) return settle(i, "No", false);
        var help = box.querySelector(".help");
        help.textContent = "Say why first. The reason goes on the record with your No.";
        help.className = "help error";
        input.setAttribute("aria-invalid", "true");
        input.focus();
        return;
      }
      box = el("div", "reason");
      var id = "reason-" + i;
      var label = el("label", "label", "Reason for No · required");
      label.setAttribute("for", id);
      var field = el("input");
      field.id = id;
      field.type = "text";
      field.autocomplete = "off";
      field.setAttribute("aria-describedby", id + "-help");
      var help2 = el("p", "help", "Say why, then press Enter or No again. Escape cancels.");
      help2.id = id + "-help";
      field.addEventListener("keydown", function (event) {
        if (event.key === "Enter") { event.preventDefault(); askReason(i, button); }
        if (event.key === "Escape") { box.remove(); button.focus(); }
      });
      box.appendChild(label);
      box.appendChild(field);
      box.appendChild(help2);
      pane.querySelector("[data-actions]").insertAdjacentElement("beforebegin", box);
      field.focus();
    };

    rows.forEach(function (row, i) {
      row.addEventListener("click", function () { select(i); });
    });
    panes.forEach(function (pane, i) {
      var buttons = pane.querySelectorAll("[data-actions] button");
      buttons[0].addEventListener("click", function () { settle(i, "Approve", false); });
      buttons[1].addEventListener("click", function () { settle(i, "Not yet", true); });
      buttons[2].addEventListener("click", function () { askReason(i, buttons[2]); });
    });
    var reset = demo.querySelector("[data-reset]");
    if (reset) {
      reset.addEventListener("click", function () {
        open = rows.map(function () { return true; });
        parked = 0;
        rows.forEach(function (r) { r.parentNode.hidden = false; });
        panes.forEach(function (p) {
          var box = p.querySelector(".decided");
          if (box) box.remove();
          p.querySelector("[data-actions]").hidden = false;
        });
        count();
        select(0);
        rows[0].focus();
      });
    }
  }

  // --- Copy buttons ----------------------------------------------------------------------

  if (navigator.clipboard) {
    Array.prototype.forEach.call(document.querySelectorAll("pre[data-copy]"), function (pre) {
      var button = el("button", "btn btn-sm copy", "Copy");
      button.type = "button";
      button.setAttribute("aria-label", "Copy these commands");
      button.addEventListener("click", function () {
        var text = pre.querySelector("code").textContent.replace(/^#.*$\n?/gm, "");
        navigator.clipboard.writeText(text).then(function () {
          button.textContent = "Copied";
          window.setTimeout(function () { button.textContent = "Copy"; }, 1500);
        }, function () {
          button.textContent = "Select and copy";
        });
      });
      pre.appendChild(button);
    });
  }
})();
