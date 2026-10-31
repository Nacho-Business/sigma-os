#!/usr/bin/env python3
"""test_retention.py - verifier for the retention-bounded organ (SIGMA wave 2 lane 16).

Consumer: the artifacts this file verifies are the retention surface shipped
  in this organ:
  systemd/mag-log-smith.service + .timer, systemd/mag-scan-smith.service + .timer,
  install_retention.sh (idempotent user-unit installer),
  log_smith.sh, scan_smith.sh (dry-run-first sweep scripts),
  room_rotate.py + test_room_rotate.py (the writer-seam bound).
  Design source: watch/inbox/SIGMA_BURN_LANE_T_OPS_20261030.md section (b).
  The estate repo carries the full five-timer board (disk-smith, heartbeat,
  gate-weekly included); this release ships the two repo-sweep timers.
Verifier: this file (pytest). Asserts every unit parses as INI, every timer
  carries OnCalendar, the installer and sweep scripts pass bash -n, and the
  dry-run-vs-apply flag discipline of log_smith / scan_smith holds on real runs.
Run: python -m pytest organs/retention-bounded/test_retention.py -q
"""
from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ORGAN = Path(__file__).resolve().parent
UNIT_DIR = ORGAN / "systemd"
INSTALL = ORGAN / "install_retention.sh"
SCRIPTS = {
    "log_smith": ORGAN / "log_smith.sh",
    "scan_smith": ORGAN / "scan_smith.sh",
}

EXPECTED_UNITS = {"log-smith", "scan-smith"}
PERSISTENT_TIMERS = {"log-smith", "scan-smith"}

BASH = shutil.which("bash")


def unit_path(name: str, kind: str) -> Path:
    return UNIT_DIR / f"mag-{name}.{kind}"


def parse_unit(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # systemd keys are case-sensitive; keep OnCalendar as written
    cp.read_string(path.read_text(encoding="utf-8"))
    return cp


def run_sh(script: Path, args: list[str], env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_extra)
    return subprocess.run(
        [BASH, str(script), *args], env=env, capture_output=True, text=True, timeout=120
    )


def age_file(path: Path, days: float) -> None:
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_unit_pair_inventory() -> None:
    found = {p.name for p in UNIT_DIR.glob("mag-*")}
    expected = {f"mag-{n}.{k}" for n in EXPECTED_UNITS for k in ("service", "timer")}
    assert found == expected, f"unit inventory drift: extra={found - expected} missing={expected - found}"


def test_every_unit_parses_as_ini() -> None:
    for path in sorted(UNIT_DIR.glob("mag-*")):
        cp = parse_unit(path)
        assert len(cp.sections()) >= 1, f"{path.name}: no sections parsed"
        assert cp.has_section("Unit"), f"{path.name}: missing [Unit] section"


def test_every_service_names_retention_bound() -> None:
    # Lane T law: every instrument names its own bound; what survives vs what ages.
    for name in sorted(EXPECTED_UNITS):
        cp = parse_unit(unit_path(name, "service"))
        unit = cp["Unit"]
        assert "Description" in unit, f"mag-{name}.service: no Description"
        comment = unit.get("Comment", "")
        assert "retention" in comment.lower(), f"mag-{name}.service: Comment does not name its retention bound"


def test_every_timer_has_oncalendar() -> None:
    for name in sorted(EXPECTED_UNITS):
        cp = parse_unit(unit_path(name, "timer"))
        assert cp.has_section("Timer"), f"mag-{name}.timer: missing [Timer]"
        assert "OnCalendar" in cp["Timer"], f"mag-{name}.timer: missing OnCalendar"


def test_timer_persistence_discipline() -> None:
    # Both shipped sweeps are daily/weekly catch-up jobs, so both are Persistent.
    for name in sorted(EXPECTED_UNITS):
        cp = parse_unit(unit_path(name, "timer"))
        persistent = cp["Timer"].get("Persistent", "").strip().lower() == "true"
        if name in PERSISTENT_TIMERS:
            assert persistent, f"mag-{name}.timer: daily/weekly sweep must be Persistent=true"


def test_timer_wires_its_service() -> None:
    for name in sorted(EXPECTED_UNITS):
        cp = parse_unit(unit_path(name, "timer"))
        assert cp["Timer"].get("Unit") == f"mag-{name}.service", f"mag-{name}.timer: wrong Unit= wiring"


def test_services_use_repo_root_placeholder_and_installer_substitutes_it() -> None:
    for name in sorted(EXPECTED_UNITS):
        cp = parse_unit(unit_path(name, "service"))
        exec_start = cp["Service"]["ExecStart"]
        workdir = cp["Service"].get("WorkingDirectory", "")
        # Either the ExecStart or the WorkingDirectory must carry the placeholder, so the
        # installer pins every service to this repo's absolute path.
        assert "@REPO_ROOT@" in (exec_start + " " + workdir), (
            f"mag-{name}.service: no @REPO_ROOT@ placeholder in ExecStart or WorkingDirectory"
        )
    install_text = INSTALL.read_text(encoding="utf-8")
    assert "s|@REPO_ROOT@|" in install_text, "install script does not substitute @REPO_ROOT@"


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_bash_syntax_installer_and_scripts() -> None:
    targets = [INSTALL, *SCRIPTS.values()]
    for path in targets:
        proc = subprocess.run([BASH, "-n", str(path)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"bash -n failed for {path.name}: {proc.stderr}"


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_log_smith_dry_run_vs_apply(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    fresh = logs / "fresh.txt"
    mid = logs / "mid.txt"
    old = logs / "old.txt"
    fresh.write_text("new", encoding="utf-8")
    mid.write_text("x" * 256, encoding="utf-8")
    old.write_text("y" * 256, encoding="utf-8")
    age_file(mid, 20)  # inside compress radius (>14d), outside delete radius (<30d)
    age_file(old, 60)  # past delete radius (>30d)

    env = {"LOG_SMITH_DIR": logs.as_posix()}  # bash-side paths print forward-slash style

    # Dry-run: lists both radii, mutates nothing, writes no receipt.
    dry = run_sh(SCRIPTS["log_smith"], [], env)
    assert dry.returncode == 0, dry.stderr
    assert f"would compress: {mid.as_posix()}" in dry.stdout
    assert f"would delete: {old.as_posix()}" in dry.stdout
    assert mid.exists() and old.exists() and fresh.exists()
    assert not (logs / "log_smith.jsonl").exists(), "dry-run must not write a receipt"

    # Apply: compress radius fires, delete radius fires, delete wins over compress past 30d.
    apply = run_sh(SCRIPTS["log_smith"], ["--apply"], env)
    assert apply.returncode == 0, apply.stderr
    assert not mid.exists() and (logs / "mid.txt.gz").exists()
    assert not old.exists() and not (logs / "old.txt.gz").exists(), "delete radius must win past 30d"
    assert fresh.exists(), "fresh file must never be touched"

    rows = (logs / "log_smith.jsonl").read_text(encoding="utf-8").strip().splitlines()
    row = json.loads(rows[-1])
    assert row["kind"] == "log_smith" and row["mode"] == "apply"
    assert row["compressed"] == 1 and row["deleted"] == 1


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_log_smith_receipt_is_bounded(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    receipt = logs / "log_smith.jsonl"
    receipt.write_text("\n".join('{"pad":%d}' % i for i in range(2050)) + "\n", encoding="utf-8")
    proc = run_sh(SCRIPTS["log_smith"], ["--apply"], {"LOG_SMITH_DIR": str(logs)})
    assert proc.returncode == 0, proc.stderr
    rows = receipt.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 2000, f"receipt bound broken: {len(rows)} rows"
    assert json.loads(rows[-1])["kind"] == "log_smith"


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_scan_smith_dry_run_vs_apply_and_keep_side(tmp_path: Path) -> None:
    mimosa = tmp_path / ".mimosa"
    (mimosa / "history").mkdir(parents=True)
    (mimosa / "finding-ledger").mkdir()
    (mimosa / "reports").mkdir()
    cache_old = mimosa / "history" / "run-2026.json"
    finding = mimosa / "finding-ledger" / "findings.json"
    report = mimosa / "reports" / "report.json"
    for p in (cache_old, finding, report):
        p.write_text("z" * 256, encoding="utf-8")
        age_file(p, 60)

    env = {"MIMOSA_DIR": mimosa.as_posix(), "SCAN_SMITH_LOG_DIR": (tmp_path / "logs").as_posix()}

    # Dry-run: lists what would age, mutates nothing.
    dry = run_sh(SCRIPTS["scan_smith"], [], env)
    assert dry.returncode == 0, dry.stderr
    assert f"would age: {cache_old.as_posix()}" in dry.stdout
    assert "keep side untouched" in dry.stdout
    assert cache_old.exists() and not cache_old.with_suffix(".json.gz").exists()

    # Apply: cache ages (gzip in place), findings and reports survive untouched.
    apply = run_sh(SCRIPTS["scan_smith"], ["--apply"], env)
    assert apply.returncode == 0, apply.stderr
    assert not cache_old.exists() and (mimosa / "history" / "run-2026.json.gz").exists()
    assert finding.exists() and report.exists(), "keep side (findings, reports) must never be aged"

    receipt = tmp_path / "logs" / "scan_smith.jsonl"
    row = json.loads(receipt.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["kind"] == "scan_smith" and row["mode"] == "apply" and row["aged"] == 1


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_scan_smith_refuses_keep_side_in_cache_list(tmp_path: Path) -> None:
    mimosa = tmp_path / ".mimosa"
    (mimosa / "finding-ledger").mkdir(parents=True)
    proc = run_sh(
        SCRIPTS["scan_smith"],
        [],
        {"MIMOSA_DIR": str(mimosa), "MIMOSA_CACHE_DIRS": "history finding-ledger"},
    )
    assert proc.returncode == 2, "naming a keep-side dir as cache must refuse with exit 2"


def test_no_em_dash_in_lane_artifacts() -> None:
    paths = [*UNIT_DIR.glob("mag-*"), INSTALL, *SCRIPTS.values()]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "\u2014" not in text, f"{path.name}: em-dash present"
        assert "\u2013" not in text, f"{path.name}: en-dash present"
