#!/usr/bin/env python3
"""ppt2png — rasterise a slide deck into one PNG per page.

Input
    .pdf              no converter needed (pure PyMuPDF)
    .pptx / .ppt      needs a converter backend

Converter backends (auto-detected in this order, override with --converter)
    soffice     system LibreOffice  (soffice / soffice.com, PATH or usual install dirs)
    dsoffice    LibreOffice Kit CLI (node <cli.js> convert) — e.g. the copy bundled with
                DeepSeek Harness; opt in only when nothing else is available

Environment overrides
    SOFFICE_BIN     absolute path to soffice(.com/.exe)
    DSOFFICE_CLI    absolute path to the Kit's lib/cli.js
    NODE_BIN        absolute path to node (only for dsoffice)

Usage
    python ppt2png.py deck.pptx --out build/png --dpi 150
    python ppt2png.py deck.pdf  --out build/png --dpi 150
    python ppt2png.py deck.pptx --out build/png --converter soffice --soffice /path/to/soffice

Prints a JSON summary on stdout; exits non-zero on failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


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

SOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    r"C:\Program Files\LibreOffice 7\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/usr/local/bin/soffice",
    "/snap/bin/libreoffice",
    "/opt/libreoffice/program/soffice",
]

# DeepSeek Harness ships a LibreOffice Kit; used only as a last resort.
DSOFFICE_DEFAULTS = [
    (r"D:\dsh\resources\runtime\primary-runtime\dependencies\node\bin\node.exe",
     r"D:\dsh\resources\app.asar.unpacked\dsh\node_modules\@deepseek-ai\libreoffice-kit\lib\cli.js"),
]


def run(cmd, cwd=None, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, timeout=timeout)


def find_soffice(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if Path(explicit).exists() else None
    env = os.environ.get("SOFFICE_BIN")
    if env and Path(env).exists():
        return env
    for name in ("soffice", "soffice.com", "libreoffice"):
        hit = shutil.which(name)
        if hit:
            return hit
    for cand in SOFFICE_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


def find_dsoffice() -> tuple[str, str] | None:
    cli = os.environ.get("DSOFFICE_CLI")
    node = os.environ.get("NODE_BIN") or shutil.which("node")
    if cli and Path(cli).exists() and node:
        return node, cli
    for n, c in DSOFFICE_DEFAULTS:
        if Path(n).exists() and Path(c).exists():
            return n, c
    return None


def convert_to_pdf(src: Path, workdir: Path, converter: str, soffice: str | None) -> tuple[Path, str]:
    """Return (pdf_path, backend_name)."""
    workdir.mkdir(parents=True, exist_ok=True)
    out_pdf = workdir / (src.stem + ".pdf")

    if converter in ("auto", "soffice"):
        exe = find_soffice(soffice)
        if exe:
            # No -env:UserInstallation here on purpose: this call lets soffice use the
            # user's own profile, so nothing is parsed as a URI and a path with spaces
            # cannot break it (the failure mode make_course.profile_uri() exists for).
            # Trade-off: a running LibreOffice GUI therefore *can* lock us out, which
            # is why make_course passes an isolated profile when it owns the work dir.
            res = run([exe, "--headless", "--norestore", "--convert-to", "pdf",
                       "--outdir", str(workdir), str(src)])
            if out_pdf.exists() and out_pdf.stat().st_size > 0:
                return out_pdf, "soffice"
            if converter == "soffice":
                raise RuntimeError(
                    f"soffice failed to convert\ncmd: {exe}\n"
                    f"stdout: {res.stdout.strip()}\nstderr: {res.stderr.strip()}")

    if converter in ("auto", "dsoffice"):
        pair = find_dsoffice()
        if pair:
            node, cli = pair
            res = run([node, cli, "convert", "--input", str(src), "--output", str(out_pdf)])
            if out_pdf.exists() and out_pdf.stat().st_size > 0:
                return out_pdf, "dsoffice"
            if converter == "dsoffice":
                raise RuntimeError(
                    f"dsoffice failed to convert\nstdout: {res.stdout.strip()}\n"
                    f"stderr: {res.stderr.strip()}")

    raise RuntimeError(
        "no PPTX->PDF converter available.\n"
        "Fix one of these:\n"
        "  1. install LibreOffice (easiest) — then re-run; it is auto-detected\n"
        "  2. point at an existing install:  --soffice \"C:/Program Files/LibreOffice/program/soffice.exe\"\n"
        "  3. export the deck to PDF yourself (PowerPoint: File > Export > PDF) and pass the .pdf instead"
    )


def render_pdf(pdf: Path, out_dir: Path, dpi: int, prefix: str) -> list[Path]:
    import pymupdf  # PyMuPDF

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf))
    made: list[Path] = []
    for idx, page in enumerate(doc, start=1):
        pix = page.get_pixmap(dpi=dpi)
        path = out_dir / f"{prefix}_{idx:02d}.png"
        pix.save(str(path))
        made.append(path)
    doc.close()
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rasterise a slide deck into PNG pages")
    ap.add_argument("input", help=".pptx / .ppt / .pdf")
    ap.add_argument("--out", required=True, help="output directory for PNGs")
    ap.add_argument("--dpi", type=int, default=150, help="raster resolution (default 150)")
    ap.add_argument("--prefix", default="slide", help="output filename prefix (default slide)")
    ap.add_argument("--converter", default="auto", choices=["auto", "soffice", "dsoffice", "none"])
    ap.add_argument("--soffice", default=None, help="explicit path to soffice")
    ap.add_argument("--keep-pdf", action="store_true", help="keep the intermediate PDF in --out")
    args = ap.parse_args(argv)

    src = Path(args.input).resolve()
    out_dir = Path(args.out).resolve()
    if not src.exists():
        print(json.dumps({"ok": False, "error": f"input not found: {src}"}, ensure_ascii=False))
        return 2

    suffix = src.suffix.lower()
    backend = "none"
    pdf_path: Path | None = None
    tmpdir = None

    try:
        if suffix == ".pdf":
            pdf_path = src
        elif suffix in (".pptx", ".ppt"):
            base = out_dir if args.keep_pdf else Path(tempfile.mkdtemp(prefix="ppt2png-"))
            if not args.keep_pdf:
                tmpdir = base
            pdf_path, backend = convert_to_pdf(src, base, args.converter, args.soffice)
        else:
            raise RuntimeError(f"unsupported input type: {suffix} (need .pptx/.ppt/.pdf)")

        pngs = render_pdf(pdf_path, out_dir, args.dpi, args.prefix)

        import pymupdf
        with pymupdf.open(str(pdf_path)) as d:
            size = d[0].rect if len(d) else None

        print(json.dumps({
            "ok": True,
            "pages": len(pngs),
            "backend": backend,
            "pdf": str(pdf_path) if args.keep_pdf or suffix == ".pdf" else None,
            "pngs": [str(p) for p in pngs],
            "first_page_pt": [round(size.width, 1), round(size.height, 1)] if size else None,
            "dpi": args.dpi,
        }, ensure_ascii=False, indent=1))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=1))
        return 1
    finally:
        if tmpdir and Path(tmpdir).exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
