"""Minimal ACE-Step 1.5 instrumental generator for MediaForge (sidecar; executed by the sidecar venv, never imported).

DiT-only (turbo), no 5Hz LM: caption + bpm + keyscale + duration go straight
to the DiT conditioning. Writes a float32 WAV at the model's native 48 kHz.
"""
import argparse
import os
import sys
import time
from pathlib import Path

# Run with the sidecar venv; ACE_STEP_ROOT points at the ACE-Step checkout (see scripts/install_ace_step.sh).
ROOT = Path(os.environ.get("ACE_STEP_ROOT", Path(__file__).resolve().parent)).expanduser()
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True, help="style/tags caption")
    ap.add_argument("--duration", type=float, default=30.0, help="seconds (10-600)")
    ap.add_argument("--bpm", type=int, default=None, help="30-300")
    ap.add_argument("--key", default="", help='e.g. "D major", "F# minor", "Am"')
    ap.add_argument("--timesig", default="", help="2|3|4|6 (beats per bar)")
    ap.add_argument("--seed", type=int, default=-1)
    ap.add_argument("--steps", type=int, default=8, help="turbo: 8 recommended")
    ap.add_argument("--device", default="mps", help="mps|cpu")
    ap.add_argument("--no-mlx", action="store_true", help="force PyTorch DiT/VAE (no MLX)")
    ap.add_argument("--out", required=True, help="output .wav path")
    a = ap.parse_args()

    import torch
    import soundfile as sf
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music

    device = a.device
    if device == "mps" and not torch.backends.mps.is_available():
        device = "cpu"

    t0 = time.time()
    dit = AceStepHandler()
    status, ok = dit.initialize_service(
        project_root=str(ROOT),
        config_path="acestep-v15-turbo",
        device=device,
        use_mlx_dit=not a.no_mlx,
    )
    if not ok:
        print(status, file=sys.stderr)
        return 1
    t_load = time.time() - t0

    params = GenerationParams(
        task_type="text2music",
        caption=a.prompt,
        lyrics="[Instrumental]",
        instrumental=True,
        vocal_language="unknown",
        bpm=a.bpm,
        keyscale=a.key,
        timesignature=a.timesig,
        duration=a.duration,
        inference_steps=a.steps,
        shift=3.0,
        seed=a.seed,
        thinking=False,
        use_cot_metas=False,
        use_cot_caption=False,
        use_cot_language=False,
    )
    cfg = GenerationConfig(
        batch_size=1,
        use_random_seed=a.seed < 0,
        seeds=[a.seed] if a.seed >= 0 else None,
    )
    t1 = time.time()
    res = generate_music(dit, None, params, cfg, save_dir=None)
    t_gen = time.time() - t1
    if not res.success or not res.audios:
        print(f"generation failed: {res.error}", file=sys.stderr)
        return 1

    audio = res.audios[0]
    wav = audio["tensor"].float().cpu().numpy().T  # [samples, channels]
    sr = int(audio.get("sample_rate", 48000))
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), wav, sr, subtype="FLOAT")
    print(f"wrote {out} sr={sr} shape={wav.shape} device={device} "
          f"load={t_load:.1f}s gen={t_gen:.1f}s seed={audio['params'].get('seed')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
