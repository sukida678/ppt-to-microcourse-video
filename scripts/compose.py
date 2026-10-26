#!/usr/bin/env python3
"""compose — stitch slide images + narration audio into a finished MP4.

Reads <project>/manifest.json:
    {
      "segments": [
        {"id": "01", "image": "png/slide_01.png",
         "audio": "build/audio/01.mp3", "duration": 12.31, "srt": "build/sub/01.srt"}
      ],
      "out": "course.mp4"
    }
All paths are resolved relative to <project>.

What it does per segment
    image (-loop) + audio  ->  optional ken-burns zoom  ->  optional burned-in subtitles
    ->  optional fade in/out  ->  one uniform H.264/AAC mp4 part
then concatenates the parts (stream copy, no generational loss) and optionally mixes
background music under the narration.

Usage
    python compose.py --project build/course
    python compose.py --project build/course --subs burn --motion kenburns --transition fade
    python compose.py --project build/course --bgm assets/bgm.mp3 --music-volume 0.12
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
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


def sub_style(font: str = "Microsoft YaHei", size: int = 21, box: bool = False) -> str:
    """libass style for burned-in subtitles.

    ffmpeg assumes a 384x288 script when the SRT carries no header, so every value
    below is multiplied by ~3.75 on a 1080p canvas; keep the outline thin or text
    turns into a black blob.

    box=True draws a translucent band behind the text (BorderStyle=3) — use it when
    the slides have content near the bottom edge and plain outlined text collides.
    """
    common = f"FontName={font},FontSize={size},PrimaryColour=&H00FFFFFF,Alignment=2,MarginV=32,Bold=0"
    if box:
        return common + ",BorderStyle=3,OutlineColour=&H73000000,Outline=1.2,Shadow=0"
    return common + ",OutlineColour=&H00000000,BorderStyle=1,Outline=0.8,Shadow=0.4"


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH — install ffmpeg and retry")
    return exe


def run(cmd, cwd=None, timeout=1800):
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, timeout=timeout)
    if res.returncode != 0:
        tail = (res.stderr or "").strip().splitlines()[-12:]
        raise RuntimeError("ffmpeg failed:\n" + "\n".join(tail))
    return res


def video_filter(w: int, h: int, fps: int, dur: float, motion: str,
                 srt_rel: str | None, transition: str, fade: float, style: str,
                 zoom: float = 1.06) -> str:
    parts: list[str] = []
    if motion == "kenburns":
        # Render at 2x then zoompan so the move is sub-pixel smooth rather than
        # pixel-snapped. The zoom step is spread over the whole segment instead of
        # racing to the target in the first seconds.
        frames = max(1, int(round(dur * fps)))
        step = max(0.0, (zoom - 1.0)) / frames
        parts.append(f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=decrease")
        parts.append(f"pad={w * 2}:{h * 2}:(ow-iw)/2:(oh-ih)/2:color=white")
        parts.append(
            f"zoompan=z='min(1+{step:.6f}*on,{zoom:.3f})'"
            ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={w}x{h}:fps={fps}"
        )
    else:
        parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease")
        parts.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white")
        parts.append(f"fps={fps}")
    if srt_rel:
        parts.append(f"subtitles={srt_rel}:force_style='{style}'")
    if transition == "fade" and dur > fade * 2:
        parts.append(f"fade=t=in:st=0:d={fade}")
        parts.append(f"fade=t=out:st={dur - fade:.3f}:d={fade}")
    parts.append("format=yuv420p")
    return ",".join(parts)


def build_part(seg: dict, idx: int, project: Path, parts_dir: Path, args,
               w: int, h: int) -> Path:
    img = (project / seg["image"]).resolve()
    if not img.exists():
        raise RuntimeError(f"segment {seg['id']}: image missing: {img}")
    audio = (project / seg["audio"]).resolve() if seg.get("audio") else None
    if audio is not None and not audio.exists():
        audio = None

    hold = float(seg.get("duration") or 0.0)
    dur = max(args.min_duration, hold + args.tail) if hold > 0 else args.default_hold

    srt_rel = None
    if args.subs == "burn" and seg.get("srt"):
        srt_abs = (project / seg["srt"]).resolve()
        if srt_abs.exists() and srt_abs.stat().st_size > 0:
            # relative path (relative to project) dodges Windows drive-colon escaping
            srt_rel = Path(seg["srt"]).as_posix()

    out = parts_dir / f"part_{idx:02d}.mp4"
    cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
           "-loop", "1", "-framerate", str(args.fps), "-t", f"{dur:.3f}", "-i", str(img)]
    if audio is not None:
        cmd += ["-i", str(audio)]
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]

    vf = video_filter(w, h, args.fps, dur, args.motion, srt_rel, args.transition, args.fade,
                      sub_style(args.sub_font, args.sub_size, args.sub_box), args.zoom)
    af = f"apad,atrim=0:{dur:.3f},asetpts=N/SR/TB"
    cmd += ["-filter_complex", f"[0:v]{vf}[v];[1:a]{af}[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf),
            "-pix_fmt", "yuv420p", "-r", str(args.fps),
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(out)]
    run(cmd, cwd=str(project))
    return out


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    for block in re.split(r"\r?\n\r?\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1])
        if not m:
            continue
        a = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        b = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
        body = "\n".join(lines[2:]).strip()
        if body:
            cues.append((a, b, body))
    return cues


def fmt_ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_global_srt(segs: list[dict], project: Path, out: Path) -> int:
    cues: list[tuple[float, float, str]] = []
    offset = 0.0
    count = 0
    for seg in segs:
        hold = float(seg.get("duration") or 0.0)
        if seg.get("srt"):
            p = (project / seg["srt"]).resolve()
            if p.exists():
                for a, b, body in parse_srt(p):
                    cues.append((a + offset, b + offset, body))
                    count += 1
        offset += max(0.0, hold)
    if not cues:
        return 0
    with open(out, "w", encoding="utf-8") as fh:
        for i, (a, b, body) in enumerate(cues, start=1):
            fh.write(f"{i}\n{fmt_ts(a)} --> {fmt_ts(b)}\n{body}\n\n")
    return count


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compose slide images + narration into an MP4")
    ap.add_argument("--project", required=True, help="project dir containing manifest.json")
    ap.add_argument("--resolution", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--motion", default="kenburns", choices=["kenburns", "none"])
    ap.add_argument("--transition", default="fade", choices=["fade", "none"])
    ap.add_argument("--fade", type=float, default=0.35)
    ap.add_argument("--subs", default="burn", choices=["burn", "soft", "none"])
    ap.add_argument("--sub-font", default="Microsoft YaHei",
                    help="subtitle font family (Noto Sans CJK SC on Linux, PingFang SC on mac)")
    ap.add_argument("--sub-size", type=int, default=21, help="subtitle font size (libass units)")
    ap.add_argument("--sub-box", action="store_true",
                    help="draw a translucent band behind subtitles (for slides with "
                         "content near the bottom edge)")
    ap.add_argument("--zoom", type=float, default=1.06,
                    help="kenburns end zoom factor; 1.0 disables the move (default 1.06)")
    ap.add_argument("--tail", type=float, default=0.7, help="silence after each narration")
    ap.add_argument("--min-duration", type=float, default=2.0)
    ap.add_argument("--default-hold", type=float, default=5.0,
                    help="duration for slides with no narration")
    ap.add_argument("--bgm", default=None, help="background music file")
    ap.add_argument("--music-volume", type=float, default=0.12)
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--preset", default="medium")
    args = ap.parse_args(argv)

    project = Path(args.project).resolve()
    man_path = project / "manifest.json"
    if not man_path.exists():
        print(json.dumps({"ok": False, "error": f"manifest.json not found in {project}"},
                         ensure_ascii=False))
        return 2
    man = json.loads(man_path.read_text(encoding="utf-8-sig"))
    segs = man["segments"]
    if not segs:
        print(json.dumps({"ok": False, "error": "no segments in manifest"}, ensure_ascii=False))
        return 2

    try:
        w, h = (int(x) for x in args.resolution.lower().split("x"))
    except Exception:
        print(json.dumps({"ok": False, "error": f"bad --resolution {args.resolution}"},
                         ensure_ascii=False))
        return 2

    out_name = man.get("out") or "course.mp4"
    out_path = (project / out_name).resolve()
    parts_dir = project / "parts"
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True)

    print(f"  composing {len(segs)} segment(s) at {w}x{h}@{args.fps}fps "
          f"(motion={args.motion}, transition={args.transition}, subs={args.subs})", flush=True)
    parts = []
    for i, seg in enumerate(segs, start=1):
        parts.append(build_part(seg, i, project, parts_dir, args, w, h))
        print(f"  [{i}/{len(segs)}] {seg.get('id')} -> {parts[-1].name}", flush=True)

    # concat (stream copy: identical encode settings, no re-encode)
    lst = parts_dir / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", "-movflags", "+faststart", str(out_path)], cwd=str(parts_dir))

    # background music
    if args.bgm:
        bgm = Path(args.bgm).resolve()
        if bgm.exists():
            mixed = project / (out_path.stem + "_bgm.mp4")
            run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(out_path), "-stream_loop", "-1", "-i", str(bgm),
                 "-filter_complex",
                 f"[1:a]volume={args.music_volume}[m];"
                 f"[0:a][m]amix=inputs=2:duration=first:normalize=0[a]",
                 "-map", "0:v", "-map", "[a]",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                 "-shortest", "-movflags", "+faststart", str(mixed)])
            out_path.unlink()
            mixed.rename(out_path)
            print("  music mixed in", flush=True)
        else:
            print(f"  ! bgm not found, skipped: {bgm}", flush=True)

    srt_count = 0
    if args.subs in ("soft", "burn"):
        srt_out = project / (out_path.stem + ".srt")
        srt_count = write_global_srt(segs, project, srt_out)

    summary = {
        "ok": True,
        "output": str(out_path),
        "size_mb": round(out_path.stat().st_size / 1048576, 2),
        "segments": len(parts),
        "resolution": f"{w}x{h}",
        "fps": args.fps,
        "srt_cues": srt_count,
        "srt": str(project / (out_path.stem + ".srt")) if srt_count else None,
        "manifest": str(man_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=1))
        sys.exit(1)
