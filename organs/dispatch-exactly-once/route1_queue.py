#!/usr/bin/env python3
"""route1_queue: SIGMA-ROUTE-1 affinity fan-out benchmark. Queue generator + ollama dispatcher.

Consumer: the SIGMA burn lane 13 operator window runs the three arms (desktop, bob,
weighted-split) with this script per watch/inbox/SIGMA_BURN_LANE_J_ROUTING_20261030.md
section d; the per-node tok/s rows feed tools/route1_ledger.py and the follow-on
capability-weighted pick_provider port (schema sigma_route.v1).
Verifier: `.venv/Scripts/python.exe -m py_compile tools/route1_queue.py`; the default
(no --run) mode re-derives every row's text_sha from seed+corpus and exits 1 on drift;
closure (30/30 ids, identical prompt_sha sets) is enforced by tools/route1_ledger.py;
the dispatch law (no id dispatches twice, strict-once default on) is verified by
tests/test_route1_queue_dispatch.py on fake in-process endpoints.

Module map (split 2026-10-30, wave 4 lane 11, to hold the 400-line ratchet cap):
  - tools/route1_corpus.py : deterministic seed -> queue derivation + drift check.
  - tools/route1_dispatch.py: Node/EMA/dispatch_one endpoint primitives + the
    InflightRegistry cross-process claim registry.
  - tools/route1_queue.py (this file): CLI + run_arm refill loop orchestration.

Design (spec, section d):
  - 30 identical summarize jobs over estate corpus text, fixed seed, ~2-4K chars each.
  - Arms: A desktop solo, B bob solo, C weighted split. All arms share the one queue file;
    only the endpoint set differs, so prompt_sha sets must match across arms.
  - Refill-on-completion work stealing (the free_lane_fleet pattern): a bounded set of
    in-flight requests, one per endpoint slot; every completion immediately refills.
  - Weighted split is expected-finish scheduling, not fixed quotas: the next job goes to
    the endpoint with the lowest estimated finish time from its live wall tok/s EMA.
    Seed the EMA with --seed-rate from arm A/B measurements; it self-corrects from the
    first completions (the implemented form of the spec's "correct them once from the
    first 5 completions").
  - retry-never within a run (latency benchmark purity, free_lane_fleet law); a failed
    row stays failed, `--run` again to resume: ok ids are skipped, failed/missing ids
    are retried on the resumed run.
  - strict-once (default on, SIGMA wave 4 lane 11 fix): a file-backed in-flight
    registry (<results>.inflight.json) claims each id before submit, and this refill
    loop consults it, so an id with a live claim in another dispatcher process is
    deferred, never re-dispatched. Root cause fixed: done_ok is built from COMPLETED
    rows only, so two concurrent run_arm invocations appending to one results file
    each saw the other's in-flight ids as pending and re-fired them (2026-10-30 arm C
    taint: 55 rows / 30 ids, bytes preserved at
    memory/runs/route1/results_split_DUPTAINTED_20261030.jsonl). --no-strict-once
    restores the legacy single-dispatcher behavior for deliberate replays.
  - Per-node serial default (--slots host=1): respects the 6GB VRAM max_resident 1 law
    (configs/lanes.yaml:54-56) and bob's single-threaded CPU reality.

Stdlib only. Repo venv only: `.venv/Scripts/python.exe tools/route1_queue.py ...`.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import sys
import time
from collections import deque
from pathlib import Path

# Same-directory imports: route1_queue runs as a script (sys.path[0] = tools/) and is
# imported by tests that put tools/ on sys.path; the re-exports below keep the
# historical route1_queue.<name> surface working for sibling tools and tests.
from route1_corpus import (  # noqa: F401  (re-exported for compat)
    DEFAULT_ROOTS,
    build_queue,
    gen_and_check,
    load_queue,
    sha16,
    write_queue,
)
from route1_dispatch import (  # noqa: F401  (re-exported for compat)
    InflightRegistry,
    Node,
    dispatch_one,
    parse_seed_rates,
    parse_slots,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "var" / "route1" / "queue.jsonl"
DESKTOP_ENDPOINT = "http://127.0.0.1:11434"
BOB_ENDPOINT = "http://100.72.138.62:11434"
DEFAULT_MODEL = "gemma:2b"
SEED = 20261030
N_ITEMS = 30


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_arm(args) -> int:
    queue_path = Path(args.out)
    if not queue_path.exists():
        print("queue file missing; generating first", file=sys.stderr)
        rc = gen_and_check(args)
        if rc:
            return rc
    jobs = load_queue(queue_path)
    endpoints = [e.rstrip("/") for e in args.endpoint]
    slots = parse_slots(args.slots, endpoints)
    rates = parse_seed_rates(args.seed_rate, endpoints)
    nodes = [Node(ep, args.model, slots[ep], rates[ep], args.fail_limit) for ep in endpoints]
    for node in nodes:
        node.probe(min(args.timeout, 15.0))

    results_path = Path(args.results) if args.results else queue_path.parent / f"results_{args.arm}.jsonl"
    done_ok: set[str] = set()
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("ok"):
                    done_ok.add(r["id"])
    pending = deque(j for j in jobs if j["id"] not in done_ok)
    print(f"ARM {args.arm}: {len(pending)} to run ({len(done_ok)} already ok), "
          f"endpoints: " + ", ".join(f"{n.url} slots={n.slots}" for n in nodes))
    print(f"results: {results_path}")

    # Strict-once dispatch law (SIGMA wave 4 lane 11, 2026-10-30): done_ok above is
    # built from COMPLETED rows only, so ids still in flight in a concurrent
    # dispatcher were invisible to it and got re-fired (root cause of the 55-row
    # taint preserved at memory/runs/route1/results_split_DUPTAINTED_20261030.jsonl:
    # 25 of 30 ids twice). The registry claims each id before submit and this refill
    # loop consults it; a live foreign claim defers the job instead of duplicating it.
    registry: InflightRegistry | None = None
    is_job_done = None
    if getattr(args, "strict_once", True):
        reg_path = (Path(args.inflight) if getattr(args, "inflight", "")
                    else results_path.parent / (results_path.name + ".inflight.json"))
        lease = args.lease if getattr(args, "lease", 0.0) > 0 else args.timeout + 60.0
        registry = InflightRegistry(reg_path, lease)
        # Ledger tail position after the startup scan; _refresh_done() extends done_ok
        # with rows that landed since (a concurrent dispatcher's completions), and
        # is_job_done runs inside registry.claim's lock so claim decisions never race
        # a racer's row append.
        ledger_pos = results_path.stat().st_size if results_path.exists() else 0

        def _refresh_done() -> bool:
            nonlocal ledger_pos
            if not results_path.exists():
                return False
            size = results_path.stat().st_size
            if size <= ledger_pos:
                return False
            with results_path.open("r", encoding="utf-8") as f:
                f.seek(ledger_pos)
                fresh = f.read()
            ledger_pos = size
            for line in fresh.splitlines():
                line = line.strip()
                if line:
                    r = json.loads(line)
                    if r.get("ok"):
                        done_ok.add(r["id"])
            return True

        def is_job_done(job_id: str) -> bool:
            _refresh_done()
            return job_id in done_ok

        foreign = registry.live_claims()
        if foreign:
            print(f"WARNING: {len(foreign)} id(s) already in flight elsewhere "
                  f"(registry {registry.path}); they will be deferred: {sorted(foreign)}")

    total_slots = sum(n.slots for n in nodes)
    inflight: dict[concurrent.futures.Future, tuple[Node, dict]] = {}
    attempted = 0
    deferred: list[str] = []
    rc = 0
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=total_slots)
    with results_path.open("a", encoding="utf-8", newline="\n") as ledger:
        def write_row(row: dict) -> None:
            ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
            ledger.flush()

        def pick_node(job: dict) -> Node | None:
            now = time.time()
            alive = [n for n in nodes if n.alive and n.free > 0]
            if not alive:
                return None
            out_est = args.num_predict
            # unknown-rate nodes go first (least assigned wins), then argmin est finish
            unknown = [n for n in alive if n.rate_ema <= 0]
            if unknown:
                return min(unknown, key=lambda n: n.assigned)
            return min(alive, key=lambda n: n.est_finish(now, out_est))

        try:
            while pending or inflight:
                while pending and attempted < args.limit:
                    job = pending[0]
                    node = pick_node(job)
                    if node is None:
                        break
                    if registry is not None:
                        granted, why = registry.claim(job["id"], node.url, args.arm,
                                                      is_done=is_job_done)
                        if not granted:
                            pending.popleft()
                            if why == "done":
                                # Completed by another dispatcher after our startup
                                # snapshot; dispatching it again would double the row.
                                print(f"[{_now_iso()}] {job['id']} skipped: ok row "
                                      f"already in ledger (concurrent dispatcher)")
                            else:
                                deferred.append(job["id"])
                                print(f"[{_now_iso()}] {job['id']} deferred: live claim "
                                      f"held elsewhere (strict-once)")
                            continue
                    pending.popleft()
                    node.free -= 1
                    node.assigned += 1
                    attempted += 1
                    fut = pool.submit(dispatch_one, node, job, args.num_predict, args.timeout)
                    inflight[fut] = (node, job)
                if not inflight:
                    break
                if registry is not None:
                    registry.touch({j["id"] for _, j in inflight.values()})
                done_set, _ = concurrent.futures.wait(
                    inflight.keys(), return_when=concurrent.futures.FIRST_COMPLETED)
                for fut in done_set:
                    node, job = inflight.pop(fut)
                    node.free += 1
                    row = fut.result()
                    if row["ok"]:
                        node.observe(row["tok_per_s_wall"])
                    else:
                        node.fail()
                    if registry is not None:
                        # Row append + claim release is one atomic step against a
                        # concurrent dispatcher's claim() (double-dispatch fix).
                        registry.complete(job["id"], lambda r=row: write_row(r))
                    else:
                        write_row(row)
                    tag = "ok" if row["ok"] else f"FAIL {row['err'][:80]}"
                    print(f"[{_now_iso()}] {job['id']} -> {node.url} "
                          f"({row['latency_s']}s, {row['output_tokens']} tok): {tag}")
                if not any(n.alive for n in nodes) and pending:
                    print(f"STOPPED: all endpoints dead, {len(pending)} jobs undispatched. "
                          f"Rerun the same command to resume.", file=sys.stderr)
                    rc = 2
                    break
        except KeyboardInterrupt:
            # Do not wait out a slow in-flight request: cancel queued work, keep written rows.
            # Claims for futures whose rows never got written stay in the registry until
            # lease expiry (strict-once): a sibling dispatcher must not re-fire a request
            # that may still be running server-side. Conservative branch, not a defect.
            pool.shutdown(wait=False, cancel_futures=True)
            print("\ninterrupted: rows already written persist; rerun the same command to resume",
                  file=sys.stderr)
            rc = 130
        pool.shutdown(wait=True)
        if deferred:
            print(f"DEFERRED {len(deferred)} id(s) held by another dispatcher "
                  f"(strict-once); rerun the same command after it exits to fill "
                  f"gaps: {sorted(deferred)}")
    return rc


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="SIGMA-ROUTE-1 queue generator + dispatcher")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="queue jsonl path")
    ap.add_argument("--n", type=int, default=N_ITEMS, help="queue size (default 30)")
    ap.add_argument("--seed", type=int, default=SEED, help="fixed sampling seed")
    ap.add_argument("--roots", default=DEFAULT_ROOTS, help="comma list of corpus roots")
    ap.add_argument("--force", action="store_true", help="overwrite queue even on drift")
    ap.add_argument("--run", action="store_true", help="dispatch mode (else generate/verify)")
    ap.add_argument("--arm", default="arm", help="arm name recorded in rows and results filename")
    ap.add_argument("--endpoint", action="append", default=[],
                    help="ollama base url; repeat for the split arm")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--results", default="", help="results jsonl (default: results_<arm>.jsonl beside queue)")
    ap.add_argument("--slots", default="", help="per-host slots, e.g. '127.0.0.1=2,100.72.138.62=1'")
    ap.add_argument("--seed-rate", default="", help="wall tok/s priors, e.g. '127.0.0.1=30,100.72.138.62=6'")
    ap.add_argument("--num-predict", type=int, default=300, help="output token cap, identical across arms")
    ap.add_argument("--timeout", type=float, default=600.0, help="per-request timeout seconds")
    ap.add_argument("--limit", type=int, default=10 ** 9, help="max attempts this session (smoke: --limit 2)")
    ap.add_argument("--fail-limit", type=int, default=5, help="consecutive fails before a node is marked dead")
    ap.add_argument("--strict-once", dest="strict_once", action="store_true", default=True,
                    help="refuse to dispatch an id holding a live in-flight claim "
                         "(default on; claims live in <results>.inflight.json)")
    ap.add_argument("--no-strict-once", dest="strict_once", action="store_false",
                    help="legacy single-dispatcher behavior: no cross-process claim check")
    ap.add_argument("--inflight", default="",
                    help="in-flight registry path (default: <results>.inflight.json)")
    ap.add_argument("--lease", type=float, default=0.0,
                    help="claim lease seconds before reclaim; 0 = --timeout + 60")
    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    if args.run and not args.endpoint:
        ap.error("--run needs at least one --endpoint")
    if args.run:
        return run_arm(args)
    return gen_and_check(args)


if __name__ == "__main__":
    sys.exit(main())
