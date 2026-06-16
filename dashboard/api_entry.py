"""
Entry point for the standalone IMERS binary (PyInstaller onefile).
"""

from __future__ import annotations

import multiprocessing
import os
import pathlib
import shutil
import sys


def _bundle_dir() -> pathlib.Path:
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys._MEIPASS)
    return pathlib.Path(__file__).parent.parent


def _user_data_dir() -> pathlib.Path:
    if sys.platform == "win32":
        base = pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home()))
        d = base / "IMERS"
    elif sys.platform == "darwin":
        d = pathlib.Path.home() / "Library" / "Application Support" / "IMERS"
    else:
        d = pathlib.Path.home() / ".imers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bootstrap() -> None:
    frozen = getattr(sys, "frozen", False)
    bundle = _bundle_dir()

    if frozen:
        data_dir = _user_data_dir()
        uploads = data_dir / "data" / "recordings" / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("IMERS_UPLOADS_DIR", str(uploads))

        src_cache = bundle / "data" / "protocol_cache.json"
        dst_cache = data_dir / "data" / "protocol_cache.json"
        if src_cache.exists() and not dst_cache.exists():
            (data_dir / "data").mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_cache, dst_cache)

        try:
            log_path = pathlib.Path(os.environ.get("TEMP", ".")) / "imers-error.log"
            sys.stderr = open(log_path, "w", encoding="utf-8", errors="replace")
        except Exception:
            pass

        os.chdir(bundle)
    else:
        os.chdir(bundle)
        if str(bundle) not in sys.path:
            sys.path.insert(0, str(bundle))

    try:
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv()
    except ImportError:
        pass

    os.environ.setdefault("IMERS_MOCK_MODE", "1")


def _wait_for_server(url: str, timeout: int = 120) -> bool:
    import time
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


_LOADING_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IMERS</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f172a;color:#94a3b8;font-family:-apple-system,BlinkMacSystemFont,
     'Segoe UI',sans-serif;display:flex;align-items:center;
     justify-content:center;height:100vh}
.card{text-align:center;padding:2.5rem}
.gem{width:56px;height:56px;background:linear-gradient(135deg,#3b82f6,#1d4ed8);
     clip-path:polygon(50% 0%,100% 38%,82% 100%,18% 100%,0% 38%);
     margin:0 auto 1.75rem;animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.7;transform:scale(.95)}}
h1{font-size:2.25rem;font-weight:700;color:#e2e8f0;letter-spacing:-.5px;margin-bottom:.4rem}
.sub{font-size:.95rem;color:#64748b}
.detail{font-size:.8rem;color:#475569;margin-top:.3rem;min-height:1.1rem}
.elapsed{font-size:.75rem;color:#334155;margin-top:1.25rem}
</style></head>
<body><div class="card">
  <div class="gem"></div>
  <h1>IMERS</h1>
  <p class="sub">Iniciando servidor de emergencias…</p>
  <p class="detail" id="detail"></p>
  <p class="elapsed" id="elapsed"></p>
</div>
<script>
var start=Date.now();
setInterval(function(){
  var s=Math.round((Date.now()-start)/1000);
  document.getElementById('elapsed').textContent=s+'s';
  document.getElementById('detail').textContent=
    s>15?'La primera ejecución puede tardar 1–2 minutos…':'';
},500);
</script>
</body></html>"""


def main() -> None:
    _bootstrap()

    import uvicorn
    from dashboard.api import app 

    frozen = getattr(sys, "frozen", False)

    if frozen:
        import threading
        import webview

        server_thread = threading.Thread(
            target=uvicorn.run,
            kwargs=dict(app=app, host="127.0.0.1", port=8000, log_level="warning"),
            daemon=True,
        )
        server_thread.start()

        def _on_webview_ready(window):
            """Runs in a pywebview background thread after the GUI is initialised."""
            if _wait_for_server("http://127.0.0.1:8000/api/health"):
                window.load_url("http://127.0.0.1:8000")

        window = webview.create_window(
            "IMERS — Panel de Emergencias 112",
            html=_LOADING_HTML,
            width=1400,
            height=900,
            min_size=(1024, 600),
        )
        webview.start(_on_webview_ready, window)

    else:
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
