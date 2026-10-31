"""Verifier for the route1 dispatcher strict-once fix (organs/dispatch-exactly-once).

Named in the tool's docstring as the dispatch-law verifier (Consumer Law).
Covers the founding regression (two concurrent run_arm invocations appending to
one results file must never double-dispatch an id: the 2026-10-30 arm C taint
preserved at memory/runs/route1/results_split_DUPTAINTED_20261030.jsonl, 55 rows
over 30 ids), the single-invocation refill loop terminating at exactly N ok rows
with the registry drained, InflightRegistry claim/lease/release semantics
including stale-claim resume, and the --strict-once default-on parser surface.
Fake ollama endpoints are in-process http.server threads; no network, no GPU.

Receipt: watch/inbox/SIGMA_W4_11_DISPATCHER_FIX_20261030.md.
"""
from __future__ import annotations

import json
import py_compile
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ORGAN = str(Path(__file__).resolve().parent)
if ORGAN not in sys.path:
    sys.path.insert(0, ORGAN)

import route1_queue as rq  # noqa: E402


def test_script_compiles() -> None:
    root = Path(__file__).resolve().parent
    for name in ("route1_queue.py", "route1_corpus.py", "route1_dispatch.py"):
        py_compile.compile(str(root / name), doraise=True)


# ----------------------------------------------------------------- fake ollama


class _FakeOllama(BaseHTTPRequestHandler):
    delay = 0.02

    def log_message(self, *args):  # keep pytest output clean
        pass

    def _reply(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # /api/tags probe
        self._reply(b'{"models": []}')

    def do_POST(self):  # /api/generate
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        time.sleep(self.delay)
        body = json.dumps({
            "response": "alpha beta gamma delta echo foxtrot",
            "eval_count": 8,
            "prompt_eval_count": 4,
        }).encode("utf-8")
        self._reply(body)


@contextmanager
def fake_endpoint(delay: float):
    handler = type("Handler", (_FakeOllama,), {"delay": delay})
    # request_queue_size 5 (socketserver default) can refuse a burst when both
    # dispatchers hammer the fast endpoint at once; a refusal would surface as a
    # FAIL row and fake a dispatch-law violation, so widen the accept backlog.
    server = type("Server", (ThreadingHTTPServer,), {"request_queue_size": 64})(
        ("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ------------------------------------------------------------------- fixtures


def write_synthetic_queue(path: Path, n: int) -> None:
    rows = []
    for i in range(n):
        prompt = f"Summarize passage {i}: " + ("lorem ipsum dolor sit " * 40)
        rows.append({
            "id": f"route1-test-{i:03d}",
            "source": f"synthetic/{i}.txt",
            "seed": 1,
            "chars": len(prompt),
            "text_sha": rq.sha16(prompt),
            "prompt_sha": rq.sha16(prompt),
            "prompt": prompt,
        })
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def make_args(tmp_path: Path, endpoints: list[str], n: int = 8, extra=()):
    queue = tmp_path / "queue.jsonl"
    write_synthetic_queue(queue, n)
    results = tmp_path / "results_split.jsonl"
    argv = ["--run", "--arm", "split", "--out", str(queue), "--results", str(results),
            "--timeout", "30", "--num-predict", "16"]
    for ep in endpoints:
        argv += ["--endpoint", ep]
    argv += list(extra)
    return rq.build_parser().parse_args(argv), results


def read_rows(results: Path) -> list[dict]:
    return [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ----------------------------------------------------------------- dispatch law


def test_strict_once_default_on_and_off_switch() -> None:
    parser = rq.build_parser()
    assert parser.parse_args([]).strict_once is True
    assert parser.parse_args(["--strict-once"]).strict_once is True
    assert parser.parse_args(["--no-strict-once"]).strict_once is False


def test_inflight_registry_claim_lease_release(tmp_path) -> None:
    reg = rq.InflightRegistry(tmp_path / "inflight.json", lease_s=0.2)
    assert reg.claim("job-a", "http://ep-a", "split") == (True, "claimed")
    assert reg.claim("job-a", "http://ep-b", "split") == (False, "live-claim")
    reg.touch({"job-a"})
    stored = json.loads((tmp_path / "inflight.json").read_text(encoding="utf-8"))
    assert stored["job-a"]["endpoint"] == "http://ep-a"
    assert stored["job-a"]["updated"] > 0
    # expired lease is reclaimable: a killed dispatcher must not block a resume forever
    time.sleep(0.3)
    assert reg.claim("job-a", "http://ep-c", "split") == (True, "claimed")
    reg.complete("job-a", lambda: None)  # row append + release is one atomic step
    assert reg.claim("job-a", "http://ep-a", "split") == (True, "claimed")
    assert reg.live_claims().keys() == {"job-a"}


def test_claim_done_branch_blocks_redispatch_of_completed_work(tmp_path) -> None:
    # A concurrent dispatcher completed the id after our startup snapshot: the ledger
    # check inside claim() must win over the (released) claim, else the caller would
    # re-dispatch finished work (the second half of the founding taint).
    reg = rq.InflightRegistry(tmp_path / "inflight.json", lease_s=60.0)
    ledger: list[dict] = []
    assert reg.claim("job-x", "http://ep-a", "split") == (True, "claimed")
    assert reg.claim("job-x", "http://ep-b", "split", is_done=lambda jid: False) \
        == (False, "live-claim")
    reg.complete("job-x", lambda: ledger.append({"id": "job-x", "ok": True}))
    assert len(ledger) == 1
    assert reg.claim("job-x", "http://ep-b", "split", is_done=lambda jid: True) \
        == (False, "done")
    assert reg.claim("job-x", "http://ep-b", "split") == (True, "claimed")


def test_stale_claim_does_not_block_resume(tmp_path) -> None:
    with fake_endpoint(0.01) as ep:
        args, results = make_args(tmp_path, [ep], n=2)
        reg_path = results.parent / (results.name + ".inflight.json")
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        stale_ts = time.time() - 10_000.0  # far past the timeout+60 lease
        reg_path.write_text(json.dumps(
            {"route1-test-000": {"endpoint": ep, "updated": stale_ts, "pid": 1,
                                 "arm": "ghost"}}), encoding="utf-8")
        rc = rq.run_arm(args)
        rows = read_rows(results)
    assert rc == 0
    assert len(rows) == 2
    assert {r["id"] for r in rows} == {"route1-test-000", "route1-test-001"}
    assert all(r["ok"] for r in rows)


def test_no_strict_once_is_the_legacy_path(tmp_path) -> None:
    # The escape hatch for deliberate replays: no registry file, no claim checks,
    # byte-identical behavior to the pre-fix dispatcher.
    with fake_endpoint(0.01) as ep:
        args, results = make_args(tmp_path, [ep], n=4,
                                  extra=["--no-strict-once"])
        assert args.strict_once is False
        rc = rq.run_arm(args)
        rows = read_rows(results)
    assert rc == 0
    assert len(rows) == 4
    assert len({r["id"] for r in rows}) == 4
    assert not (results.parent / (results.name + ".inflight.json")).exists()


def test_single_invocation_terminates_at_exactly_n_ok_rows(tmp_path) -> None:
    with fake_endpoint(0.01) as ep:
        args, results = make_args(tmp_path, [ep], n=30, extra=["--slots", "127.0.0.1=4"])
        rc = rq.run_arm(args)
        rows = read_rows(results)
        reg_path = results.parent / (results.name + ".inflight.json")
        registry = json.loads(reg_path.read_text(encoding="utf-8"))
    assert rc == 0
    assert len(rows) == 30
    assert len({r["id"] for r in rows}) == 30
    assert all(r["ok"] for r in rows)
    assert registry == {}  # every claim released: a job completes exactly once


def test_two_concurrent_dispatchers_never_double_dispatch(tmp_path) -> None:
    # The founding taint, replayed on fake endpoints: invocation A starts, a second
    # invocation B opens the same results file while A still has ids in flight on the
    # slow endpoint. Old behavior re-dispatched them (55 rows over 30 ids); strict-once
    # defers them, so the union of both ledgers holds each id exactly once.
    with fake_endpoint(0.02) as fast, fake_endpoint(2.5) as slow:
        args_a, results = make_args(tmp_path, [fast, slow], n=8)
        args_b, _ = make_args(tmp_path, [fast, slow], n=8)
        out: dict = {}

        def run_a() -> None:
            out["rc_a"] = rq.run_arm(args_a)

        thread_a = threading.Thread(target=run_a)
        thread_a.start()
        time.sleep(0.15)  # B joins mid-flight of A
        rc_b = rq.run_arm(args_b)
        thread_a.join(timeout=60)
        rows = read_rows(results)
        reg_path = results.parent / (results.name + ".inflight.json")
        registry = json.loads(reg_path.read_text(encoding="utf-8"))
    assert not thread_a.is_alive()
    assert out["rc_a"] == 0
    assert rc_b == 0
    assert len(rows) == 8
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == 8  # the law: no id dispatches twice
    assert all(r["ok"] for r in rows)
    assert registry == {}  # all claims released by both dispatchers
