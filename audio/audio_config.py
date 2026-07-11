"""Audio Device Configuration"""
from dataclasses import dataclass
from typing import List
import sounddevice as sd

@dataclass
class AudioDevice:
    """Represents an audio device."""
    index: int
    name: str
    channels: int
    sample_rate: int
    is_default: bool = False

def list_audio_devices() -> List[AudioDevice]:
    """List all available audio input devices."""
    try:
        devices_list = sd.query_devices()
        devices = []
        for i, info in enumerate(devices_list):
            if info['max_input_channels'] > 0:
                device = AudioDevice(
                    index=i,
                    name=info['name'],
                    channels=info['max_input_channels'],
                    sample_rate=int(info['default_samplerate']),
                )
                devices.append(device)
        return devices
    except Exception as e:
        print(f"Error listing audio devices: {e}")
        return []

def get_default_input_device() -> int:
    """Get the default input device index."""
    try:
        default_device = sd.query_devices(kind='input')
        return default_device['index']
    except Exception:
        return 0

def print_audio_devices():
    """Print all available audio devices for debugging."""
    devices = list_audio_devices()
    print("\n=== Available Audio Devices ===")
    for device in devices:
        print(f"  [{device.index}] {device.name} ({device.channels}ch, {device.sample_rate}Hz)")
    print()
