"""Verify the fixed WASAPILoopbackSource captures audio (poll mode).

Run this while playing audio on your PC (e.g. YouTube video).
"""
import time
from backend.audio.wasapi_source import WASAPILoopbackSource

src = WASAPILoopbackSource(target_sample_rate=16000, chunk_frames=512)
src.start()
print("WASAPI started (poll + blocking-read mode), capturing 5 seconds...")
print("Play some audio on your PC NOW!")

chunks = 0
total_samples = 0
max_rms = 0.0
t0 = time.time()
while time.time() - t0 < 5.0:
    chunk = src.read_chunk()
    if chunk is not None:
        chunks += 1
        total_samples += len(chunk.data)
        rms = float((chunk.data**2).mean() ** 0.5)
        if rms > max_rms:
            max_rms = rms
        if chunks <= 5 or chunks % 50 == 0:
            print(f"  chunk {chunks}: samples={len(chunk.data)} rms={rms:.6f}")

print("Stopping source...")
t_stop = time.time()
src.stop()
print(f"Stopped in {time.time() - t_stop:.3f}s")
assert not src.is_active

duration = total_samples / 16000 if total_samples > 0 else 0
print(f"\nResult: {chunks} chunks, {total_samples} samples, {duration:.2f}s audio")
print(f"Max RMS: {max_rms:.6f}")
if chunks > 0:
    print("SUCCESS - WASAPI loopback is capturing audio!")
else:
    print("No chunks captured (expected if no audio was playing)")
    print("This is normal - WASAPI loopback only captures when system outputs audio")
