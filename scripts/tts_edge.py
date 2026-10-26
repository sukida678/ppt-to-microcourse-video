#!/usr/bin/env python3
"""tts_edge — synthesise narration with Microsoft Edge TTS (free, no API key).

Reads a segments JSON  [{"id": "01", "text": "..."}, ...]
Writes:
    <out>/audio/<id>.mp3     narration audio
    <out>/sub/<id>.srt       subtitles, timed from 0 for that segment
    <out>/tts.json           [{"id","text","duration","chars","cues","srt","audio"}]

Usage
    python tts_edge.py --segments build/segments.json --out build --voice zh-CN-XiaoxiaoNeural
    python tts_edge.py --voices            # list Chinese voices

Notes
    * Chinese voices: zh-CN-XiaoxiaoNeural (F, warm), zh-CN-YunxiNeural (M),
      zh-CN-YunyangNeural (M, newsreader), zh-CN-XiaoyiNeural (F, young),
      zh-CN-YunjianNeural (M, sports), zh-CN-liaoning-XiaobeiNeural (F, NE accent),
      zh-CN-shaanxi-XiaoniNeural (F, Shaanxi accent)
    * the service returns per-word timings (WordBoundary) with punctuation stripped;
      subtitles are built from YOUR original text (punctuation intact) and each line
      is snapped to the real word timings, so they neither drift nor read like a
      machine transcript. --max-chars controls line length (default 18).
    * output format is mp3 24k mono — good enough for speech, tiny files
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

TICKS = 10_000_000  # edge-tts offsets are in 100-nanosecond units
PUNCT_RE = re.compile(r"[\s\u3000，。！？；、…,;:.!?\"'“”‘’（）()\[\]【】《》〈〉—\-–_/\\|~·]")
CLAUSE_SPLIT = re.compile(r"(?<=[，。！？；、…,;!?])")


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


def ffprobe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def fmt_ts(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_lines(text: str, max_chars: int) -> list[str]:
    """Break narration into subtitle lines at punctuation, never mid-phrase."""
    text = re.sub(r"[ \t\u3000]+", " ", text.strip())
    frags = [f for f in CLAUSE_SPLIT.split(text) if f.strip()]
    lines: list[str] = []
    cur = ""
    for frag in frags:
        if cur and len(cur) + len(frag) > max_chars:
            lines.append(cur.strip())
            cur = frag
        else:
            cur += frag
    if cur.strip():
        lines.append(cur.strip())
    # don't leave a 2-character orphan on its own line
    if len(lines) >= 2 and len(lines[-1]) <= 6 and len(lines[-2]) + len(lines[-1]) <= max_chars + 6:
        lines[-2] = lines[-2] + lines[-1]
        lines.pop()
    return lines


def _merge_short(cues: list[tuple[float, float, str]], max_chars: int):
    if len(cues) >= 2 and len(cues[-1][2]) <= 6:
        a, b, t = cues[-2]
        a2, b2, t2 = cues[-1]
        if len(t) + len(t2) <= max_chars + 6:
            cues[-2] = (a, b2, t + t2)
            cues.pop()
    return cues


def build_cues(text: str, words: list[dict], max_chars: int) -> list[tuple[float, float, str]]:
    """Snap punctuation-aware lines onto the real per-word timings."""
    lines = split_lines(text, max_chars)
    if not lines:
        return []
    if not words:
        return []

    clean_text = PUNCT_RE.sub("", text)
    words_join = "".join(w["t"] for w in words)
    aligned = clean_text == words_join

    cues: list[tuple[float, float, str]] = []
    if aligned:
        wi = 0
        consumed = 0  # chars of words_join already used
        for line in lines:
            n = len(PUNCT_RE.sub("", line))
            if n <= 0:
                continue
            start_wi = wi
            need = consumed + n
            while wi < len(words) and consumed < need:
                consumed += len(words[wi]["t"])
                wi += 1
            if wi == start_wi:
                continue
            first = words[start_wi]
            last = words[wi - 1]
            cues.append((first["o"], last["o"] + max(0.30, last["d"]), line))
    else:
        # text/time misalignment (numbers spelled out, language changed…) ->
        # distribute proportionally over the spoken span
        total = sum(len(PUNCT_RE.sub("", ln)) for ln in lines) or 1
        t0 = words[0]["o"]
        t1 = words[-1]["o"] + words[-1]["d"]
        span = max(0.5, t1 - t0)
        acc = 0
        for line in lines:
            n = len(PUNCT_RE.sub("", line))
            a = t0 + span * acc / total
            acc += n
            b = t0 + span * acc / total
            cues.append((a, b, line))

    cues = _merge_short(cues, max_chars)
    fixed = []
    for i, (a, b, t) in enumerate(cues):
        if i + 1 < len(cues) and b > cues[i + 1][0]:
            b = cues[i + 1][0] - 0.02
        fixed.append((a, max(a + 0.3, b), t))
    return fixed


def cues_to_srt(cues: list[tuple[float, float, str]]) -> str:
    return "".join(
        f"{i}\n{fmt_ts(a)} --> {fmt_ts(b)}\n{text}\n\n"
        for i, (a, b, text) in enumerate(cues, start=1)
    )


def _submaker():
    """edge-tts moved SubMaker around between majors; be forgiving."""
    try:
        from edge_tts import SubMaker  # noqa: PLC0415
        return SubMaker
    except Exception:
        pass
    try:
        from edge_tts.submaker import SubMaker  # noqa: PLC0415
        return SubMaker
    except Exception:
        return None


async def synth_one(text: str, voice: str, rate: str, volume: str, pitch: str,
                    mp3_path: Path, srt_path: Path, max_chars: int) -> tuple[float, str]:
    import edge_tts  # noqa: PLC0415

    SubMaker = _submaker()
    last_err: Exception | None = None
    for attempt in range(3):
        words: list[dict] = []
        try:
            try:
                com = edge_tts.Communicate(text, voice, rate=rate, volume=volume,
                                           pitch=pitch, boundary="WordBoundary")
            except TypeError:  # older edge-tts has no `boundary` kwarg
                com = edge_tts.Communicate(text, voice, rate=rate, volume=volume, pitch=pitch)
            sub = SubMaker() if SubMaker else None
            with open(mp3_path, "wb") as fh:
                async for chunk in com.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
                    elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                        words.append({"o": int(chunk["offset"]) / TICKS,
                                      "d": int(chunk["duration"]) / TICKS,
                                      "t": chunk["text"]})
                        if sub is not None:
                            try:
                                sub.feed(chunk)
                            except Exception:
                                pass
            srt_text = cues_to_srt(build_cues(text, words, max_chars)) if words else ""
            if not srt_text and sub is not None:
                try:
                    srt_text = sub.get_srt()
                except Exception:
                    srt_text = ""
            if srt_text.strip():
                srt_path.write_text(srt_text, encoding="utf-8")
            elif srt_path.exists():
                srt_path.unlink()
            return ffprobe_duration(mp3_path), srt_text.strip()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if mp3_path.exists():
                mp3_path.unlink()
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"TTS failed after 3 attempts: {last_err}")


async def main_async(args) -> int:
    out = Path(args.out).resolve()
    (out / "audio").mkdir(parents=True, exist_ok=True)
    (out / "sub").mkdir(parents=True, exist_ok=True)

    segs = json.loads(Path(args.segments).read_text(encoding="utf-8-sig"))
    if isinstance(segs, dict) and "segments" in segs:
        segs = segs["segments"]

    results = []
    for i, seg in enumerate(segs, start=1):
        sid = str(seg.get("id") or f"{i:02d}")
        text = (seg.get("text") or "").strip()
        if not text:
            results.append({"id": sid, "text": "", "duration": 0.0, "chars": 0,
                            "cues": 0, "srt": None, "audio": None})
            continue
        mp3 = out / "audio" / f"{sid}.mp3"
        srt = out / "sub" / f"{sid}.srt"
        t0 = time.time()
        dur, srt_text = await synth_one(text, args.voice, args.rate, args.volume,
                                        args.pitch, mp3, srt, args.max_chars)
        cues = srt_text.count("-->")
        print(f"  [{sid}] {len(text)} chars -> {dur:.1f}s audio"
              f"  ({time.time() - t0:.1f}s synth)"
              f"{f', {cues} subtitle cues' if cues else '  [no subtitles]'}",
              flush=True)
        results.append({
            "id": sid, "text": text, "duration": round(dur, 3),
            "chars": len(text), "cues": cues,
            "srt": str(srt) if srt_text else None,
            "audio": str(mp3),
        })

    (out / "tts.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    total = sum(r["duration"] for r in results)
    print(json.dumps({
        "ok": True,
        "voice": args.voice,
        "segments": len(results),
        "total_seconds": round(total, 1),
        "total_minutes": round(total / 60, 2),
        "total_chars": sum(r["chars"] for r in results),
        "with_subtitles": sum(1 for r in results if r["srt"]),
        "tts_json": str(out / "tts.json"),
    }, ensure_ascii=False, indent=1))
    return 0


VALUE_OPTS = {"--segments", "--out", "--voice", "--rate", "--volume", "--pitch", "--max-chars"}


def normalize_argv(argv):
    """`--rate -6%` -> `--rate=-6%` (argparse would treat '-6%' as an option)."""
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in VALUE_OPTS and i + 1 < len(argv):
            out.append(f"{a}={argv[i + 1]}")
            i += 2
        else:
            out.append(a)
            i += 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Edge TTS narration for slide segments")
    ap.add_argument("--segments", help="segments JSON file")
    ap.add_argument("--out", help="project output dir")
    ap.add_argument("--voice", default="zh-CN-XiaoxiaoNeural")
    ap.add_argument("--rate", default="+0%", help="e.g. +10%% faster, -10%% slower")
    ap.add_argument("--volume", default="+0%")
    ap.add_argument("--pitch", default="+0Hz")
    ap.add_argument("--max-chars", type=int, default=18,
                    help="max characters per subtitle line (default 18)")
    ap.add_argument("--voices", action="store_true", help="list zh voices and exit")
    args = ap.parse_args(normalize_argv(sys.argv[1:] if argv is None else argv))

    if args.voices:
        import edge_tts  # noqa: PLC0415
        voices = asyncio.run(edge_tts.list_voices())
        for v in voices:
            if v["Locale"].startswith("zh"):
                print(f"{v['ShortName']:34s} {v['Gender']:6s} {v['Locale']}")
        return 0

    if not args.segments or not args.out:
        ap.error("--segments and --out are required")
    try:
        return asyncio.run(main_async(args))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=1))
        return 1


if __name__ == "__main__":
    sys.exit(main())
