"""Quick comparison of devices 1 vs 19 to find which gives louder input."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import sounddevice as sd

devices_to_test = [1, 19]

for dev_idx in devices_to_test:
    info = sd.query_devices(dev_idx)
    sr = 16000
    ch = 1
    print(f"\n--- Device {dev_idx}: {info['name']} (native sr={int(info['default_samplerate'])}) ---")
    print(f"    Recording 3s at {sr}Hz, {ch}ch... SPEAK NOW!")
    try:
        data = sd.rec(int(3 * sr), samplerate=sr, channels=ch, dtype="int16", device=dev_idx)
        sd.wait()
        audio = data.flatten()
        peak = int(np.abs(audio).max())
        rms = int(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        print(f"    Peak: {peak}/32767   RMS: {rms}")
        if peak < 500:
            print(f"    ** SILENT **")
        elif peak < 2000:
            print(f"    FAINT")
        else:
            print(f"    OK")
    except Exception as e:
        print(f"    FAILED: {e}")

print("\nDone. Pick the device with the higher Peak/RMS.")
