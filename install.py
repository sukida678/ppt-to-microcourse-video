#!/usr/bin/env python3
"""One-command dependency check and installer for ppt-to-microcourse-video.

    python install.py            # check, print what is missing + the exact fix
    python install.py --install  # also pip-install the missing Python packages
    python install.py --json     # machine-readable output for an agent

Exit code 0 = the full pipeline can run. Exit code 1 = something is missing.
No third-party imports at module level, so this runs even on a bare Python.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_PY = (3, 9)


def safe_stdio() -> None:
    """Keep a legacy Windows code page from turning a print() into a crash.

    When stdout is a pipe an agent reads our JSON, so write UTF-8; when it is a real
    console keep the console's encoding.
    """
    opts = {"errors": "replace"}
    try:
        if not sys.stdout.isatty():
            opts["encoding"] = "utf-8"
    except Exception:
        opts["encoding"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(**opts)
        except Exception:
            pass


safe_stdio()

PY_PKGS = [
    ("edge_tts", "edge-tts", "text-to-speech + word timings"),
    # PyMuPDF renamed its import to `pymupdf`; `fitz` still ships as a shim that warns
    # on import, so try the new name first and never import the shim needlessly.
    ("pymupdf", "pymupdf", "PDF / slide rasterising"),
    ("pptx", "python-pptx", "speaker notes + text extraction fallback"),
]

MODULE_ALIASES = {"pymupdf": ("pymupdf", "fitz")}

SOFFICE_HINTS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    r"%LOCALAPPDATA%\Programs\LibreOffice\program\soffice.exe",
    r"D:\LibreOffice\program\soffice.exe",
    r"C:\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
]


def find_soffice() -> str | None:
    # make_course owns the authoritative search (env vars, PATH, common install
    # roots, portable copies). It imports nothing third-party at module level, so
    # this is safe to call on a machine where nothing is installed yet.
    sys.path.insert(0, str(HERE / "scripts"))
    try:
        from make_course import find_soffice as _search  # noqa: PLC0415
        hit = _search()
        if hit:
            return hit
    except Exception:  # noqa: BLE001
        pass
    finally:
        sys.path.pop(0)

    for hint in SOFFICE_HINTS:
        p = Path(os.path.expandvars(hint))
        if p.exists():
            return str(p)
    return None


def install_hint(tool: str) -> str:
    """Platform-correct install command — `winget` is Windows-only and useless on a Mac."""
    win, mac = sys.platform == "win32", sys.platform == "darwin"
    hints = {
        "ffmpeg": ("winget install Gyan.FFmpeg",
                   "brew install ffmpeg",
                   "sudo apt install ffmpeg   (or: sudo dnf install ffmpeg)"),
        "soffice": ("winget install TheDocumentFoundation.LibreOffice",
                    "brew install --cask libreoffice",
                    "sudo apt install libreoffice-impress   (or: sudo dnf install libreoffice)"),
    }
    w, m, l = hints[tool]
    return w if win else (m if mac else l)


def have_module(mod: str) -> bool:
    return any(importlib.util.find_spec(m) is not None
               for m in MODULE_ALIASES.get(mod, (mod,)))


def version_of(mod: str) -> str:
    for name in MODULE_ALIASES.get(mod, (mod,)):
        try:
            m = importlib.import_module(name)
        except Exception:  # noqa: BLE001
            continue
        return str(getattr(m, "__version__", "") or "")
    return ""


def check() -> dict:
    py_ok = sys.version_info[:2] >= MIN_PY
    rows = []

    for mod, pkg, why in PY_PKGS:
        ok = have_module(mod)
        rows.append({
            "kind": "python",
            "name": pkg,
            "ok": ok,
            "version": version_of(mod) if ok else "",
            "why": why,
            "fix": f'"{sys.executable}" -m pip install {pkg}',
        })

    for exe, why, fix in (
        ("ffmpeg", "video encoding", install_hint("ffmpeg")),
        ("ffprobe", "duration / stream checks", install_hint("ffmpeg")),
    ):
        path = shutil.which(exe)
        rows.append({
            "kind": "binary",
            "name": exe,
            "ok": bool(path),
            "version": Path(path).name if path else "",
            "why": why,
            "fix": fix,
        })

    so = find_soffice()
    rows.append({
        "kind": "binary",
        "name": "soffice",
        "ok": bool(so),
        "version": so or "",
        "why": "PPTX → images (optional: PDF or PNG input works without it)",
        "fix": install_hint("soffice"),
    })

    required = [r for r in rows if r["name"] != "soffice"]
    missing_required = [r["name"] for r in required if not r["ok"]]
    return {
        "python": {"ok": py_ok, "version": sys.version.split()[0], "executable": sys.executable},
        "rows": rows,
        "missing_required": missing_required,
        "libreoffice": bool(so),
        "pipeline_ready": py_ok and not missing_required,
    }


def pip_install(pkgs: list[str]) -> int:
    if not pkgs:
        return 0
    print(f"\n→ {sys.executable} -m pip install " + " ".join(pkgs))
    return subprocess.call([sys.executable, "-m", "pip", "install", *pkgs])


def main() -> int:
    ap = argparse.ArgumentParser(description="Check / install what this skill needs")
    ap.add_argument("--install", action="store_true", help="pip-install missing Python packages")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    args = ap.parse_args()

    result = check()

    if args.install:
        want = [r["name"] for r in result["rows"]
                if r["kind"] == "python" and not r["ok"]]
        if want:
            rc = pip_install(want)
            if rc != 0:
                print("\npip failed. Install manually, then re-run this script.", file=sys.stderr)
            result = check()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0 if result["pipeline_ready"] else 1

    print(f"python  {result['python']['version']}  {result['python']['executable']}")
    print("-" * 66)
    for r in result["rows"]:
        mark = "OK  " if r["ok"] else "MISS"
        extra = f"  {r['version']}" if r["version"] and r["ok"] else ""
        opt = "" if r["name"] != "soffice" else "   (optional)"
        print(f"{mark}  {r['name']:<12}{opt}{extra}")

    if result["pipeline_ready"] and result["libreoffice"]:
        print("\nAll good. Start with:")
        print("  python scripts/doctor.py --fix")
        print("  python scripts/make_course.py extract --deck <your-deck>.pptx --work build")
        return 0

    if result["pipeline_ready"] and not result["libreoffice"]:
        print("\nPipeline ready, but no LibreOffice: PPTX cannot be rasterised.")
        print(f"Either install it ({install_hint('soffice')}),")
        print("or have the deck exported to PDF / PNG and use that instead — AGENTS.md §8.")
        return 0

    print("\nMissing (run these, then re-run this script):")
    for r in result["rows"]:
        if not r["ok"]:
            print(f"  {r['name']:<12} {r['fix']}")
    print("\nOr let this script install the Python ones:  python install.py --install")
    return 1


if __name__ == "__main__":
    sys.exit(main())
