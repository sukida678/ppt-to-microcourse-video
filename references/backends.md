# 换后端：语音、渲染器、手工组装

本 skill 的数据契约只有两个 JSON 文件。任何工具只要产出这两个文件，就能接进来。

## 一、契约

### `build/segments.json`（配音输入，`tts_edge.py` 读）

```json
[
  {"id": "01", "image": "png/slide_01.png", "text": "第一页的旁白"},
  {"id": "02", "image": "png/slide_02.png", "text": ""}
]
```

### `build/manifest.json`（合成输入，`compose.py` 读）

```json
{
  "out": "course.mp4",
  "segments": [
    {"id": "01", "image": "png/slide_01.png",
     "audio": "build/audio/01.mp3", "duration": 12.31, "srt": "build/sub/01.srt"},
    {"id": "02", "image": "png/slide_02.png", "duration": 0}
  ]
}
```

- 所有相对路径**相对 work 目录**解析。
- `duration` = 该段音频时长（秒，保留 3 位小数）；无音频写 `0`，改用 `--hold`。
- `audio` / `srt` 可缺省。缺 `audio` 时该页静音；缺 `srt` 时该页无字幕。
- 段序 = 播放序。

只要这两个文件对，`compose.py` 不关心音频是哪个引擎合成的、图片是谁渲染的。

## 二、换 TTS

### 默认：edge-tts（免费、免密钥、需联网）

```bash
python scripts/tts_edge.py --voices                      # 列中文音色
python scripts/tts_edge.py --segments build/segments.json --out build \
       --voice zh-CN-YunxiNeural --rate -8%
```

中文常用音色：

| voice | 听感 | 适合 |
|---|---|---|
| `zh-CN-XiaoxiaoNeural` | 女声，温暖自然 | 通用微课（默认） |
| `zh-CN-YunxiNeural` | 男声，年轻 | 技术/软件 |
| `zh-CN-YunyangNeural` | 男声，播音腔 | 正式培训、法规 |
| `zh-CN-XiaoyiNeural` | 女声，偏年轻 | 面向学生 |
| `zh-CN-YunjianNeural` | 男声，解说腔 | 宣传片、赛事 |
| `zh-CN-liaoning-XiaobeiNeural` | 东北话 | 接地气的科普 |
| `zh-CN-shaanxi-XiaoniNeural` | 陕西话 | 同上 |

它同时产出**逐词 SRT**（`build/sub/NN.srt`），这是烧字幕的基础。换引擎时若拿不到时间戳，
就只能用 `--subs none`，或者按字数估算时间码（见下 §四）。

### 离线：piper（ONNX，无网络、无密钥）

```bash
# 模型：zh_CN-huayan-medium.onnx + .json  (huggingface / hf-mirror)
echo "第一页的旁白" | piper --model zh_CN-huayan-medium.onnx --output_file 01.wav
ffmpeg -y -i 01.wav -ar 48000 -ac 2 build/audio/01.mp3
```

产不出 SRT；用 `--subs none`，或按 §四 生成。

### 系统自带

```bash
# macOS
say -v Tingting -o 01.aiff "第一页的旁白" && ffmpeg -y -i 01.aiff build/audio/01.mp3
# Windows (SAPI)
powershell -c "Add-Type -AssemblyName System.Speech; \
  (New-Object System.Speech.Synthesis.SpeechSynthesizer).SetOutputToWaveFile('01.wav'); \
  (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('第一页的旁白')"
```

### 商业 API（更自然，需密钥）

Azure Neural TTS、OpenAI `tts-1`、ElevenLabs、火山/阿里云 —— 都是 POST 一段文本拿
mp3。流程一样：按 `segments.json` 逐段合成 → 写 `audio/NN.mp3` → `ffprobe` 取时长 →
填 `manifest.json` → 直接跑 `compose.py`。

## 三、换渲染器（deck → PNG）

`make_course.py extract` 默认走 LibreOffice。要换的时候，只要自己产出
`png/slide_NN.png`，然后：

```bash
python scripts/make_course.py extract --deck slides_png/ --work build   # 目录形式
```

### LibreOffice（跨平台默认）

```bash
soffice --headless --norestore --convert-to pdf --outdir build lesson.pptx
```

再用 pymupdf 转 PNG（`make_course.py` 已内置）。

### PowerPoint COM（Windows，装了 Office）

```python
import win32com.client                      # pip install pywin32
app = win32com.client.Dispatch("PowerPoint.Application")
pres = app.Presentations.Open(r"C:\path\lesson.pptx", WithWindow=False)
for i, s in enumerate(pres.Slides, 1):
    s.Export(rf"C:\path\png\slide_{i:02d}.png", "PNG", 2000, 1125)
pres.Close()
```

保真度最高（动画、SmartArt、字体都是原样）。WPS 演示的 ProgID 是 `Kwpp.Application`，
接口基本一致。

### Keynote（macOS）

```bash
osascript -e 'tell application "Keynote" to open POSIX file "/path/lesson.key"'
# 导出为 PDF 后再用 pymupdf 转 PNG；Keynote 的 applescript 导出图像支持有限
```

### 宿主自带的 office 工具

某些 agent 平台自带 pptx→图片的渲染能力（例如 DSH 的 LibreOffice Kit）。有就直接用，
产出 PNG 后走目录形式。**这是可选优化，不是依赖** —— skill 本身不假设宿主有它。

### PDF → PNG 的替代品

```bash
pdftoppm -png -r 150 lesson.pdf build/png      # poppler-utils，Linux/mac brew
magick -density 150 lesson.pdf slide-%02d.png  # ImageMagick
```

## 四、无时间戳时手工造 SRT

按 4.5 字/秒 估算，单条字幕不超过 22 字：

```python
rate = 4.5          # 字/秒
t = 0.0
for i, line in enumerate(lines, 1):
    dur = max(1.2, len(line) / rate)
    out.append(f"{i}\n{ts(t)} --> {ts(t+dur)}\n{line}\n")
    t += dur
```

`ts()` 输出 `HH:MM:SS,mmm`。精度不如逐词对齐，但比没有字幕强。

## 五、手搓清单（最省事的自定义路径）

1. `png/slide_NN.png` —— 自己渲染好
2. `audio/NN.mp3` —— 自己合成好
3. 用 `ffprobe` 逐段取时长
4. 写 `manifest.json`（见 §一）
5. `python scripts/compose.py --project build --subs none`

只用到 ffmpeg，一条命令出成片。
