"""Test WASAPI loopback using the proper WasapiLoopback context manager."""
import time

import numpy as np
import pyaudiowpatch as pyaudio

pa = pyaudio.PyAudio()

# Get default WASAPI output speakers
wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
default_out_idx = wasapi_info["defaultOutputDevice"]
default_out = pa.get_device_info_by_index(default_out_idx)
print(f"Default output: [{default_out_idx}] {default_out['name']}")
print(f"  rate={default_out['defaultSampleRate']}, ch={default_out['maxOutputChannels']}")

# Find loopback device
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

# Test 1: Callback mode with explicit WASAPI-specific parameters
print()
print("=== Test 1: Callback with stream_callback ===")
chunks1 = [0]

def cb1(in_data, frame_count, time_info, status):
    chunks1[0] += 1
    return (None, pyaudio.paContinue)

try:
    s1 = pa.open(
        format=pyaudio.paInt16,  # Try int16 instead of float32
        channels=lb_dev["maxInputChannels"],
        rate=int(lb_dev["defaultSampleRate"]),
        input=True,
        input_device_index=lb_dev["index"],
        frames_per_buffer=512,
        stream_callback=cb1,
    )
    print(f"  Stream active: {s1.is_active()}")
    print(f"  Stream stopped: {s1.is_stopped()}")
    s1.start_stream()
    print(f"  After start - active: {s1.is_active()}")
    time.sleep(3)
    print(f"  Chunks: {chunks1[0]}")
    s1.stop_stream()
    s1.close()
except Exception as e:
    print(f"  Error: {e}")

# Test 2: Use output device directly as loopback (pyaudiowpatch special feature)
print()
print("=== Test 2: Open OUTPUT device as INPUT (pyaudiowpatch loopback trick) ===")
chunks2 = [0]

def cb2(in_data, frame_count, time_info, status):
    chunks2[0] += 1
    return (None, pyaudio.paContinue)

try:
    # pyaudiowpatch allows opening an output device as input for loopback
    s2 = pa.open(
        format=pyaudio.paInt16,
        channels=default_out["maxOutputChannels"],
        rate=int(default_out["defaultSampleRate"]),
        input=True,
        input_device_index=default_out["index"],  # Use output device directly!
        frames_per_buffer=512,
        stream_callback=cb2,
    )
    print(f"  Stream active: {s2.is_active()}")
    s2.start_stream()
    time.sleep(3)
    print(f"  Chunks: {chunks2[0]}")
    s2.stop_stream()
    s2.close()
except Exception as e:
    print(f"  Error: {e}")

# Test 3: Blocking read with very small buffer
print()
print("=== Test 3: Blocking read, small buffer, 2-second timeout ===")
try:
    s3 = pa.open(
        format=pyaudio.paInt16,
        channels=lb_dev["maxInputChannels"],
        rate=int(lb_dev["defaultSampleRate"]),
        input=True,
        input_device_index=lb_dev["index"],
        frames_per_buffer=512,
    )
    print(f"  Stream active: {s3.is_active()}")
    import threading
    result = {"chunks": 0, "error": None}

    def reader():
        try:
            for _ in range(10):
                data = s3.read(512, exception_on_overflow=False)
                result["chunks"] += 1
        except Exception as e:
            result["error"] = str(e)

    t = threading.Thread(target=reader)
    t.start()
    t.join(timeout=5)
    if t.is_alive():
        print("  Blocking read TIMED OUT (stream.read() never returns)")
    else:
        print(f"  Chunks: {result['chunks']}, Error: {result['error']}")
    s3.stop_stream()
    s3.close()
except Exception as e:
    print(f"  Error: {e}")

pa.terminate()
print()
print("Done.")
