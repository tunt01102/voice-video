# MediaForge — Hát & Ngâm thơ (singing and poetry chanting)

Status: v1 (implemented) · 2026-10-14 · Parent: [SDD.md](SDD.md) §5, §7

The owner's cloned voice can **chant a poem (ngâm thơ)** or **sing lyrics (hát)**, with accompaniment and,
optionally, an illustrated film ("video hoạt cảnh") with karaoke lyrics. Two dashboard tabs (🪷 Ngâm thơ,
🎵 Hát), two job types (`recite`, `sing`), MCP tools `recite_poem`, `sing_song`, `list_poems`, and CLI
`mediaforge recite | sing | poems`.

## 1. Why not a singing model

Research (2026-10) found no singing-voice model that is both **permissively licensed** and **Vietnamese-capable**:

| Option | Why not (now) |
|---|---|
| Seed-VC (zero-shot singing voice conversion) | GPL-3.0 — CLAUDE.md allows Apache/MIT/BSD engines only |
| SoulX-Singer, YingMusic-Singer, Vevo2 | zh/en/yue only; licences unclear or non-commercial |
| RVC | needs ~10 min of the owner singing to train; owner has ~80 s of speech |
| ACE-Step 1.5 (MIT) | full-song generator: its vocals are not the owner's voice. Kept as a candidate for **instrumental** backing only (§5) |

Vietnamese is tonal, so a generic melody makes the lyrics unintelligible. The design therefore builds singing
**from the owner's own cloned speech** and a **tone-aware melody**. That guarantees the timbre is the owner's,
and the melody keeps the words clear.

## 2. Pipeline (`sing/pipeline.py`)

```
text ─► tones.parse ─► interpret (LLM, text only) ─► melody.plan ─┐
                                                                  ▼
 per line: speak (VieNeu/VoxCPM, owner's voice, ASR QA) ─► WORLD analyse ─► syllable bounds ─► sing_line
           └── listen back (Whisper) ── unclear? another take (seed/engine), keep the clearest ──┘
                                                                  ▼
             accompany.arrange (same key, same timing) ─► mix (EQ, hall reverb, -14 LUFS) ─► song.wav/mp3
                                                                  ▼ (video=true)
             one image per line + opening/closing shot ─► Ken Burns + fades ─► karaoke ASS + title card ─► final.mp4
```

## 3. Text side (`sing/tones.py`)

- Tone from diacritics (NFD combining marks): ngang, huyền, sắc, hỏi, ngã, nặng; bằng = {ngang, huyền}.
- Checked syllables (final p/t/c/ch, e.g. "hết") are sung short.
- Form detection: thất ngôn (7), ngũ ngôn (5), lục bát (6/8), else free. Caesura: 7 → 4/3, 5 → 2/3.
- A poem pasted on one row ("…sao sáng, Nâng chén…") is split at punctuation followed by a capital.

## 4. Melody (`sing/melody.py`)

Pitches live on a **pentatonic ladder** (0 = tonic, 5 = octave), so a mode is just a scale:
`bac` (hơi Bắc: 0 2 4 7 9 — upright, majestic), `oan` (hơi Oán: 0 3 5 7 10 — plaintive), `ai` (0 3 5 7 8).

| Mood | Scale | Character |
|---|---|---|
| `hung-trang` | Bắc | proud, heroic, "khí khái" — wider arch, faster |
| `tram-mac` | Bắc | calm, slower |
| `u-hoai` | Oán | longing |
| `bi-ai` | Ai | grief, slowest |

**Ngâm (free rhythm).** A tone sets register and contour: ngang level at "home", huyền lower and falling, sắc
rising above home, hỏi dipping, ngã rising with a break, nặng short with a steep glottal drop. A phrase arch
lifts the line toward its caesura. The caesura syllable is held ×1.9 with a breath. The line-final syllable
resolves on a cadence note on the side of home that its tone points to: dominant on open lines, tonic on
closed lines. It is held 2.8–3.6 s with a turn ornament (melisma), vibrato and a soft fall.

**Hát (metered).** The BPM is 76 for proud moods, else 66, on a 4/4 grid. Each syllable gets one beat; checked
syllables get half a beat and the caesura two beats. The last syllable fills the bar. Each step's direction
follows the tone (sắc/ngã up, huyền/nặng down, hỏi dips, ngang drifts toward the phrase aim), within ±3 ladder
steps of home. A short tone-shaped glide leads into each steady note.

**Key.** The tonic is chosen so a level tone sits 2 semitones above the owner's own median speaking pitch, plus
the voice's `pitch` FX. It is snapped to equal temperament so the backing shares the tuning.

## 5. Resynthesis (`sing/resynth.py`, WORLD via pyworld — MIT wrapper, modified-BSD WORLD)

1. Analyse each spoken line: Harvest f0, CheapTrick envelope, D4C aperiodicity. Voicing is taken from D4C
   (periodic below 3 kHz), because Harvest fills pitch into fricatives (s, x, ch). Without this, "sương"
   became "xương" or "chết".
2. **Syllable boundaries.** Whisper word times on fast speech are near-uniform guesses (±100 ms), so each
   boundary is re-found within ±125 ms:
   - before an unvoiced onset (s, x, ch, tr, h, t, c…), the boundary is the voiced→unvoiced frame;
   - before a voiced onset, it is the deepest energy dip;
   - every syllable must contain ≥ 60 ms of voicing (it has a vowel).
3. **Timeline per syllable.** The onset consonant, the consonant→vowel transition, the vowel→coda glide and the
   coda play at natural speed. This keeps the "u" of "sầu" and the "ng" of "sáng" audible. Only the steadiest
   30 ms of the nucleus (minimum spectral flux in the first 60 % of the voiced part) is held. The hold sways
   ±15 ms around that point so the timbre keeps moving.
4. **Pitch.** f0 is set to the melody contour, with ~45 ms legato smoothing, vibrato (5.3 Hz, ramping in after
   30 % of the note) and a tiny random drift. Notes longer than 1 s swell and ease (messa di voce).
   Aperiodicity on sung vowels is reduced by 25 %.
   **Speech micro-prosody** (`PROSODY = 0.7`): each syllable's own spoken pitch movement, as a deviation from its
   median and clipped to ±4 st, is added on top of the note. This keeps the cues Vietnamese ears use to tell the
   tones apart: nặng's drop, ngã's break, sắc's rise.
   **Consonant passthrough**: natural-speed unvoiced runs (s, x, ch, h, stop bursts) are copied from the spoken
   waveform with 4 ms crossfades, because they carry no pitch and WORLD renders them as plain hiss.
5. **Self-repair.** Each sung line is transcribed back. Clarity = mean(syllables right ignoring tones,
   syllables right with tones). Up to 6 takes are tried, alternating seed and engine (VieNeu/VoxCPM), and the
   clearest is kept. Each take is still the owner's cloned speech. Scores are **advisory**: Whisper is trained
   on speech and under-hears held vowels.

Measured on "Mình ta với cả trời sao sáng / Nâng chén sương đêm cạn hết sầu" (owner voice, 2026-10-14):

| Step | Result |
|---|---|
| WORLD identity resynthesis of the spoken line | 7/7 and 4/7 syllables (line 2's speech was already marginal: "xương") |
| Line 1 sung (ngâm and hát) | 6–7/7 syllables, 6–7/7 with tones |
| Line 2 sung, best take | 5/7 syllables |
| Audio only, both lines | ~35 s (cached speech) |
| With LLM + 4 images + video | ~3 min |

### 5.1 Tone-aware vs tone-agnostic melody (the design's core claim)

The measurement uses the owner's voice, the same 6 speech takes per line and the same resynthesis.
"Agnostic" means every syllable is treated as a level tone with no speech prosody, which is how a generic
melody ignores Vietnamese tones. Clarity is the mean of syllables-right and syllables-right-with-tones (Whisper,
6 takes); "tones right" is out of 7.

| Line | Agnostic clarity | Tone-aware clarity | Tones right (agnostic → aware) |
|---|---|---|---|
| Ngâm, line 1 | 0.60 | **0.81** | 3.8 → **5.7** |
| Ngâm, line 2 | 0.49 | **0.66** | 2.2 → **4.3** |
| Hát, line 1 | 0.61 | **0.86** | 4.0 → **5.8** |
| Hát, line 2 | 0.45 | **0.51** | 2.2 → **3.2** |

Line 2's speech is the weak point before any singing: both engines tend to read "sương" as "xương". Takes per
line went from 4 to 6 so the selector can pick past it.

### 5.2 Against an existing AI singer: ACE-Step 1.5 singing the same lyrics (2026-10-15)

ACE-Step 1.5 was given the owner's two lines as Vietnamese lyrics (`vocal_language="vi"`, 76 BPM, A# major,
4 seeds). Its voice is a generic singer, not the owner's. Scoring used Whisper on the full mix, in 8 s windows
with a 4 s hop and known hallucinations removed, counting syllables right out of 14.

| Output | Syllables right | With tones |
|---|---|---|
| ACE-Step, seeds 1 / 2 / 3 / 4 | 7 / 0 / 4 / 12 | 7 / 0 / 4 / 11 |
| MediaForge ngâm (ace+traditional mix) | 10 | 10 |
| MediaForge hát (ace+traditional mix) | 4 (8 when scored whole) | 2 |

Findings:
- ACE-Step sings real melodies, but its Vietnamese intelligibility is a lottery across seeds.
- MediaForge is consistent and in the owner's voice.
- MediaForge's *mixed* hát loses clarity under the ACE bed.

**Fix (same day):** the backing level drops to 0.35 for hát and 0.32 for ngâm, and the backing is **ducked** under the
voice: it dips 60 % on a 100 ms envelope of the dry vocal. Re-mixing the same hát job gave:

| Backing level | Without ducking | With ducking |
|---|---|---|
| 0.55 | 4/14 | 10/14 |
| 0.35 | 8/14 | **12/14 (11 with tones)** |
| 0.25 | 8/14 | 13/14 |

At 0.35 with ducking, the owner's voice matches ACE-Step's best seed on clarity.

Upgrade path: generate ACE-Step vocals, keep the clearest of N, and convert the timbre to the owner's voice with
a permissive pitch-preserving converter (candidate: OpenVoice v2 tone-colour converter, MIT; needs evaluation).

## 6. Accompaniment (`sing/accompany.py`)

The accompaniment is synthesised additively: no samples, no licence questions, always in the voice's key, and
locked to its timing because it knows every sung note.

- **Đàn tranh**: plucked partials with stiff-string inharmonicity, nhấn (bend) and rung (vibrato).
- **Sáo trúc**: breathy tone with a band-passed noise layer and vibrato.
- **Drone**: tonic and fifth pad. **Mõ** and **trống** are used for songs only.

| Style | Arrangement |
|---|---|
| Ngâm | Falling glissando prelude; flute states the cadence; a soft pluck under each line's start and caesura; tremolo (vê) under the held final syllable; the flute answers each line in the breath after it (lòng bản); rising glissando and a low ringing tonic to close |
| Hát | Per bar, the triad (I IV V vi ii, or minor i iv v VI VII) that best covers the sung notes; bass pluck on beats 1 and 3, broken-chord tranh in eighths, mõ on 2 and 4, trống on 1; flute lead-in and answers |

`accompaniment` may also be `none`, a music-library id, or a local path. A library track is faded to length,
and its CC BY credit is reported as `report.music_credit`.

**ACE-Step 1.5** (`accompaniment: "ace+traditional"` or `"ace"`, `sing/ace.py`) adds an AI-orchestrated bed.

- **Licence.** Code is MIT (github.com/ACE-Step/ACE-Step-1.5). The weights are MIT
  (huggingface.co/ACE-Step/Ace-Step1.5), and the model card allows commercial use of output. The bundled
  Qwen3-Embedding-0.6B is Apache-2.0.
- **Install.** It runs as a **sidecar** in its own venv, because its torch 2.10/transformers pins conflict with
  ours. `scripts/install_ace_step.sh` pins commit ca1e85f under `~/.local/share/mediaforge-sidecars/ace-step`
  (override with `MEDIAFORGE_ACE_STEP_ROOT`).
- **Invocation.** The job runs `src/mediaforge/sing/ace_generate.py` with the sidecar's python (`-P`, so our
  package directory is not on its path). It sends a caption per mood, the key (`bac` → major, otherwise minor,
  on the voice's tonic) and the BPM for songs. For ngâm it adds "free rhythm, no percussion".
- **Cost.** Measured on M4 Max: ~9 s per 30 s clip warm, 9.4 GB of weights, 41–60 GB peak unified memory.
- **Mix.** BPM and key are only *hints* to ACE-Step, so in `ace+traditional` the bed sits at 0.55 under the
  key-locked instruments. ACE-Step ends early, so the job asks for 15 % extra and trims, with a 1.5 s fade-in
  and a 3 s fade-out.

## 7. Video hoạt cảnh

The LLM receives the poem text only and returns:
- `mood`;
- `spirit_vi`, one sentence that affirms the poem's stance;
- an opening prompt, one prompt per line and a closing prompt. The prompts keep one consistent lone figure,
  era and setting, and show the line's imagery and the speaker's stance (pose, scale against the landscape,
  light).

The owner's `spirit` hint steers it. Shots change at line starts. The images use Z-Image "standard" with an
ink-wash and moonlit cinematic preset, and are joined with 1 s fades. Lyrics are one cue per verse line with
per-syllable karaoke timed **from the melody**: exact, with no ASR. A title card shows over the prelude.

## 8. Interfaces

| Interface | |
|---|---|
| REST | `POST /api/jobs {type: "recite"\|"sing", params}`; `GET /api/poems`; `GET /api/sing/moods` |
| Params | `text` or `poem_id` (+ `version`: `phien-am` \| `dich:<translator>`), `voice`, `style` (`ngam`\|`hat`), `mood` (`auto` \| …), `spirit`, `title`, `author`, `tempo`, `bpm`, `accompaniment`, `video`, `aspect`, `key_shift`, `seed` |
| Result | `audio`, `mp3`, `vocal`, `accompaniment`, `melody`, `key`, `tonic_hz`, `words` (syllable timings), `report.lines[]` (clarity per line), `report.spirit`; with video: `video`, `thumbnail`, `subtitles`, `images` |
| Outputs dir | `data/outputs/songs/<job>/` |

## 9. Poem catalogue (`sing/poems_catalog.json`)

The catalogue holds 13 Tang poems with chữ Hán, the Hán-Việt reading (public domain) and verse translations only
where the translator died ≥ 50 years ago. Every text was copied from a fetched source page, recorded per poem
in `source_urls`.

- The chữ Hán was cross-checked against zh.wikisource, and the phiên âm against vi.wikisource where it exists.
- Translators are Tản Đà (d. 1939), Trần Trọng Kim (1953), Ngô Tất Tố (1954), Trúc Khê (1947) and Nam Trân / Tương Như (1967).
- Disputed attributions and source variants are kept in each translation's `note`, which the tab shows.

## 10. Limits and next steps

- The timbre comes from speech, so the result is a *chanting/singing voice built from speech*: it is not a
  trained singer's belting.
- Two upgrades need licence review first:
  - a permissive Vietnamese singing-voice model, if one appears;
  - RVC fine-tuned on the owner singing, once ≥ 10 min of singing is recorded.
- Possible additions:
  - the owner records a guide melody (hum), followed by f0 extraction;
  - more ngâm điệu (Sa mạc, Bồng mạc, Kiều) as melodic templates;
  - keep the ACE-Step process warm (load is ~3 s of the 9 s).
