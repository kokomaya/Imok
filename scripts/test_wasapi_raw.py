"""Test raw WASAPI loopback capture."""
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
print(f"  rate={lb_dev['defaultSampleRate']}, ch={lb_dev['maxInputChannels']}")

chunks_received = [0]
max_rms = [0.0]


def callback(in_data, frame_count, time_info, status):
    chunks_received[0] += 1
    audio = np.frombuffer(in_data, dtype=np.float32)
    rms = float((audio**2).mean() ** 0.5)
    if rms > max_rms[0]:
        max_rms[0] = rms
    return (None, pyaudio.paContinue)


stream = pa.open(
    format=pyaudio.paFloat32,
    channels=lb_dev["maxInputChannels"],
    rate=int(lb_dev["defaultSampleRate"]),
    input=True,
    input_device_index=lb_dev["index"],
    frames_per_buffer=1024,
    stream_callback=callback,
)

print(f"Stream active: {stream.is_active()}")
print("Capturing 5 seconds... (play audio on your PC NOW)")
time.sleep(5)
print(f"Chunks received: {chunks_received[0]}")
print(f"Max RMS: {max_rms[0]:.6f}")

stream.stop_stream()
stream.close()
pa.terminate()
