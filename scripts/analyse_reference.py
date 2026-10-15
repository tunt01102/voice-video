"""Measure singing/chanting technique on isolated vocal stems: pitch range, note holds, vibrato, glides,
phrase/pause lengths, scale histogram, swell, singer's-formant ratio. Prints one JSON line per file."""
import json
import sys

import numpy as np
import pyworld as pw
import soundfile as sf

FP = 5.0


def smooth(x, k):
    k = max(1, int(k))
    return np.convolve(np.pad(x, (k // 2, k - 1 - k // 2), mode="edge"), np.ones(k) / k, mode="valid")


def runs(mask):
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def analyse(path):
    x, sr = sf.read(path)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = x.astype(np.float64)
    if sr != 16000:
        import scipy.signal as ss
        x = ss.resample_poly(x, 16000, sr)
        sr = 16000
    f0, t = pw.harvest(x, sr, f0_floor=65, f0_ceil=1000, frame_period=FP)
    hop = int(sr * FP / 1000)
    rms = np.array([np.sqrt(np.mean(x[max(0, min(i * hop, len(x) - hop)):][:hop] ** 2) + 1e-12) for i in range(len(f0))])
    db = 20 * np.log10(rms + 1e-9)
    voiced = (f0 > 0) & (db > np.percentile(db, 30))
    semis = np.where(voiced, 12 * np.log2(np.where(f0 > 0, f0, 1) / 440.0) + 69, np.nan)  # MIDI
    med = float(np.nanmedian(semis))
    p5, p95 = np.nanpercentile(semis, [5, 95])

    # Phrases: voiced runs merged across gaps < 150 ms; pauses = gaps >= 250 ms.
    vr = runs(voiced)
    merged = []
    for a, b in vr:
        if merged and (a - merged[-1][1]) * FP < 150:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    phrases = [(b - a) * FP / 1000 for a, b in merged if (b - a) * FP > 300]
    pauses = [(merged[i + 1][0] - merged[i][1]) * FP / 1000 for i in range(len(merged) - 1)
              if (merged[i + 1][0] - merged[i][1]) * FP >= 250]

    # Notes: stretches where the 120 ms-smoothed pitch stays within ±0.6 st of its running mean.
    holds, vib_rate, vib_depth, vib_onset, swell_peak, glides = [], [], [], [], [], []
    for a, b in vr:
        if (b - a) * FP < 200:
            continue
        s = semis[a:b]
        sm = smooth(s, 120 / FP)
        start = 0
        for i in range(1, len(sm) + 1):
            end_note = i == len(sm) or abs(sm[i] - np.mean(sm[start:i])) > 0.7
            if end_note:
                n = i - start
                dur = n * FP / 1000
                if dur >= 0.25:
                    holds.append(dur)
                if dur >= 0.7:
                    seg = s[start:i]
                    base = smooth(seg, 200 / FP)
                    osc = seg - base
                    spec = np.abs(np.fft.rfft(osc * np.hanning(len(osc)), n=4096))
                    fr = np.fft.rfftfreq(4096, FP / 1000)
                    band = (fr > 3.5) & (fr < 9)
                    if spec[band].max() > 3 * np.median(spec[(fr > 0.5) & (fr < 20)]):
                        vib_rate.append(float(fr[band][np.argmax(spec[band])]))
                        env = smooth(np.abs(osc), 200 / FP)
                        vib_depth.append(float(np.percentile(np.abs(osc), 90)))
                        thr = 0.5 * env.max()
                        vib_onset.append(float(np.argmax(env > thr) / len(env)))
                    e = smooth(rms[a + start:a + i], 100 / FP)
                    swell_peak.append(float(np.argmax(e) / len(e)))
                if i < len(sm):
                    # glide: frames until the pitch settles within 0.3 st of the next stretch's level
                    nxt = sm[i:i + int(400 / FP)]
                    if len(nxt) > 10:
                        lvl = np.median(nxt[-max(1, len(nxt) // 3):])
                        settle = np.argmax(np.abs(nxt - lvl) < 0.3)
                        glides.append(settle * FP)
                start = i

    # Pitch-class histogram relative to the most common class (proxy for the tonal centre).
    pc = np.round(semis[~np.isnan(semis)]) % 12
    hist = np.bincount(pc.astype(int), minlength=12).astype(float)
    hist /= hist.sum()
    top = int(np.argmax(hist))
    rel = np.roll(hist, -top)

    # Singer's formant: energy 2.5–3.5 kHz vs 0.1–1 kHz on voiced frames (needs full band, so reread at native sr).
    y, sr2 = sf.read(path)
    if y.ndim > 1:
        y = y.mean(axis=1)
    import scipy.signal as ss
    f, tt, S = ss.stft(y, sr2, nperseg=2048)
    P = np.abs(S) ** 2
    frame_db = 10 * np.log10(P.sum(0) + 1e-12)
    loud = frame_db > np.percentile(frame_db, 60)
    hf = P[(f > 2500) & (f < 3500)][:, loud].sum()
    lf = P[(f > 100) & (f < 1000)][:, loud].sum()

    def q(v, p):
        return round(float(np.percentile(v, p)), 3) if len(v) else None

    return {
        "file": path.split("/")[-2] if path.endswith("vocals.wav") else path.split("/")[-1],
        "voiced_s": round(voiced.sum() * FP / 1000, 1),
        "median_hz": round(440 * 2 ** ((med - 69) / 12), 1),
        "range_st_p5_p95": [round(p5 - med, 1), round(p95 - med, 1)],
        "phrase_s_p50_p90": [q(phrases, 50), q(phrases, 90)],
        "pause_s_p50_p90": [q(pauses, 50), q(pauses, 90)],
        "note_hold_s_p50_p90_max": [q(holds, 50), q(holds, 90), round(max(holds), 2) if holds else None],
        "notes_ge1s_share": round(float(np.mean(np.array(holds) >= 1.0)), 3) if holds else None,
        "vibrato_rate_hz_p50": q(vib_rate, 50), "vibrato_rate_p10_p90": [q(vib_rate, 10), q(vib_rate, 90)],
        "vibrato_depth_st_p50": q(vib_depth, 50), "vibrato_onset_frac_p50": q(vib_onset, 50),
        "vibrato_share_of_long_notes": round(len(vib_rate) / max(1, len(swell_peak)), 2),
        "swell_peak_frac_p50": q(swell_peak, 50),
        "glide_ms_p50_p90": [q(glides, 50), q(glides, 90)],
        "pitch_class_hist_rel_top": [round(float(v), 3) for v in rel],
        "singers_formant_db": round(10 * np.log10(hf / lf), 1),
    }


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(json.dumps(analyse(p), ensure_ascii=False), flush=True)
