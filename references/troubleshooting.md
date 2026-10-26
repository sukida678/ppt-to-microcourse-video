# 故障排查

先跑 `python scripts/doctor.py`，它会明确指出缺什么。下面是各类报错的实际处理办法。

---

## 一、LibreOffice / 渲染

**`LibreOffice not found`**
装上再跑，或显式指路：

```bash
python scripts/make_course.py extract --deck x.pptx --soffice "D:\LibreOffice\program\soffice.exe"
```

Linux 常见路径 `/usr/bin/soffice`、`/opt/libreoffice*/program/soffice`、`/snap/bin/libreoffice`。
macOS：`/Applications/LibreOffice.app/Contents/MacOS/soffice`。
便携版（MSI 用 `msiexec /a` 解出来的那种）只要指到 `program/soffice.exe` 即可。

**`soffice` 卡住不动 / 返回 0 但没生成 PDF**
- 已有 soffice 进程占着用户配置：脚本用独立的 `-env:UserInstallation=build/_lo_profile` 规避，
  若仍卡住，先 `pkill soffice.bin` / `taskkill /IM soffice.bin /F` 再重试。
- 首次启动要建 profile，慢 5–20 秒是正常的；**不要**在 10 秒内超时重试。
- 深路径 + 中文文件名偶发失败：把 pptx 复制到 `build/` 下短名再转。

**PDF 生成了但 PNG 全白 / 全黑**
- 白：字体缺失导致文字没画出来，或页面本来就只有白色形状。换 `--dpi 200` 再试。
- 黑：alpha 通道问题。`pdf_to_png` 已经用 `alpha=False` 合成白底。

**中文变方框（tofu）**
Linux 容器缺 CJK 字体：

```bash
apt-get install -y fonts-noto-cjk        # Debian/Ubuntu
dnf install -y google-noto-sans-cjk-fonts
```

之后清 LibreOffice 缓存（`build/_lo_profile`）再重新渲染。

**PPTX 里的动画 / 视频没了**
正常。静态渲染只保留每页的最终版面。动画需求请用原版 PPT 播放，或改用录屏方案。

---

## 二、TTS

**`edge-tts` 报 `403` / `NoAudioReceived` / 超时**
- 需要外网访问微软的语音端点。公司网/校园网常见被拦。
- 脚本自带 3 次重试（1.5s/3s/4.5s 退避）。仍失败就换离线方案（`references/backends.md` §二），
  或者只做无声版 + 外挂字幕。
- 别短时间内反复重跑同一段：会触发限流，等 30 秒。

**`Invalid voice`**
`python scripts/tts_edge.py --voices` 看实际可用的 `ShortName`，必须一字不差。

**念得太快/太慢**
`--rate -8%` 或 `+10%`。中文超过 `+15%` 开始吃字；超过 `-15%` 会显得拖沓。

**数字/符号被念错**
TTS 对 `%`、`≥`、`/`、`→` 的处理不稳定。**脚本里就写成汉字**：
百分之三十、大于等于、或者、从 A 到 B。这是写脚本时的责任，不是后处理能救的。

**英文缩写念成字母**
`PLC` 念"屁艾欧西"取决于引擎。首次出现给中文全称 + 缩写，或者接受字母读法（工程类视频里
字母读法反而更自然）。

---

## 三、ffmpeg / 合成

**`ffmpeg not found`**
macOS **`brew install ffmpeg-full`**（见下一条，官方 `ffmpeg` 是瘦身版）/ `apt install ffmpeg` / `winget install Gyan.FFmpeg`。
Windows 也可以直接下 `ffmpeg-release-essentials.zip` 解压后把 `bin` 加进 PATH。

**`height not divisible by 2` / `width not divisible by 2`**
`yuv420p` 要求偶数边长。用 `--resolution 1920x1080`（已保证），别用奇数分辨率。

**`Error opening filters` / `No such filter: 'subtitles'`**
这个 ffmpeg 构建没带 libass。检查 `ffmpeg -filters | grep subtitles`。
**macOS 上这就是 `brew install ffmpeg` 的默认结果**：Homebrew 的 `ffmpeg` 9.0 起依赖里没有
libass / fontconfig / freetype，能转码、烧不出字幕；换 `brew install ffmpeg-full` 才带。
`doctor.py` 会把这条直接列成阻塞项，`compose.py` 在开始渲染前也会拦下来（而不是跑到一半才炸）。
只想先要成品的话用 `--subs soft`（只出 .srt 不烧进画面）。

**烧字幕时报 `Unable to open ... : Invalid argument`（Windows）**
`subtitles=` 滤镜里的绝对路径 `C:\...` 会被当成选项分隔符。脚本现在会把路径换算成
**相对 `--project`** 的 `build/sub/01.srt`；若还剩空格/引号/逗号/方括号，就复制成
`parts/sub_NN.srt` 这种纯 ASCII 短名再引用。路径在 project 外时不会烧字幕，并打
`! [01] subtitle file lives outside --project`。自己改代码时别改回绝对路径。

**字幕字体被替换成宋体 / 位置偏了**
`--sub-font` 的默认值现在是**按平台**给的（Windows `Microsoft YaHei` / macOS `PingFang SC` /
Linux `Noto Sans CJK SC`）。`compose.py` 会先探测字体是否真的装了：没装就换成第一个可用的
CJK 字体并打印 `! subtitle font 'X' is not installed — using 'Y'`；一个 CJK 字体都找不到时
`ok` 会变 `false`（否则 libass 会静默烧出豆腐块）。

**`DTS out of order` / 拼接后音画错位**
所有分段必须**同一套编码参数**才能 `-c copy` 拼接。`compose.py` 已统一（libx264/aac/48k/stereo），
自己插片段时务必保持一致；否则去掉 `-c copy` 改成重新编码。

**音画不同步 / 每段尾部被切**
`apad,atrim=0:DUR` 负责把短音频补齐、长音频截断，`DUR` 来自 `manifest.duration + --tail`。
手工写 manifest 时 `duration` 一定要用 `ffprobe` 的真实值，别按字数瞎估。

**运镜（kenburns）抖动**
`zoompan` 的整数取整会造成抖动，脚本用 2 倍超采样（`scale=w*2:h*2`）缓解。
文本密集的页面直接 `--motion none`，观感更稳。

**文件太大**
`--crf 24`、`--preset veryfast`、`--resolution 1280x720`。10 分钟 1080p 微课
通常在 80–200 MB（crf 20）。课件类画面静止多，码率浪费，crf 23 也够看。

**合成很慢**
1080p 30fps + kenburns，1 分钟成片约 20–40 秒编码。想快：`--motion none --transition none
--preset veryfast`。4K 会慢 3–4 倍。

---

## 四、结果不对但没报错

**成片比预期短很多**
某几页 `narration` 写成了空串（静音，只停 `--hold` 秒）。看 `build/tts.json` 里
`duration: 0` 的条目。

**字幕一句都没有（`srt_cues: 0`）**
- 用了 `--subs soft/burn` 但 TTS 没给时间戳（换过引擎）。
- 或者 `narration` 全空。
**不要在没字幕的情况下对外说"已加字幕"**，如实报告 `srt_cues`。

**某一页明显比旁白长/短**
`--tail`（默认 0.7s）是每页尾部留白。要紧凑就 `--tail 0.3`，要留呼吸就 `0.9–1.2`。

**画面被拉伸变形**
`scale=...:force_original_aspect_ratio=decrease` + `pad` 是等比缩放加白边。若源图比例
和 `--resolution` 差太多（比如竖版 9:16），白边会很宽 —— 换成匹配的分辨率，比如 `1080x1920`。

---

## 五、Windows 编码 / 中文路径

**`UnicodeEncodeError: 'gbk' codec can't encode character '\u03a2' in position 367`**
典型现场：`build` 的配音其实已经成功（`[01] 50 chars -> 11.2s audio` 都打出来了），
随后整体 `ok: false` 退出，看起来像"配音完了却失败"。

原因：`--work` 指向的目录名里有中文。子进程（`tts_edge.py` / `compose.py`）的 stdout 走
Windows ANSI 码页（cp936），父进程却按 UTF-8 去解 —— `微` 的 GBK 编码是 `CE A2`，这两个字节
在 UTF-8 里恰好是合法码位 **U+03A2**。父进程于是拿到一个 cp936 编不回去的字符，崩在
`print()` 上，而不是崩在业务逻辑里。

已修（`safe_stdio()` + 子进程 `PYTHONIOENCODING=utf-8`）。如果你手上是旧副本：
把 `--work` 换成纯英文路径，或先 `set PYTHONIOENCODING=utf-8` 再跑。

**中文路径到底能不能用？**
能。整条链路已按中文路径（`D:\微课skill-交接\_regress`）回归过：extract → TTS → 合成 →
ffprobe 全部正常，产物与英文路径下逐字节一致。仍然建议路径短一点：LibreOffice 对很深的
路径偶发失败（见 §一）。

**控制台里中文显示成 `΢��skill-����`**
只是显示层。脚本往管道里写的是 UTF-8 JSON，终端按 GBK 解释就会花屏；`.json` / `.srt` /
文件名本身没问题，用编辑器或文件管理器看是对的。

---

## 六、交付前必做

```bash
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 build/course.mp4
ffmpeg -y -ss 5 -i build/course.mp4 -frames:v 1 /tmp/check.png
```

看 `/tmp/check.png`：字幕可读、没有黑帧、内容没被裁。跑得出时长不等于视频正确。
