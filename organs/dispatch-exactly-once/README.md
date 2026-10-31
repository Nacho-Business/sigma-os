# dispatch-exactly-once

**Invariant:** a job id dispatches exactly once per queue, no matter how many
dispatchers race the same results file - the claim, the result append, and the
claim release are one atomic step in a cross-process registry with leases.

**Falsifier:** two concurrent `route1_queue --run` invocations on one results
file producing more rows than queue ids (the founding taint: 55 rows over 30
ids, preserved in the estate at memory/runs/route1/results_split_DUPTAINTED_
20261030.jsonl). The test suite replays that race on fake in-process ollama
endpoints and asserts one row per id.

**Consumer:** `route1_queue.py --run` (the only dispatch caller); the SIGMA
cost lane reads the results files it writes (`route1_cost.py`).
**Verifier:** `test_route1_dispatch.py` (pytest, no network, no GPU).

Files: route1_corpus.py (deterministic seed-to-corpus queue derivation),
route1_dispatch.py (endpoint primitives + InflightRegistry), route1_queue.py
(orchestrator; default no---run mode re-derives and drift-checks the queue).
Note: the default corpus roots (`corpus/...`) are estate paths; pass
`--roots` to point the derivation at your own corpus. Estate receipts:
watch/inbox/SIGMA_W4_11_DISPATCHER_FIX_20261030.md,
watch/inbox/SIGMA_ROUTE1_VERDICT_20261030.md.
