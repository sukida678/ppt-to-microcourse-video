# AGENTS.md — operating manual for AI coding agents

**Read this whole file before running anything.** It is written for whatever agent the
user is driving: Codex, Claude Code, Cursor, Gemini CLI, DSH, or a plain terminal. There
is no vendor API and no cloud service — only local Python, ffmpeg and edge-tts.

The task: **a slide deck in, one narrated teaching video out.**

```
deck.pptx ──extract──▶ png/slide_NN.png + slides.json
                          │
                          ▼   ← step 3: you write narration.json (the real work)
narration.json ──build──▶ per-slide mp3 ──▶ course.mp4 + course.srt
```

### What is in this folder

```
AGENTS.md              ← this file: the authoritative manual, edit rules here first
SKILL.md               ← frontmatter + short flow for skill-aware hosts (DSH / Claude Code)
CLAUDE.md              ← pointer file → AGENTS.md
README.md              ← Chinese guide for teachers (3 steps, FAQ, screenshots)
PROMPTS.md             ← copy-paste prompts for common requests
install.py             ← dependency check / install (--json for agents)
docs/                  ← real frames from the demo build, used by README
scripts/doctor.py      ← preflight: python, ffmpeg, soffice, edge-tts, network
scripts/make_course.py ← extract / build / all — the entry point you normally call
scripts/ppt2png.py     ← deck → pdf → png (LibreOffice, then PyMuPDF)
scripts/tts_edge.py    ← narration → mp3 + word-timed SRT
scripts/compose.py     ← slides + audio → per-slide mp4 → course.mp4 (+ BGM, global SRT)
references/            ← scriptwriting math, input backends, troubleshooting
examples/              ← demo.pptx + narration.example.json, safe to run end-to-end
```

Put every generated file in a work directory (`--work build`), never in the repo root:
`build/`, `*.mp4` and `__pycache__/` are git-ignored.

---

## 0. Rules that override everything else

1. **Never ship the generated narration template unchanged.** `extract` writes a
   `narration.json` pre-filled with the slides' own text. That is a placeholder. If you
   build with it, the video is a screen reader reading bullet points. Rewrite every line.
2. **Never report success without evidence.** `build` must end with `"ok": true`,
   `srt_cues > 0`, and a duration close to the sum of the narration lengths. If
   `srt_cues` is 0, subtitles failed — say so, do not claim the video is done.
3. **Report real numbers to the user**: duration, file size, resolution, segment count,
   subtitle count. No "done!" without them.
4. **Do not invent content.** Narration explains what is on the slide plus general
   knowledge that is safely true. If a slide is unreadable or ambiguous, either keep the
   narration generic or ask the user — never guess specifics (dates, model numbers,
   names).
5. **Run the commands yourself.** The user may not know a terminal. Only ask them for
   things a machine cannot do: approving a UAC/admin install, picking a voice, or
   supplying content only they know.

---

## 1. Preflight — can this machine do it?

```bash
python scripts/doctor.py --fix
```

Prints JSON. Continue only when `ready.full_pipeline` is `true`.

| blocker | fix |
|---|---|
| python package missing | `pip install edge-tts pymupdf python-pptx` |
| `ffmpeg` missing | Windows `winget install Gyan.FFmpeg` · macOS `brew install ffmpeg` · Linux `apt install ffmpeg` (or `dnf`) |
| `soffice` (LibreOffice) missing | Windows `winget install TheDocumentFoundation.LibreOffice` · macOS `brew install --cask libreoffice` · Linux `apt install libreoffice-impress` |
| TTS probe fails (403/timeout) | network blocked. Try later, or see §7 "no TTS". |

`python scripts/doctor.py --no-net` skips the TTS network probe. Its exit code is 0 when the pipeline can run for the input you have (PNG/PDF slides); add `--strict` to exit 1 unless the full `.pptx` pipeline can run (LibreOffice present).

**Only LibreOffice missing is survivable**: ask the user to export the deck to PDF (or
PNG images) and use §8 instead. Everything else must be fixed first.

---

## 2. Extract the slides

```bash
python scripts/make_course.py extract --deck lesson.pptx --work build --dpi 150
```

Writes:

- `build/png/slide_01.png …` — one image per slide (2000×1125 at 150 dpi from a 13.33″ deck; use `--dpi 200` for 4K output)
- `build/slides.json` — per slide: `title`, `bullets`, `notes` (speaker notes when present)
- `build/narration.json` — **the template you must overwrite**

Read `build/slides.json` now. That is your source material.

Never keep working files in the skill directory — always pass `--work` a path inside the
user's project so the artefact stays next to their deck.

---

## 3. Write the narration — this is the actual work

Write `build/narration.json`:

```json
{
  "voice": "zh-CN-XiaoxiaoNeural",
  "slides": {
    "1": "先问一句：车间里那么多按钮、传感器和电机，是谁在指挥它们按顺序配合的？",
    "2": "拆开看只有三块：输入单元负责听，CPU 负责想，输出单元负责做。",
    "3": ""
  }
}
```

`slides` keys are the slide numbers as strings, `1..N`, one entry each.

### The rules that decide whether the video is watchable

- **Speak, don't read.** Never copy the bullet text. Explain it the way you would to one
  person sitting next to you, looking at the same slide.
- **One idea per slide, 15–30 s.** Mandarin runs ≈ **4.5 字/秒** at default rate →
  **60–140 字 per slide**. Over ~180 字 means the slide should be split, not rushed.
- **Slide 1 is the hook, the last slide is the recap.** Open with a question, a stake or
  a wrong belief. Close by restating the 2–3 things to remember — not with "谢谢观看".
- **`""` means silence.** Use it for a cover, a diagram, or a breather; the compositor
  holds that slide for `--hold` seconds (default 5).
- **Spell out symbols and units**: "百分之三十", "三点五千伏", "C 加加" — not `30%`,
  `3.5kV`, `C++`. TTS reads symbols unpredictably.
- **Punctuate for breath.** `，` and `。` are the rhythm; a long unpunctuated run comes
  out at one machine-gun tempo.
- **If the user supplied a syllabus, transcript or textbook excerpt, follow it** —
  their order and their emphasis beat your own.

Deeper guidance, timing math, worked before/after examples:
[references/scriptwriting.md](references/scriptwriting.md).

---

## 4. Build

```bash
python scripts/make_course.py build --work build
```

Prints a JSON summary ending in `output`, `size_mb`, `srt_cues`.

### Flag decisions

| flag | default | change it when |
|---|---|---|
| `--voice` | `zh-CN-XiaoxiaoNeural` | user wants a male voice → `zh-CN-YunxiNeural`; list all: `python scripts/tts_edge.py --voices` |
| `--rate` | `+0%` | training content that is followed step by step → `--rate=-8%` |
| `--resolution` | `1920x1080` | `3840x2160` for 4K, `1280x720` for a small file |
| `--motion` | `kenburns` | **`none` when the deck is full-bleed or text touches the slide edge** — any zoom crops it |
| `--zoom` | `1.06` | lower to `1.03` for a subtler push-in; never above 1.08 |
| `--sub-box` | off | slides with content near the bottom edge — draws a translucent band so the subtitle stops fighting the deck's own text |
| `--subs` | `burn` | `soft` to keep subtitles as a sidecar `.srt` only, `none` to skip |
| `--sub-font` | platform CJK font | Windows `Microsoft YaHei`, macOS `PingFang SC`, Linux `Noto Sans CJK SC`; a family that is not installed is replaced (`! subtitle font … not installed — using …`) instead of burning boxes |
| `--bgm` | — | background music, mixed under the voice (`--music-volume 0.12`) |
| `--hold` | `5.0` | seconds a silent slide stays up |

Check `--motion` first on any deck that is more than text-on-white: a full-bleed cover or
a chart running to the edges will lose content to the zoom. When in doubt, use
`--motion none` — a static, complete slide always beats a cropped one.

---

## 5. Verify before you deliver

```bash
ffprobe -v error -show_entries format=duration,size -of default=nw=1 build/course.mp4
ffmpeg -y -hide_banner -loglevel error -ss 20 -i build/course.mp4 -frames:v 1 check.png
```

1. **Duration** should be within a few seconds of the sum of the narration plus the tail
   padding (`--tail 0.7` per segment).
2. **Open 2–3 extracted frames** and actually look at them: is the subtitle on screen,
   readable, and not covering the slide's own bottom text? Is any text cut at the edges?
3. **`srt_cues` ≈ total characters / 18.** Wildly lower means the word timings were lost.

Fix and rebuild rather than delivering something you have not looked at.

---

## 6. Deliver

Copy the MP4 (and the `.srt` if `--subs soft`) to where the user wants it, verify the
copy by hash or size, and report:

> 10 分 03 秒 · 27.5 MB · 1920×1080 · 41 页 · 197 条字幕

Then offer the three knobs users actually care about: length (denser script), voice/rate,
and motion. If the deck has natural chapters, offer to split it into separate shorter
videos.

---

## 7. Failure → fix

| symptom | cause | fix |
|---|---|---|
| `UnicodeDecodeError: 'gbk' codec` on Windows | console encoding | already handled in the scripts; if it appears, run with `PYTHONUTF8=1` |
| `UnicodeEncodeError: 'gbk' codec can't encode '\u03a2'` right after the TTS lines | a non-ASCII `--work` path: the child wrote cp936 while we decoded UTF-8 (`微` = `CE A2` = U+03A2, a legal code point) | fixed by `safe_stdio()`; on an old copy use an ASCII path or set `PYTHONIOENCODING=utf-8` |
| `argument --rate: expected one argument` | `-8%` looks like an option | write `--rate=-8%` (scripts also normalise this) |
| `srt_cues: 0` | edge-tts returned no word boundaries | check `python -c "import edge_tts;print(edge_tts.__version__)"`; subtitles are rebuilt from word timings, so a version skew here silently kills them |
| CJK subtitles render as empty boxes | font missing | `compose.py` now checks the family and substitutes an installed one (`! subtitle font 'X' is not installed — using 'Y'`); pass `--sub-font` to choose |
| `ffmpeg not found on PATH` | not installed / not on PATH | see §1 |
| `soffice` not found | LibreOffice missing or non-standard path | pass `--soffice "C:\Program Files\LibreOffice\program\soffice.exe"` |
| LibreOffice conversion fails while the GUI is open | profile lock | close LibreOffice and retry |
| TTS 403 / timeout | network or service hiccup | retry once; if it persists, ask the user whether a silent slideshow (`--allow-silent`) or manual subtitle file is acceptable |
| subtitles sit on top of the deck's own bottom text | dense slides | `--sub-box` |
| edge text cut off | ken-burns zoom | `--motion none` |
| narration sounds rushed | rate | `--rate=-10%` |

More: [references/troubleshooting.md](references/troubleshooting.md).

---

## 8. Inputs other than `.pptx`

- **PDF** — `--deck lesson.pdf` works; slides are rasterised with PyMuPDF at `--dpi`.
- **A folder of images** — `python scripts/make_course.py extract --images ./slides --work build`.
  Files sort by name, so zero-pad them (`01.png`, `02.png`, …).
- **Already a video** — out of scope; this tool makes video from stills.

---

## 9. Definition of done

- [ ] `doctor.py` reported `full_pipeline: true`
- [ ] `slides.json` was read and every slide got a hand-written line
- [ ] `build` returned `ok: true` with `srt_cues > 0`
- [ ] duration and size were checked against the narration total
- [ ] two frames were opened and looked at
- [ ] the MP4 (and `.srt`) are in the user's requested location
- [ ] the user was given the numbers, not just "done"
