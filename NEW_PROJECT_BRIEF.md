# Video Highlight Reel Generator — New Project Brief

---

## Application Description

A web application hosted in a Docker container that turns long-form video recordings into concise, polished highlight reels. The user accesses it from any browser on the local network, marks interesting moments in a video, optionally enriches them with AI-detected speech segments, mixes in background music, and gets a finished MP4 — all without touching a video editor.

Video files (which can be 60–300 GB) are never uploaded. They are accessed directly from mounted volumes — typically a NAS share mounted into the container. The app reads and writes files through the filesystem only; the browser is purely a UI.

The tool is built for power users who record long sessions (sports, events, travel, meetings) and need to produce shareable summaries fast. Processing is fully local — no cloud uploads, no subscriptions.

**Deployment model:**
- Docker container running on a Linux server (e.g. Fedora)
- NAS shares mounted on the host via NFS/SMB, then passed into the container as volumes
- User accesses the web UI from any device on the local network
- Standard mount points: `/videos` (source files, read-only), `/music` (audio library, read-only), `/output` (finished files, read-write), `/archive` (session data, read-write)

---

## Functional Requirements

### Core Concepts

- **Source Video**: One or more long recordings (MP4, MKV, MOV, AVI, or similar). Multiple files can be concatenated into a single logical source before processing.
- **Highlight**: A time-bounded clip from the source, or a still image inserted at a point in the timeline.
- **Summary**: The finished output video — a subset of the source stitched together in chronological order.
- **Target Length**: The user-specified duration for the final output (e.g. 15 minutes).
- **Auto-fill**: If manual highlights are shorter than the target length, the system automatically selects evenly-distributed segments from the source to fill the gap without repeating already-marked content.

### Feature Set

#### F1 — Highlight Management
- Add, edit, and remove highlights with start time, end time, and an optional comment.
- Add picture highlights: a still image (JPG/PNG) that appears in the timeline for a configurable duration.
- Highlights must be sorted and deduplicated: overlapping ranges merge into a single segment.
- Persist all highlights and settings to a per-video JSON archive so a session can be resumed.
- Auto-load the archive when the same source video is selected again.

#### F2 — Auto-Fill
- Calculate total duration of manual highlights.
- If the total is below the target summary length, divide the remaining time evenly across the video's unused timeline.
- Select non-overlapping segments from those slots to reach the target length.
- Slight extensions (e.g. +1 second) on existing highlights are acceptable to close small gaps.

#### F3 — AI Speech Detection & Transcription
- Extract the audio track from the source video.
- Run Voice Activity Detection (VAD) to identify segments containing human speech. VAD must filter out non-speech noise (beeping, wind, engine).
- Pass only VAD-detected segments to a speech-to-text engine (Whisper or equivalent).
- Merge transcribed speech segments with manual highlights (with deduplication).
- Generate an SRT subtitle file alongside the output video.
- Support multiple model sizes (tiny → large) for a speed/accuracy trade-off.
- Use GPU acceleration when available; fall back to CPU automatically.

#### F4 — Background Music
- **Folder mode**: Recursively scan a folder for audio files, shuffle, and auto-select enough tracks to cover the summary duration. Cache file durations per folder (keyed by folder path hash) to avoid repeated scanning.
- **File mode**: User picks a single audio file; the tool loops it if the video is longer.
- Decode selected tracks losslessly, concatenate seamlessly, then mix with the original audio.
- Audio normalization to broadcast standard (EBU R128 loudnorm) before mixing.
- User sets the music/original-audio balance (e.g. 40% music, 60% original).
- Generate a music credits text file listing every track used.

#### F5 — Full-Video Music Overlay
- Apply background music to the entire original video without re-encoding the video stream (stream-copy where possible) for speed.
- Uses the same music selection and mixing logic as F4.

#### F6 — Video Concatenation
- Select multiple source files; concatenate them into a single logical source before the main workflow runs.
- User can see the order in which (initial alphabetical) the videos will be concatenated and can change the order so that it fits his will.

#### F7 — Output
- Primary output codec: H.264 video + AAC audio, MP4 container.
- Use hardware encoding (NVIDIA NVENC or equivalent) when available; fall back to software encoding.
- Output filename derived from source filename with a descriptive suffix (e.g. `_Summary`, `_WithMusic`).
- No intermediate files left behind after successful encoding (temp files cleaned up).

#### F8 — Server-Side File Browser
- The web UI must never ask the user to upload a video file.
- Provide a file browser UI that lists directories and files on the server's mounted volumes (`/videos`, `/music`, `/output`).
- The browser sends a selected path (string) to the backend; the backend resolves it against the allowed mount roots.
- Path traversal outside the allowed roots must be rejected server-side.
- For picture highlights, display a thumbnail preview of the selected image in the UI.

#### F9 — Real-Time Progress Feedback
- Long operations (encoding, transcription, VAD, music scanning) run as background tasks on the server.
- The UI receives live progress updates via WebSocket or Server-Sent Events (SSE).
- Progress includes: current stage name, percentage complete, estimated time remaining where possible.
- The UI must remain fully interactive while a job is running.
- If the user closes and reopens the browser, active job status must still be visible.

#### F10 — Prerequisite Management (Docker)
- FFmpeg and all Python dependencies are installed at image build time — no runtime installation.
- On startup, the app verifies that mount points exist and are readable/writable as required.
- If a mount is missing, the UI shows a clear error with the expected path.

---

## Use Cases

### UC-01 — Create a Highlight Reel from a Single Video
**Actor**: User  
**Precondition**: Source video file exists on a mounted volume.  
1. User opens the web UI in a browser.
2. User navigates the server-side file browser to select the source video.
3. App reads the video metadata (duration, resolution, codec) via ffprobe.
4. User sets target summary length (e.g. 15 minutes).
5. User adds highlights by entering start/end times and optional comments.
6. User clicks "Generate Summary."
7. App auto-fills any remaining time with evenly-distributed segments.
8. App encodes the output MP4; UI shows live progress.
9. App saves the highlight data to a JSON archive.
10. User sees a finished `VideoName_Summary.mp4` in the output browser.

---

### UC-02 — Resume a Previous Session
**Actor**: User  
**Precondition**: A JSON archive exists for the source video.  
1. User opens the web UI and selects the same source video.
2. App detects the existing archive and loads all highlights, target length, and music settings automatically.
3. User modifies or adds highlights.
4. User regenerates the summary.

---

### UC-03 — Add AI-Detected Speech to Highlights
**Actor**: User  
**Precondition**: Source video has audio track with human speech.  
1. User has already added manual highlights (or starts with none).
2. User selects "Add Speech Highlights."
3. User picks Whisper model size.
4. App extracts audio, runs VAD, then runs Whisper on VAD-filtered segments — progress shown live.
5. App merges transcribed segments with manual highlights (deduplicating overlaps).
6. App encodes the output MP4 with an accompanying SRT file.

---

### UC-04 — Insert a Photo into the Highlight Reel
**Actor**: User  
1. User clicks "Add Picture Highlight."
2. User browses the server filesystem to select an image file (JPG/PNG).
3. UI shows a thumbnail preview of the image.
4. User sets a display duration (e.g. 5 seconds) and optional comment.
5. The image appears as a still frame in the timeline at the specified position.
6. App scales/letterboxes the image to match the video resolution during encoding.

---

### UC-05 — Add Background Music to a Summary
**Actor**: User  
1. After adding highlights, user enables background music.
2. User browses the server filesystem to select a music folder or specific audio file.
3. User sets the music-to-original ratio (e.g. 40%).
4. App selects enough tracks (shuffled), concatenates, normalizes, and mixes.
5. Output video has music blended under the original audio.
6. A credits text file lists every track used.

---

### UC-06 — Add Background Music to the Full Original Video
**Actor**: User  
1. User selects source video and selects "Full Video with Music."
2. User selects music source and ratio.
3. App mixes music with video's original audio; video stream is copied without re-encoding.
4. Output is `VideoName_WithMusic.mp4` produced quickly.

---

### UC-07 — Concatenate Multiple Videos then Summarize
**Actor**: User  
1. User selects multiple video files via the server-side file browser.
2. App shows files and in which order it will concatenate; user can re-order via drag-and-drop or move-up/move-down buttons.
3. App concatenates them into one logical source.
4. User marks highlights across the combined timeline.
5. Standard summary workflow proceeds.

---

### UC-08 — GPU-Accelerated Processing
**Actor**: System (automatic)  
1. On startup, app detects available hardware encoders (NVIDIA NVENC via `nvidia-smi`, or software fallback).
2. If hardware encoding is available, it is used automatically.
3. If not, the app falls back to software encoding with no user action required.
4. The encoding method in use is shown in the UI status area.

---

### UC-09 — Monitor a Running Job from a Different Device
**Actor**: User  
**Precondition**: A job was started from one browser session.  
1. User opens the web UI on a different device (e.g. phone or tablet on the same network).
2. The active job and its live progress are visible immediately.
3. User can see which stage is running and estimated completion time.

---

### UC-10 — Browse and Download Output Files
**Actor**: User  
1. After encoding completes, user sees the output file listed in the UI.
2. User can browse the `/output` directory from the web UI.
3. User can download a finished MP4 directly from the browser, or leave it on the NAS (it's already there via the volume mount).

---

## Build Prompt

Use this prompt verbatim when starting the AI coding session:

---

```
You are building a web application called "Video Highlight Reel Generator."
Its purpose: turn long-form video recordings (60–300 GB source files) into concise highlight reels with optional AI speech detection and background music mixing.

The app runs in a Docker container on a Linux server. Users access it via browser on their local network. Video files are never uploaded — they are accessed directly from Docker volume mounts (NAS shares mounted on the host via NFS/SMB, then passed into the container). The browser is purely a UI; all file I/O is server-side.

## Stack & Constraints

- Language: Python 3.12+
- Backend: FastAPI (async, with background task support via asyncio or a task queue like arq/dramatiq if needed for job persistence across reconnects)
- Frontend: Choose the most appropriate modern approach — either (a) a lightweight server-rendered stack (Jinja2 + HTMX + TailwindCSS) for simplicity, or (b) a React/Vue SPA if the interactivity level justifies it. Evaluate and recommend with rationale.
- Real-time updates: WebSocket or Server-Sent Events (SSE) for live job progress. Do NOT use polling.
- Video processing: FFmpeg (via asyncio subprocess, NOT a Python wrapper). Invoke ffmpeg and ffprobe as subprocesses; parse JSON from ffprobe for metadata.
- AI inference: OpenAI Whisper (local) + Silero VAD (PyTorch).
- Audio: soundfile or torchaudio for WAV I/O.
- GPU: Detect NVIDIA CUDA via nvidia-smi at startup; use h264_nvenc for FFmpeg encoding when available. Fall back to libx264 automatically.
- Data: Persist all project state (highlights, settings) as JSON. One JSON file per source video, stored in the `/archive` volume mount.
- Mount points (configurable via environment variables with these defaults):
  - `/videos` — source video files (read-only)
  - `/music` — audio library (read-only)
  - `/output` — finished files (read-write)
  - `/archive` — session JSON files (read-write)
- No cloud calls, no telemetry, no external APIs. Everything runs locally.
- Path traversal protection: server-side file browser must reject any path that escapes the configured mount roots.

## Performance Requirements

- FFmpeg filter_complex must be built in a single pass — no intermediate re-encoding of individual clips.
- VAD runs before Whisper: only send VAD-confirmed segments to Whisper to minimize compute.
- Music folder scanning results cached (by folder path hash) as JSON under `/archive/music_cache/`; never re-scan an unchanged folder.
- Hardware encoding (NVENC) used by default; software encoding only as fallback.
- Encoding and transcription run as non-blocking background tasks — the HTTP server must remain responsive while jobs run.
- If the user closes and reopens the browser, active job status must still be visible (job state persisted server-side).

## Functional Scope

Implement all of the following:

1. **Server-Side File Browser** — REST API + UI component to navigate `/videos`, `/music`, and `/output` directories. Returns file listings with name, size, and last-modified date. No file upload for source videos or music. For image files, serve thumbnails.

2. **Highlight Management** — add/edit/remove video highlights (start, end, comment) and picture highlights (image path on server, duration, comment). Deduplicate and sort highlights. Auto-fill remaining time to reach a target summary length with evenly distributed non-overlapping segments.

3. **Session Persistence** — save and auto-reload a per-video JSON archive (source path, target length, music settings, all highlights). Stored in `/archive`.

4. **Video Concatenation** — accept multiple source files (selected via file browser), show them in order, allow user to re-order (drag-and-drop or up/down buttons), concatenate into one logical source before processing.

5. **Summary Encoding** — build a single FFmpeg filter_complex command that trims, scales/pads images, concatenates all segments, and encodes to H.264 + AAC MP4 in one pass. Output written to `/output`.

6. **AI Speech Detection** — extract audio → Silero VAD → Whisper transcription → merge with manual highlights → generate SRT alongside output video. Support model size selection (tiny/base/small/medium/large).

7. **Background Music** — folder mode (shuffle, auto-select enough tracks, cache durations) and file mode (with loop). Decode to WAV, concatenate, EBU R128 loudnorm, mix with original audio at user-set ratio. Generate music credits file in `/output`.

8. **Full-Video Music Overlay** — apply music to original video with stream-copy for the video track (fast path, no video re-encode).

9. **Live Job Progress** — WebSocket or SSE endpoint that streams stage name, percent complete, and elapsed time for active jobs. Job state survives browser refresh.

10. **Startup Health Check** — on container start, verify all mount points exist and have correct permissions; report missing mounts clearly in the UI.

## Code Quality Rules

- Organize into modules: `api/` (routes, websocket), `core/` (video, audio, archive, speech, filebrowser), `utils/`.
- No global state outside of a single app config object loaded from environment variables.
- All FFmpeg commands logged to a structured log (JSON lines) in `/archive/logs/`; errors surface to the UI with the full failing command shown.
- Type-annotate all public functions.
- Dependencies: fastapi, uvicorn, torch, openai-whisper, soundfile, and the chosen frontend tooling. No MoviePy or similar Python video wrappers.
- `Dockerfile` (CPU): Python 3.12 slim base, install ffmpeg via apt.
- `Dockerfile.gpu`: nvidia/cuda base image, same ffmpeg install, CUDA-enabled torch.
- `docker-compose.yml`: defines both cpu and gpu service variants, volume mounts for `/videos`, `/music`, `/output`, `/archive`, and optional NVIDIA runtime for the GPU variant.

## Deliverables

1. Full source code, organized as described above.
2. `requirements.txt` with pinned versions.
3. `Dockerfile` (CPU) and `Dockerfile.gpu` (CUDA).
4. `docker-compose.yml` with example volume mounts.
5. `README.md` covering: how to mount a NAS share on a Linux host (NFS and SMB examples), how to run the CPU and GPU containers, how to verify GPU passthrough is working.
6. No placeholders — every feature listed above must be implemented and working.

Start by proposing the project structure and the frontend approach choice (server-rendered vs SPA) with a one-paragraph rationale for each layer. Wait for approval before writing code.
```

---

## Notes for the AI Session

- **Do not mention PowerShell** in the new project — the AI should have no bias toward the old implementation.
- If the AI proposes MoviePy or similar Python video wrappers, reject them. FFmpeg subprocess is mandatory for performance.
- If the AI proposes `<input type="file">` for selecting source videos or music folders, reject it. File selection must go through the server-side file browser API.
- The JSON archive schema from the old project is a good reference but the AI is free to improve it.
- The VAD threshold (0.6) and audio sample rate (48kHz stereo 16-bit) from the old project are sensible defaults.
- For the NAS mount on the Fedora host: `mount -t nfs 192.168.x.x:/volume1/videos /mnt/nas/videos` then `-v /mnt/nas/videos:/videos:ro` in Docker. Add to `/etc/fstab` for persistence across reboots.
