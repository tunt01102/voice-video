# MediaForge — Artistry upgrade (ngâm, đọc thơ, hát, hoạt cảnh) and voice blending (pha giọng)

Status: v0.2 · 2026-10-15 · **§6.2 pha giọng implemented and measured** (§6.7); §3–§5 and §6.3–§6.4 are design · Parent: [SDD.md](SDD.md), [SDD-sing-recite.md](SDD-sing-recite.md)

This document covers four improvements:

1. ngâm thơ and đọc thơ that sound like a skilled artist;
2. hát that sounds more like a singer;
3. livelier, more consistent video hoạt cảnh;
4. a new **pha giọng** feature: the owner's voice stays dominant, with a 20–30 % share of a second voice.

The research behind it has three parts:
- **Repo survey.** About 120 GitHub/HF projects. Stars, licences and push dates were read live on 2026-10-15 with `gh api` and the HF API.
- **Measurements.** Four reference recordings chosen by the owner, measured on this Mac (§2).
- **Code reading.** The engines we already ship. VieNeu exposes a blendable speaker vector (§6.2).

---

## 1. Problems, in the owner's words → what they mean technically

| Owner's goal | What is wrong today (measured, §2) | Main lever |
|---|---|---|
| "đọc thơ hay hơn" | The reading is plain TTS prosody. There are no poem-aware pauses, no slowing of the last line, no "poetry voice" reference | Rule + LLM scansion → pause/duration/style plan (§4.1) |
| "ngâm hay hơn" | Glides between notes are 2.5× slower than an artist's. Vibrato is metronomic at 5.3 Hz. The pitch range is narrow (~9 st vs ~12). Final notes are held *steady* far longer than artists ever do; artists keep the pitch moving (luyến láy) | Measured parameter changes to `melody.py`/`resynth.py` (§3) |
| "hát giống ca sĩ hơn" | The sung median pitch sits **at the speaking pitch** (~110 Hz, tonic 55 Hz + octave). R3/R4 sing at 197–262 Hz, roughly an octave above a typical male speaking median of ~100–130 Hz (their own speech was not measured). The melody is a rule-made pentatonic line, not a singer's melody | Raise the tessitura (§3.3). "Sing to a reference melody" mode (§4.3). Neural re-voicing with RVC (§4.4) |
| "hoạt cảnh sinh động" | Ken Burns on unrelated stills. The character changes between shots | Shot-list planner, character sheet + edit model, 2.5D parallax, LTX keyframe morphs (§5) |
| "pha giọng 20–30 % giống X" | Missing | VieNeu speaker-vector blend (speech), WORLD envelope morph (song), later RVC blend (§6) |

## 2. Reference measurements

### 2.1 Sources and method

| # | Recording | Used as |
|---|---|---|
| R1 | YouTube `sPBqlmXeHJ0`: "Thơ Ngâm Hay Nhất NSND Thúy Mùi Diễn Ngâm" (VOV Live, 1 h 43 min) | ngâm technique |
| R2 | YouTube `yY7XzCJym_c`: "Ngâm Thơ Trần Thị Tuyết …" (VOV Live, 1 h 35 min) | ngâm technique |
| R3 | YouTube `87oTHfSaR10`: Stratovarius, "Forever" (karaoke upload, 3 min) | singing technique |
| R4 | YouTube `RjsBwu4f9Ao`: Nightwish, "While Your Lips Are Still Red" (official, 4 min) | singing technique |

**How the audio was handled.**
- The audio was downloaded with `yt-dlp` into the session scratchpad, for analysis only. It is **not** stored in `data/`, not redistributed, and never sent to an LLM.
- For R1 and R2, three 90 s excerpts were taken at 5, 25 and 50 min. R3 and R4 were used in full.
- The vocals were isolated with Demucs `htdemucs --two-stems vocals` (MIT, CPU).
- Each stem was analysed with pyworld Harvest at a 5 ms frame.
- The script is `scripts/analyse_reference.py` (`uv run python scripts/analyse_reference.py <stem.wav>…`).

**Scope.** These are *technique* references: pitch movement, vibrato, phrasing and holds. They are **not** timbre sources. The design never clones the voices of the artists in R1–R4. R3 and R4 are English rock ballads sung by male tenors/baritones. They show how a singer uses range, vibrato and holds; their melodies are not used.

**Metrics.**
- Notes are stretches where the 120 ms-smoothed pitch stays within ±0.7 st.
- Vibrato is the 3.5–9 Hz peak of the detrended pitch on notes ≥ 0.7 s; depth is the 90th percentile of |deviation|.
- Glide is the time to settle within 0.3 st of the next note.

### 2.2 Results

| Metric | R1 Thúy Mùi (3 excerpts) | R2 Trần Thị Tuyết (3) | R3 / R4 singers | **MediaForge ngâm** (2 jobs) | **MediaForge hát** (1 job) |
|---|---|---|---|---|---|
| Pitch range p5…p95 vs median (st) | −5.9…+5.3 to −6.9…+5.2 (**≈ 11–12 st**) | −6.9…+5.5 to −9.8…+5.9 (12–16 st) | −7.0…+6.1 / −7.4…+7.0 (13–14 st) | −3.5…+5.2, −3.8…+5.2 (**≈ 9 st**) | −4.6…+5.2 (10 st) |
| Sung median (Hz) | ~300–330 Hz (female, ngâm sits high) | ~325–340 Hz | 262 / 197 Hz (tenor/baritone; typical male speech is ~100–130 Hz) | **107–114 Hz = the owner's speaking pitch** | 113 Hz |
| Steady-note length p50 / p90 / max (s) | 0.42–0.47 / 0.69–1.16 / 1.5–2.0 | 0.37–0.41 / 0.59–0.79 / 1.05–1.31 | 0.53 / 1.01 / 2.1 · 0.46 / 1.08 / 2.4 | 0.55–0.57 / 1.35–1.63 / 2.2–**3.2** | 0.67 / 1.83 / **7.0** |
| Glide into a note p50 / p90 (ms) | 65–90 / 235–289 | 75–85 / 235–305 | 80–85 / 285–295 | **190–205** / 290–320 | 75 / 245 |
| Vibrato rate p50 (p10…p90) (Hz) | 5.2–7.1 (4.4…8.0) | 4.6–6.1 (4.3…7.2) | 5.6 (4.4…6.7) · 5.7 (5.1…6.3) | 5.3–5.4 (**5.3…5.7**) | 5.5 (5.0…6.0) |
| Vibrato depth (st) | 0.20–0.24 (light) | 0.49–0.81 (deep) | 0.36 · 0.74 | 0.40–0.44 | 0.40 |
| Long notes with vibrato | 70–79 % | 83–100 % | 54 % · 82 % | 50–60 % | 78 % |
| Vibrato onset (fraction of note) | 0–0.11 | 0.07–0.45 | 0 | 0.09–0.20 | 0 |
| Phrase length p50 (s) | 2.5–3.2 | 1.5–4.6 | 4.9 · 2.7 | (one line ≈ one phrase) | — |
| Pause p50 / p90 (s) | 0.47–1.0 / 2.1–3.8 | 0.47–0.6 / 1.8–3.6 | 0.6 / 4.1 · 0.5 / 4.7 | caesura 0.22, line end 1.1–1.7 (planned) | — |

### 2.3 Findings

These drive §3.

1. **Artists never hold a pitch still for long.** Even a 3 s final syllable is made of 0.4–0.8 s pitch stretches joined by fast glides. That is luyến láy.
   - Ours holds the final syllable steady for up to 3.2 s (ngâm) and 7 s (last hát note). The ear hears that as "stretched speech".
2. **Glides are fast.** The p50 is 65–90 ms for every artist. Ours in ngâm is ~200 ms: 45 ms smoothing plus a contour that starts at the tone's onset and slides across the whole onset fraction. Slow glides sound like sliding speech, not sung notes.
3. **Vibrato varies.** The rate ranges over ~3 Hz between notes, and the depth is a personal signature: Thúy Mùi is light (0.2 st), Trần Thị Tuyết deep (0.5–0.8 st). Ours is a single 5.3 Hz sine ±0.25 Hz on every note.
4. **The range is about an octave.** Ours is ~9 st, and it goes low only 3.5 st below median. Huyền and nặng could go deeper.
5. **Tessitura.** R3/R4 sing at 197–262 Hz, well above a typical male speaking median (~100–130 Hz; their speech was not measured). Ours sings **at the owner's own speaking pitch** (measured: sung median 107–114 Hz, tonic 55–58 Hz with the level tone an octave up), because `key = speech median + 2 st` and the level-tone home sits at the upper tonic. This is the single largest "sounds like speech" cue in hát.
6. **Breaths are longer.** Pauses between phrases have a p50 of ~0.5–1.0 s. Ours uses a 0.22 s caesura breath.

The pitch-class histograms of R1/R2 cluster on five classes. That is consistent with a pentatonic ladder, but the traditional intervals are not equal-tempered, so this is not used to retune the ladders.

## 3. Parameter changes from the measurements (P1, no new dependencies)

All changes are in `sing/melody.py` and `sing/resynth.py`. Each is exposed through a new **`ngam_style`** preset (`nhe` light / `sau` deep / `auto`) instead of new hard-coded constants.

| # | Change | Today | New (target band from §2) |
|---|---|---|---|
| 3.1 | **Luyến láy on long notes.** Any note > 1.2 s is planned as a chain of 0.35–0.8 s stretches. The chain is: tone onset → main note → upper neighbour (+1 ladder step) → main note → lower neighbour → main note, then a settled tail ≤ 1.0 s with vibrato and a soft fall. The number of stretches is ⌈dur/0.6⌉. | One turn, then a steady hold up to 3.6 s | Steady p90 ≤ 1.2 s, max ≤ 2.0 s |
| 3.2 | **Glides.** Legato smoothing 45 → 25 ms. The onset slide covers ≤ 80 ms (not 14–25 % of the note). The tone's own contour stays at full length for **short** syllables only (< 0.45 s), where it carries the word. | p50 ~200 ms | p50 70–100 ms |
| 3.3 | **Tessitura (hát and ngâm).** Choose the tonic so the sung *median* lands at `speech_median + tessitura`. `tessitura` defaults to +5 st for hát and +3 st for ngâm, and the owner can set it ("Tông giọng" slider, which exists). WORLD keeps the spectral envelope, so formants stay the owner's. | +2 st, level tone at the upper tonic → median ≈ speech | Sung median +3…+7 st above speech |
| 3.4 | **Range.** Huyền and nặng shapes go 0.6 ladder steps lower; the phrase arch amplitude rises by 25 %. Each line's lowest note may reach −1 octave + V (pentatonic). | ~9 st | 11–13 st |
| 3.5 | **Vibrato.** The rate is drawn per note from N(5.6, 0.7) Hz, clipped to 4.5–7.0. There is a slow within-note drift (±0.4 Hz). The depth comes from the preset (`nhe` 0.22 st, `sau` 0.6 st) with ±25 % jitter per note. Onset is at 10 % of the note, with a 150 ms ramp. It applies to notes ≥ 0.45 s (today 0.45 s and only `vib>0`). | 5.3 Hz ± 0.25, depth 0.32–0.42, onset 30 % | Rate p10…p90 spread ≥ 1.5 Hz |
| 3.6 | **Breaths.** Caesura breath 0.22 → 0.45 s × pace. The line-end breath stays 1.1–1.7 s. The breath is **audible**: a 150–250 ms inhale noise from the owner's own speech (unvoiced, low-passed, −24 dB) is placed before the next line. | Silence | Pause p50 0.45–1.0 s |
| 3.7 | **Hát long-note cap.** The final note of a line is ≤ 2.5 s sung, and the rest of the bar is breath and backing. The last line may hold up to 4 s with decrescendo. | Up to 7 s | ≤ 2.5 s (4 s final) |
| 3.8 | **Swell/decay.** Keep the messa di voce, but move the peak to 0.35–0.55 of the note (measured p50 0.22–0.59) and add breathiness on the decay: aperiodicity +20 % in the last 25 %. | Peak ~0.5, no breathy decay | — |

**Acceptance (P1).** Re-run §2 on the same two poems used in SDD-sing-recite §5.1. Every row must land in its target band. Whisper clarity per line must not drop more than 0.05 below the §5.1 table, and the owner's ears are the final judge. A/B pairs (old vs new) go in the job report.

## 4. Voice quality upgrades

### 4.1 Đọc thơ (poetry reading)

1. **Scansion = rules + LLM.**
   - **Rules** come from diacritics, using our `tones.py` (and `sea-g2p`, Apache-2.0, already a VieNeu dependency). They cover:
     - bằng/trắc and the lục bát rhyme chain (6→6, 8→6);
     - caesurae: 7 = 4|3, 5 = 2|3, lục bát 2|2|2 and 4|4;
     - stanza ends.
   - **LLM** (`claude -p`, text only) returns JSON: `{emphasis: [syllable idx], mood per stanza, breath_after: [idx], tempo_curve}`.
2. **Rendering plan.**
   - Caesura pause 0.3–0.5 s; stanza end 1.2–2.0 s.
   - The last line is 15–25 % slower and ends on a lowered cadence: the final sentence's f0 is scaled −1.5 st over its last 2 syllables in post, via WORLD.
   - Emphasised syllables are lengthened ×1.15.
   - This needs **phrase-wise synthesis** (exists, `takes.py`) plus a WORLD duration/f0 post-pass that reuses `resynth.analyse`.
3. **A "poetry voice" reference.** VieNeu's style follows the reference clip (`style` is deprecated upstream), so the owner records 3–5 poems read slowly and solemnly. Voice profiles get a `refs.poetry` slot, picked automatically for `recite`/`đọc thơ` jobs.
4. **VoxCPM2 controllable cloning.** Inline instructions such as `(chậm rãi, trang nghiêm)` keep the timbre. This needs evaluation first: upstream says control quality "may vary" outside zh/en.
5. **Candidate engine: MOSS-TTS Local-Transformer v1.5** (OpenMOSS, 4.2k★, Apache-2.0 code and weights, Vietnamese added in v1.5, MLX 4/8-bit in mlx-audio). It has explicit `[pause 1.2s]` tags and token-level duration control, which fit scansion directly. **EVALUATE** against VieNeu/VoxCPM2 on the same poem: CER plus listening.

### 4.2 Ngâm

Use §3, plus:
- **More điệu as templates.** Sa mạc, Bồng mạc and Kiều become melodic *templates*: a skeleton of cadence notes and ornaments per line position, written by hand from public-domain notation. They replace the single rule set, and the user picks one in the tab.
- **Prosody-template mode (later, needs consent; see §4.4).** Convert a licensed ngâm recording's melody onto the owner's timbre with RVC.

### 4.3 Hát: "sing to a reference melody" (new mode, P2)

The strongest lever for "giống ca sĩ hơn" is to sing **the singer's actual melody**, not a rule-made one.

1. **Input.** A reference song (file the owner supplies and has the right to use), or a MIDI/MusicXML file, or a hummed guide recorded in the dashboard.
2. **Vocal isolation.** `python-audio-separator` (MIT; allow-list only checkpoints whose card licence is confirmed) or Demucs `htdemucs` (MIT, archived → pin the version).
3. **Pitch.** RMVPE (`rmvpe.pt` from `lj1995/VoiceConversionWebUI`, MIT; Apache-2.0 inference code from `Dream-High/RMVPE`), or FCPE (MIT) for speed. Harvest stays the fallback.
4. **Notes.** Our own segmenter (the §2 note rule), with SOME (openvpi, MIT code) as an EVALUATE [VERIFY: checkpoint licence]. The output is notes with onset, duration and pitch, and *the singer's own f0 curve*: vibrato, scoops, falls.
5. **Lyric alignment.** The owner's lyrics (VI or EN) are mapped onto note onsets: one syllable per note, and melisma when there are more notes than syllables.
   - **VI:** tone constraint check. If a note's direction contradicts the syllable's tone (e.g. a huyền on a rising note), flag it in the UI and offer the nearest tone-safe alternative (keep the rhythm, shift ±1 step). Vietnamese songwriters do this by ear.
   - **EN:** no tone constraint; stressed syllables go on strong beats.
6. **Render.** `resynth.sing_line` takes the reference **f0 curve** (transposed to the owner's tessitura) instead of the planned contour. Vibrato and ornaments then come from the singer by construction.
7. **Accompaniment.** Our key-locked synthesis (§6 of the sing SDD) follows the extracted chords; or, if the owner has the rights, the separated instrumental.

**Copyright note.** A commercial song's melody and backing are copyrighted. The mode is designed for the owner's private use, for songs the owner has rights to, and for public-domain or traditional melodies. The UI states this, and the job records the source.

### 4.4 Neural re-voicing (P3, closes the "stretched speech" gap)

WORLD vocoding is buzzy on long notes. The research's best licence-clean fix is a **voice-conversion stage after WORLD**.

| Choice | Licence (code / weights) | Why |
|---|---|---|
| **RVC via Applio** (IAHispano/Applio 3.8k★; RVC-Project 38.7k★) | MIT / MIT (`lj1995` pretrains, HuBERT, RMVPE) | Re-synthesises with real voice detail and follows the target f0 closely. Language-agnostic, since the words come from the input. Its `model_blender.py` also gives §6.4 |
| DDSP-SVC (2.7k★) | MIT / **default NSF-HiFiGAN vocoder CC-BY-NC-SA** | Only with BigVGAN v2 (MIT weights) swapped in. EVALUATE |
| DiffSinger (openvpi, 3.2k★) | Apache-2.0 / vocoder NC | True singing synthesis, but there is no Vietnamese corpus or voicebank anywhere. Long-term only, needs owner singing data |

**Plan.**
- Train an RVC model on the owner's recordings: ≥ 10 min of speech plus any sung or ngâm takes. Keep it under `data/voices/<id>/rvc/`; it is never committed, because it is derived from biometric data.
- The sing pipeline becomes: WORLD (melody + tone-aware f0) → RVC (timbre and natural vocal texture) → mix.
- [VERIFY: Applio training on MPS. macOS install and run is documented; training speed on M4 Max is unmeasured.]

### 4.5 Mix

- **Doubling.** On hát choruses, add a second copy offset by +8…15 cents and 15–30 ms, at −12 dB. The pan is set by FFmpeg filters.
- **Reverb.** Convolution reverb from a pyroomacoustics (MIT) generated hall IR, via FFmpeg `afir`.
- Both are on `settings.ffmpeg`, with no new GPL dependency. pedalboard (GPL-3.0) is already used internally for FX and stays acceptable while the tool is internal and not distributed (SDD.md §11).

## 5. Video hoạt cảnh upgrades

| # | Upgrade | Tool (licence) | Notes |
|---|---|---|---|
| 5.1 | **Structured shot list.** Per verse line the LLM returns: shot type (wide/medium/close), camera move, motion tier (still / parallax / LTX), recurring motif, palette and transition | LLM (exists) | Turns separate pictures into a visual arc. The idea comes from MovieAgent (unlicensed; idea only) |
| 5.2 | **Character sheet + edits.** One Z-Image still of the lone figure; every shot is an *edit* of it ("same scholar, now on the river at dawn") | FLUX.2-klein-**4B** in mflux (Apache-2.0; **not** the 9B/dev, which are NC). Fallback Qwen-Image-Edit-2509/2511 (Apache-2.0) | ⚠ mflux's Qwen-Image-**2.1** is `qwen-research`: do not adopt by accident |
| 5.3 | **Composition lock** across a stanza | Z-Image-Turbo-Fun-ControlNet-Union (Apache-2.0, in mflux) with depth/pose from the previous shot | |
| 5.4 | **2.5D parallax tier** between Ken Burns and LTX: depth map → 2–3 layers → per-layer zoom and pan in FFmpeg, plus alpha overlays (mist, petals, rain, drifting ink) | Depth-Anything-V2-**Small** (Apache-2.0; Base/Large are CC-BY-NC) | Re-implements DepthFlow's idea (AGPL, rejected) |
| 5.5 | **Morph transitions** between line stills | `ltx-2-mlx keyframe --start lineN --end lineN+1` (already installed path) | LTX community licence [VERIFY: confirm with compliance] |
| 5.6 | **Music-synced cuts** for hát: shot boundaries and zoom easing snap to downbeats; zoom speed follows RMS | librosa (ISC [VERIFY: accept ISC as BSD-equivalent]) or beat_this (MIT) | Ngâm keeps line-start cuts (free rhythm) |
| 5.7 | **Karaoke word effects.** The LLM tags key images (trăng, sông, quê) → glow/size pulse on those syllables; `\kf` sweep per syllable | ASS (exists) | Idea from pycaps (MIT), which is not adopted (headless browser) |
| 5.8 | **Own ink-wash style LoRA.** Z-Image LoRA trained with mflux on public-domain ink paintings and Đông Hồ prints | mflux (MIT) | No Apache ink-wash LoRA exists for Z-Image/Qwen-Image today |
| 5.9 | **Optional Wan 2.2 i2v fallback** | Blaizzy/mlx-video (MIT; Wan weights Apache-2.0) | EVALUATE. Last push 2026-05, so it carries maintenance risk |

Rejected in this round: StoryDiffusion (SDXL/CUDA), PuLID/InstantID/PhotoMaker (InsightFace non-commercial models; also face-centric), FLUX.1 Kontext/UNO/USO (FLUX.1-dev NC base), HunyuanVideo/FramePack (licence), CogVideoX I2V (non-Apache weights), story-flicks/MovieAgent (no licence), waoowaoo (Elastic). Talking-head/lip-sync stays out of scope (owner decision).

## 6. Pha giọng (voice blending)

### 6.1 Requirement

"My voice stays dominant, with 20–30 % resemblance to a second voice."

As a design requirement: produce speech, ngâm or song whose speaker identity sits between the owner's voice **A** and a second enrolled voice **B**. The **perceived share** of B is set by a knob `blend` ∈ [0, 0.5], with 0.2–0.3 recommended. At every setting the result must stay closer to A than to B, and it must stay intelligible (CER ≤ 8 %).

### 6.2 Mechanism 1 (P1): VieNeu speaker-vector blend, for speech and đọc thơ

**What the code exposes.** VieNeu v3 Turbo conditions on two things (`vieneu/v3turbo.py`, `_v3_turbo_engine/modeling_v3_turbo.py`):
- a **192-d speaker embedding** (`speaker_emb`, from its ONNX speaker encoder). It is projected by `xvec_proj` and added to every input row;
- the reference's **codec codes** (`ref_codes`), an in-context prompt that also carries timbre and speaking style.

`Vieneu.infer(voice={"speaker_emb": …, "codes": …})` accepts a preset dict directly, so blending needs **no fork of VieNeu**. One correction to the research agent: VieNeu *does* expose a continuous vector, so a blend is possible without training.

**Blend.**
```
e_A, codes_A = tts.encode_reference(ref_A)      # owner, cached per reference
e_B, _       = tts.encode_reference(ref_B)
e = slerp(e_A/|e_A|, e_B/|e_B|, w) · |e_A|      # w = internal weight
voice = {"speaker_emb": e, "codes": codes_A}     # owner's codes keep A dominant: prosody, style, accent
```
The codes prompt pulls strongly toward A, so the internal weight `w` is **not** the perceived share. That is why `w` is calibrated:

**Calibration.** This runs once per (A, B) pair as a `blend` job (~30 s) and is stored in the blend profile.
1. Synthesise two fixed VI probe sentences at `w ∈ {0, 0.3, 0.4, 0.5, 0.6, 0.75, 1}`.
2. Measure each output's position on the A→B axis with VieNeu's own speaker encoder: `pos = ⟨e_out − e_A, e_B − e_A⟩ / |e_B − e_A|²`.
3. Normalise by the w=0 and w=1 renders: cloning is never exact, so a pure-A render does not sit at 0. Then make it monotone: `share(w) = (pos(w) − pos(0)) / (pos(1) − pos(0))`.
4. Invert by interpolation, `w = w(amount)`.
5. **Verify** with fresh seeds at the chosen `w`, and store `measured` next to `expected`.
6. Later: a second, independent check with WavLM-SV or ECAPA (see SDD.md P4 "speaker-similarity score"; licence check needed), so the grading does not use the same encoder that steers.

**Guards.**
- `cos(out, A) > cos(out, B)` must hold, otherwise the blend is clamped.
- The usual ASR QA and self-repair apply unchanged: a blended voice is just another `VoiceRef`-like input to `voice/speak.py`.

**Routing.** VoxCPM2 conditions on reference *audio* only, with no vector. Blend jobs therefore route both VI and EN to VieNeu, which handles English. VoxCPM2 blending, by mixing prompt latents, is out of scope until measured.

**Engine boundary change (implemented).**
- `VoiceRef` gains an optional `blend: BlendSpec | None` (`partner_audio`, `weight`, `partner_id`).
- Engines declare `blends: bool`. `VieNeuEngine.synth` builds the preset dict when `blend` is set.
- `speak.synth_sentence` restricts candidates to `blend_engines(lang)`. That covers the first pass, the engine switch in self-repair and the sing pipeline's alternate-engine takes, so a blend can never silently fall back to a non-blending engine.
- The sentence cache key includes `blend.key`.
- A blend profile borrows its owner's lexicon, guides and learned respellings (`profiles.base_voice`), and guided-repair recordings made on a blend job are stored under the owner's voice.

### 6.3 Mechanism 2 (deferred): WORLD envelope morph, for ngâm and hát

*Status: deferred.* Ngâm and hát start from VieNeu speech, so §6.2's vector blend already carries into sung lines
(measured in §6.7: a 25 % blend ngâm sang 7/7 syllables with tones on both lines). The morph below stays as an
option for a *stronger* colour transfer on held notes, if listening shows the blend fading under WORLD.


Singing is WORLD-resynthesised already, so blending can happen in the same domain at zero cost.

1. **Analysis of B, once.** From B's enrolled samples (voiced frames only), compute the long-term mean log spectral envelope `L_B(f)` and a vocal-tract-length ratio `r = F̄_B / F̄_A`. F̄ is the median of the first three formant estimates from LPC on the envelope.
2. **Per frame of A's sung output.** Apply a frequency warp `f → f · r^β` (β = blend), then `log sp' = log sp + β · (L_B − L_A)(f)`, smoothed over 1/3 octave so B's *colour* is borrowed but not B's phoneme detail.
3. **Pitch.** Not touched for singing (the melody owns it). For speech, an optional `β · (log f0_B − log f0_A)` median shift.

This borrows B's resonance and colour (e.g. "trầm, ấm, vang") and keeps A's articulation, timing and micro-prosody. It is cruder than 6.2 and is the default only where 6.2 cannot reach (sung lines). It lives in `voice/blend.py` and is called from `resynth.sing_line` via an optional `envelope_morph` argument.

### 6.4 Mechanism 3 (P3): RVC model blend, best for singing

Once §4.4 exists:
1. Train an RVC model for A and for B from the **same** `lj1995` pretrain.
2. Blend them with Applio's `model_blender` (`ratio·A + (1−ratio)·B`, MIT), with `ratio = 1 − blend`.
3. Cache one checkpoint per used blend value (e.g. 0.2/0.25/0.3).

This needs ≥ 10 min of clean recordings of B, which is realistic only for a consenting person who records for it.

Alternatives evaluated:
- **OpenVoice v2** tone-colour converter (MIT/MIT; `tgt_se = (1−β)·se_A + β·se_B`). Zero-shot, but built for speech, may flatten held notes, and adds a WavMark watermark. **EVALUATE** as a fallback for 6.2 on engines without vectors.
- **kNN-VC** (MIT; pool ≈ 75 % A / 25 % B frames). Simple, but weak on singing. EVALUATE, low priority.

### 6.5 Whose voice may be B: consent (owner decision required)

The owner's examples are three real public figures: NSND Lại Văn Sâm, NSND Tự Long and NSND Xuân Bắc. **A 20–30 % blend is still derived from their voice**, and getting a reference means taking a recording of them. A voice recording is biometric personal data of a third party; the store currently holds only the owner's.

The design therefore:
- **Makes blending persona-agnostic.** B is *any enrolled voice profile*. Nothing in the code is specific to a person, and the feature is fully testable with the owner's two profiles (`gi-ng-t`, `demo`).
- **Adds a consent record** to every profile used as B (implemented): `consent: {kind: self | written | synthetic, by, note, date}`.
  - `written` requires `by`; `none` clears the record.
  - REST (403), MCP and CLI all **refuse** a partner without one, and the dashboard lists only partners that have one.
  - The blend profile stores `partner_consent` as it stood at creation.
  - The demo voice (macOS `say`) is marked `synthetic` when created.
  - VieNeu's watermark stays on.
- **Offers a style-only alternative that needs no one's voice.** The dashboard has "phong cách" presets ("MC trầm ấm", "kể chuyện dân dã", "hài sân khấu"). Each is just a bundle of existing parameters (pitch −2…−3 st, warmth 0.6–0.8, pace 0.9, pause scale 1.3, emphasis) applied to the owner's own voice. They are named by style, **never** after a real person. This gets "a bit like a veteran broadcaster's delivery" without copying anyone's voice.
- **Leaves the decision to the owner.** Enrolling Lại Văn Sâm / Tự Long / Xuân Bắc as B requires their permission, and is [VERIFY: confirm with compliance/legal — Vietnamese personal-data rules on biometric data, and rights to one's voice and likeness]. The tool must not ship scraped reference packs of public figures.

### 6.6 Interfaces

| Interface | Addition |
|---|---|
Everything in this table is implemented.

| Interface | Addition |
|---|---|
| REST | `POST /api/blends {owner, partner, amount, name?}` → `blend` job (calibrate + verify), result `{voice, weight, expected, measured, curve}`; `GET /api/blends`; `PATCH /api/blends/{id} {amount}` re-maps `weight`/`expected` on the stored curve (instant; `measured` keeps referring to the amount at creation); `PATCH /api/voices/{id} {consent}` |
| Job params | Every voice-taking job (`tts, video, short, explainer, recite, sing`, takes/exports) accepts `voice: "<blend-id>"`: a blend profile behaves like a voice |
| MCP | `blend_voices(owner, partner, amount)`; `list_voices` shows `kind`, `parts`/`amount` for blends and `consent` for real voices |
| CLI | `mediaforge voice consent <id> self\|written\|synthetic\|none [--by]`; `mediaforge voice blend A B --amount 0.25`; `voice list` shows consent and blends |
| Dashboard | Giọng nói tab: section 3 "Cho phép dùng làm giọng pha" (consent), section 4 "Pha giọng" (partner, 5–50 % slider with the 20–30 % band marked, create). A blend profile's page has the share bar, measured similarity, a share slider and **▶ Nghe A · Pha · B** |
| Storage | `data/voices/<blend-id>/profile.json` with `kind: "blend"`, `parts: [{voice, share}]`, `blend: {owner, partner, amount, weight, curve, expected, measured, partner_consent}`. No audio is copied; it resolves A's and B's current reference clips |
| Code | `voice/blend.py` (maths, consent, calibration, profiles), `tts/base.py` (`BlendSpec`, `blend_engines`), `tts/vieneu.py` (`blend_voice`, `speaker_embedding`), `tests/test_blend.py` |

### 6.7 Measured (owner voice `gi-ng-t` × `demo`, on a copy of the data store, 2026-10-15)

Calibration curve. Each value is the mean of 2 probes; `share` is normalised by the w=0 and w=1 renders.

| w (internal) | 0 | 0.3 | 0.4 | 0.5 | 0.6 | 0.75 | 1 |
|---|---|---|---|---|---|---|---|
| perceived share | 0 | 0.16 | 0.26 | 0.44 | 0.71 | 0.85 | 1 |
| cos to owner / partner | 0.82 / 0.25 | 0.77 / 0.37 | 0.70 / 0.39 | 0.57 / 0.47 | 0.43 / 0.68 | 0.34 / 0.79 | 0.20 / 0.81 |

- **Target 25 %.** Calibration chose w = 0.39. **Verified with fresh seeds: 25.5 %**, cos to owner 0.76 vs partner 0.44, so the owner is still clearly dominant. Calibration plus verification took 31 s.
- **Target 30 %** → w = 0.42 (re-mapped on the stored curve).
- **Speech.** `tts` with the blend voice, EN + VI: both sentences scored 0.0 (pass), on VieNeu.
- **Ngâm.** `recite` (hùng tráng, 2 lines): 7/7 syllables and 7/7 with tones on both lines.
- **Why calibration is needed.** The response is an S-curve: w 0.3 gives only 16 %, and w 0.6 already gives 71 %. A raw "w = 25 %" slider would sound like no blend at all.
- **Caveat.** The share is measured by VieNeu's own speaker encoder. The owner's ears, and later an independent SV model, are the real judge. `demo` is a synthetic macOS voice, very far from the owner (cos 0.25), so a nearer human partner may give a flatter curve; it is recalibrated per pair anyway.

## 7. UI/UX changes

These build on what exists: tabs Tạo mới / 🪷 Ngâm thơ / 🎵 Hát / Jobs / Giọng nói / Phát âm / Hệ thống; saved voice sliders (Độ trầm, Tốc độ, Độ ấm, Độ rõ); ▶ Nghe thử; takes and export.

### 7.1 Giọng nói tab → "Giọng & pha giọng"
- **Voice cards** show the sample count, the best reference per language, a new **poetry reference** slot (§4.1.3) and a **consent badge** (Tự thân / Có văn bản / Chưa có).
- **"Pha giọng" panel.**
  - Two pickers: **Giọng chính** (A) and **Giọng pha** (B; only voices with consent are enabled).
  - A slider `Mức pha` 0–50 %, with a shaded **20–30 %** band marked "khuyên dùng".
  - **▶ Nghe thử A · Pha · B**, three buttons playing the same sentence, so the owner hears the position.
  - A **similarity bar** showing the measured share and "vẫn gần giọng chính hơn ✓".
  - Then **Lưu thành giọng mới**, which creates the blend profile.
- **"Phong cách" presets** (style-only, §6.5) sit beside the sliders on Tạo mới: one click sets the four sliders plus pause scale.

### 7.2 Ngâm thơ tab
- **Điệu ngâm** select: Tự do (today), Sa mạc, Bồng mạc, Kiều (§4.2).
- **Kiểu rung** (`nhe`/`sau`/`auto`). It replaces nothing and defaults to `auto` from the mood.
- **Melody view.** A small piano-roll of `melody.json`, with the syllables under their notes and caesurae marked. Clicking a syllable plays it. Dragging a note up or down a ladder step and choosing **Hát lại câu này** re-renders only that line as a take (reuses `takes.py`).
- **Per-line takes strip**: clarity score, ▶, ✓ choose, ↻ another take.
- **Before/after A/B** toggle when a job was re-rendered with new parameters.

### 7.3 Hát tab
- **Nguồn giai điệu**: Tự động (tone-aware, today) | **Theo bài mẫu** (upload song / MIDI / hum, §4.3) | **Ngân nga** (record a hummed guide).
- After analysis, show the extracted notes with the lyric syllables laid on. Tone conflicts are underlined in orange, each with a "đổi nốt cho đúng dấu" fix.
- **Tầm giọng** slider (tessitura, §3.3), with a live readout ("cao hơn giọng nói 5 cung").
- **Bè đôi** (doubling) and **Vang** (reverb) toggles.

### 7.4 Video hoạt cảnh (both song tabs and Tạo mới)
- **Storyboard grid** before rendering. Each card shows the line text, prompt, thumbnail, motion tier (Tĩnh / Chiều sâu / Chuyển động AI) and duration.
- **Per-card actions**: ↻ new seed, ✎ edit by instruction ("chuyển sang hoàng hôn"), 🔒 lock, and pick from **4 candidates**.
- A pinned **character sheet** and style card, used by every edit (§5.2).
- **Nháp / Bản cuối** toggle: draft = 768 px, Ken Burns only, about 1 min; final = full quality with parallax/LTX.

### 7.5 General
- **Stage progress** (analyse → speech → sing → mix → images → video) with per-stage ETA from past jobs in SQLite. Today there is a single bar.
- **"Hỏi trước khi dựng"** for long jobs: show the plan (melody, storyboard) first, then render. This is the prepare-then-render pattern that Tạo mới already has (`1 · Viết kịch bản & kiểm tra phát âm`).

## 8. Licence summary for this round

| Component | Code / weights | Status |
|---|---|---|
| Demucs (analysis), python-audio-separator | MIT / MIT (per-checkpoint allow-list) | ADOPT (P2) |
| RMVPE (Dream-High code; lj1995 weights), FCPE | Apache-2.0, MIT / MIT | ADOPT (P2) |
| SOME | MIT / [VERIFY: checkpoint] | EVALUATE |
| RVC via Applio | MIT / MIT | ADOPT (P3) |
| OpenVoice v2, kNN-VC | MIT / MIT (kNN-VC: WavLM-Large card has no licence field [VERIFY]) | EVALUATE |
| MOSS-TTS Local-Transformer v1.5 | Apache-2.0 / Apache-2.0 | EVALUATE |
| FLUX.2-klein-4B, Qwen-Image-Edit-2509/2511, Z-Image ControlNet-Union, Depth-Anything-V2-Small | Apache-2.0 | EVALUATE → ADOPT (P4) |
| librosa | ISC [VERIFY: accept as BSD-equivalent] | EVALUATE |
| pyroomacoustics, audiomentations | MIT | ADOPT (P3) |
| **Rejected this round** | OmniVoice weights (CC-BY-NC; was "fallback TTS" in SDD.md §3), Spark-TTS, MaskGCT/Vevo (NC), Llasa, DDSP-SVC default vocoder, openvpi/vocoders (AGPL/NC), so-vits-svc (AGPL), Seed-VC (GPL, archived), FLUX.1-dev family, Qwen-Image-2.1, Krea-2, InsightFace-based ID tools, HunyuanVideo, FramePack, DepthFlow (AGPL), madmom models (NC), VietVoice-TTS (possible F5 lineage), ZeroTTS (trained on NC data) | |

## 9. Phases

| Phase | Deliverable | Acceptance |
|---|---|---|
| **P1a Pha giọng** ✅ 2026-10-15 | `voice/blend.py`: VieNeu vector blend with calibration and verification (6.2); consent record; blend profiles; REST/MCP/CLI; Giọng tab consent and blend panels, A·Pha·B preview | **Met:** 25.5 % measured for a 25 % target; CER 0 (EN + VI); ngâm 7/7 with tones; `cos(out,A) 0.76 > cos(out,B) 0.44` (§6.7) |
| P1b Measured artistry | §3 parameter changes behind `ngam_style`; WORLD envelope morph (6.3) only if listening asks for it; "phong cách" style presets (§6.5) | §3 bands met on the §5.1 poems |
| P2 Reference melody | §4.3 separation → RMVPE → notes → lyric alignment with tone check; Hát tab "Theo bài mẫu"/"Ngân nga"; tessitura slider | Pitch error vs reference ≤ 30 cents median on held notes; clarity ≥ the tone-aware baseline on VI lyrics |
| P3 Neural re-voicing | Applio RVC sidecar (own venv, like ACE-Step); owner model; RVC blend (6.4); doubling and reverb | Owner listening test: prefers RVC over WORLD on ≥ 4/5 lines |
| P4 Hoạt cảnh | Shot list, character sheet + FLUX.2-klein-4B edits, parallax tier, LTX keyframe morphs, beat-synced cuts, storyboard UI | Same figure recognisable across all shots (owner check); draft render ≤ 90 s for a 4-line poem |
| P5 Đọc thơ | Scansion plan, poetry reference slot, MOSS-TTS evaluation, VoxCPM2 instructions evaluation | Owner listening preference vs today on 3 poems |

## 10. Risks

- **Whisper under-hears sung vowels**, so clarity stays advisory (as in the sing SDD). Owner listening is the gate.
- **A calibrated `w` may not generalise** across sentences. Recalibrate per blend profile on 3 probes, and keep the guard `cos(out,A) > cos(out,B)`.
- **Higher tessitura pushes WORLD harder.** Above about +7 st, the buzz grows. P3 (RVC) is the real fix; until then the slider is capped at +7 st.
- **Third-party voices.** See §6.5. The design gates on consent; the decision about named public figures belongs to the owner [VERIFY: confirm with compliance/legal].
- **Reference media copyright.** The R1–R4 audio was used only for local measurement and is not kept in `data/`. Reference-melody mode records the source, and the UI states the private/rights-held use.
