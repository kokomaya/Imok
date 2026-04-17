"""Test WASAPI loopback with blocking read (no callback)."""
import time

import numpy as np
import pyaudiowpatch as pyaudio

pa = pyaudio.PyAudio()

# Find default WASAPI output
wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
default_out_idx = wasapi_info["defaultOutputDevice"]
default_out = pa.get_device_info_by_index(default_out_idx)
print(f"Default output: [{default_out_idx}] {default_out['name']}")

# Find loopback counterpart
lb_dev = None
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if info.get("isLoopbackDevice") and default_out["name"] in info["name"]:
        lb_dev = info
        break

if lb_dev is None:
    print("No loopback device found!")
    pa.terminate()
    exit(1)

print(f"Loopback: [{lb_dev['index']}] {lb_dev['name']}")
rate = int(lb_dev["defaultSampleRate"])
ch = lb_dev["maxInputChannels"]
print(f"  rate={rate}, ch={ch}")

# Open in BLOCKING mode (no callback)
stream = pa.open(
    format=pyaudio.paFloat32,
    channels=ch,
    rate=rate,
    input=True,
    input_device_index=lb_dev["index"],
    frames_per_buffer=1024,
)

print(f"Stream active: {stream.is_active()}")
print("Capturing 5 seconds with BLOCKING read... (play audio NOW)")

chunks = 0
max_rms = 0.0
t0 = time.time()
while time.time() - t0 < 5.0:
    try:
        data = stream.read(1024, exception_on_overflow=False)
        audio = np.frombuffer(data, dtype=np.float32)
        rms = float((audio**2).mean() ** 0.5)
        if rms > max_rms:
            max_rms = rms
        chunks += 1
        if chunks <= 3 or chunks % 50 == 0:
            print(f"  chunk {chunks}: samples={len(audio)}, rms={rms:.6f}")
    except Exception as e:
        print(f"  Read error: {e}")
        break

print(f"Chunks received: {chunks}")
print(f"Max RMS: {max_rms:.6f}")

stream.stop_stream()
stream.close()
pa.terminate()
