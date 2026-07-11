"""Quick microphone diagnostics: list devices + record 3 s and report input level."""
import numpy as np
import sounddevice as sd

print("=== Default input device ===")
print("idx:", sd.default.device[0])

print("\n=== All input devices ===")
for i, x in enumerate(sd.query_devices()):
    if x["max_input_channels"] > 0:
        print(f"  {i}: {x['name']}  ch={x['max_input_channels']}  sr={int(x['default_samplerate'])}")

print("\n=== Recording 3 seconds at 16 kHz mono... speak now! ===")
data = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="int16")
sd.wait()
peak = int(np.abs(data).max())
rms = int(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
print(f"Peak amplitude (0..32767): {peak}")
print(f"RMS  amplitude          : {rms}")
if peak < 500:
    print("⚠️  Almost silent. Mic may be muted or wrong device selected.")
elif peak < 3000:
    print("⚠️  Very quiet. Move closer to the mic or raise input gain in Windows Sound settings.")
else:
    print("✅ Microphone level looks good.")
