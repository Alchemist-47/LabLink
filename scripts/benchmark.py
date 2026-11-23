"""Measure LabLink's server-side latency and memory on the demo data.

Runs 2,000 mixed requests in-process (every role, every main page, searches, booking
attempts, fair-allocation plans) with Gemini switched off, so the numbers are LabLink's
own code and not network or model time.

    python scripts/benchmark.py
"""

from __future__ import annotations

import logging
import statistics
import sys
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lablink import create_app  # noqa: E402
from lablink.ai import LabAssistant  # noqa: E402
from lablink.repository import MemoryRepository  # noqa: E402

REQUESTS = 2000
NOW = datetime(2026, 11, 23, 8, 0)  # Monday before labs open, like the tests

READS = {
    "s_ananya": ["/", "/?q=3d+print+a+drone+frame", "/?q=train+a+neural+network", "/equipment/eq_fdm_printer",
                 "/equipment/eq_gpu", "/requests", "/requests/rq_drone/plan", "/bookings"],
    "f_iyer": ["/requests", "/allocate?equipment=eq_fdm_printer", "/insights", "/bookings"],
    "a_lab": ["/allocate?equipment=eq_fdm_printer", "/insights", "/bookings", "/requests"],
}
BOOKING = {"request_id": "rq_drone", "date": "2026-11-24", "start_hour": "10", "hours": "2"}  # full slot: rules + conflict path


def build_clients():
    app = create_app(
        repo=MemoryRepository(),
        assistant=LabAssistant(None),
        clock=lambda: NOW,
        config={"TESTING": True, "SECRET_KEY": "bench", "AI_RATE_LIMIT": 10**6, "AI_GLOBAL_RATE_LIMIT": 10**6,
                "WRITE_RATE_LIMIT": 10**6, "WRITE_GLOBAL_RATE_LIMIT": 10**6},
    )
    clients = {}
    for user in READS:
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"], session["_csrf"] = user, "bench"
        clients[user] = client
    return clients


def run(clients) -> tuple[list[float], dict[int, int]]:
    """Send REQUESTS mixed requests; returns per-request milliseconds and a status-code count."""
    plan = [(user, path) for user, paths in READS.items() for path in paths]
    timings: list[float] = []
    statuses: dict[int, int] = {}
    for i in range(REQUESTS):
        if i % 10 == 9:  # 10% writes: a booking attempt that confirms, conflicts or waitlists
            client = clients["s_ananya"]
            started = time.perf_counter()
            response = client.post("/equipment/eq_fdm_printer/book", data={**BOOKING, "csrf_token": "bench"})
        else:
            user, path = plan[i % len(plan)]
            started = time.perf_counter()
            response = clients[user].get(path)
        timings.append((time.perf_counter() - started) * 1000)
        statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
    return timings, statuses


def main() -> None:
    logging.disable(logging.WARNING)
    run(build_clients())  # warm-up: template compilation, imports
    timings, statuses = run(build_clients())
    tracemalloc.start()  # separate pass, because tracing slows every allocation
    run(build_clients())
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    q = statistics.quantiles(timings, n=100)
    pages = sum(len(p) for p in READS.values())
    print(f"{REQUESTS} requests ({pages} pages across student, faculty and coordinator + 10% booking attempts), Gemini off")
    print(f"p50 {q[49]:.1f} ms   p95 {q[94]:.1f} ms   p99 {q[98]:.1f} ms   max {max(timings):.1f} ms")
    print(f"throughput {REQUESTS / (sum(timings) / 1000):.0f} requests/s on one worker")
    print(f"peak Python heap during a run {peak / 1_048_576:.1f} MB")
    print(f"status codes {dict(sorted(statuses.items()))}")


if __name__ == "__main__":
    main()
