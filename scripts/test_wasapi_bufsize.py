"""Debug: compare different buffer sizes for WASAPI blocking read."""
import time
import threading
import numpy as np
import pyaudiowpatch as pyaudio

pa = pyaudio.PyAudio()

wasapi_info = None
for i in range(pa.get_host_api_count()):
    api_info = pa.get_host_api_info_by_index(i)
    if api_info["name"] == "Windows WASAPI":
        wasapi_info = api_info
        break

default_out_idx = wasapi_info["defaultOutputDevice"]
default_out = pa.get_device_info_by_index(default_out_idx)
lb_dev = None
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if info.get("isLoopbackDevice") and default_out["name"] in info["name"]:
        lb_dev = info
        break

print(f"Loopback: [{lb_dev['index']}] {lb_dev['name']}")

for buf_size in [512, 256, 1024, 1536]:
    print(f"\n=== Buffer size: {buf_size} ===")
    try:
        s = pa.open(
            format=pyaudio.paInt16,
            channels=lb_dev["maxInputChannels"],
            rate=int(lb_dev["defaultSampleRate"]),
            input=True,
            input_device_index=lb_dev["index"],
            frames_per_buffer=buf_size,
        )
        result = {"chunks": 0}

        def reader(stream=s, bs=buf_size):
            try:
                for _ in range(5):
                    stream.read(bs, exception_on_overflow=False)
                    result["chunks"] += 1
            except Exception as e:
                print(f"  Error: {e}")

        t = threading.Thread(target=reader)
        t.start()
        t.join(timeout=3)
        if t.is_alive():
            print(f"  TIMEOUT - stuck at read()")
        else:
            print(f"  OK - got {result['chunks']} chunks")
        s.stop_stream()
        s.close()
    except Exception as e:
        print(f"  Open error: {e}")

pa.terminate()
