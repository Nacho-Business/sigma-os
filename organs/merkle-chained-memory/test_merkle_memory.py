"""Verifier for the merkle-chained-memory organ.

Consumer Law: named in knot_core.py, checkpoint.py and verkle_verify.py as
their verifier. Covers, end to end on a synthetic chain under tmp_path:
mint-verify round trip (checkpoint.mint -> verkle_verify exit 0), tamper
fail-closed (one flipped leaf hash -> exit 1), the mint gate refusing to
extend a tampered chain, proof round trip (--prove -> --proof), tampered
proof exit 1, missing chain exit 2 cannot-run, the tip census (status), and
the MAG_POSITION_BOUND_LEAVES leaf-binding flag.

Subprocess-only for the CLI faces: this file never imports the modules in a
way that could retarget a live chain; the estate's prepush_verkle_hermeticity
guard exists because unretargeted verkle globals once let a pytest run
overwrite a live chain. The library face (checkpoint.mint) runs in-process
but only ever against tmp_path roots.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ORGAN = Path(__file__).resolve().parent
CHECKPOINT = ORGAN / "checkpoint.py"
VERIFY = ORGAN / "verkle_verify.py"


def _run(tool: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(tool), *argv], capture_output=True, text=True, timeout=120
    )


def _last_json(text: str) -> dict:
    return json.loads(text.strip().splitlines()[-1])


def _mint(tmp_path: Path, label: str, payload: str) -> dict:
    proc = _run(CHECKPOINT, "mint", "--label", label, "--payload", payload,
                "--root", str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _last_json(proc.stdout)


def test_scripts_compile() -> None:
    import py_compile

    for src in (ORGAN / "knot_core.py", CHECKPOINT, VERIFY):
        py_compile.compile(str(src), doraise=True)


def test_mint_then_verify_round_trip(tmp_path: Path) -> None:
    for i in range(3):
        verdict = _mint(tmp_path, f"seal-{i}", f"payload {i}")
        assert verdict["ok"] is True
        assert verdict["minted_index"] == i
        assert verdict["n_leaves"] == i + 1
    proc = _run(VERIFY, "--root", str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    v = _last_json(proc.stdout)
    assert v["ok"] is True
    assert v["n_leaves"] == 3


def test_tampered_leaf_fails_verify_closed(tmp_path: Path) -> None:
    for i in range(2):
        _mint(tmp_path, f"seal-{i}", f"payload {i}")
    chain = tmp_path / "var" / "biography" / "verkle_chain.jsonl"
    lines = chain.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    first = row["leaf_hash"][0]
    row["leaf_hash"] = ("0" if first != "0" else "1") + row["leaf_hash"][1:]
    lines[0] = json.dumps(row)
    chain.write_text("\n".join(lines) + "\n", encoding="utf-8")
    proc = _run(VERIFY, "--root", str(tmp_path))
    assert proc.returncode == 1
    assert _last_json(proc.stdout)["ok"] is False


def test_mint_gate_refuses_tampered_chain(tmp_path: Path) -> None:
    _mint(tmp_path, "honest", "payload")
    chain = tmp_path / "var" / "biography" / "verkle_chain.jsonl"
    lines = chain.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["leaf_hash"] = "f" * 64
    lines[0] = json.dumps(row)
    chain.write_text("\n".join(lines) + "\n", encoding="utf-8")
    proc = _run(CHECKPOINT, "mint", "--label", "liar", "--payload", "x", "--root", str(tmp_path))
    assert proc.returncode == 1
    verdict = _last_json(proc.stdout)
    assert verdict["ok"] is False
    assert "gate refused" in verdict["error"]
    # nothing appended: the chain still has exactly one row
    assert len(chain.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_proof_round_trip(tmp_path: Path) -> None:
    for i in range(5):
        _mint(tmp_path, f"seal-{i}", f"payload {i}")
    made = _run(VERIFY, "--prove", "2", "--root", str(tmp_path))
    assert made.returncode == 0, made.stdout + made.stderr
    proof = _last_json(made.stdout)
    assert proof["ok"] is True and proof["verified"] is True
    proof_file = tmp_path / "proof.json"
    proof_file.write_text(json.dumps(proof), encoding="utf-8")
    checked = _run(VERIFY, "--proof", str(proof_file), "--root", str(tmp_path))
    assert checked.returncode == 0, checked.stdout + checked.stderr
    verdict = _last_json(checked.stdout)
    assert verdict["ok"] is True and verdict["proof_ok"] is True
    assert verdict["proof_index"] == 2


def test_tampered_proof_fails_closed(tmp_path: Path) -> None:
    for i in range(4):
        _mint(tmp_path, f"seal-{i}", f"payload {i}")
    made = _run(VERIFY, "--prove", "0", "--root", str(tmp_path))
    assert made.returncode == 0
    proof = _last_json(made.stdout)
    first = proof["path"][0][0]
    proof["path"][0] = "0" * 64 if first != "0" else "1" * 64
    proof_file = tmp_path / "bad_proof.json"
    proof_file.write_text(json.dumps(proof), encoding="utf-8")
    proc = _run(VERIFY, "--proof", str(proof_file), "--root", str(tmp_path))
    assert proc.returncode == 1
    verdict = _last_json(proc.stdout)
    assert verdict["ok"] is False and verdict["proof_ok"] is False


def test_missing_chain_cannot_run(tmp_path: Path) -> None:
    proc = _run(VERIFY, "--root", str(tmp_path))
    assert proc.returncode == 2
    assert _last_json(proc.stdout)["ok"] is False


def test_status_reports_tip_census(tmp_path: Path) -> None:
    _mint(tmp_path, "one", "a")
    _mint(tmp_path, "two", "b")
    proc = _run(CHECKPOINT, "status", "--root", str(tmp_path))
    assert proc.returncode == 0
    st = _last_json(proc.stdout)
    assert st["ok"] is True and st["n_leaves"] == 2
    assert st["tip_n_leaves"] == 2 and st["tip_root"] == st.get("tip_root")


def test_mint_refuses_empty_label_and_missing_payload(tmp_path: Path) -> None:
    proc = _run(CHECKPOINT, "mint", "--label", "  ", "--payload", "x", "--root", str(tmp_path))
    assert proc.returncode == 1
    proc = _run(CHECKPOINT, "mint", "--label", "no-payload", "--root", str(tmp_path))
    assert proc.returncode == 1


def test_position_bound_flag_binds_leaf_index(tmp_path: Path, monkeypatch) -> None:
    # With MAG_POSITION_BOUND_LEAVES the leaf hash binds its index, so the
    # duplicate-last padding no longer lets one root cover two leaf counts.
    sys.path.insert(0, str(ORGAN))
    import knot_core as kc

    monkeypatch.setenv("MAG_POSITION_BOUND_LEAVES", "1")
    obj = {"a": 1}
    h_plain = kc._leaf_hash(obj)
    h_bound = kc._leaf_hash(obj, index=0)
    assert h_plain != h_bound
    assert h_bound == kc._leaf_hash(obj, index=0)
    monkeypatch.delenv("MAG_POSITION_BOUND_LEAVES")
    assert kc._leaf_hash(obj, index=0) == h_plain
