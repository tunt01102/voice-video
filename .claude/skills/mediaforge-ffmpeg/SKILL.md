---
name: mediaforge-ffmpeg
description: FFmpeg rules for MediaForge — which binary, the scene timeline contract, the filters the composer uses (zoompan, xfade, ass, sidechaincompress, loudnorm, videotoolbox) and how to debug a render. Use when touching video/compose.py, audio.py, export.py, animate.py, or when a render looks or sounds wrong.
---

# FFmpeg in MediaForge

- Binary: always `settings.ffmpeg` (keg-only `ffmpeg-full`, has libass/freetype). The PATH `ffmpeg` cannot burn subtitles.
- Helpers: `audio.run_ffmpeg(args)`, `audio.probe_duration(path)`. Don't call models or ffmpeg from request
  handlers — submit a job (`jobs.py`).
- Timeline contract (`video/compose.py`): scene i occupies `[B_i, B_i + d_i)`; each segment is rendered
  `d_i + T` long (T = 0.5 s xfade) so the cross-fade into scene i+1 starts at `B_{i+1}`. The last scene absorbs
  rounding so video length = narration + 0.5 s.
- Stills: Ken Burns via `zoompan` on a 2× upscale (no jitter). Videos: `fill="loop"` (`-stream_loop -1`) or
  `fill="hold"` (`tpad=stop_mode=clone`); `pipelines.fit_clips` reports cut/hold/loop in `report.warnings`.
- Audio: voice + optional music looped and ducked with `sidechaincompress`, then `loudnorm`. AAC 192k.
- Video: `h264_videotoolbox` (14M at 1080p), `+faststart`; HEVC via export.
- Subtitles: ASS burned with `ass=filename=subs.ass` run with `cwd` = temp dir (avoids path escaping); soft
  subtitles as `mov_text`.
- Debug: run the failing command from the exception text (stderr tail is included); extract a frame with
  `-ss <t> -frames:v 1 x.png` and look at it; `ffprobe -show_streams` for durations.
- Licence: FFmpeg LGPL/GPL build used locally, not distributed.
