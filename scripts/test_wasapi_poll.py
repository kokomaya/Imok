"""Test get_read_available() on WASAPI loopback."""
import time
import pyaudiowpatch as pyaudio

pa = pyaudio.PyAudio()

# Find loopback
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

stream = pa.open(
    format=pyaudio.paInt16,
    channels=lb_dev["maxInputChannels"],
    rate=int(lb_dev["defaultSampleRate"]),
    input=True,
    input_device_index=lb_dev["index"],
    frames_per_buffer=512,
)

print("Polling get_read_available() for 3 seconds...")
print("(Play audio to see non-zero values)")
t0 = time.time()
polls = 0
nonzero = 0
max_avail = 0
while time.time() - t0 < 3.0:
    try:
        avail = stream.get_read_available()
        polls += 1
        if avail > 0:
            nonzero += 1
            if avail > max_avail:
                max_avail = avail
            if nonzero <= 3:
                print(f"  poll {polls}: available={avail}")
    except Exception as e:
        print(f"  Error: {e}")
        break
    time.sleep(0.005)

print(f"\nPolls: {polls}, non-zero: {nonzero}, max_available: {max_avail}")
stream.stop_stream()
stream.close()
pa.terminate()
