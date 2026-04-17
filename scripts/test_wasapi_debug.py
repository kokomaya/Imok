"""Debug the blocking read WASAPI loopback."""
import time
import threading
import numpy as np
import pyaudiowpatch as pyaudio

pa = pyaudio.PyAudio()

# Find loopback device (same logic as _find_wasapi_loopback_device)
wasapi_info = None
for i in range(pa.get_host_api_count()):
    api_info = pa.get_host_api_info_by_index(i)
    if api_info["name"] == "Windows WASAPI":
        wasapi_info = api_info
        break

default_out_idx = wasapi_info["defaultOutputDevice"]
default_out = pa.get_device_info_by_index(default_out_idx)
print(f"Default output: [{default_out_idx}] {default_out['name']}")

lb_dev = None
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if info.get("isLoopbackDevice") and default_out["name"] in info["name"]:
        lb_dev = info
        break

print(f"Loopback: [{lb_dev['index']}] {lb_dev['name']}")
print(f"  rate={lb_dev['defaultSampleRate']}, ch={lb_dev['maxInputChannels']}")

device_sr = int(lb_dev["defaultSampleRate"])
device_ch = max(lb_dev["maxInputChannels"], 1)
target_sr = 16000
chunk_frames = 512
device_chunk = int(chunk_frames * device_sr / target_sr)
print(f"  device_chunk={device_chunk} (from chunk_frames={chunk_frames})")

stream = pa.open(
    format=pyaudio.paInt16,
    channels=device_ch,
    rate=device_sr,
    input=True,
    input_device_index=lb_dev["index"],
    frames_per_buffer=device_chunk,
)
print(f"Stream active: {stream.is_active()}")
print(f"Stream stopped: {stream.is_stopped()}")

# Try reading
print("Reading 10 chunks with blocking read...")
result = {"chunks": 0, "error": None, "rms_vals": []}

def reader():
    try:
        for i in range(10):
            data = stream.read(device_chunk, exception_on_overflow=False)
            audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float((audio**2).mean() ** 0.5)
            result["chunks"] += 1
            result["rms_vals"].append(rms)
            if i < 3:
                print(f"  chunk {i}: len={len(audio)} rms={rms:.6f}")
    except Exception as e:
        result["error"] = str(e)

t = threading.Thread(target=reader)
t.start()
t.join(timeout=8)
if t.is_alive():
    print("TIMEOUT - blocking read is stuck!")
else:
    print(f"Chunks: {result['chunks']}, Error: {result['error']}")
    if result['rms_vals']:
        print(f"Max RMS: {max(result['rms_vals']):.6f}")

stream.stop_stream()
stream.close()
pa.terminate()
