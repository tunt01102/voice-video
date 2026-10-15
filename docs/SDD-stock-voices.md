# SDD — Giọng mẫu (stock presenter voices)

v1.0, 2026-10-15. Status: built on branch `feature/stock-voices`, reviewed on a copy of the data store. Not yet
deployed to the live service; the owner decides that after listening.

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
| Catalogue (10 voices: 8 VieNeu presets in the news / natural / storytelling styles, 2 VoxCPM2 designs) | `voice/stock.py` `CATALOGUE` |
| Profile `kind: "stock"`: `engine`, `preset` or `instruct`, `gender`, `region`, `style`, `fx`, `preview{file,key}` | `stock.ensure_profiles`, `profiles.voice_ref` |
| Speaker reaches the engine | `VoiceRef.preset` → `VieNeu.infer(voice=…)`; `VoiceRef.instruct` → `VoxCPM2.generate(instruct=…)` |
| One engine only (no `best`, no alternates) | `speak.synth_sentence` (`stock` branch) |
| Preview job | type `stock_previews` (resumable). It re-renders only when `sha(engine, preset/instruct, fx, text)` changes. |
| Instant preview | `GET /api/voices/{id}/preview` (mp3); `GET /api/voices` adds `preview_ready` |
| Dashboard | Voices tab → **Giọng mẫu** card: Tất cả/Nữ/Nam, region filter, ▶ / ■, **Dùng** (selects the voice for new jobs), **Cài giọng mẫu** / **Tạo bản nghe thử** |
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

See §5.1 (filled in by the first review run).
