"""Test summary coordinator end-to-end with manual trigger."""
import asyncio
import logging
import sys

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")

from backend.config import load_llm_provider_config
from backend.llm.client import CompanyLLMClient
from backend.summary.summary_coordinator import SummaryCoordinator


async def test():
    cfg = load_llm_provider_config()
    client = CompanyLLMClient(
        cfg.settings, extra_headers=cfg.extra_headers, verify_ssl=cfg.ssl_verify
    )
    coord = SummaryCoordinator(client, window_duration_s=60.0)

    results = {"segment": [], "global": []}
    coord.on_segment_summary(lambda s: results["segment"].append(s))
    coord.on_global_summary(lambda s, items: results["global"].append((s, items)))

    await coord.start()
    print("Coordinator started")

    # Simulate feeding transcriptions
    class FakeResult:
        text = "John will handle the auth module by next Friday. Team decided OAuth2. Mary raised a concern about the database migration timeline."
        language = "en"

    class FakeEvent:
        result = FakeResult()
        segment_start_time = 5.0
        segment_end_time = 10.0
        timestamp = 5.0

    coord.feed_transcription(FakeEvent())
    pending = coord._time_window.pending_count
    print(f"Fed 1 transcription, pending={pending}")

    # Manual segment trigger
    print("Triggering segment summary...")
    await coord.trigger_segment_summary()
    seg_count = len(results["segment"])
    print(f"Segment results: {seg_count}")
    if results["segment"]:
        s = results["segment"][0]
        print(f"  topics: {s.topics}")
        print(f"  conclusions: {s.conclusions}")
        print(f"  action_items: {s.action_items}")
        print(f"  raw_text (first 300): {s.raw_text[:300]}")

    # Manual global trigger
    print("\nTriggering global summary...")
    await coord.trigger_global_summary()
    glob_count = len(results["global"])
    print(f"Global results: {glob_count}")
    if results["global"]:
        gs, items = results["global"][0]
        print(f"  raw_text (first 300): {gs.raw_text[:300]}")
        print(f"  action_items: {[(a.description, a.assignee) for a in items]}")

    await coord.stop()
    await client.close()
    print("\nDone!")


asyncio.run(test())
