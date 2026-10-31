#!/usr/bin/env python3
"""route1_dispatch: SIGMA-ROUTE-1 endpoint primitives + cross-process claim registry.

Split out of tools/route1_queue.py (SIGMA wave 4 lane 11, 2026-10-30) so the
orchestrator stays under the 400-line ratchet cap. Node/dispatch_one carry the
per-request contract (stream=False, temperature 0, num_predict cap, SSRF no-
redirect guard); InflightRegistry carries the strict-once dispatch law that
fixed the 2026-10-30 arm C double dispatch (55 rows / 30 ids preserved at
memory/runs/route1/results_split_DUPTAINTED_20261030.jsonl).

Consumer: tools/route1_queue.py run_arm (the only dispatch caller).
Verifier: `.venv/Scripts/python.exe -m py_compile tools/route1_dispatch.py`;
tests/test_route1_queue_dispatch.py exercises the registry claim/lease/complete
semantics and the dispatcher end to end on fake in-process ollama endpoints.

Stdlib only. Repo venv only: `.venv/Scripts/python.exe tools/route1_queue.py ...`.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from route1_corpus import sha16


# Cross-process claim lease. LOCK_STALE_S bounds how long a crashed dispatcher's lock
# file can wedge the registry before takeover; LOCK_WAIT_S bounds how long an honest
# contender waits before failing closed (never dispatch unclaimed under strict-once).
LOCK_STALE_S = 15.0
LOCK_WAIT_S = 5.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """An ollama endpoint must never bounce us elsewhere (free_lane_fleet SSRF guard)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


class Node:
    def __init__(self, url: str, model: str, slots: int, seed_rate: float, fail_limit: int):
        self.url = url.rstrip("/")
        self.model = model
        self.slots = slots
        self.free = slots
        self.seed_rate = seed_rate
        self.rate_ema = seed_rate  # wall tok/s (output_tokens / end-to-end latency)
        self.obs = 0
        self.consec_fails = 0
        self.fail_limit = fail_limit
        self.alive = True
        self.assigned = 0

    def observe(self, wall_tok_s: float) -> None:
        w = 0.5 if self.obs < 5 else 0.2  # fast early correction, then stable EMA
        base = wall_tok_s if self.rate_ema <= 0 else self.rate_ema
        self.rate_ema = w * wall_tok_s + (1 - w) * base
        self.obs += 1
        self.consec_fails = 0

    def fail(self) -> None:
        self.consec_fails += 1
        if self.consec_fails >= self.fail_limit:
            self.alive = False
            print(f"NODE DEAD (self {self.consec_fails} consecutive fails, health law): "
                  f"{self.url}", file=sys.stderr)

    def est_finish(self, now: float, out_tokens: int) -> float:
        if self.rate_ema > 0:
            return now + out_tokens / self.rate_ema
        return 0.0  # unknown rate: tie-break by least-assigned in pick()

    def probe(self, timeout: float) -> None:
        try:
            with _OPENER.open(self.url + "/api/tags", timeout=timeout) as resp:
                resp.read()
        except (urllib.error.URLError, OSError) as e:
            raise SystemExit(f"endpoint unreachable at probe ({e}): {self.url}")


def parse_slots(spec: str, endpoints: list[str]) -> dict[str, int]:
    out = {ep: 1 for ep in endpoints}
    if not spec:
        return out
    for pair in spec.split(","):
        host, _, k = pair.partition("=")
        host = host.strip()
        for ep in endpoints:
            if host in ep:
                out[ep] = max(1, int(k))
    return out


def parse_seed_rates(spec: str, endpoints: list[str]) -> dict[str, float]:
    out = {ep: 0.0 for ep in endpoints}
    if not spec:
        return out
    for pair in spec.split(","):
        host, _, r = pair.partition("=")
        host = host.strip()
        for ep in endpoints:
            if host in ep:
                out[ep] = float(r)
    return out


def dispatch_one(node: Node, job: dict, num_predict: int, timeout: float) -> dict:
    body = json.dumps({
        "model": node.model,
        "prompt": job["prompt"],
        "stream": False,
        "options": {"num_predict": num_predict, "temperature": 0},
    }).encode("utf-8")
    req = urllib.request.Request(
        node.url + "/api/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    row = {
        "endpoint": node.url, "model": node.model, "id": job["id"],
        "prompt_sha": job["prompt_sha"], "ok": False,
    }
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latency = time.time() - t0
        text = str(data.get("response", ""))
        tokens = data.get("eval_count")
        estimated = False
        if not tokens:
            tokens = max(1, round(len(text) / 4))
            estimated = True
        row.update({
            "ok": True, "latency_s": round(latency, 3),
            "wall_start": round(t0, 3), "wall_end": round(time.time(), 3),
            "output_sha": sha16(text), "output_chars": len(text),
            "output_tokens": int(tokens), "tokens_estimated": estimated,
            "tok_per_s_wall": round(int(tokens) / latency, 3) if latency > 0 else 0.0,
            "err": "",
        })
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as e:
        row.update({
            "latency_s": round(time.time() - t0, 3),
            "wall_start": round(t0, 3), "wall_end": round(time.time(), 3),
            "output_sha": "", "output_chars": 0, "output_tokens": 0,
            "tok_per_s_wall": 0.0,
            "err": f"{type(e).__name__}: {str(e)[:300]}",
        })
    return row


class InflightRegistry:
    """File-backed in-flight dispatch registry, lock-serialized read-modify-write.

    Fix for the 2026-10-30 arm C double dispatch (SIGMA wave 4 lane 11): two
    concurrent run_arm invocations appended to one results file and re-dispatched
    ids that were in flight in the sibling process, because done_ok only sees
    COMPLETED rows (evidence bytes:
    memory/runs/route1/results_split_DUPTAINTED_20261030.jsonl; receipt:
    watch/inbox/SIGMA_W4_11_DISPATCHER_FIX_20261030.md). Two duties, one lock:
    claim() refuses an id with a live claim or an ok row that landed in the
    ledger after the caller's startup snapshot (checked under the same lock), and
    complete() appends the result row and releases the claim as one atomic step,
    so a racer can never observe "claim free, row not yet written". Claim
    liveness is a timestamp lease only: pid is recorded forensically but never
    trusted for liveness (os.kill liveness is not portable to win32), and an
    expired lease is reclaimable so the resume contract (rerun fills gaps)
    survives a killed dispatcher. All state transitions run under an
    O_CREAT|O_EXCL lock file and land via os.replace, so a reader never sees a
    half-written registry.
    """

    def __init__(self, path: Path, lease_s: float):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.lease_s = lease_s

    def _acquire_lock(self) -> None:
        deadline = time.time() + LOCK_WAIT_S
        while True:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            except FileExistsError:
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                except OSError:
                    age = 0.0  # lock vanished mid-probe: treat as fresh, keep waiting
                if age > LOCK_STALE_S:
                    try:
                        self.lock_path.unlink(missing_ok=True)
                    except OSError as e:
                        # Not silent: takeover failed this pass; the bounded loop
                        # below still fails closed at the deadline.
                        print(f"inflight registry stale-lock takeover deferred "
                              f"({e}): {self.lock_path}", file=sys.stderr)
                    continue
                if time.time() >= deadline:
                    raise SystemExit(
                        f"inflight registry lock busy over {LOCK_WAIT_S}s, refusing "
                        f"uncoordinated dispatch: {self.lock_path}")
                time.sleep(0.05)

    def _release_lock(self) -> None:
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError as e:
            # Not silent: Windows can briefly refuse the unlink while a contender
            # probes the same lock; the stale-takeover path reclaims it.
            print(f"inflight registry lock cleanup deferred ({e}): {self.lock_path}",
                  file=sys.stderr)

    def _update(self, mutate) -> dict:
        """Run mutate(data) under the lock and persist the result atomically."""
        self._acquire_lock()
        try:
            data: dict = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
                except (json.JSONDecodeError, OSError) as e:
                    # Fail closed: dispatching past an unreadable registry is exactly
                    # the double-dispatch this class exists to prevent.
                    raise SystemExit(f"inflight registry unreadable ({e}), refusing "
                                     f"dispatch: {self.path}")
            if not isinstance(data, dict):
                raise SystemExit(f"inflight registry malformed (not a JSON object), "
                                 f"refusing dispatch: {self.path}")
            data = mutate(data)
            tmp = self.path.with_name(f"{self.path.name}.tmp{os.getpid()}"
                                      f"-{threading.get_ident()}")
            tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True),
                           encoding="utf-8")
            os.replace(tmp, self.path)
            return data
        finally:
            self._release_lock()

    def _live(self, claim: dict, now: float) -> bool:
        return (now - float(claim.get("updated", 0.0))) <= self.lease_s

    def claim(self, job_id: str, endpoint: str, arm: str = "",
              is_done=None) -> tuple[bool, str]:
        """Claim job_id for this dispatcher.

        Returns (granted, reason); reason is "claimed", "live-claim" (another
        live dispatch holds it) or "done" (an ok row for job_id landed in the
        results ledger after the caller's startup snapshot, so the caller would
        re-dispatch finished work). is_done(job_id) is called under the lock so
        the ledger check and the claim decision are one atomic step against a
        concurrent dispatcher's complete().
        """
        now = time.time()
        outcome = [False, "claimed"]

        def mutate(data: dict) -> dict:
            if is_done is not None and is_done(job_id):
                outcome[0], outcome[1] = False, "done"
                return data
            held = data.get(job_id)
            if isinstance(held, dict) and self._live(held, now):
                outcome[0], outcome[1] = False, "live-claim"
                return data
            outcome[0] = True
            data[job_id] = {
                "endpoint": endpoint, "arm": arm, "pid": os.getpid(),
                "wall_start": round(now, 3), "updated": round(now, 3),
            }
            return data

        self._update(mutate)
        return outcome[0], outcome[1]

    def complete(self, job_id: str, ledger_append) -> None:
        """Append the result row and release the claim as one atomic step.

        ledger_append (the row write + flush) runs under the registry lock so a
        concurrent claim() can never slide into the gap between "row written"
        and "claim released" and re-dispatch finished work.
        """
        def mutate(data: dict) -> dict:
            ledger_append()
            data.pop(job_id, None)
            return data

        self._update(mutate)

    def touch(self, job_ids: set) -> None:
        """Refresh the lease on this dispatcher's live claims (call per loop pass)."""
        ids = set(job_ids)
        if not ids:
            return
        now = round(time.time(), 3)

        def mutate(data: dict) -> dict:
            for jid in ids:
                if isinstance(data.get(jid), dict):
                    data[jid]["updated"] = now
            return data

        self._update(mutate)

    def live_claims(self) -> dict:
        now = time.time()
        data = self._update(lambda d: d)
        return {k: v for k, v in data.items() if isinstance(v, dict) and self._live(v, now)}
