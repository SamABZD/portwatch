# Five-minute PortWatch demonstration

Use the [README setup](../README.md#run-the-historical-demo), keep the API/frontend running,
and open http://127.0.0.1:3000. Select **Historical replay**; database sessions begin in Live,
while prepared sessions select Historical automatically.

1. **0:00–1:00 — Evidence.** Select Los Angeles at January 1, 2024 12:30 UTC. The recorded
   result has 99 fresh reporting vessels and six completed dwell samples. Explain freshness,
   partial visits and source labels.
2. **1:00–2:00 — Replay.** Move backward, then play forward. Later reports, hours and
   confirmations remain excluded. Turn off Basemap to show the local geometry fallback.
3. **2:00–3:00 — Vessel and port.** Select a real vessel and inspect report time, zone,
   profile availability and incomplete visits. Switch to Oakland after its historical run;
   prepared sessions expose only their saved port.
4. **3:00–4:00 — Pipeline.** Select PostgreSQL, Python or Prefect on **Data pipeline**.
   Explain original evidence, normalization, transactional analytics and read-only queries.
   Current source/run states are unavailable in prepared mode.
5. **4:00–5:00 — Reproduction.** Show the [242-test transcript](evidence/validation.txt) and
   [fresh two-port results](evidence/reproducibility.json). Explain matching repeat digests,
   recovery drills, blocked hosted CI and pending public hosting.

For a live demonstration, start the owned runtime using [LIVE_MULTIPORT.md](LIVE_MULTIPORT.md).
Explain last-received times, gaps and rate-limited news. Do not display private `.env`, provider
keys, raw live payloads or database credentials while recording.
