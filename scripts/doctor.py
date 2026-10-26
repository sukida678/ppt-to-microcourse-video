#!/usr/bin/env python3
"""doctor — verify (and optionally repair) everything this skill needs.

    python doctor.py            # report only
    python doctor.py --fix      # pip-install the missing python packages
    python doctor.py --no-net   # skip the edge-tts network probe
    python doctor.py --strict   # also fail when only the images/PDF pipeline can run

Exit code 0 when the video pipeline can run for the input you have (slides as
PNG/PDF, or a .pptx only if LibreOffice is present), 1 when a blocker remains.
`--strict` additionally requires `ready.full_pipeline`, i.e. LibreOffice found.
The JSON always tells you which one is true (`ready.images_to_video` /
`ready.full_pipeline`), so a missing LibreOffice is reported, not hidden.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_course import find_soffice  # noqa: E402
# one authoritative dependency list: install.py is what the user runs to set the
# machine up, so doctor must not disagree with it about what is required
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from install import MODULE_ALIASES, PY_PKGS  # noqa: E402

PY_DEPS = {mod: (pkg, why) for mod, pkg, why in PY_PKGS}


def has_module(name: str) -> bool:
    """`pymupdf` is importable as `pymupdf` (new) or `fitz` (legacy shim, warns)."""
    candidates = MODULE_ALIASES.get(name, (name,))
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


def ffmpeg_has(ff: str, flag: str, name: str) -> bool | None:
    """Does this ffmpeg build provide `-flag` entry `name`? None when the query failed."""
    try:
        res = subprocess.run([ff, "-hide_banner", flag], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return re.search(rf"\b{re.escape(name)}\b", res.stdout or "") is not None


def ffmpeg_hint() -> str:
    """The install command that actually gives a build with libass on this platform.

    Homebrew's `ffmpeg` formula was slimmed down: as of 9.0 it is built without
    libass, freetype or fontconfig, so `brew install ffmpeg` on macOS yields a binary
    that cannot burn a single subtitle. `ffmpeg-full` is the formula that still has
    them, and it is not an obvious guess.
    """
    if sys.platform == "darwin":
        return ("macOS: brew install ffmpeg-full "
                "(plain `brew install ffmpeg` is the slim build: no libass, no subtitles)")
    if sys.platform.startswith("win"):
        return "Windows: winget install Gyan.FFmpeg"
    return "Linux: sudo apt install ffmpeg (distro builds ship libass)"


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
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 unless the full pipeline can run (LibreOffice present)")
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
        report["blockers"].append(f"ffmpeg not on PATH — {ffmpeg_hint()}")
    if ff and not fp:
        report["blockers"].append("ffprobe not on PATH (usually ships with ffmpeg)")

    # `which ffmpeg` only proves a binary exists. A build without libass cannot burn a
    # single subtitle and one without libx264 cannot encode the parts — both blow up
    # two minutes into the render, not here.
    if ff:
        caps = {}
        for flag, name, what in (("-filters", "subtitles", "burned-in subtitles (libass)"),
                                 ("-encoders", "libx264", "H.264 part encoding"),
                                 ("-encoders", "aac", "AAC audio")):
            has = ffmpeg_has(ff, flag, name)
            caps[name] = has
            if has is False:
                report["blockers"].append(
                    f"this ffmpeg has no {name} → no {what}; {ffmpeg_hint()}")
        checks["ffmpeg_capabilities"] = {"ok": all(v is not False for v in caps.values()), **caps}

        # libass silently substitutes a wrong/missing font and burns empty boxes
        ffmpeg_compose = Path(__file__).resolve().parent / "compose.py"
        if ffmpeg_compose.exists():
            try:
                sys.path.insert(0, str(ffmpeg_compose.parent))
                from compose import default_sub_font, pick_sub_font  # noqa: PLC0415
            except Exception as exc:  # noqa: BLE001
                checks["subtitle_font"] = {"ok": None, "detail": f"font probe unavailable: {exc}"}
            else:
                family, font_msg, font_fatal = pick_sub_font(default_sub_font())
                checks["subtitle_font"] = {"ok": not font_fatal, "family": family,
                                           "message": font_msg}
                if font_fatal:
                    report["blockers"].append(font_msg)
                elif font_msg:
                    report["warnings"].append(font_msg)

    try:
        free_gb = shutil.disk_usage(Path.cwd()).free / 1024 ** 3
    except OSError as exc:
        checks["disk"] = {"ok": None, "detail": str(exc)}
    else:
        checks["disk"] = {"ok": free_gb >= 4, "free_gb": round(free_gb, 1)}
        if free_gb < 4:
            report["warnings"].append(
                f"only {free_gb:.1f} GB free in {Path.cwd()} — a 41-slide 1080p render "
                f"needs roughly 2–4 GB of temporary files")

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
    if args.strict and not report["ready"]["full_pipeline"]:
        return 1
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
