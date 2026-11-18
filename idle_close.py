"""
Close a page left open without use, in the browser.

An open Streamlit page keeps calling the server: when its connection drops it polls
/_stcore/health and /_stcore/host-config every couple of seconds. Once the sign-in gate's
12-hour cookie runs out, each of those calls is sent to sign in. On 2026-11-12 and again on
2026-11-18, tabs left open did that until the gate (Pomerium, shared by every app on the
server) ran out of memory and stopped answering for all of them.

After IDLE_CLOSE_MINUTES (default 60, 0 turns it off) without mouse, keyboard, touch or
scrolling, the page's own network calls are switched off and a notice with a Reopen button
covers it. A laptop that slept counts as idle, so a tab opened yesterday closes as soon as
the machine wakes. Copied from the GRN analyser (app/lib/auth.py idle_close).
"""

import json
import os

import streamlit.components.v1 as components

# Runs in the page itself (not in the component's frame), once per page load.
_IDLE_JS = r"""
(function () {
  var w = window.parent;
  if (!w || w.__ftIdleClose) return;
  w.__ftIdleClose = true;
  var LIMIT = %(ms)d, label = %(label)s;
  var s = w.document.createElement("script");
  s.textContent = "(" + function (LIMIT, label) {
    var last = Date.now(), timer = null;
    var bump = function () { last = Date.now(); };
    ["mousemove", "mousedown", "keydown", "wheel", "touchstart", "scroll"].forEach(function (e) {
      window.addEventListener(e, bump, { capture: true, passive: true });
    });
    function close() {
      clearInterval(timer);
      window.__ftClosed = true;
      // Nothing on this page may call the server any more. Calls already waiting never finish.
      window.fetch = function () { return new Promise(function () {}); };
      XMLHttpRequest.prototype.send = function () {};
      window.WebSocket = function () { return { close: function () {}, send: function () {}, readyState: 3, addEventListener: function () {} }; };
      document.title = "Closed - Fidelity Tools";
      var d = document.createElement("div");
      d.setAttribute("role", "dialog");
      d.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:#f2f0ea;font-family:system-ui,sans-serif;overflow:auto";
      d.innerHTML = '<div style="max-width:440px;margin:15vh auto;background:#fff;border:1px solid #d9d4c4;border-radius:8px;padding:28px 32px;color:#222">' +
        '<h2 style="margin:0 0 12px;font-size:22px">This page was closed</h2>' +
        '<p style="color:#555;line-height:1.5;margin:0 0 20px">Nobody had used it for ' + label + ', so it was closed to stop it ' +
        'calling the server in the background. Anything already saved is kept.</p>' +
        '<button type="button" style="background:#F46000;color:#fff;border:0;padding:10px 18px;border-radius:6px;font-size:15px;cursor:pointer">Reopen</button></div>';
      d.querySelector("button").addEventListener("click", function () { location.reload(); });
      document.body.appendChild(d);
    }
    function check() { if (!window.__ftClosed && Date.now() - last >= LIMIT) close(); }
    timer = setInterval(check, Math.min(30000, Math.max(1000, LIMIT / 4)));
    document.addEventListener("visibilitychange", function () { if (!document.hidden) check(); });
  } + ")(" + LIMIT + "," + JSON.stringify(label) + ");";
  w.document.head.appendChild(s);
})();
"""


def _label(minutes: float) -> str:
    if minutes >= 120 and minutes % 60 == 0:
        return f"{int(minutes // 60)} hours"
    if minutes >= 1:
        return f"{minutes:g} minutes"
    return f"{round(minutes * 60)} seconds"


def idle_close() -> None:
    """Close this page in the browser after IDLE_CLOSE_MINUTES without use."""
    try:
        minutes = float(os.environ.get("IDLE_CLOSE_MINUTES", "60"))
    except ValueError:
        minutes = 60.0
    if minutes <= 0:
        return
    js = _IDLE_JS % {"ms": int(minutes * 60_000), "label": json.dumps(_label(minutes))}
    components.html("<script>" + js + "</script>", height=0)
