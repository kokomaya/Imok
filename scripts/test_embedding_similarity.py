"""Test embedding cosine similarity for same-speaker segments."""
import numpy as np
from backend.speaker.embedder import SpeakerEmbedder

e = SpeakerEmbedder()
e.load()

np.random.seed(0)
base = np.random.randn(16000 * 3).astype(np.float32) * 0.3

embeddings = []
for i in range(8):
    noise = np.random.randn(16000 * 3).astype(np.float32) * 0.05
    segment = (base + noise)[: 16000 * 2]  # 2 seconds
    emb = e.embed(segment, 16000)
    embeddings.append(emb)
    print(f"Segment {i}: norm={np.linalg.norm(emb):.2f}")


def cos_sim(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


print("\nCosine similarity matrix (same 'speaker'):")
for i in range(len(embeddings)):
    sims = [f"{cos_sim(embeddings[i], embeddings[j]):.3f}" for j in range(len(embeddings))]
    print(f"  [{i}] " + " ".join(sims))

# Different 'speaker'
base2 = np.random.randn(16000 * 3).astype(np.float32) * 0.3
emb_other = e.embed(base2[: 16000 * 2], 16000)
print("\nCross-speaker similarities:")
for i in range(len(embeddings)):
    print(f"  speaker_A[{i}] vs speaker_B: {cos_sim(embeddings[i], emb_other):.3f}")
