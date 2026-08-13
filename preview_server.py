"""Preview server for ADB-AUTOKLIK (Freebuff preview entrypoint).

ADB-AUTOKLIK is a Windows desktop application (PyQt6 + ADB + scrcpy),
so it cannot render inside a browser preview. This minimal stdlib-only
server serves an informational page describing the project, its current
status, and what is needed to actually run it on a desktop machine.

Usage:
    python3 preview_server.py [port]

Listens on 0.0.0.0. If the PORT environment variable is set (Freebuff
injects it for isolated workspaces), it takes precedence over the
default/argument port.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

PAGE = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>ADB-AUTOKLIK — podgląd projektu</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #0b0f14; color: #d8e1ea; font: 15px/1.6 system-ui, "Segoe UI", sans-serif;
    padding: 32px 16px;
  }
  main { max-width: 760px; width: 100%; background: #11161d; border: 1px solid #232c38;
         border-radius: 14px; padding: 32px 34px; box-shadow: 0 18px 50px rgba(0,0,0,.45); }
  h1 { margin: 0 0 4px; font-size: 26px; letter-spacing: .2px; color: #fff; }
  .tag { display: inline-block; margin-bottom: 18px; padding: 3px 10px; border-radius: 999px;
         background: #1b2735; border: 1px solid #2c3b4d; color: #7fd1ff; font-size: 12px; }
  h2 { font-size: 15px; text-transform: uppercase; letter-spacing: 1.4px; color: #7a8ba0; margin: 26px 0 8px; }
  p  { margin: 0 0 10px; }
  code, pre { background: #0b0f14; border: 1px solid #232c38; border-radius: 6px; font-size: 13px; }
  code { padding: 1px 6px; }
  pre  { padding: 12px 14px; overflow-x: auto; margin: 8px 0 0; }
  ul  { margin: 0; padding-left: 20px; }
  li  { margin-bottom: 6px; }
  .muted { color: #8fa1b5; }
  .warn { color: #ffc46b; }
</style>
</head>
<body>
<main>
  <h1>ADB-AUTOKLIK</h1>
  <span class="tag">Podgląd projektu (aplikacja desktopowa, nie webowa)</span>
  <p>
    Autokliker dla Androida sterowany klawiaturą laptopa: mapujesz klawisze
    na współrzędne dotknięć ekranu telefonu, a aplikacja symuluje je przez
    <code>adb shell input tap</code>, z podglądem ekranu na żywo via scrcpy.
  </p>

  <h2>Stos</h2>
  <ul>
    <li><code>PyQt6</code> — GUI aplikacji</li>
    <li><code>pynput</code> — globalne przechwytywanie klawiszy (keymapper)</li>
    <li><code>adbutils</code> — klient ADB (symulacja dotknięć)</li>
    <li><code>scrcpy-client</code> + <code>av</code>/<code>numpy</code> — podgląd ekranu Androida</li>
  </ul>

  <h2>Stan repozytorium</h2>
  <p>
    Kompletna aplikacja desktopowa:
    <code>config_manager.py</code> (profil keymapy w <code>keymap.json</code>),
    <code>adb_controller.py</code> (komunikacja ADB — dotknięcia),
    <code>stream_widget.py</code> (podgląd ekranu via scrcpy + nakładka punktów),
    <code>main_window.py</code> / <code>main.py</code> (GUI PyQt6 i keymapper pynput).
    Uruchomienie na desktopie: <code>python main.py</code>.
  </p>

  <h2>Wymagania do uruchomienia na desktopie</h2>
  <ul>
    <li>Windows + Python 3.11+ (plik wymagań zakłada Windows)</li>
    <li><code>pip install -r requirements.txt</code></li>
    <li><code>adb</code> (Android platform-tools) w <code>PATH</code></li>
    <li>Telefon z włączonym USB debugging i podłączony przez ADB (<code>adb devices</code>)</li>
  </ul>
  <p class="muted warn">
    Uwaga: ten podgląd w przeglądarce to strona informacyjna — aplikacja GUI
    nie działa w środowisku webowym Freebuff.
  </p>
</main>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main() -> None:
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 3000))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"ADB-AUTOKLIK preview listening on http://0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
