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
2. ◐ **Corpus ≥ 80 %** — **25/36 (69 %)** on the original 36 rows (was 21); **33/36 (92 %)** once the owner gives
   ✓ Nghe đúng rồi to the 8 "thoăn thoắt" rows. Without that ✓ the ceiling is 28/36 (78 %), because those rows
   must not be "fixed" in code. Done in code: phrase-first for VI > 25 words, dotted acronyms (lexicon; letters
   in English), Whisper outro hallucinations ("subscribe … Ghiền Mì Gõ") re-judged. Left: 2 long babble/repeat,
   the cut fragment "ệu PNJ…", "Ây Ai - ét-em-i". The corpus on the data-store copy grew to 40 rows (qa_fail
   signals from the A/B renders): 26/40.
3. ✅ **`engine_route` lessons** — `lessons.propose_route`/`route`; applied in engine order and in repair.
4. ✅ **Sing/recite checkpoints** — interpret, each sung line, mix, each drawn shot.
5. ◐ **P6-D owner A/B** — 3 topics rendered old vs new on the data-store copy, copied (random A/B order) to
   `~/.local/share/mediaforge/outputs/ab-p6d/`. The owner rates with ⭐ in `DANH-GIA.md`; `.key.json` holds the
   mapping. Do not rate on the owner's behalf.
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
