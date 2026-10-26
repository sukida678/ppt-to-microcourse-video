---
name: ppt-to-microcourse-video
description: Turn a PowerPoint deck (or PDF / exported slide images) into a finished micro-lecture video — narrated MP4 with burned-in subtitles, Ken Burns motion and fades. Use when the user wants 把PPT做成微课/课件视频/幻灯片配旁白成片/培训视频/知识点讲解视频, or asks to "convert slides to video", "narrate this deck", "PPT 转视频", "生成微课". Runs on plain Python + ffmpeg + edge-tts, so it works in any agent platform (Claude Code, Codex, Cursor, Gemini CLI, DSH, or a plain shell) — no vendor API, no paid TTS, no cloud upload.
---

# PPT → micro-lecture video

A deck becomes a teaching video: **slide image → spoken narration → subtitles → one MP4**.

The pipeline is deterministic and cheap; the thing that decides whether the result is
*watchable* is the narration you write. Your job is the script. The scripts do the rest.

```
deck.pptx ──extract──▶ png/slide_NN.png + slides.json (text & speaker notes)
                          │
                          ▼   ← you write narration.json here (the teaching script)
narration.json ──build──▶ mp3 per slide ──▶ course.mp4 + course.srt
```

Three commands, in order — the middle step (writing the narration) is yours:

```bash
python scripts/doctor.py --fix                                  # 1 can this machine do it
python scripts/make_course.py extract --deck deck.pptx --work build --dpi 150   # 2 slides out
#   ...write build/narration.json (step 3, the actual work)...   # 3
python scripts/make_course.py build --work build                # 4 narration in, video out
```

## 1. Check the machine

```bash
python scripts/doctor.py --fix      # add --no-net to skip the TTS probe
                                    # add --strict to fail without LibreOffice
```

Parse the JSON. `ready.full_pipeline: false` means stop and fix the named blocker
(`blockers[]` carries an install hint). If only `libreoffice.ok` is false you can still
work — ask for the slides as PNG, or export a PDF, and pass that instead (§"Other inputs").

## 2. Extract the deck

```bash
python scripts/make_course.py extract --deck lesson.pptx --work build --dpi 150
```

Outputs `build/png/slide_01.png…`, `build/slides.json`, and a **template**
`build/narration.json` pre-filled with each slide's raw text. That template is a
placeholder, not a script — never ship it unchanged.

`--dpi 150` gives 2000×1125 from a 13.33″ deck (crisp at 1080p). Use `200` for 4K.

## 3. Write the script — this is the actual work

Open `build/slides.json`, read each slide's `title`, `bullets` and `notes`, then write
`build/narration.json`:

```json
{
  "voice": "zh-CN-XiaoxiaoNeural",
  "slides": {
    "1": "先问一句：车间里那些按钮、传感器、电机，是谁在指挥它们配合的？答案就是今天的主角——PLC。",
    "2": "拆开来看，它只有三大块：输入单元负责听，CPU 负责想，输出单元负责做。",
    "3": ""
  }
}
```

Rules that matter:

- **Speak, don't read.** Never paste the bullet text. Explain it as you would to one
  person sitting next to you. If a slide's text appears verbatim in the audio, the video
  feels like a screen reader.
- **One idea per slide, 15–30 秒.** Mandarin narration runs ≈ **4.5 字/秒** at default
  rate, so 60–140 字 per slide. Anything over ~180 字 means the slide should be split.
- **Slide 1 = hook, last slide = recap.** Open with a question, a stake, or a wrong
  belief. Close by restating the 2–3 things to remember, not with "谢谢观看".
- **Empty string = silence.** Use `""` for a title card, a diagram, or a breather; the
  compositor holds that slide for `--hold` seconds (default 5).
- **Numbers and units spoken out loud**, symbols spelled: "百分之三十", "三点五千伏",
  not "30%" or "3.5kV" — TTS reads symbols unpredictably.
- Leave a beat between ideas with `，` and `。` punctuation; long unpunctuated text gets
  machine-gunned out at one tempo.

Deeper pedagogy, timing math and worked examples: [references/scriptwriting.md](references/scriptwriting.md).

## 4. Build

```bash
python scripts/make_course.py build --work build
```

Options worth knowing (`--help` for all):

| flag | default | use it when |
|---|---|---|
| `--voice` | `zh-CN-XiaoxiaoNeural` | pick a voice: `python scripts/tts_edge.py --voices` |
| `--rate` | `+0%` | `-8%` for training content that must be followed step by step |
| `--resolution` | `1920x1080` | `3840x2160` for 4K, `1280x720` for size |
| `--motion` | `kenburns` | `none` when the deck is full-bleed or text runs to the slide edge — any zoom crops it |
| `--zoom` | `1.06` | end zoom factor; keep ≤1.08 or edge text gets cut |
| `--transition` | `fade` | `none` for the fastest encode |
| `--subs` | `burn` | `soft` for a sidecar `.srt` only, `none` to skip |
| `--sub-box` | off | slides with content near the bottom edge: draws a translucent band so the subtitle doesn't fight the deck's own text |
| `--sub-font` | 平台 CJK 字体 | Windows `Microsoft YaHei` / macOS `PingFang SC` / Linux `Noto Sans CJK SC`；指定的字体没装时 `compose.py` 会换成第一个已装 CJK 字体并打印 `! subtitle font … is not installed — using …` |
| `--bgm` | — | background music, mixed under the voice at `--music-volume 0.12` |
| `--hold` | `5.0` | seconds a slide with no narration stays on screen |

`build` refuses to run when `narration.json` is **missing** (nothing was written at all);
`--allow-silent` forces a silent slideshow instead. If the file exists but still copies the
slide text, `build` only prints a warning —
`! narration.json still matches the slide text verbatim — that is a screen reader, not a lesson. Rewrite it in spoken language.`
— and goes on to render, so a screen-reader video is possible: rewrite the lines, don't
assume the tool stopped you.

It prints a JSON summary ending in `output`, `size_mb`, `srt_cues`. **Report those
numbers to the user.** A finished micro-lecture with 0 srt cues means the subtitles
silently failed — say so rather than claiming success.

## 5. Verify before you deliver

1. `ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 build/course.mp4`
   — duration should be within a few seconds of the sum of the narration lengths.
2. Extract one frame and **look at it**:
   `ffmpeg -y -ss 5 -i build/course.mp4 -frames:v 1 /tmp/f.png` then read the image.
   Check: subtitles legible and not clipped, no black frame, slide not distorted.
3. Deliver `course.mp4` and `course.srt`.

## Other inputs

- **PDF deck** — `--deck lesson.pdf` (skips LibreOffice's pptx step).
- **No LibreOffice** — export slides yourself and pass a folder:
  `--deck slides_png/` (any `*.png/jpg` order = playback order).
- **Another renderer** (PowerPoint COM, Keynote, a host's office tool) — produce the PNGs
  yourself, then run the folder form. The skill only needs images + a script.
- **No network / TTS blocked** — synthesize audio yourself and hand-build the manifest;
  see [references/backends.md](references/backends.md).

## Layout

```
scripts/doctor.py      环境自检 + 可选自动 pip 安装
scripts/make_course.py 编排：extract / build / all
scripts/ppt2png.py     渲染：pptx/pdf → pdf → png（LibreOffice + PyMuPDF）
scripts/tts_edge.py    edge-tts 旁白合成 + 逐词 SRT
scripts/compose.py     ffmpeg 合成：缩放/运镜/字幕/转场/拼接/配乐
references/            脚本撰写、后端替换、故障排查
```

## Hard rules

- **Never fabricate a delivery.** If `compose` failed, report the error, not a duration.
- Keep the work dir inside the user's project; delete `parts/` only after `course.mp4` exists.
- Don't re-encode the user's source deck; the pipeline copies images, it never edits the PPTX.
- A 20-minute "微课" is not a micro-lecture. If the deck needs more than ~10 minutes of
  narration, tell the user to split it into sections — that is a content decision, not a
  rendering one.
