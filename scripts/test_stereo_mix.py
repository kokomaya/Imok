"""Test sounddevice with Stereo Mix / loopback alternatives."""
import time

import numpy as np
import sounddevice as sd

print("=== Testing Stereo Mix (device 10) via sounddevice ===")
try:
    chunks = []
    max_rms = 0.0

    def callback(indata, frames, time_info, status):
        global max_rms
        rms = float((indata**2).mean() ** 0.5)
        chunks.append(rms)

    with sd.InputStream(
        device=10,  # Stereo Mix (WDM-KS)
        samplerate=48000,
        channels=2,
        dtype="float32",
        callback=callback,
        blocksize=1024,
    ):
        print("Capturing 5 seconds... (play audio NOW)")
        time.sleep(5)

    print(f"Chunks: {len(chunks)}")
    if chunks:
        print(f"Max RMS: {max(chunks):.6f}")
        print(f"Avg RMS: {np.mean(chunks):.6f}")
        non_silent = sum(1 for r in chunks if r > 1e-5)
        print(f"Non-silent chunks: {non_silent}/{len(chunks)}")
    else:
        print("No chunks received!")
except Exception as e:
    print(f"Error: {e}")

print()
print("=== Testing WASAPI loopback via sounddevice (device 8) ===")
try:
    chunks2 = []

    def callback2(indata, frames, time_info, status):
        rms = float((indata**2).mean() ** 0.5)
        chunks2.append(rms)

    # sounddevice doesn't support WASAPI loopback directly,
    # but let's see what device 8 (WASAPI Speakers) gives us
    with sd.InputStream(
        device=8,
        samplerate=48000,
        channels=2,
        dtype="float32",
        callback=callback2,
        blocksize=1024,
    ):
        print("Capturing 3 seconds...")
        time.sleep(3)

    print(f"Chunks: {len(chunks2)}")
except Exception as e:
    print(f"Error (expected): {e}")
