# SDD — Giọng mẫu (stock presenter voices)

v1.0, 2026-10-15. Status: merged to `main` (c179c1d) and deployed 2026-10-15; reviewed on a copy of the data store.

## 1. Goal

Ready-made Vietnamese voices that sound like a clear, standard television presenter (MC), male and female.
The owner can listen to each one at once in the dashboard. Every voice has a preview rendered in advance, so
"▶ Nghe thử" plays a file and does not start a job. The owner can then use a stock voice for any speech or
video job, like a cloned voice.

Reference for the experience: vbee.vn's voice picker (gender filter, instant samples). Vbee is used only as a
benchmark for clarity, pace and the picker's layout. No Vbee audio is downloaded, cloned or compared
automatically.

## 2. Rules

- **No real person is imitated.** We never clone famous presenters, and never clone a commercial service's voice
  talents. Stock voices are named by gender · region · style ("Nữ · Bắc · Tin tức"). The engine's preset id is an
  internal field only, because several VieNeu preset ids are also the names of real people.
- **Sources:**
  - VieNeu-TTS-v3-Turbo preset speakers. Apache-2.0, and the model card says this covers the preset-voice assets
    as well. The publisher says the speakers granted "appropriate rights and consent", but does not name the
    speakers or say how consent was obtained. [VERIFY: owner/compliance before commercial use]
  - VoxCPM2 voice design: a voice created from an English description (`instruct`). No person is involved.
- Consent kind is `synthetic`. Stock voices take no recordings (`add_sample` refuses them) and cannot be a blend
  owner or partner yet.
- Review uses only local measures (Whisper, pyworld, VieNeu's speaker encoder). No audio is sent to an LLM, and no
  scoring model (UTMOS, DNSMOS, …) is added without the SDD §3 licence review.

## 3. Design

| Piece | Where |
|---|---|
| Catalogue (12 voices: 10 VieNeu presets in the news / natural / storytelling styles — Bắc, Trung, Nam — and 2 VoxCPM2 designs) | `voice/stock.py` `CATALOGUE` |
| Profile `kind: "stock"`: `engine`, `preset` or `instruct`, `gender`, `region`, `style`, `fx`, `preview{file,key}` | `stock.ensure_profiles`, `profiles.voice_ref` |
| Speaker reaches the engine | `VoiceRef.preset` → `VieNeu.infer(voice=…)`; `VoiceRef.instruct` → `VoxCPM2.generate(instruct=…)` |
| One engine only (no `best`, no alternates) | `speak.synth_sentence` (`stock` branch) |
| Preview job | type `stock_previews` (resumable). It re-renders only when `sha(engine, preset/instruct, fx, text)` changes. |
| Instant preview | `GET /api/voices/{id}/preview` (mp3); `GET /api/voices` adds `preview_ready` |
| Dashboard | Voices tab → **Giọng mẫu** card: Tất cả/Nữ/Nam, region filter, ▶ / ■, **Dùng** (selects the voice for new jobs), **Cài giọng mẫu** / **Tạo bản nghe thử**, **★** (make it the default voice: `voices/DEFAULT`, same as `mediaforge voice default <id>`, MCP `set_default_voice`) |
| MCP | `list_voices` returns `kind: "stock"` with `gender`, `region`, `style`, `preview_url` |

## 4. Multi-dimensional review (`mediaforge eval stock`)

Every voice reads the same 8-sentence presenter test set (`stock_review.TEST_SET`), with 2 seeds each. The set
covers a greeting, a percentage, a date, an English term, a question, a 40-word sentence, a tone-dense sentence
and a closing. Each take is synthesised once. Every fx variant in `FX_GRID` (pace, warmth, clarity) is then
applied to it and listened to again, so tuning costs no TTS time.

| Dimension | Measure | Score band (100 = in band) | Weight |
|---|---|---|---|
| Clarity | Whisper error rate on the final audio | 100·(1 − 2·cer) | 0.35 |
| Tones | syllables heard with the right letters but the wrong tone | 100·(1 − 3·err) | 0.20 |
| Stability | share of sentences whose first take passes QA | 100·share | 0.15 |
| Pace | syllables per second ÷ the project's speaking rate (`RATE["vi"]` = 5.2) | 0.92–1.10 (0 at 0.70 / 1.35) | 0.10 |
| Expressiveness | semitone std of the voiced pitch | 1.8–4.5 st (0 at 0.5 / 7) | 0.10 |
| Consistency | cosine of each take's speaker vector to the voice centroid | 0.6 → 0, 0.95 → 100 | 0.10 |
| Pauses (reported) | silent gaps ≥ 120 ms per internal comma | not scored | — |

The bands are heuristics written down so that runs can be compared. They do not define what makes a good
presenter. The composite ranks the candidates; the owner's listening decides. `--reference gi-ng-t` adds the
owner's voice as a yardstick, and it is not ranked. `--apply` stores each stock voice's best fx, and the previews
then rebuild because they are stale.

## 5. Results

### 5.1 Run 1 (2026-10-15, data-store copy, 10 voices + the owner's gi-ng-t as yardstick, 1 144 s)

| Voice | Composite | Clarity | Tones | Stability | Pace (syll/s) | Pitch std (st) | Speaker cos | Gaps/comma |
|---|---|---|---|---|---|---|---|---|
| Nữ · Bắc · Tự nhiên | **96.5** | 97.4 | 97.7 | 93.8 | 4.89 | 3.6 | 0.91 | 0.73 |
| Nam · Bắc · Tự nhiên | **95.7** | 99.1 | 100 | 87.5 | 4.71 | 3.6 | 0.90 | 1.02 |
| Nam · Bắc · Kể chuyện | 92.9 | 97.8 | 98.7 | 81.2 | 4.51 | 4.1 | 0.92 | 0.97 |
| Nữ · Nam · Tin tức | 92.0 | 90.9 | 94.4 | 81.2 | 4.93 | 3.2 | 0.92 | 1.08 |
| Nữ · Bắc · Kể chuyện | 90.8 | 98.5 | 97.5 | 81.2 | 4.29 | 3.0 | 0.91 | 0.57 |
| Nam · Nam · Tin tức | 90.8 | 92.7 | 99.5 | 75.0 | 4.57 | 2.8 | 0.92 | 1.08 |
| Nữ · Bắc · Tin tức | 89.7 | 97.9 | 98.7 | 87.5 | 4.02 | 3.9 | 0.92 | 1.23 |
| Nữ · MC (thiết kế) | 88.4 | 98.2 | 100 | 87.5 | 4.60 | 3.1 | **0.69** | 0.75 |
| Nam · MC (thiết kế) | 87.2 | 98.4 | 98.9 | 81.2 | 4.46 | 4.0 | **0.73** | 1.00 |
| Nam · Bắc · Tin tức | 85.5 | 98.5 | 100 | 81.2 | **3.68** | 4.7 | 0.93 | **2.37** |
| *gi-ng-t (owner, not ranked)* | *85.8* | | | 87.5 | 3.31 | 2.3 | 0.92 | 1.53 |

Findings:

1. **Clarity and tones are not the problem.** Every voice is heard at 0.5–4.6 % error. The two Southern presets
   are the least clear to Whisper (3.7–4.6 %). This may be the judge's Northern bias rather than the voices.
2. **Designed voices drift.** A VoxCPM2 `instruct` voice is a different person on every call: its speaker cosine
   is 0.69–0.73, against 0.90+ for presets. Fix: anchor each designed voice once (`stock.anchor_designed`). The
   clearest of 4 designed takes becomes a synthetic reference clip, and later sentences clone that clip.
3. **The "Tin tức" presets read slowly and over-pause.** Nam · Bắc · Tin tức reads 3.7 syll/s with 2.4 silent
   gaps per comma, so it breaks inside phrases. Every voice picked the fastest fx variant (pace 1.05), so the
   grid gained pace 1.1.
4. Pitch movement (2.8–4.7 st) is in the lively range for all voices; none is monotone.

### 5.2 Run 2 (after anchoring + pace 1.1, `--apply`, 780 s)

| Voice | Composite (run 1 →) | fx kept (pace / warmth / clarity) | Pace (syll/s) | Speaker cos | Gaps/comma |
|---|---|---|---|---|---|
| Nam · MC (thiết kế) | 87.2 → **97.2** | 1.05 / 0.2 / 0.8 | 4.88 | 0.73 → **0.94** | 1.72 |
| Nữ · Bắc · Tự nhiên | 96.5 → 96.5 | 1.05 / 0.2 / 0.8 | 4.89 | 0.91 | 0.63 |
| Nam · Bắc · Tự nhiên | 95.7 → 96.1 | 1.1 / 0.2 / 0.8 | 4.94 | 0.89 | 0.98 |
| Nam · Bắc · Kể chuyện | 92.9 → 94.4 | 1.1 / 0.2 / 0.8 | 4.73 | 0.92 | 0.97 |
| Nữ · MC (thiết kế) | 88.4 → **93.2** | 1.1 / 0.2 / 0.8 | 4.30 | 0.69 → **0.95** | 1.70 |
| Nữ · Nam · Tin tức | 92.0 → 92.4 | 1.1 / 0.2 / 0.8 | 5.17 | 0.91 | 1.03 |
| Nữ · Bắc · Kể chuyện | 90.8 → 92.0 | 1.1 / 0.2 / 0.8 | 4.49 | 0.91 | 0.60 |
| Nam · Nam · Tin tức | 90.8 → 91.7 | 1.1 / 0.2 / 0.8 | 4.78 | 0.91 | 0.78 |
| Nữ · Bắc · Tin tức | 89.7 → 89.6 | 1.05 / 0.2 / 0.8 | 4.02 | 0.92 | 1.23 |
| Nam · Bắc · Tin tức | 85.5 → 87.2 | 1.1 / 0.2 / 0.8 | 3.85 | 0.92 | 2.13 |
| *gi-ng-t (owner)* | *85.9* | | 3.14 | 0.92 | 1.53 |

- Anchoring fixed the drift of the designed voices. "Nam · MC (thiết kế)" is now the top-scored voice.
- Every voice keeps clarity 0.8 and warmth 0.2, so presenters sound brighter than the narrator default
  (clarity 0.6).
- The two Northern "Tin tức" presets still pause too often inside phrases (2.1 and 1.2 gaps per comma). Time
  stretching does not remove pauses. Next options, if the owner's ears agree: shorten internal silences (pause
  compression), or drop them from the catalogue.
- The tuned fx are written into `CATALOGUE`, so a fresh install gets them.
- Machine top 2 per gender: **Nam · MC (thiết kế)**, **Nam · Bắc · Tự nhiên**; **Nữ · Bắc · Tự nhiên**,
  **Nữ · MC (thiết kế)**. The owner's ⭐ in `outputs/giong-mau-review/DANH-GIA.md` decides.

### 5.3 Owner listening

Ten previews (tuned fx), ranked by the composite, are in `~/.local/share/mediaforge/outputs/giong-mau-review/`
with a rating sheet. **Deployed 2026-10-15 at the owner's request** (first from the branch `feature/stock-voices`, merged to `main` the same day)
(`scripts/service.sh update`). The `stock_previews` job then ran on the live store and created the 10 profiles,
2 anchors and 10 previews; the owner's voice is still the default. To roll back, run `scripts/service.sh update`
from `main`; the stock profiles stay in `voices/stock-*`, and main's dashboard does not list them correctly.

### 5.4 Miền Trung (2026-10-16, data-store copy, 393 s)

VieNeu-TTS-v3-Turbo ships exactly two Central presets (`voices_v3_turbo.json`, region `Trung`, style `tu_nhien`).
Both were added as **Nữ · Trung · Tự nhiên** and **Nam · Trung · Tự nhiên** with the common tuned fx (pace 1.1,
warmth 0.2, clarity 0.8); `eval stock --apply` kept it. Nữ · Bắc · Tự nhiên was re-run as the yardstick.

| Voice | Composite | Clarity | Tones | Stability | Pace (syll/s) | Pitch std (st) | Speaker cos | Gaps/comma |
|---|---|---|---|---|---|---|---|---|
| Nữ · Bắc · Tự nhiên (yardstick) | 96.5 | 97.4 | 97.7 | 93.8 | 4.9 | 3.6 | 0.91 | 0.7 |
| Nữ · Trung · Tự nhiên | 74.0 | 83.5 | 63.6 | 62.5 | 4.0 | 3.8 | 0.93 | 1.3 |
| Nam · Trung · Tự nhiên | 69.2 | 83.5 | 59.4 | 43.8 | ≈ 4.0 | — | ≈ 0.88 | — |

- Most "tone errors" are the Northern-trained judge hearing Central tones the Northern way ("thời sự" → "thơi sửa",
  "số liệu" → "sổ liệu"), the same bias seen on the Southern presets in §5.1, only stronger. The composite is not a
  verdict on these voices; the owner's listening decides.
- Side effect of that bias: more first takes fail QA (stability 44–63 %), so Central voices cost more repair
  attempts per sentence. Learned readings stay scoped to the stock voice that produced them (lessons are per voice).
- Both read slower than the band (4.0 vs 4.8–5.7 syll/s) even at pace 1.1, the top of the grid.

