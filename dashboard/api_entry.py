"""
dashboard/api_entry.py
──────────────────────
PyInstaller entry point for the IMERS backend sidecar.

When frozen (--onefile), this sets up paths and env-vars before handing off
to uvicorn so that api.py's relative-path assumptions still hold.

Dev usage (no PyInstaller):
    python -m dashboard.api_entry
or via uvicorn directly:
    uvicorn dashboard.api:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import multiprocessing
import os
import pathlib
import shutil
import sys



def _bundle_dir() -> pathlib.Path:
    """Directory that contains all frozen read-only assets (sys._MEIPASS)."""
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys._MEIPASS)
    return pathlib.Path(__file__).parent.parent


def _user_data_dir() -> pathlib.Path:
    """Writable directory for mutable runtime data (uploads, logs, user cache)."""
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
    """
    1. Set IMERS_UPLOADS_DIR to a writable location so api.py stores uploads
       outside the ephemeral _MEIPASS temp directory.
    2. Seed IMERS_MOCK_MODE=1 as the safe default (no HF_TOKEN required).
    3. Copy bundled protocol_cache.json into user-data dir on first run so the
       API has something to return even before the user indexes real documents.
    """
    frozen = getattr(sys, "frozen", False)
    bundle = _bundle_dir()

    if frozen:
        data_dir = _user_data_dir()
        uploads  = data_dir / "data" / "recordings" / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)

        os.environ.setdefault("IMERS_UPLOADS_DIR", str(uploads))

        src_cache = bundle / "data" / "protocol_cache.json"
        dst_cache = data_dir / "data" / "protocol_cache.json"
        if src_cache.exists() and not dst_cache.exists():
            (data_dir / "data").mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_cache, dst_cache)

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



def main() -> None:
    _bootstrap()
    import uvicorn
    uvicorn.run(
        "dashboard.api:app",
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
