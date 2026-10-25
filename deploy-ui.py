#!/usr/bin/env python3
"""
Point-and-click deployment UI for the CTOP middleware.

    sudo python3 deploy-ui.py

Then open the URL it prints and press the buttons.

WHY THIS IS A COMMAND AND NOT JUST AN HTML FILE
A page opened from the filesystem cannot install Docker, write to
/etc/systemd, or configure a firewall — browsers sandbox local pages away
from the operating system on purpose, and that sandbox is not something to
be clever about. So this script is the small local web server that actually
holds the privileges, and the page it serves is only a remote control for
it. One command to start, then everything else is clicking.

Standard library only: an installer that needs packages installed before it
can install anything is not an installer. Works on a stock Ubuntu 22.04 or
24.04 server with nothing added.

SECURITY
  * Binds to 127.0.0.1 by default. This server runs shell commands as root;
    it must not be reachable from the network. To use it from your laptop,
    forward the port over SSH (the page tells you how):
        ssh -L 8099:127.0.0.1:8099 user@server
  * Every request needs a random token generated at startup and printed to
    the terminal, so a process on the same machine cannot drive it blindly.
  * --host 0.0.0.0 exists for convenience and prints a warning. Prefer the
    SSH tunnel.
  * Only a fixed list of actions can be run. The browser never sends a
    command; it sends an action name that is looked up in ACTIONS below.
"""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = secrets.token_urlsafe(24)
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

# The only things the UI is allowed to run. The browser sends a key from
# this table, never a command line.
ACTIONS = {
    "check": {
        "label": "Check the server",
        "cmd": ["bash", "scripts/preflight.sh"],
        "needs_root": False,
        "blurb": "Verifies the three secret files and that Docker is usable. Changes nothing.",
    },
    "setup": {
        "label": "Set up this server (first time only)",
        "cmd": ["bash", "scripts/bootstrap-ubuntu.sh", "--skip-start"],
        "needs_root": True,
        "blurb": "Installs Docker, the firewall, auto-start on reboot, nightly backups and monitoring. Safe to run again.",
    },
    "deploy": {
        "label": "Deploy",
        "cmd": ["bash", "scripts/deploy.sh", "--skip-tests"],
        "needs_root": False,
        "blurb": "Builds and starts the app, waits for it to answer, and puts the old version back automatically if the new one fails.",
    },
    "rollback": {
        "label": "Undo last deploy",
        "cmd": ["bash", "scripts/rollback.sh"],
        "needs_root": False,
        "blurb": "Returns to the previously deployed version.",
    },
    "backup": {
        "label": "Back up now",
        "cmd": ["bash", "scripts/backup.sh"],
        "needs_root": False,
        "blurb": "Saves encryption.key, the Firebase key and the device database into backups/.",
    },
    "watchdog": {
        "label": "Are readings reaching CTOP?",
        "cmd": ["bash", "scripts/watchdog.sh"],
        "needs_root": False,
        "blurb": "Checks whether deliveries are succeeding or failing right now.",
    },
    "status": {
        "label": "Show status",
        "cmd": ["docker", "compose", "ps"],
        "needs_root": False,
        "blurb": "Which containers are running.",
    },
    "logs": {
        "label": "Recent logs",
        "cmd": ["docker", "compose", "logs", "--tail=120", "middleware"],
        "needs_root": False,
        "blurb": "The last 120 log lines from the app.",
    },
}

# ---------------------------------------------------------------------------
# Job runner: one at a time, output buffered so the page can poll for it.
# ---------------------------------------------------------------------------


class Job:
    def __init__(self, key):
        self.key = key
        self.lines = []
        self.done = False
        self.returncode = None
        self.started = time.time()
        self.lock = threading.Lock()

    def add(self, text):
        with self.lock:
            self.lines.append(text)

    def snapshot(self, start):
        with self.lock:
            return self.lines[start:], len(self.lines), self.done, self.returncode


CURRENT = {"job": None}
CURRENT_LOCK = threading.Lock()


def run_job(key):
    spec = ACTIONS[key]
    job = Job(key)
    with CURRENT_LOCK:
        running = CURRENT["job"]
        if running is not None and not running.done:
            return None, "Something is already running — wait for it to finish."
        CURRENT["job"] = job

    cmd = list(spec["cmd"])
    if spec["needs_root"] and not IS_ROOT:
        # Non-interactive sudo: a web request cannot type a password, so fail
        # with an explanation rather than hanging on a prompt forever.
        cmd = ["sudo", "-n"] + cmd

    def worker():
        job.add(f"$ {' '.join(cmd)}\n")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=HERE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "dumb"},
            )
            for line in proc.stdout:
                job.add(line)
            proc.wait()
            job.returncode = proc.returncode
            if proc.returncode == 0:
                job.add("\n*** FINISHED OK ***\n")
            else:
                job.add(f"\n*** FAILED (exit code {proc.returncode}) ***\n")
                if "sudo" in cmd[0] and proc.returncode in (1, 126, 127):
                    job.add(
                        "\nThis step needs administrator rights. Stop this tool "
                        "and start it again with:\n    sudo python3 deploy-ui.py\n"
                    )
        except FileNotFoundError as exc:
            job.returncode = 127
            job.add(f"\n*** COULD NOT START: {exc} ***\n")
        except Exception as exc:  # noqa: BLE001 - surfaced to the page
            job.returncode = 1
            job.add(f"\n*** ERROR: {type(exc).__name__}: {exc} ***\n")
        finally:
            job.done = True

    threading.Thread(target=worker, daemon=True).start()
    return job, None


# ---------------------------------------------------------------------------
# Server state shown in the page header
# ---------------------------------------------------------------------------


def http_ok(url, timeout=3):
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def server_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def gather_state():
    secrets_state = {}
    for name in (".env", "encryption.key", "service-account-key.json"):
        path = os.path.join(HERE, name)
        if os.path.isdir(path):
            secrets_state[name] = "directory"
        elif os.path.isfile(path) and os.path.getsize(path) > 0:
            secrets_state[name] = "ok"
        elif os.path.isfile(path):
            secrets_state[name] = "empty"
        else:
            secrets_state[name] = "missing"

    docker = shutil.which("docker") is not None
    compose = False
    if docker:
        try:
            compose = (
                subprocess.run(
                    ["docker", "compose", "version"],
                    capture_output=True,
                    timeout=20,
                ).returncode
                == 0
            )
        except Exception:
            compose = False

    healthy = http_ok("http://127.0.0.1:8090/health")
    return {
        "secrets": secrets_state,
        "docker": docker,
        "compose": compose,
        "root": IS_ROOT,
        "healthy": healthy,
        "ip": server_ip(),
        "dir": HERE,
        "app_url": f"http://{server_ip()}/",
        "app_url_direct": f"http://{server_ip()}:8090/",
    }


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deploy — CTOP Middleware</title>
<style>
  :root{
    --bg:#0f1518; --panel:#161f23; --panel2:#1c272c; --line:#27353b;
    --ink:#e6eef0; --dim:#93a6ac; --accent:#36b9a6; --accent-ink:#06211d;
    --ok:#4ec98b; --warn:#e0b355; --bad:#f08a76;
    --mono:ui-monospace,Consolas,"Cascadia Mono",Menlo,monospace;
    --sans:system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
       line-height:1.55;padding:clamp(1rem,3vw,2.25rem)}
  .wrap{max-width:60rem;margin:0 auto;display:flex;flex-direction:column;gap:1.4rem}
  header{border-bottom:1px solid var(--line);padding-bottom:1rem}
  h1{margin:0;font-size:clamp(1.5rem,4vw,2.1rem);letter-spacing:-.02em}
  .sub{color:var(--dim);margin:.3rem 0 0;font-size:.95rem}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.7rem}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:.8rem .9rem}
  .card h3{margin:0 0 .45rem;font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);font-family:var(--mono)}
  .row{display:flex;align-items:center;gap:.5rem;font-family:var(--mono);font-size:.82rem;padding:.12rem 0}
  .dot{width:.6rem;height:.6rem;border-radius:50%;flex:none;background:var(--dim)}
  .dot.ok{background:var(--ok)} .dot.bad{background:var(--bad)} .dot.warn{background:var(--warn)}
  .actions{display:flex;flex-wrap:wrap;gap:.6rem}
  button{font-family:var(--sans);font-size:.95rem;font-weight:600;cursor:pointer;
         border:1px solid var(--line);background:var(--panel2);color:var(--ink);
         padding:.6rem .95rem;border-radius:7px;transition:.12s}
  button:hover:not(:disabled){border-color:var(--accent);transform:translateY(-1px)}
  button:disabled{opacity:.45;cursor:not-allowed;transform:none}
  button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-size:1.05rem;padding:.75rem 1.4rem}
  button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .blurb{color:var(--dim);font-size:.85rem;margin:.1rem 0 0}
  #log{background:#0a1012;border:1px solid var(--line);border-radius:8px;
       font-family:var(--mono);font-size:.8rem;line-height:1.5;padding:.85rem;
       height:26rem;overflow:auto;white-space:pre-wrap;word-break:break-word;color:#cfe0e3}
  #log .ok{color:var(--ok);font-weight:700} #log .bad{color:var(--bad);font-weight:700}
  #log .cmd{color:var(--accent)} #log .warn{color:var(--warn)}
  .banner{border-radius:8px;padding:.9rem 1.05rem;display:none;border:1px solid}
  .banner.show{display:block}
  .banner.good{background:#11271f;border-color:var(--ok)}
  .banner.bad{background:#2a1512;border-color:var(--bad)}
  .banner a{color:var(--accent);font-family:var(--mono);font-size:1.05rem}
  .spin{display:inline-block;width:.75rem;height:.75rem;border:2px solid var(--dim);
        border-top-color:var(--accent);border-radius:50%;animation:s .8s linear infinite;vertical-align:-1px}
  @keyframes s{to{transform:rotate(360deg)}}
  @media (prefers-reduced-motion:reduce){.spin{animation:none}button:hover{transform:none}}
  code{background:var(--panel2);padding:.08em .35em;border-radius:3px;font-family:var(--mono);font-size:.85em}
  .hint{color:var(--dim);font-size:.85rem}
</style></head><body><div class="wrap">

<header>
  <h1>Deploy the CTOP Middleware</h1>
  <p class="sub">Press the buttons in order. Everything runs on this server; output appears live below.</p>
</header>

<div id="banner" class="banner"></div>

<div class="grid">
  <div class="card"><h3>Secret files</h3><div id="s-secrets"></div></div>
  <div class="card"><h3>Server</h3><div id="s-server"></div></div>
  <div class="card"><h3>Application</h3><div id="s-app"></div></div>
</div>

<div class="card">
  <h3>Step 1 &middot; first time only</h3>
  <div class="actions">
    <button data-act="check">Check the server</button>
    <button data-act="setup">Set up this server</button>
  </div>
  <p class="blurb" id="b1">Check first. Then set up installs Docker, the firewall, reboot auto-start, backups and monitoring.</p>
</div>

<div class="card">
  <h3>Step 2 &middot; every time</h3>
  <div class="actions">
    <button class="primary" data-act="deploy">Deploy now</button>
    <button data-act="rollback">Undo last deploy</button>
  </div>
  <p class="blurb">Deploy builds and starts the app, waits for it to answer, and automatically puts the old version back if the new one will not start.</p>
</div>

<div class="card">
  <h3>Anytime</h3>
  <div class="actions">
    <button data-act="status">Show status</button>
    <button data-act="logs">Recent logs</button>
    <button data-act="watchdog">Readings reaching CTOP?</button>
    <button data-act="backup">Back up now</button>
  </div>
</div>

<div id="log">Ready. Output will appear here.
</div>
<p class="hint">Keep this page open while a step runs. Closing it does not stop the step.</p>

</div><script>
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const logEl = document.getElementById("log");
const banner = document.getElementById("banner");
let polling = null, cursor = 0, busy = false;

function api(path, opts={}){
  const sep = path.includes("?") ? "&" : "?";
  return fetch(path + sep + "t=" + encodeURIComponent(TOKEN), opts);
}
function setBusy(b){
  busy = b;
  document.querySelectorAll("button[data-act]").forEach(x => x.disabled = b);
}
function append(text){
  const frag = document.createDocumentFragment();
  for (const raw of text.split("\n")) {
    if (raw === "" ) { frag.appendChild(document.createTextNode("\n")); continue; }
    let cls = "";
    if (/FINISHED OK|ok\s|Healthy|Preflight passed|success/i.test(raw)) cls = "ok";
    if (/FAILED|ERROR|FATAL|CRITICAL|not healthy/i.test(raw)) cls = "bad";
    if (/WARN|WARNING/i.test(raw)) cls = "warn";
    if (raw.startsWith("$ ")) cls = "cmd";
    const span = document.createElement("span");
    if (cls) span.className = cls;
    span.textContent = raw + "\n";
    frag.appendChild(span);
  }
  logEl.appendChild(frag);
  logEl.scrollTop = logEl.scrollHeight;
}
function showBanner(kind, htmlText){
  banner.className = "banner show " + kind;
  banner.innerHTML = htmlText;
}
function dot(state){
  const m = {ok:"ok", missing:"bad", empty:"bad", directory:"bad", true:"ok", false:"bad"};
  return m[String(state)] || "warn";
}
async function refresh(){
  try{
    const st = await (await api("/api/state")).json();
    const sec = document.getElementById("s-secrets");
    sec.innerHTML = "";
    for (const [k,v] of Object.entries(st.secrets)){
      const label = v==="ok" ? "found" : v==="directory" ? "IS A FOLDER — bad" : v;
      sec.insertAdjacentHTML("beforeend",
        `<div class="row"><span class="dot ${dot(v)}"></span><span>${k}</span>
         <span style="margin-left:auto;color:var(--dim)">${label}</span></div>`);
    }
    document.getElementById("s-server").innerHTML =
      `<div class="row"><span class="dot ${dot(st.docker)}"></span>Docker
         <span style="margin-left:auto;color:var(--dim)">${st.docker?"installed":"not installed"}</span></div>
       <div class="row"><span class="dot ${dot(st.compose)}"></span>Compose
         <span style="margin-left:auto;color:var(--dim)">${st.compose?"ready":"missing"}</span></div>
       <div class="row"><span class="dot ${st.root?"ok":"warn"}"></span>Admin rights
         <span style="margin-left:auto;color:var(--dim)">${st.root?"yes":"no (setup needs sudo)"}</span></div>`;
    document.getElementById("s-app").innerHTML =
      `<div class="row"><span class="dot ${dot(st.healthy)}"></span>Responding
         <span style="margin-left:auto;color:var(--dim)">${st.healthy?"yes":"not yet"}</span></div>
       <div class="row" style="color:var(--dim)">${st.dir}</div>`;
    if (st.healthy && !busy){
      showBanner("good",
        `<strong>The app is running.</strong><br>Open it at
         <a href="${st.app_url}" target="_blank" rel="noopener">${st.app_url}</a>
         &nbsp;or&nbsp;<a href="${st.app_url_direct}" target="_blank" rel="noopener">${st.app_url_direct}</a>`);
    }
  }catch(e){ /* server stopped; leave the last state on screen */ }
}
async function poll(){
  const r = await api("/api/output?from=" + cursor);
  if (!r.ok) return;
  const d = await r.json();
  if (d.text) { append(d.text); cursor = d.next; }
  if (d.done){
    clearInterval(polling); polling = null; setBusy(false);
    if (d.returncode === 0){
      append("");
      await refresh();
    } else {
      showBanner("bad", "<strong>That step failed.</strong> Read the output above — the last lines say why.");
    }
    refresh();
  }
}
async function run(act, label){
  if (busy) return;
  setBusy(true);
  banner.className = "banner";
  logEl.textContent = "";
  cursor = 0;
  append("### " + label + " ###\n");
  const r = await api("/api/run/" + act, {method:"POST"});
  if (!r.ok){
    const t = await r.text();
    append("Could not start: " + t + "\n");
    setBusy(false); return;
  }
  polling = setInterval(poll, 700);
}
document.querySelectorAll("button[data-act]").forEach(b => {
  b.addEventListener("click", () => run(b.dataset.act, b.textContent.trim()));
});
refresh();
setInterval(() => { if (!busy) refresh(); }, 8000);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ctop-deploy-ui"

    def log_message(self, fmt, *args):  # quieter terminal
        pass

    # -- helpers --------------------------------------------------------
    def _authed(self):
        qs = urllib.parse.urlparse(self.path).query
        token = urllib.parse.parse_qs(qs).get("t", [""])[0]
        return secrets.compare_digest(token, TOKEN)

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    # -- routes ---------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            if not self._authed():
                self._send(
                    403,
                    "<h1>Open the exact link printed in the terminal</h1>"
                    "<p>It contains a one-time token.</p>",
                    "text/html; charset=utf-8",
                )
                return
            self._send(200, PAGE, "text/html; charset=utf-8")
            return

        if not self._authed():
            self._send(403, {"error": "bad token"})
            return

        if path == "/api/state":
            self._send(200, gather_state())
            return

        if path == "/api/output":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            start = int(qs.get("from", ["0"])[0])
            with CURRENT_LOCK:
                job = CURRENT["job"]
            if job is None:
                self._send(200, {"text": "", "next": 0, "done": True, "returncode": 0})
                return
            chunk, nxt, done, rc = job.snapshot(start)
            self._send(
                200,
                {"text": "".join(chunk), "next": nxt, "done": done, "returncode": rc},
            )
            return

        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._authed():
            self._send(403, {"error": "bad token"})
            return
        if path.startswith("/api/run/"):
            key = path[len("/api/run/") :]
            if key not in ACTIONS:
                self._send(400, {"error": "unknown action"})
                return
            job, err = run_job(key)
            if err:
                self._send(409, err, "text/plain; charset=utf-8")
                return
            self._send(200, {"started": key})
            return
        self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser(description="Point-and-click deploy UI")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Keep 127.0.0.1 and use an SSH tunnel; 0.0.0.0 exposes "
        "a root shell runner to your network.",
    )
    ap.add_argument("--open", action="store_true", help="Try to open a local browser")
    args = ap.parse_args()

    for needed in ("scripts/preflight.sh", "scripts/deploy.sh"):
        if not os.path.isfile(os.path.join(HERE, needed)):
            print(f"ERROR: {needed} not found next to this script.")
            print("Run this from inside the extracted project folder.")
            return 2

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}/?t={TOKEN}"

    print()
    print("=" * 68)
    print("  CTOP Middleware — deployment UI")
    print("=" * 68)
    if not IS_ROOT:
        print()
        print("  NOTE: not running as administrator.")
        print("  'Set up this server' needs root. Stop this and run:")
        print("      sudo python3 deploy-ui.py")
    if args.host not in ("127.0.0.1", "localhost"):
        print()
        print("  *** WARNING ***")
        print("  Bound to a network address. This tool runs shell commands as")
        print("  root; anyone who reaches this port and guesses the token can")
        print("  use it. Prefer 127.0.0.1 plus an SSH tunnel.")
    print()
    print("  Open this exact link (it carries a one-time token):")
    print()
    print(f"      {url}")
    print()
    if args.host in ("127.0.0.1", "localhost"):
        print("  Server has no desktop browser? From your own computer run:")
        print(
            f"      ssh -L {args.port}:127.0.0.1:{args.port} {os.environ.get('SUDO_USER') or os.environ.get('USER','user')}@<server-ip>"
        )
        print("  then open the link above on your own computer.")
        print()
    print("  Press Ctrl+C here when you are finished.")
    print("=" * 68)
    print()

    if args.open:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
