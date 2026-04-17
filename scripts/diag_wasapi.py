"""Quick WASAPI loopback diagnostic."""
import pyaudiowpatch as pyaudio

pa = pyaudio.PyAudio()

print("=== Loopback Devices ===")
found = False
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if info.get("isLoopbackDevice"):
        found = True
        name = info["name"]
        ch = info["maxInputChannels"]
        rate = info["defaultSampleRate"]
        print(f"  [{i}] {name}  (ch={ch}, rate={rate})")
if not found:
    print("  No loopback devices found!")

print()
print("=== Default WASAPI Output ===")
try:
    wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    default_out = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
    name = default_out["name"]
    idx = default_out["index"]
    ch = default_out["maxOutputChannels"]
    rate = default_out["defaultSampleRate"]
    print(f"  [{idx}] {name}  (ch={ch}, rate={rate})")

    # Find loopback counterpart
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("isLoopbackDevice") and info["name"].startswith(name):
            print(f"  Loopback match: [{i}] {info['name']}")
except Exception as e:
    print(f"  Error: {e}")

pa.terminate()
