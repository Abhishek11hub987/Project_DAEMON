"""
Comprehensive microphone diagnostic for D.A.E.M.O.N.

Tests:
  1. List all input devices + highlight default
  2. Record 4 seconds and show raw signal stats
  3. Apply MIC_GAIN and show boosted stats
  4. Quick Whisper transcription of the recording
  5. Suggest fixes

Run:  python tests/mic_diagnostic.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import sounddevice as sd

# Load project config
try:
    from core_logic.config import Config
    SAMPLE_RATE = Config.SAMPLE_RATE
    CHANNELS = Config.CHANNELS
    DEVICE = Config.AUDIO_INPUT_DEVICE
    MIC_GAIN = float(getattr(Config, "MIC_GAIN", 1.0) or 1.0)
    WHISPER_MODEL = getattr(Config, "WHISPER_MODEL", "small")
except Exception:
    SAMPLE_RATE = 16000
    CHANNELS = 1
    DEVICE = None
    MIC_GAIN = 1.0
    WHISPER_MODEL = "small"

SEP = "=" * 60

# ── 1. Device listing ─────────────────────────────────────────
print(f"\n{SEP}")
print("  1. AUDIO INPUT DEVICES")
print(SEP)

default_idx = sd.default.device[0]
print(f"  System default input device index: {default_idx}")
print(f"  D.A.E.M.O.N. AUDIO_INPUT_DEVICE : {DEVICE!r} ({'system default' if DEVICE is None else f'pinned to {DEVICE}'})")
print()

input_devs = []
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        tag = ""
        if i == default_idx:
            tag += " [SYSTEM DEFAULT]"
        if DEVICE is not None and i == DEVICE:
            tag += " [DAEMON SELECTED]"
        print(f"  {i:>2}: {d['name']:<50}  ch={d['max_input_channels']}  sr={int(d['default_samplerate'])}{tag}")
        input_devs.append(i)

use_device = DEVICE if DEVICE is not None else default_idx
print(f"\n  → Will record using device {use_device}")

# ── 2. Raw recording ──────────────────────────────────────────
duration = 4
print(f"\n{SEP}")
print(f"  2. RECORDING {duration}s AT {SAMPLE_RATE} Hz (speak NOW!)")
print(SEP)

try:
    data = sd.rec(
        int(duration * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        device=use_device,
    )
    sd.wait()
except Exception as e:
    print(f"  ❌ Recording failed: {e}")
    print(f"  Try setting AUDIO_INPUT_DEVICE in .env to one of: {input_devs}")
    sys.exit(1)

audio = data.flatten()
peak = int(np.abs(audio).max())
rms = int(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
nonzero = int(np.count_nonzero(audio))
zero_pct = 100.0 * (1 - nonzero / max(len(audio), 1))

print(f"  Samples recorded : {len(audio)}")
print(f"  Peak amplitude   : {peak:>6}  / 32767")
print(f"  RMS  amplitude   : {rms:>6}")
print(f"  Zero samples     : {zero_pct:.1f}%")

# Histogram of amplitudes
abs_audio = np.abs(audio.astype(np.float32))
bins = [0, 100, 500, 1000, 3000, 5000, 10000, 32767]
print(f"\n  Amplitude distribution:")
for lo, hi in zip(bins[:-1], bins[1:]):
    count = int(np.sum((abs_audio >= lo) & (abs_audio < hi)))
    pct = 100.0 * count / len(audio)
    bar = "█" * int(pct / 2)
    print(f"    {lo:>5}-{hi:>5}: {pct:5.1f}%  {bar}")

# ── 3. Gain-boosted stats ─────────────────────────────────────
print(f"\n{SEP}")
print(f"  3. AFTER MIC_GAIN = {MIC_GAIN}x")
print(SEP)

if MIC_GAIN != 1.0:
    boosted = audio.astype(np.int32) * MIC_GAIN
    np.clip(boosted, -32768, 32767, out=boosted)
    audio_boosted = boosted.astype(np.int16)
else:
    audio_boosted = audio

peak_b = int(np.abs(audio_boosted).max())
rms_b = int(np.sqrt(np.mean(audio_boosted.astype(np.float32) ** 2)))
clipped = int(np.sum(np.abs(audio_boosted) >= 32700))
clip_pct = 100.0 * clipped / max(len(audio_boosted), 1)

print(f"  Boosted Peak     : {peak_b:>6}  / 32767")
print(f"  Boosted RMS      : {rms_b:>6}")
print(f"  Clipped samples  : {clipped} ({clip_pct:.2f}%)")

if clip_pct > 5.0:
    print(f"  ⚠️  Heavy clipping! Lower MIC_GAIN (currently {MIC_GAIN})")
elif clip_pct > 1.0:
    print(f"  ⚠️  Some clipping. Consider lowering MIC_GAIN slightly.")
else:
    print(f"  ✅ No significant clipping.")

# ── 4. Whisper transcription test ─────────────────────────────
print(f"\n{SEP}")
print(f"  4. WHISPER TRANSCRIPTION TEST (model={WHISPER_MODEL})")
print(SEP)

try:
    import whisper
    model = whisper.load_model(WHISPER_MODEL)
    audio_f32 = audio_boosted.astype(np.float32) / 32768.0
    result = model.transcribe(
        audio_f32,
        language="en",
        temperature=0.0,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        logprob_threshold=-1.0,
    )
    text = result["text"].strip()
    segments = result.get("segments", [])

    print(f"  Transcribed text : \"{text}\"")
    print(f"  Language         : {result.get('language', '?')}")
    if segments:
        for s in segments:
            print(f"    seg [{s['start']:.1f}-{s['end']:.1f}s]: "
                  f"logprob={s.get('avg_logprob', 0):.2f}  "
                  f"no_speech={s.get('no_speech_prob', 0):.2f}  "
                  f"\"{s['text'].strip()}\"")
    if not text:
        print("  ⚠️  Whisper returned empty. Mic might be muted or picking up wrong source.")
except ImportError:
    print("  ⚠️  Whisper not installed — skipping transcription test.")
except Exception as e:
    print(f"  ❌ Whisper error: {e}")

# ── 5. Verdict & recommendations ─────────────────────────────
print(f"\n{SEP}")
print("  5. DIAGNOSIS")
print(SEP)

issues = []
if peak < 200:
    issues.append("🔇 Mic is essentially SILENT — wrong device or hardware muted.")
elif peak < 1000:
    issues.append("🔈 Signal very faint — check Windows Sound > Input level, or set AUDIO_INPUT_DEVICE in .env.")
elif peak < 3000 and MIC_GAIN <= 1.0:
    issues.append("🔉 Signal is quiet — increase MIC_GAIN to 3.0-5.0 in .env.")

if rms_b > 20000:
    issues.append("📢 Signal is EXTREMELY loud / clipping — lower MIC_GAIN or Windows input volume.")

if zero_pct > 95:
    issues.append("⚠️  >95% zero samples — mic stream might be broken or using wrong device.")

if not issues:
    print("  ✅ Microphone looks healthy! If D.A.E.M.O.N. still mishears you:")
    print("     - Speak clearly, ~30cm from mic")
    print("     - Reduce background noise")
    print("     - Try WHISPER_MODEL=medium for better accuracy (slower)")
else:
    for issue in issues:
        print(f"  {issue}")
    print()
    print("  Suggested .env changes:")
    if peak < 200:
        print(f"    AUDIO_INPUT_DEVICE=<correct device index from list above>")
    if peak < 3000 and MIC_GAIN < 4.0:
        print(f"    MIC_GAIN=5.0")
    if rms_b > 20000:
        print(f"    MIC_GAIN=1.0")

print(f"\n{SEP}\n")
