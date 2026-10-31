"""Verifier for organs/cost-priced/route1_cost.py (route1 results -> fleet cost ledger wire).

Named in the tool's docstring as its verifier (Consumer Law). Covers the
providers.yaml endpoint-map scan, the row conversion math against hand-computed
lane O Wh (desktop 110 W scavenger row, laptop 25 W row), fail-closed skip
handling (unknown endpoint, not-ok, bad latency), the experiment rollup math
(tokens, Wh, $, $/1M per arm / lung / fleet), idempotent append against an
existing ledger, and a CLI smoke test. No em-dashes in any render (estate law).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ORGAN = str(Path(__file__).resolve().parent)
if ORGAN not in sys.path:
    sys.path.insert(0, ORGAN)

import fleet_cost_ledger as fcl  # noqa: E402
import route1_cost as rc  # noqa: E402


def test_script_compiles() -> None:
    import py_compile

    src = Path(__file__).resolve().parent / "route1_cost.py"
    py_compile.compile(str(src), doraise=True)


# ------------------------------------------------------------- endpoint map

PROVIDERS_YAML = """\
# fixture in the shape of fleet/providers.yaml: provider keys at two spaces,
# base_url rows inside the block, plus a commented-out decoy and a base_url_env
# decoy that must not be picked up.
defaults:
  prefer_order:
    - ollama
  ollama_base:
    base_url_env: OLLAMA_HOST
  #   base_url: https://commented.out/v1
  ollama:
    kind: openai_compat
    base_url: http://127.0.0.1:11434/v1
    base_url_env: null
  ollama-bob-2:
    kind: openai_compat
    base_url: http://100.72.138.62:11434/v1
  mlx-cluster:
    base_url: http://127.0.0.1:7777/v1
"""


def test_load_endpoint_map_parses_providers_rows(tmp_path: Path) -> None:
    p = tmp_path / "providers.yaml"
    p.write_text(PROVIDERS_YAML, encoding="utf-8")
    emap = rc.load_endpoint_map(p)
    assert emap == {
        "127.0.0.1:11434": "ollama",
        "100.72.138.62:11434": "ollama-bob-2",
        "127.0.0.1:7777": "mlx-cluster",
    }


def test_load_endpoint_map_real_file_has_both_lungs() -> None:
    """The live fleet/providers.yaml must map both route1 endpoints."""
    real = Path(__file__).resolve().parents[2] / "fleet" / "providers.yaml"
    emap = rc.load_endpoint_map(real)
    assert emap.get("127.0.0.1:11434") == "ollama"
    assert emap.get("100.72.138.62:11434") == "ollama-bob-2"


# ------------------------------------------------------------- conversion

DESKTOP_ROW = {
    "endpoint": "http://127.0.0.1:11434", "model": "gemma:2b", "id": "route1-20261030-00",
    "prompt_sha": "9efe68371ec9af4c", "ok": True, "latency_s": 36.0,
    "wall_start": 1793383352.0, "output_sha": "2cc1d5ca6db42ff4", "output_chars": 146,
    "output_tokens": 45, "tokens_estimated": False, "tok_per_s_wall": 12.608, "err": "",
}
BOB_ROW = {
    "endpoint": "http://100.72.138.62:11434", "model": "gemma:2b", "id": "route1-20261030-00",
    "ok": True, "latency_s": 72.0, "wall_start": 1793383370.31, "output_tokens": 114,
    "tokens_estimated": False,
}


@pytest.fixture()
def emap() -> dict[str, str]:
    return {"127.0.0.1:11434": "ollama", "100.72.138.62:11434": "ollama-bob-2"}


def test_convert_desktop_row_uses_scavenger_watts(emap: dict[str, str]) -> None:
    """Lane O: desktop tower CPU inference is the 110 W scavenger load row."""
    job, reason = rc.convert_row(DESKTOP_ROW, emap, rc.DEFAULT_LUNG_CLASSES, "desktop_r2")
    assert reason is None and job is not None
    assert job["schema"] == fcl.JOB_SCHEMA
    assert job["lung"] == "ollama"
    assert job["class"] == "scavenger"
    assert job["watts"] == 110.0
    assert job["tokens"] == 45.0
    assert job["latency_s"] == 36.0
    assert job["est_wh"] == pytest.approx(110.0 * 36.0 / 3600.0)  # 1.1 Wh
    assert job["ts"] == "2026-10-30T18:02:32Z"  # wall_start epoch, 14:02:32 EDT
    assert job["src"] == "route1:desktop_r2"
    assert job["route1_id"] == "route1-20261030-00"
    assert job["arm"] == "desktop_r2"


def test_convert_bob_row_uses_laptop_watts(emap: dict[str, str]) -> None:
    """Lane O: bob-class T470 laptop is the 25 W inference row."""
    job, reason = rc.convert_row(BOB_ROW, emap, rc.DEFAULT_LUNG_CLASSES, "bob")
    assert reason is None and job is not None
    assert job["lung"] == "ollama-bob-2"
    assert job["class"] == "laptop"
    assert job["watts"] == 25.0
    assert job["est_wh"] == pytest.approx(25.0 * 72.0 / 3600.0)  # 0.5 Wh


def test_convert_row_tokens_fallback_key(emap: dict[str, str]) -> None:
    """The lane brief described rows with a bare `tokens` key; accept either."""
    row = dict(BOB_ROW)
    del row["output_tokens"]
    row["tokens"] = 77
    job, reason = rc.convert_row(row, emap, rc.DEFAULT_LUNG_CLASSES, "bob")
    assert reason is None and job is not None
    assert job["tokens"] == 77.0


def test_convert_row_skips_fail_closed(emap: dict[str, str]) -> None:
    not_ok = dict(DESKTOP_ROW, ok=False, err="connection refused")
    job, reason = rc.convert_row(not_ok, emap, rc.DEFAULT_LUNG_CLASSES, "a")
    assert job is None and reason == "not-ok"

    job, reason = rc.convert_row(dict(DESKTOP_ROW, endpoint="http://10.9.9.9:11434"),
                                 emap, rc.DEFAULT_LUNG_CLASSES, "a")
    assert job is None and reason == "unknown-endpoint:http://10.9.9.9:11434"

    job, reason = rc.convert_row(DESKTOP_ROW, emap, {"ollama-bob-2": "laptop"}, "a")
    assert job is None and reason == "no-class-for-lung:ollama"

    job, reason = rc.convert_row(dict(DESKTOP_ROW, latency_s=0.0), emap,
                                 rc.DEFAULT_LUNG_CLASSES, "a")
    assert job is None and reason == "bad-latency"

    job, reason = rc.convert_row(dict(DESKTOP_ROW, wall_start=None), emap,
                                 rc.DEFAULT_LUNG_CLASSES, "a")
    assert reason is None and job is not None and job["ts"] is not None  # stamped now


def test_arm_name() -> None:
    assert rc.arm_name("memory/runs/route1/results_desktop_r2.jsonl") == "desktop_r2"
    assert rc.arm_name("results_bob.jsonl") == "bob"
    assert rc.arm_name("other.jsonl") == "other"


# ------------------------------------------------------------- rollup

def _jobs() -> list[dict]:
    emap = {"127.0.0.1:11434": "ollama", "100.72.138.62:11434": "ollama-bob-2"}
    d1, _ = rc.convert_row(dict(DESKTOP_ROW, id="d0", latency_s=36.0, output_tokens=100),
                           emap, rc.DEFAULT_LUNG_CLASSES, "desktop_r2")
    d2, _ = rc.convert_row(dict(DESKTOP_ROW, id="d1", latency_s=36.0, output_tokens=100),
                           emap, rc.DEFAULT_LUNG_CLASSES, "desktop_r2")
    b1, _ = rc.convert_row(dict(BOB_ROW, id="b0", latency_s=72.0, output_tokens=50),
                           emap, rc.DEFAULT_LUNG_CLASSES, "bob")
    assert d1 and d2 and b1
    return [d1, d2, b1]


def test_rollup_math_hand_checked() -> None:
    """Desktop 2x36s@110W = 2.2 Wh; bob 1x72s@25W = 0.5 Wh; tariff 0.15."""
    rollup = rc.build_rollup(_jobs(), tariff=0.15)
    by_lung = {l["name"]: l for l in rollup["lungs"]}
    by_arm = {a["name"]: a for a in rollup["arms"]}

    desk = by_lung["ollama"]
    assert desk["jobs"] == 2 and desk["tokens"] == 200.0
    assert desk["wh"] == pytest.approx(2.2, abs=1e-6)
    assert desk["cost_usd"] == pytest.approx(2.2 / 1000.0 * 0.15, abs=1e-9)  # 0.00033
    assert desk["usd_per_m"] == pytest.approx(0.00033 / 200.0 * 1e6, abs=1e-6)  # 1.65

    bob = by_lung["ollama-bob-2"]
    assert bob["jobs"] == 1 and bob["tokens"] == 50.0
    assert bob["wh"] == pytest.approx(0.5, abs=1e-6)
    assert bob["usd_per_m"] == pytest.approx(0.000075 / 50.0 * 1e6, abs=1e-6)  # 1.5

    assert by_arm["desktop_r2"]["wh"] == pytest.approx(2.2, abs=1e-6)
    assert by_arm["bob"]["wh"] == pytest.approx(0.5, abs=1e-6)

    fleet = rollup["fleet"]
    assert fleet["jobs"] == 3 and fleet["tokens"] == 250.0
    assert fleet["wh"] == pytest.approx(2.7, abs=1e-6)
    assert fleet["cost_usd"] == pytest.approx(2.7 / 1000.0 * 0.15, abs=1e-9)  # 0.000405
    assert fleet["usd_per_m"] == pytest.approx(0.000405 / 250.0 * 1e6, abs=1e-6)  # 1.62
    assert rollup["schema"] == "route1_cost_rollup.v1"


def test_rollup_zero_tokens_is_none_not_crash() -> None:
    rows = [dict(rc.convert_row(dict(BOB_ROW, output_tokens=0),
                                {"100.72.138.62:11434": "ollama-bob-2"},
                                rc.DEFAULT_LUNG_CLASSES, "bob")[0])]
    rollup = rc.build_rollup(rows, tariff=0.15)
    assert rollup["lungs"][0]["usd_per_m"] is None
    assert rollup["fleet"]["usd_per_m"] is None


# ------------------------------------------------------------- ledger wire

def test_append_is_idempotent(tmp_path: Path) -> None:
    """A second run over the same results must not double-book the watts."""
    ledger = tmp_path / "jobs.jsonl"
    results = tmp_path / "results_bob.jsonl"
    results.write_text(json.dumps(BOB_ROW) + "\n", encoding="utf-8")
    emap = {"100.72.138.62:11434": "ollama-bob-2"}

    first, _ = rc.convert_row(BOB_ROW, emap, rc.DEFAULT_LUNG_CLASSES, "bob")
    assert first is not None
    fcl.append_row(first, ledger)
    assert rc.load_existing_keys(ledger) == {("route1:bob", "route1-20261030-00")}

    existing = rc.load_existing_keys(ledger)
    job, _ = rc.convert_row(BOB_ROW, emap, rc.DEFAULT_LUNG_CLASSES, "bob")
    assert job is not None
    dup = (job["src"], job.get("route1_id")) in existing
    assert dup is True


def test_fleet_ledger_reads_appended_rows(tmp_path: Path) -> None:
    """The wire's whole point: fcl.load_jobs parses the appended rows back."""
    ledger = tmp_path / "jobs.jsonl"
    emap = {"127.0.0.1:11434": "ollama", "100.72.138.62:11434": "ollama-bob-2"}
    for row in _jobs():
        fcl.append_row(row, ledger)
    parsed, skipped = fcl.load_jobs(ledger)
    assert skipped == 0 and len(parsed) == 3
    assert {r["lung"] for r in parsed} == {"ollama", "ollama-bob-2"}


# ------------------------------------------------------------- CLI smoke

def _write_results(tmp_path: Path) -> Path:
    results = tmp_path / "results_split.jsonl"
    rows = [DESKTOP_ROW,
            dict(BOB_ROW, id="route1-20261030-01"),
            dict(DESKTOP_ROW, id="route1-20261030-02", ok=False, err="boom"),
            dict(DESKTOP_ROW, id="route1-20261030-03", endpoint="http://10.9.9.9:11434")]
    results.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return results


def test_cli_converts_appends_and_rolls_up(tmp_path: Path) -> None:
    results = _write_results(tmp_path)
    (tmp_path / "providers.yaml").write_text(PROVIDERS_YAML, encoding="utf-8")
    ledger = tmp_path / "jobs.jsonl"
    script = Path(ORGAN) / "route1_cost.py"
    cmd = [sys.executable, str(script),
           "--results", str(results),
           "--providers", str(tmp_path / "providers.yaml"),
           "--ledger", str(ledger), "--tariff", "0.15"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "ROUTE-1 COST WIRE" in out
    assert "FLEET (experiment)" in out
    # 2 ok convertible rows appended; 1 not-ok + 1 unknown endpoint skipped
    assert "not-ok x1" in out
    assert "unknown-endpoint:http://10.9.9.9:11434 x1" in out
    ledger_rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(ledger_rows) == 2
    assert all(r["schema"] == "fleet_cost_job.v1" for r in ledger_rows)

    # rerun: both rows already present, nothing re-appended
    proc2 = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert proc2.returncode == 0, proc2.stderr
    assert "0 rows appended, 2 already present" in proc2.stdout
    ledger_rows2 = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(ledger_rows2) == 2


def test_cli_json_mode(tmp_path: Path) -> None:
    results = _write_results(tmp_path)
    (tmp_path / "providers.yaml").write_text(PROVIDERS_YAML, encoding="utf-8")
    script = Path(ORGAN) / "route1_cost.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--results", str(results), "--dry", "--json",
         "--providers", str(tmp_path / "providers.yaml"),
         "--ledger", str(tmp_path / "never.jsonl")],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    rollup = json.loads(proc.stdout)
    assert rollup["schema"] == "route1_cost_rollup.v1"
    assert rollup["appended"] == 0 and rollup["already_present"] == 0
    assert rollup["fleet"]["jobs"] == 2
    # split fixture: desktop 36s@110W = 1.1 Wh, bob 72s@25W = 0.5 Wh -> 1.6 Wh
    assert rollup["fleet"]["wh"] == pytest.approx(1.6, abs=1e-6)
    assert not (tmp_path / "never.jsonl").exists()  # --dry appended nothing


def test_cli_rejects_bad_class_and_tariff(tmp_path: Path) -> None:
    results = _write_results(tmp_path)
    (tmp_path / "providers.yaml").write_text(PROVIDERS_YAML, encoding="utf-8")
    script = Path(ORGAN) / "route1_cost.py"
    bad_class = subprocess.run(
        [sys.executable, str(script), "--results", str(results), "--dry",
         "--providers", str(tmp_path / "providers.yaml"),
         "--ledger", str(tmp_path / "j.jsonl"), "--class", "ollama=mainframe"],
        capture_output=True, text=True, timeout=60)
    assert bad_class.returncode == 2 and "unknown class" in bad_class.stderr

    bad_tariff = subprocess.run(
        [sys.executable, str(script), "--results", str(results), "--dry", "--tariff", "0",
         "--providers", str(tmp_path / "providers.yaml"),
         "--ledger", str(tmp_path / "j.jsonl")],
        capture_output=True, text=True, timeout=60)
    assert bad_tariff.returncode == 2 and "tariff" in bad_tariff.stderr


def test_render_has_no_em_dashes() -> None:
    rollup = rc.build_rollup(_jobs(), tariff=0.15)
    text = rc.render_report(rollup, 3, 0, {"not-ok": 1}, ["memory/runs/route1/results_bob.jsonl"])
    assert "\u2014" not in text and "\u2013" not in text
    assert "lane O" in text
