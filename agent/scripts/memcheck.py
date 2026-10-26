"""Memory check for decision 0004/0005. Runs inside the agent image, as one process.

Starts uvicorn in-process (graph compiled at startup), calls /health over HTTP, then
embeds a batch in the same process, sampling cgroup v2 memory throughout.

    agent/scripts/memcheck.sh          # builds the image and runs this at --memory=512m

The working set is memory.current minus inactive_file (what docker stats reports).
Raw memory.peak is not used: page cache fills it to the limit and is reclaimable.
"""

import json
import threading
import time
import urllib.request

import uvicorn

from app.embeddings import DIMENSIONS, embed

BATCH = 64
CGROUP = "/sys/fs/cgroup"


def _cgroup_mb() -> dict[str, int]:
    with open(f"{CGROUP}/memory.stat") as f:
        stat = dict(line.split() for line in f)
    with open(f"{CGROUP}/memory.current") as f:
        current = int(f.read())
    return {
        "working_set_mb": (current - int(stat["inactive_file"])) // 1048576,
        "anon_mb": int(stat["anon"]) // 1048576,
    }


def _rss_peak_mb() -> int:
    with open("/proc/self/status") as f:
        return next(int(line.split()[1]) // 1024 for line in f if line.startswith("VmHWM"))


def main() -> None:
    peak = {"working_set_mb": 0, "anon_mb": 0}
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            for key, value in _cgroup_mb().items():
                peak[key] = max(peak[key], value)
            time.sleep(0.02)

    threading.Thread(target=sample, daemon=True).start()
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    with urllib.request.urlopen("http://127.0.0.1:8000/health") as response:  # noqa: S310
        health = json.load(response)
    time.sleep(0.5)
    after_health = dict(peak)

    texts = [f"things to do in London, number {i}: jazz, markets, galleries" for i in range(BATCH)]
    vectors = embed(texts)
    assert len(vectors) == BATCH and all(len(v) == DIMENSIONS for v in vectors)  # noqa: S101
    time.sleep(0.5)
    stop.set()

    result = {
        "health": health,
        "vectors": len(vectors),
        "dimensions": len(vectors[0]),
        "peak_after_health": after_health,
        "peak_after_embed": peak,
        "process_rss_peak_mb": _rss_peak_mb(),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
