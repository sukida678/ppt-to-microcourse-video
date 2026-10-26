#!/usr/bin/env python3
"""doctor — verify (and optionally repair) everything this skill needs.

    python doctor.py            # report only
    python doctor.py --fix      # pip-install the missing python packages
    python doctor.py --no-net   # skip the edge-tts network probe

Exit code 0 when the pipeline can run, 1 when a blocker remains.
Prints a single JSON object so an agent can parse it.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_course import find_soffice  # noqa: E402

PY_DEPS = {
    "pymupdf": ("pymupdf", "pdf -> png rendering"),
    "pptx": ("python-pptx", "slide text / speaker notes"),
    "edge_tts": ("edge-tts", "free neural text-to-speech"),
}


def has_module(name: str) -> bool:
    """`pymupdf` is importable as `pymupdf` (new) or `fitz` (legacy shim, warns)."""
    candidates = ("pymupdf", "fitz") if name == "pymupdf" else (name,)
    for mod in candidates:
        try:
            if importlib.util.find_spec(mod) is not None:
                return True
        except Exception:
            continue
    return False


def bin_version(exe: str, args=("-version",)) -> str | None:
    try:
        res = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        text = (res.stdout or res.stderr or "").strip().splitlines()
        return text[0].strip() if text else "?"
    except Exception:
        return None


def pip_install(pkgs: list[str]) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", *pkgs]
    mirror = os.environ.get("PIP_INDEX_URL")
    if mirror:
        cmd += ["-i", mirror]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        return res.returncode == 0, (res.stderr or "").strip()[-400:]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def net_probe() -> tuple[bool, str]:
    """edge-tts talks to Microsoft; a failed probe means no narration."""
    try:
        import asyncio
        import edge_tts  # noqa: PLC0415

        async def _go():
            voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=20)
            zh = [v for v in voices if str(v.get("Locale", "")).startswith("zh")]
            return len(voices), len(zh)

        total, zh = asyncio.run(_go())
        return True, f"{total} voices ({zh} Chinese)"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the ppt-to-microcourse pipeline")
    ap.add_argument("--fix", action="store_true", help="pip install missing packages")
    ap.add_argument("--no-net", action="store_true")
    args = ap.parse_args(argv)

    report: dict = {"python": sys.version.split()[0], "python_exe": sys.executable,
                    "checks": {}, "blockers": [], "warnings": []}
    checks = report["checks"]

    ok_py = sys.version_info >= (3, 9)
    checks["python"] = {"ok": ok_py, "detail": sys.version.split()[0]}
    if not ok_py:
        report["blockers"].append("Python 3.9+ required")

    missing = []
    for mod, (pkg, why) in PY_DEPS.items():
        ok = has_module(mod)
        checks[mod] = {"ok": ok, "package": pkg, "why": why}
        if not ok:
            missing.append(pkg)
    if missing and args.fix:
        ok, msg = pip_install(missing)
        report["pip"] = {"ok": ok, "installed": missing if ok else [], "message": msg}
        for mod, (pkg, why) in PY_DEPS.items():
            checks[mod]["ok"] = has_module(mod)
    for mod, (pkg, why) in PY_DEPS.items():
        if not checks[mod]["ok"]:
            report["blockers"].append(f"missing python package: {pkg} ({why}) -> pip install {pkg}")

    ff = shutil.which("ffmpeg")
    fp = shutil.which("ffprobe")
    checks["ffmpeg"] = {"ok": bool(ff), "path": ff, "version": bin_version(ff) if ff else None}
    checks["ffprobe"] = {"ok": bool(fp), "path": fp}
    if not ff:
        report["blockers"].append("ffmpeg not on PATH (brew install ffmpeg / apt install ffmpeg / "
                                  "winget install Gyan.FFmpeg)")
    if ff and not fp:
        report["blockers"].append("ffprobe not on PATH (usually ships with ffmpeg)")

    so = find_soffice()
    checks["libreoffice"] = {"ok": bool(so), "path": so}
    if so:
        # on Windows only soffice.com attaches to the console and prints the version;
        # soffice.exe returns immediately with empty stdout
        ver_exe = so
        if so.lower().endswith("soffice.exe"):
            com = so[:-4] + ".com"
            if Path(com).exists():
                ver_exe = com
        checks["libreoffice"]["version"] = bin_version(ver_exe, ("--version",))
    else:
        report["warnings"].append(
            "LibreOffice not found: .pptx input cannot be rendered. Install LibreOffice, "
            "pass --soffice, or hand the skill pre-exported PNG slides.")

    if not args.no_net and checks.get("edge_tts", {}).get("ok"):
        ok, detail = net_probe()
        checks["edge_tts_network"] = {"ok": ok, "detail": detail}
        if not ok:
            report["blockers"].append(f"edge-tts cannot reach the Microsoft endpoint: {detail}")

    report["ok"] = not report["blockers"]
    report["ready"] = {
        "full_pipeline": report["ok"] and checks["libreoffice"]["ok"],
        "images_to_video": report["ok"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
