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

1. **Repair budget: mean attempts on failing sentences 4.3 → ≤ 3** (SDD §6).
   - Cause: `engine=best` spends two first-pass attempts, then the stall rule needs two more.
   - Idea: when the first pass of `best` fails on both engines with the same class, start the repair from the
     class strategy instead of re-seeding both.
   - Measure with `mediaforge eval voice --voice gi-ng-t` on a data-store copy.
2. **Corpus ≥ 80 %** (now 20/36). The remaining failures:
   - 8 × "thoăn thoắt": owner ✓ Nghe đúng rồi; do not "fix" them in code.
   - 3 × babble/repeat on long sentences: try phrase-first for VI sentences > 25 words, not only > 35.
   - 4 × lone acronyms ("A.I.", "S, M, E.", "dot V.N."): lint could expand dotted acronyms with the lexicon.
   - 1 fragment.
3. **`engine_route` lessons** (SDD §5.1.3): now signals only (`reading_win`, `qa_fail` with class/engine). Promote to
   a lesson when one engine repairs a class ≥ 3 times; apply it in `synth_sentence` engine order.
4. **Sing/recite checkpoints**: an interrupted job re-runs from the start. Add stage checkpoints to
   `sing/pipeline.py` like `run_video`'s `stages.json`.
5. **P6-D owner A/B**: prepare 3 topics rendered old-style (`critique=false`, `cards=false`, no art direction) vs
   new. The owner rates blind with ⭐. Do not rate on the owner's behalf.
6. **P6-E evaluate** (licence review first, SDD §3 rules): an image–text consistency score (CLIP/SigLIP), WavLM-SV.
   [VERIFY: licences before adding any model]

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
