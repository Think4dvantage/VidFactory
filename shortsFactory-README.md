# ShortFactory

A Windows desktop application that automates the creation of **vertical YouTube Shorts** (1080×1920) from paragliding footage. The operator marks reusable segments of their raw flight videos once, and ShortFactory then assembles randomized, hardware-accelerated shorts on demand — single or in batches — writing the finished `.mp4` files to a configured output folder (a NAS share, by default).

This README is a complete functional + technical specification: it describes everything the app does and how it is built, in enough detail to recreate it from scratch.

---

## 1. What it does (product overview)

The user runs a paragliding YouTube channel. They fly with an Insta360 camera, export raw clips, and want a steady stream of short-form vertical videos without editing each one by hand.

ShortFactory solves this by separating **one-time tagging** from **on-demand generation**:

1. **Tag a flight once.** For each flight the user creates a "flight archive" and marks:
   - a **takeoff** clip (a pre-made vertical file, used whole),
   - a **landing** clip (pre-made vertical file, used whole),
   - a **flying pool**: one or more source videos with marked IN→OUT ranges of good flying footage,
   - (for "hike & fly") a **hiking pool**: source videos with marked ranges of the hike up.
2. **Generate shorts.** Pick a short type, choose how many to render, and click render. The app randomly samples sub-clips from the pools, assembles them in a fixed narrative order, transcodes everything to a uniform vertical format with a hardware encoder, appends a branded call-to-action end screen, optionally mixes in a background-music track (see §11), and saves the result.
3. **Repeat.** Because tagging is stored in a small JSON file per flight, the same flight can produce many non-overlapping shorts over time. Generated shorts are recorded back into the archive (with the exact source ranges used), so history and footage usage are tracked.

### Short structure

Two short types, both ending in a static branded CTA:

```
Hike & Fly:    hiking ×2 (3s each) → takeoff → flying ×3 (3s each) → landing → CTA (3s)
Normal Flight:                       takeoff → flying ×3 (3s each) → landing → CTA (3s)
```

- **No fixed total length** — the duration is simply the sum of the sections. Takeoff and landing are pre-made clips used at their full natural length; everything else is a fixed multiple of the configured clip duration.
- **Takeoff & landing** are used as-is (no trimming).
- **Flying clips** are picked at random from the flying pool, then **sorted chronologically** by `(file, start)` so the final video preserves the natural progression of the flight.
- **Hiking footage** is assumed to be already sped-up on delivery (the user exports time-lapse-style hiking from Insta360 Studio), so by default no additional speed-up is applied (`hiking_speed_factor = 1.0`). The app *can* re-speed it if the factor is changed.

### Batch rendering & de-duplication

When rendering N shorts in one batch, the app tracks every time-range it has already consumed (per source file) in a `UsedMap`, and only samples from the **free** sub-ranges that remain. This guarantees that shorts in a batch don't reuse the same footage. If the pool runs out of unused footage, rendering stops early with a clear message. The same de-dup logic also prevents a single short from picking overlapping flying clips.

---

## 2. Tech stack

- **Language:** Python ≥ 3.11
- **GUI:** PySide6 (Qt 6) — `QMainWindow`, `QMediaPlayer`/`QVideoWidget` for preview, custom `QPainter` timeline widget
- **Video processing:** FFmpeg + ffprobe (invoked as subprocesses; not a Python binding)
- **HTTP (for FFmpeg auto-download):** httpx
- **Packaging:** hatchling; dev tooling: pytest, ruff (line length 100)
- **Platform:** Windows (uses `ffmpeg.exe`/`ffprobe.exe`, `nvidia-smi`, Windows font paths, UNC/NAS output path)

`pyproject.toml` essentials:

```toml
[project]
name = "shortfactory"
requires-python = ">=3.11"
dependencies = ["PySide6>=6.7.0", "httpx>=0.27.0"]

[project.scripts]
shortfactory = "shortfactory.__main__:main"
```

---

## 3. Architecture & module map

```
src/shortfactory/
  __init__.py          __version__ = "0.1.0"
  __main__.py          Startup: ensure FFmpeg → load config → detect GPU → show MainWindow
  core/
    prereq_manager.py  Auto-download ffmpeg/ffprobe from BtbN GitHub releases; staleness check
    gpu_detector.py    NVIDIA → Intel QSV (test-encode) → libx264 fallback; cached per session
    ffmpeg_runner.py   subprocess wrappers: probe() + encode() with stderr progress parsing
    archive.py         JSON load/save of flight archives in archive/<stem>.json
    duration_calc.py   Fixed section-budget math; atempo filter-chain builder
    short_builder.py   Clip selection, de-dup, filter_complex assembly, encode orchestration
  models/
    segment.py         PoolSegment, SourceFile, GeneratedShort, FlightArchive (dataclasses + to/from_dict)
    short_config.py    AppConfig, ShortBudget (dataclasses)
  ui/
    main_window.py     QMainWindow with a 3-panel horizontal QSplitter + status bar
    video_library.py   Left panel: flight list with ready/partial/empty status colors
    segment_editor.py  Center panel: video player, IN/OUT marking, takeoff/landing, pools
    short_composer.py  Right panel: type selector, budget preview, batch count, render, history
    progress_dialog.py Modal progress dialog running the encode task on a worker thread
    widgets/
      pool_group.py    One segment pool (file list + segment list + Set Files / Add / Rename / Delete)
      timeline.py      Custom-painted timeline showing pool segments + IN/OUT selection; click to seek
      segment_list.py  Standalone segment list widget (rename/delete)
```

Supporting directories (not all in git):

```
config/app_config.json        App configuration (committed)
archive/<stem>.json           Per-flight tagging + history (committed; synced across the user's PCs)
resources/
  fonts.conf                  Minimal fontconfig pointing at C:/Windows/Fonts (for drawtext)
  EndScreenBackground.JPG     CTA end-screen image (9:16, branding baked in by the user)
  ffmpeg/                     Auto-downloaded ffmpeg.exe/ffprobe.exe + .version.json (gitignored)
output/                       Default output dir (gitignored); real default is a NAS UNC path
```

### Data flow

```
VideoLibraryPanel  --flight_selected(stem)-->  MainWindow
       └─ archive/<stem>.json  --load-->  SegmentEditorPanel + ShortComposerPanel
SegmentEditorPanel  --archive_changed(stem)-->  MainWindow  --> refresh library + composer
ShortComposerPanel  --render-->  ProgressDialog(worker thread)
       └─ short_builder.select_clips() -> build_and_encode() -> FFmpegRunner.encode()
       └─ on success: GeneratedShort appended to archive, saved back to JSON
```

---

## 4. Startup sequence (`__main__.py`)

`main()`:

1. Create `QApplication`, set name/version.
2. **Ensure FFmpeg** (`_ensure_ffmpeg`): if `prereq_manager.needs_download()`, show a Yes/No dialog ("FFmpeg was not found or is outdated. Download it now? (~80 MB)"). On Yes, show a modal `QProgressDialog` and stream the download with a cancelable progress callback. Abort startup if declined or it fails.
3. Load config from `config/app_config.json` (fall back to `AppConfig.default()` on any error).
4. Build an `FFmpegRunner` from the resolved ffmpeg/ffprobe paths.
5. `gpu_detector.detect(ffmpeg)` to choose the encoder.
6. Ensure the output directory exists.
7. Construct and show `MainWindow(runner, gpu, config, repo_root)` and enter the Qt event loop.

`_repo_root()` is `Path(__file__).parent.parent.parent` (i.e. `src/shortfactory/__main__.py` → repo root). **Note the path-depth nuance:** modules under `core/` are one level deeper, so they use `parents[3]` / `.parent.parent.parent.parent` to reach the same repo root. Get this wrong and resources/archive folders resolve to the wrong place.

---

## 5. Core modules in detail

### 5.1 `core/prereq_manager.py` — FFmpeg bootstrapping

- Downloads a static Windows GPL build from BtbN's releases:
  `https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip`
- Target dir: `<repo>/resources/ffmpeg/`. Exposes `ffmpeg_path()` and `ffprobe_path()`.
- `needs_download()` is true if either exe is missing **or** the cached build is stale.
- **Staleness:** a `.version.json` stores `downloaded_at` (ISO timestamp) + source URL; builds older than `_REFRESH_DAYS = 30` are considered stale.
- `download_ffmpeg(progress_cb)` streams the zip with httpx (`follow_redirects=True`, 64 KiB chunks, reporting `(bytes_done, total)`), then `_extract()` pulls **only** `ffmpeg.exe` and `ffprobe.exe` out of the archive (ignoring the nested folder structure), deletes the zip, and writes the version stamp.

### 5.2 `core/gpu_detector.py` — encoder selection

`detect(ffmpeg_path)` is memoized per process and returns a `GPUConfig(codec, preset, display_name, extra_args)` with an `encoding_args(bitrate)` helper that yields `["-c:v", codec, "-preset", preset, "-b:v", bitrate, *extra_args]`. Detection order:

1. **NVIDIA** — run `nvidia-smi --query-gpu=name --format=csv,noheader` (5s timeout). On success → `h264_nvenc`, preset `p4`, no extra args.
2. **Intel QSV** — run `ffmpeg -encoders`; if `h264_qsv` is listed, **test-encode** a tiny lavfi `color` source through `h264_qsv` to `-f null` (15s timeout). Only if the test exits 0 → `h264_qsv`, preset `medium`, extra args `["-look_ahead", "1"]`. (The test-encode matters: the encoder can be present but non-functional.)
3. **CPU fallback** — `libx264`, preset `medium`, extra args `["-crf", "18"]`.

### 5.3 `core/ffmpeg_runner.py` — subprocess wrapper

Constructed with the ffmpeg/ffprobe paths. On init it locates `resources/fonts.conf` (sibling of the `ffmpeg/` dir) and, if present, sets `FONTCONFIG_FILE` in a copied environment that is passed to **every** subprocess. This is what makes `drawtext` find Windows fonts by name.

- `probe(file)` → ffprobe JSON for the first video stream (`width,height,duration,r_frame_rate,codec_name`) + `format=duration`.
- `get_video_info(file)` → `(width, height, duration_seconds)`, preferring the stream duration and falling back to the container/format duration.
- `encode(args, total_duration, progress_cb, cancel_event)` → runs `ffmpeg <args>` via `Popen`, reading **stderr** line by line. It parses `time=HH:MM:SS.ss` to compute a `0..1` fraction and extracts `speed=…`, calling `progress_cb(fraction, speed)`. Honors a `threading.Event` for cancellation (terminates the process). On non-zero exit it raises `RuntimeError` containing the last ~30 stderr lines.

### 5.4 `core/duration_calc.py` — budget math

`calculate_budget(short_type, takeoff_duration, landing_duration, config)` returns a `ShortBudget`:

- `hiking_output_secs = hiking_clip_count * clip_duration` (only for `hike_and_fly`, else 0)
- `hiking_source_secs = hiking_output_secs * hiking_speed_factor` (how much raw pool footage is consumed)
- `flying_secs = flying_clip_count * clip_duration`
- `total = takeoff + hiking_output + flying + landing + cta`

There is **no clamping/cap** — the total is whatever the sections add up to.

`build_atempo_chain(speed_factor)` produces a chained `atempo` filter string (FFmpeg's `atempo` is limited to ≤2× per instance), e.g. `4×` → `atempo=2.0,atempo=2.0`. Used to keep audio in sync when hiking footage is re-sped.

### 5.5 `core/short_builder.py` — the heart of generation

**Clip selection**

- `UsedMap = dict[str, list[tuple[float, float]]]` — used time-intervals per file.
- `_available_windows(seg, needed, used)` — interval subtraction: returns the free sub-ranges of a pool segment that are still long enough for `needed` seconds.
- `_pick_subclip(segments, needed_secs, used)` — with a `UsedMap`, gathers all viable free windows across all segments, picks one at random, picks a random offset inside it, records the chosen `(start, end)` in the map, and returns `(file, start, end)`. Returns `None` when nothing free remains. Without a `UsedMap`, it does a simpler random pick (falling back to the longest segment if none is long enough).
- `_pick_clips(segments, count, needed_secs, used)` — picks up to `count` clips, then **sorts them chronologically** by `(file, start)`.
- `select_clips(archive, short_type, config, runner, used)` — probes takeoff/landing durations, computes the budget, then picks flying clips (and hiking clips for `hike_and_fly`). Raises clear `ValueError`s if pools are empty or exhausted. For hiking, the **source** window length is `clip_duration * hiking_speed_factor` (so that after speed-up it yields `clip_duration` of output).
- `suggest_batch_count(...)` — estimates how many non-overlapping shorts the current footage supports (limited by the flying pool, and additionally by the hiking pool for hike & fly).

**Filter graph assembly** (`_build_filter_complex`)

Produces FFmpeg `-i` input args plus a `filter_complex` string. One `-i` per clip (even when two clips come from the same source file). For each input, video and audio are trimmed and normalized, then all streams are concatenated.

Normalization (`_normalize_filter(w, h)`) targets **1080×1920 @ 30fps CFR**:

- Horizontal source → center-crop to 9:16 then scale:
  `crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,setsar=1:1,fps=30`
- Already-vertical source → pillarbox:
  `scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1:1,fps=30`

Two normalization rules are **load-bearing** for the QSV encoder and the concat filter:

- **`setsar=1:1` on every stream** — without it, fractional sample-aspect-ratios produced by the crop arithmetic make `concat` fail.
- **`fps=30` on every video stream** — without forcing constant frame rate, QSV rejects the mixed-frame-rate stream that results from concatenating camera footage (e.g. 60fps) with the static CTA image. (This `fps=30` is appended to *all* normalize outputs and to the CTA.)

Per-section construction, in concat order:

1. **Hiking** (hike & fly only): for each clip, `trim`/`atrim` to the chosen range, reset PTS, normalize. If `speed_factor != 1.0`, apply `setpts=(1/factor)*PTS` to video and `build_atempo_chain` (or `volume=0` when `mute_hiking_audio`) to audio.
2. **Takeoff**: normalized video + `aresample=async=1` audio (used whole).
3. **Flying**: one trimmed+normalized clip per pick.
4. **Landing**: normalized video + `aresample=async=1` audio (used whole).
5. **CTA**: a looped still image input plus a separate silent `anullsrc` audio input:
   ```
   -loop 1 -t <dur> -i resources/EndScreenBackground.JPG
   -f lavfi -t <dur> -i anullsrc=channel_layout=stereo:sample_rate=48000
   ```
   Filtered with `fps=30,scale=1080:1920,setsar=1:1,drawtext(...two lines...),format=yuv420p,setpts=PTS-STARTPTS`. The two `drawtext` lines render the channel call-to-action ("for more relaxed Paragliding" / "Like & Subscribe") using `font=Arial` and `font=Arial Black` — resolved via fontconfig (`FONTCONFIG_FILE`). The CTA image path is discovered at import time via `resources/EndScreenBackground.*` (with `.jpg`/`.JPG` fallbacks).

All sections are concatenated with `concat=n=<N>:v=1:a=1[outv][outa]`. The concat label order for hike & fly is:
`[vhike0][ahike0][vhike1][ahike1][vto][ato][vfly0][afly0][vfly1][afly1][vfly2][afly2][vland][aland][vcta][acta]`.

**Encode orchestration** (`build_and_encode`)

Assembles the final ffmpeg argument list: inputs, `-filter_complex`, `-map [outv] -map [outa]`, the GPU video args (dropping `-b:v` when libx264/CRF is in use), `-c:a aac -b:a <audio_bitrate>`, `-t <total_secs>`, `<output> -y`. Runs it through `FFmpegRunner.encode`. On success, appends a `GeneratedShort` record to the archive — including the exact source ranges used per section — and saves the archive JSON.

---

## 6. Data models

### `models/segment.py`

- `PoolSegment(start, end, label="")` with a `duration` property.
- `SourceFile(file, pool: list[PoolSegment])`.
- `GeneratedShort(output_file, short_type, created_at, total_duration_seconds, segments_used)`.
- `FlightArchive(schema_version=1, flight_type="normal_flight", takeoff_file, landing_file, hiking_sources, flying_sources, shorts_generated)` with helpers: `all_hiking_segments()`, `all_flying_segments()`, `is_ready_for_normal_flight()` (takeoff + landing + at least one flying segment), `is_ready_for_hike_and_fly()` (the above **and** at least one hiking segment).

All models implement `to_dict()` / `from_dict()` for stable JSON round-tripping.

### `models/short_config.py`

- `ShortBudget` — per-section seconds plus `display_rows()` for the UI table.
- `AppConfig` — all tunables (see below), with `to_dict` / `from_dict` / `default()`.

---

## 7. Configuration (`config/app_config.json`)

```json
{
  "schema_version": 1,
  "gpu_override": null,
  "output_directory": "\\\\172.18.10.10\\pg\\Shorts",
  "default_short_type": "hike_and_fly",
  "clip_duration_seconds": 3.0,
  "hiking_clip_count": 2,
  "flying_clip_count": 3,
  "cta_duration_seconds": 3.0,
  "ffmpeg_video_bitrate": "8M",
  "ffmpeg_audio_bitrate": "128k",
  "ffmpeg_preset_nvenc": "p4",
  "ffmpeg_preset_qsv": "medium",
  "ffmpeg_preset_cpu": "medium",
  "hiking_speed_factor": 1.0,
  "mute_hiking_audio": false
}
```

| Key | Meaning |
|---|---|
| `output_directory` | Where shorts are written. Default is a NAS UNC share; relative paths resolve under the repo root. |
| `default_short_type` | `hike_and_fly` or `normal_flight`. |
| `clip_duration_seconds` | Length of each sampled flying/hiking sub-clip in the output. |
| `hiking_clip_count` / `flying_clip_count` | Number of sub-clips per section. |
| `cta_duration_seconds` | End-screen duration. |
| `ffmpeg_video_bitrate` / `ffmpeg_audio_bitrate` | Encode bitrates (video bitrate ignored for the CRF libx264 path). |
| `ffmpeg_preset_*` | Per-encoder presets. |
| `hiking_speed_factor` | Extra speed-up applied to hiking footage. `1.0` = none (footage already sped up on delivery). |
| `mute_hiking_audio` | Silence hiking audio in the output. |
| `music_directory` | Folder of audio tracks for background music (see §11). Empty = no music. |
| `music_volume` | Gain applied to the music track before mixing (e.g. `0.35`). |
| `original_audio_volume` | Gain applied to the clip/camera audio before mixing music under it (e.g. `1.0`; lower to duck wind noise). |

> **Currently inert fields** (present in `AppConfig` but not consumed by any code path — keep them inert when reproducing): `gpu_override` (the GPU detector ignores it), `default_short_type` (the composer uses the flight's own `flight_type`), and `ffmpeg_preset_nvenc` / `ffmpeg_preset_qsv` / `ffmpeg_preset_cpu` (the presets are hardcoded inside `GPUConfig` as `p4` / `medium` / `medium` and these config values are never read). Wiring them up would change behavior. The `ui/widgets/segment_list.py` `SegmentListWidget` is likewise unused (the editor uses `pool_group.py`).

---

## 8. Per-flight archive schema (`archive/<stem>.json`)

One file per flight (the `stem` is the flight name). These are small, human-readable, and intended to be version-controlled / synced across machines.

```json
{
  "schema_version": 1,
  "flight_type": "hike_and_fly",
  "takeoff_file": "D:/Flights/Takeoff.mp4",
  "landing_file": "D:/Flights/Landing.mp4",
  "hiking_sources": [
    { "file": "D:/Flights/hike.mp4", "pool": [{ "start": 0.0, "end": 45.0, "label": "" }] }
  ],
  "flying_sources": [
    { "file": "D:/Flights/flight.mp4", "pool": [{ "start": 30.0, "end": 180.0, "label": "" }] }
  ],
  "shorts_generated": [
    {
      "output_file": "\\\\172.18.10.10\\pg\\Shorts\\MyFlight_Short_01.mp4",
      "short_type": "hike_and_fly",
      "created_at": "2026-06-20T14:30:00",
      "total_duration_seconds": 22.5,
      "segments_used": {
        "hiking":  [{ "file": "...", "start": 10.0, "end": 13.0 }],
        "takeoff": "D:/Flights/Takeoff.mp4",
        "flying":  [{ "file": "...", "start": 45.0, "end": 48.0 }],
        "landing": "D:/Flights/Landing.mp4",
        "music":   "D:/Music/uplift.mp3"
      }
    }
  ]
}
```

`core/archive.py` provides `list_archives()`, `load(stem)`, `save(stem, archive)`, `create_empty(stem, flight_type)`, plus path helpers — all rooted at `<repo>/archive/` (created on demand).

---

## 9. User interface

`MainWindow` is a 1400×900 window with a horizontal 3-panel `QSplitter` (stretch ~1 / 3 / 1) and a status bar showing the detected GPU and the loaded flight. Signals wire the panels together: selecting a flight loads it into both the editor and composer; edits refresh the library and composer; finishing a render refreshes the library and reports success/failure.

### Left — `VideoLibraryPanel` (flight list)

- Lists every `archive/*.json` by stem, colored by readiness: **green** = ready, **orange** = partially configured, **grey** = empty (with matching tooltips and a legend).
- **+ New Flight** prompts for a name (spaces → underscores) and a type (`hike_and_fly` / `normal_flight`), creates an empty archive, and selects it. **Refresh** rescans.
- Emits `flight_selected(stem)`.

### Center — `SegmentEditorPanel` (tagging)

- **Video player**: `QMediaPlayer` + `QVideoWidget` with play/pause (Space), a position label/slider, and a duration label. A `QTimer` (200 ms) syncs the position label and slider during playback.
- **Custom timeline** (`TimelineWidget`): a `QPainter`-drawn bar showing all pool segments for the currently-loaded source (hiking = blue, flying = green) and the live IN/OUT selection (amber, semi-transparent). Click anywhere to seek (`seek_requested`). (Note: no moving play-head line is drawn.)
- **Takeoff & Landing**: each has a "Set File…" button (file dialog); the label turns green when set, red when not.
- **Mark Segments**: `[I]` Set IN / `[O]` Set OUT buttons (with `I`/`O` keyboard shortcuts) capture the current player position.
- **Two pools** (`PoolGroupWidget` for hiking and flying): each has its own "📂 Set Files…" (multi-select source videos), a file list (click to load a file into the player; active file shown bold), a segment list for the active file, and **Add IN→OUT / Rename / Delete** controls. "Add IN→OUT" requires a loaded source and both marks set, enforces a ≥0.5 s minimum, and refuses to add to a pool that doesn't contain the loaded file (with a helpful warning). Every mutation saves the archive immediately and emits `changed`.
- The panel emits `archive_changed(stem)` so the rest of the app stays in sync.

### Right — `ShortComposerPanel` (generation)

- **Short Type**: radio buttons (Hike & Fly / Normal Flight), defaulting to the flight's type.
- **Duration Budget** table + a "🎲 Preview Budget" button that computes an estimated budget (using placeholder takeoff/landing durations of 3.0 s / 2.0 s for the preview only).
- **Batch Render**: a spinbox (1–99) for how many shorts to produce; the render button label updates to match ("▶ Render N Shorts").
- **Output Folder**: editable path (defaults to `repo_root / output_directory`) with a Browse button.
- **Render**: builds the list of output paths (`<stem>_Short_NN.mp4`, auto-incrementing past existing files), then runs the whole batch inside a `ProgressDialog`. The render task shares one `UsedMap` across all shorts in the batch, calls `select_clips` + `build_and_encode` per short, and reports per-short status. On completion it reloads the archive, updates the **Generated Shorts** history table, and shows a success/failure message box.

### `ProgressDialog`

A modal dialog that runs a task on a background `threading.Thread` via a `_Worker(QObject)` that emits Qt signals (`progress`, `status`, `finished`, `error`). The task signature is:

```python
fn(progress_cb(fraction: float, speed: str), status_cb(label: str), cancel_event: threading.Event) -> None
```

It shows a progress bar, an encode-speed label, and a Cancel button that sets the cancel event. `succeeded()` / `error_message()` are read by the caller afterward.

---

## 10. Critical implementation notes (gotchas to preserve)

These are the non-obvious details that make the pipeline actually work on the target machine:

1. **`setsar=1:1` on every stream before concat** — fractional SAR from crop math otherwise breaks `concat`.
2. **`fps=30` on every stream before concat** — QSV rejects variable/mixed frame rates (e.g. 60fps footage + a still image). Force CFR on *all* inputs including the CTA still.
3. **`FONTCONFIG_FILE` env var** — set on the FFmpegRunner to point at `resources/fonts.conf`, which lists `C:/Windows/Fonts`; this is what lets `drawtext` resolve `font=Arial` / `font=Arial Black` by name on Windows.
4. **CTA as image input + separate silent audio** — `-loop 1 -t <dur> -i image` paired with an `anullsrc` audio input keeps the concat's stream layout uniform (every concatenated unit has both a video and an audio leg).
5. **One `-i` per clip** — even when two clips come from the same file, give each its own input so the trims are independent.
6. **Path depth** — repo root is `parents[3]` from a `core/` module and `parent.parent.parent` from `__main__.py`; `resources/` is `parents[3] / "resources"`. Resolve these at import time so behavior is independent of the current working directory.
7. **GPU test-encode** — don't trust the encoder list alone; actually test-encode through `h264_qsv` before selecting it.
8. **Chronological sort after random sampling** — randomize *which* clips, but always emit them in `(file, start)` order so the short reads as a coherent flight.

---

## 11. Background music

> **Status:** specified feature to include in the build (not present in the original ShortFactory code this README was reverse-engineered from). It is described here at the same level of detail as the rest so it can be implemented directly.

Optionally mix a background-music track under each short. Behavior:

- **Source:** a `music_directory` folder of audio files (`.mp3 .m4a .aac .wav .flac .ogg .opus`, non-recursive). Empty/unset ⇒ no music; the pipeline is unchanged and clip audio plays alone (fully backward compatible).
- **Selection:** one track is chosen **at random per short**. In a batch render, selection spreads across the folder before repeating (track de-dup mirrors the footage `UsedMap`: keep a `set` of used track paths across the batch, prefer unused, fall back to random once exhausted). The chosen track is recorded in the archive under `segments_used.music`.
- **Mixing:** the music is looped to cover the full short and mixed **under** the concatenated clip audio (music continues over the CTA end screen). It does not replace the original audio unless `original_audio_volume` is set to `0`.

### UI

In `ShortComposerPanel`, a **Background Music** group below Output Folder: a path field (pre-filled from `music_directory`) + Browse button, and a small status line ("N tracks found — one picked at random per short" / "No music — original clip audio only" / a warning when the folder has no playable files). The field value is read at render time, exactly like the output-folder field.

### Pipeline (FFmpeg)

Extend the audio side of `short_builder` only — the video graph is untouched:

1. Add the music as a looped input so it always covers the short:
   ```
   -stream_loop -1 -i <music_file>
   ```
2. After the existing `concat=…[outv][outa]`, append to `filter_complex`:
   ```
   [outa]volume=<original_audio_volume>[amain];
   [<music_idx>:a]volume=<music_volume>[amusic];
   [amain][amusic]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]
   ```
3. Map `[aout]` instead of `[outa]` for audio. The existing `-t <total_secs>` still bounds the output.

**Why these flags:**
- `duration=first` ends the mix with the clip audio (which equals the short's length), trimming the looped music cleanly — without it, `amix` would run to the *longest* input (infinite, because of `-stream_loop -1`).
- `normalize=0` makes `amix` a true sum so the `volume=` gains are exact; the default (`normalize=1`) would silently scale every input by `1/inputs` and make the music far quieter than requested. (Sum of gains can clip if both are loud — keep `music_volume` modest, e.g. `0.3–0.4`, or duck the clip audio via `original_audio_volume`.)
- `-stream_loop -1` (an **input** option, before `-i`) loops short tracks to cover longer shorts.

**Input indexing caveat:** the music `-i` must be appended **after** all clip and CTA inputs, and its filter index must match. The CTA block adds *two* inputs (the looped still image + the silent `anullsrc`) but doesn't advance the running input counter in the original code — so when adding music, first fix the counter to point past the CTA inputs, then use it as the music index. Off-by-one here maps the wrong stream.

### Config

```jsonc
"music_directory": "",          // folder of tracks; empty = no music
"music_volume": 0.35,           // gain on the music before mixing
"original_audio_volume": 1.0    // gain on the clip audio (lower to duck wind noise)
```

---

## 12. Running it

```bash
# install (editable) with uv or pip
uv sync           # or: pip install -e .

# launch
python -m shortfactory     # or the installed `shortfactory` entry point
```

On first run, accept the FFmpeg download prompt (~80 MB, cached for 30 days under `resources/ffmpeg/`). Then: create a flight, set takeoff/landing, mark some flying (and hiking) segments, pick a short type and count, and render.

---

## 13. Glossary

- **Flight / archive** — one outing, stored as `archive/<stem>.json`, holding the takeoff/landing files, the tagged pools, and the history of shorts generated from it.
- **Pool** — a collection of source files each with marked IN→OUT ranges (`PoolSegment`s) that are randomly sampled at render time. There is a *hiking pool* and a *flying pool*.
- **Short** — one finished 1080×1920 vertical `.mp4`, assembled from sampled clips plus the takeoff, landing, and CTA.
- **CTA** — the branded "call to action" end screen (static image + drawn text + silent audio).
- **Background music** — an optional audio track (randomly chosen per short from a folder) looped and mixed under the clip audio; see §11.
- **Budget** — the computed per-section duration breakdown for a short.
- **UsedMap** — the per-file record of already-consumed time-ranges that guarantees non-overlapping footage within a render batch.
```
