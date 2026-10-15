# Handoff — P6 review & upgrade, next session

Written 2026-10-15 at the end of the session that built P6-A…D. Read this, then [SDD-review-upgrade.md](SDD-review-upgrade.md)
§8 (phases), §11 (what was built, measurements, deviations). Delete this file when its items are done.

## State

- `main` = `b4b4a10` (merge of `feature/sdd-review-upgrade`). 127 tests pass. **Not pushed**: `main` is 10 commits
  ahead of `origin/main`. Push only if the owner asks.
- The live service (`ai.block8.mediaforge`, :8765) runs this code (`scripts/service.sh update` from this checkout).
- Live data: the old `learned` lexicon entries were migrated into lessons. 14 were retired (backup
  `~/.local/share/mediaforge/backups/lexicon-gi-ng-t-20261015-142619.json`). The candidate `for → pho` awaits the
  owner's decision.
- Owner decisions (2026-10-15): review gates on by default for Video 16:9 + Explainer (off for Short); title/end
  cards on for 16:9; self-critique on; Claude decides lesson clean-ups itself at ≥ 90 % confidence, asks below.

## Open items, in priority order

Status 2026-10-15 (second session, branch `feature/p6-repair-budget`, not merged/pushed; numbers in
SDD-review-upgrade §11.2–11.4):

1. ✅ **Repair budget ≤ 3** — 3.0 mean attempts on failing sentences (was 4.27), passes unchanged at 21/36, eval
   time 466 s → 296 s.
2. ◐ **Corpus ≥ 80 %** — **27/36 (75 %)** on the original 36 rows (was 21); **35/36 (97 %)** once the owner gives
   ✓ Nghe đúng rồi to the 8 "thoăn thoắt" rows, which must not be "fixed" in code. Done in code: phrase-first for
   VI > 25 words, dotted acronyms (lexicon; letters in English), Whisper outro hallucinations ("subscribe … Ghiền
   Mì Gõ") and decoder loops ("lư lư lư …") re-judged. Left besides "thoăn thoắt": the broken script fragment
   "ệu PNJ … goép … năm hai n" and "Ây Ai - ét-em-i - chấm Vi En" heard as "AI SMI.VM" (one letter off; loosening
   the letter judge would forgive real errors). On the copy's grown corpus (40 rows): 29/40.
3. ✅ **`engine_route` lessons** — `lessons.propose_route`/`route`; applied in engine order and in repair.
4. ✅ **Sing/recite checkpoints** — interpret, each sung line, mix, each drawn shot.
5. ✅ **P6-D owner A/B** — 3 topics rendered old vs new on the data-store copy (random A/B order per topic,
   `~/.local/share/mediaforge/outputs/ab-p6d/`). The owner picked B in all 3 topics (pairwise choice, no stars);
   the key shows B = new style every time → **new wins 3/3** (exit criterion ≥ 2/3). Recorded in `ket-qua.json`.
6. ✅ **P6-E licence review** — SigLIP (Apache-2.0) allowed; WavLM-SV rejected (CC BY-SA 3.0); SpeechBrain ECAPA
   a candidate [VERIFY: VoxCeleb training-data terms]. No model added.

## How to work safely (see memory: e2e-testing-safety)

- Tests: `uv run --with pytest python -m pytest -q` (pytest is not in the venv).
- Never GPU-test against the live store. Make a copy:
  ```
  C=<scratchpad>/mfcopy; rsync -a --exclude outputs --exclude cache --exclude music --exclude trash \
    --exclude backups --exclude uploads --exclude tmp --exclude logs ~/.local/share/mediaforge/ $C/
  ln -s ~/.local/share/mediaforge/music $C/music   # do this BEFORE starting a server (it creates music/)
  MEDIAFORGE_DATA_DIR=$C uv run mediaforge serve --port 8766     # restart it after code changes
  ```
- Check `curl -s 127.0.0.1:8765/api/health` → `running: null` before GPU work.
- Dashboard screenshots: headless Chrome hangs on SSE, so wrap it:
  `perl -e 'alarm 30; exec @ARGV' "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --window-size=1400,1500 --virtual-time-budget=8000 --screenshot=out.png "http://127.0.0.1:8766/#lessons"`
- New dashboard elements inside `.job-out` (a flex container) need `flex: 1 0 100%`, or they collapse.
- Deploy: `scripts/service.sh update` (waits for a running job), then check `/api/health` and
  `~/.local/share/mediaforge/logs/server.log`.
- Keep `docs/SDD.md` and `SDD-review-upgrade.md` §11 in sync; commit on a feature branch, merge to main with
  `--no-ff` when the owner asks.
