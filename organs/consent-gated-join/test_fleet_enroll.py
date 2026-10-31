"""Verifiers for fleet enrollment (organs/consent-gated-join/fleet_enroll.py): proof-gate refusal,
yaml insertion integrity, anchor preservation, idempotent refusal on
re-enrollment, plus the SIGMA wave-2 fences (live re-probe, name squat,
SSRF IP fence, model charset fence, proof-model-verbatim default_model) and
the cost-ledger producer (ractor_engine 2026-10-30 repair step 3): a proven
enrollment lands exactly one fleet_cost_job.v1 row, NO PROOF OF GENERATION
leaves no row, a refusal or no-op appends nothing.
The introducer's own verifier lives in test_mag_introduce.py (same organ).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

organ = str(Path(__file__).resolve().parent)
if organ not in __import__("sys").path:
    __import__("sys").path.insert(0, organ)

import fleet_enroll  # noqa: E402


@pytest.fixture
def providers_fixture(tmp_path: Path) -> Path:
    # ollama-bob holds a DIFFERENT endpoint IP than the test card so the plain
    # insertion path is exercised; the cross-name same-IP update-in-place path
    # has its own fixture in test_cross_name_same_ip_updates_in_place.
    fixture = tmp_path / "providers.yaml"
    fixture.write_text(
        "providers:\n"
        "  ollama:\n"
        "    name: Ollama (local)\n"
        "    kind: openai_compat\n"
        "    base_url: http://127.0.0.1:11434/v1\n"
        "  ollama-bob:\n"
        "    name: Ollama bob\n"
        "    base_url: http://100.72.138.61:11434/v1\n"
        "  mlx-cluster:\n"
        "    name: stub\n"
        "    kind: openai_compat\n",
        encoding="utf-8",
    )
    return fixture


def _good_card() -> dict:
    return {
        "kind": "mag_lung_introduction.v1",
        "hostname": "aspire",
        "tailscale_ip": "100.72.138.62",
        "ollama": {"version": "0.40.13", "models": ["gemma:2b", "deepseek-r1:1.5b"]},
        "generation": {"ok": True, "model": "gemma:2b", "seconds": 91.2, "reply": "READY"},
    }


def _live_stub(card: dict):
    """Prober stub serving exactly the card's declared models (network-free)."""
    models = [m for m in card.get("ollama", {}).get("models", []) if m]
    return lambda ip: models


def test_enrollment_refused_without_generation_proof(providers_fixture: Path) -> None:
    card = _good_card()
    card["generation"] = {"ok": False, "error": "skipped"}
    with pytest.raises(ValueError, match="NO PROOF OF GENERATION"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture)


def test_enrollment_refused_off_tailnet(providers_fixture: Path) -> None:
    card = _good_card()
    card["tailscale_ip"] = "not on tailnet"
    with pytest.raises(ValueError, match="tailnet"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture)


def test_enrollment_inserts_parsed_row_and_preserves_anchor(providers_fixture: Path) -> None:
    card = _good_card()
    name = fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=_live_stub(card))
    assert name == "ollama-aspire"
    text = providers_fixture.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    row = parsed["providers"][name]
    assert row["base_url"] == "http://100.72.138.62:11434/v1"
    assert row["default_model"].startswith("gemma")
    assert "  mlx-cluster:" in text  # anchor preserved
    assert text.index("  ollama-bob:") < text.index(f"  {name}:") < text.index("  mlx-cluster:")  # inserted before anchor


def test_re_enrollment_is_refused(providers_fixture: Path) -> None:
    card = _good_card()
    fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=_live_stub(card))
    with pytest.raises(ValueError, match="already present"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=_live_stub(card))


# --- SIGMA wave 2 fences (attacks A5/A6/A7/A8, lane 03 2026-10-30) ---


@pytest.mark.parametrize(
    "bad_ip",
    ["169.254.169.254", "8.8.8.8", "10.1.2.3.9", "example.com", "100.63.255.255", "100.128.0.1", "fd00::1"],
)
def test_bad_ip_refused_by_ssrf_fence(providers_fixture: Path, bad_ip: str) -> None:
    card = _good_card()
    card["tailscale_ip"] = bad_ip
    with pytest.raises(ValueError, match="SSRF fence"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=_live_stub(card))
    assert "ollama-aspire" not in providers_fixture.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "bad_model",
    ["gemma:2b\n    injected-row: boom", "gemma:2b # trailing-comment", "gemma 2b", '"quoted"'],
)
def test_injection_string_refused_by_model_charset_fence(providers_fixture: Path, bad_model: str) -> None:
    card = _good_card()
    card["ollama"]["models"] = ["gemma:2b", bad_model]
    with pytest.raises(ValueError, match="charset validation"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=_live_stub(card))


def test_proof_model_not_in_declared_list_refused(providers_fixture: Path) -> None:
    card = _good_card()
    card["generation"]["model"] = "phantom:latest"
    with pytest.raises(ValueError, match="declared model list"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=_live_stub(card))


def test_missing_proof_model_refused_no_fallback(providers_fixture: Path) -> None:
    card = _good_card()
    del card["generation"]["model"]
    with pytest.raises(ValueError, match="proof model"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=_live_stub(card))


def test_default_model_is_proof_model_verbatim(providers_fixture: Path) -> None:
    card = _good_card()
    card["ollama"]["models"] = ["deepseek-r1:1.5b", "qwen2.5:3b-instruct", "gemma:2b"]
    card["generation"]["model"] = "qwen2.5:3b-instruct"
    name = fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=_live_stub(card))
    row = yaml.safe_load(providers_fixture.read_text(encoding="utf-8"))["providers"][name]
    assert row["default_model"] == "qwen2.5:3b-instruct"  # verbatim proof model, not the first listed


def test_live_probe_dead_lung_refused_and_prober_gets_card_ip(providers_fixture: Path) -> None:
    card = _good_card()
    calls: list[str] = []

    def dead_prober(ip: str) -> list[str]:
        calls.append(ip)
        raise ConnectionError("refused")

    with pytest.raises(ValueError, match="LIVE RE-PROBE failed"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=dead_prober)
    assert calls == ["100.72.138.62"]  # probed the card's IP, nothing else
    assert "ollama-aspire" not in providers_fixture.read_text(encoding="utf-8")  # no registry write


def test_live_probe_model_mismatch_refused(providers_fixture: Path) -> None:
    card = _good_card()

    def lying_prober(ip: str) -> list[str]:
        return ["totally-different:1b"]  # a live box that does not serve the card's models

    with pytest.raises(ValueError, match="LIVE RE-PROBE mismatch"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=lying_prober)
    assert "ollama-aspire" not in providers_fixture.read_text(encoding="utf-8")


def test_name_squat_refused(providers_fixture: Path) -> None:
    # A6: the name is already enrolled pointing at a DIFFERENT IP than this
    # card claims - the second card must die loudly, not no-op.
    first = _good_card()
    fleet_enroll.enroll_into_yaml(first, providers_fixture, prober=_live_stub(first))
    squatter = _good_card()
    squatter["tailscale_ip"] = "100.72.138.99"  # same hostname, hijacked IP
    with pytest.raises(ValueError, match="NAME SQUAT"):
        fleet_enroll.enroll_into_yaml(squatter, providers_fixture, prober=_live_stub(squatter))
    row = yaml.safe_load(providers_fixture.read_text(encoding="utf-8"))["providers"]["ollama-aspire"]
    assert row["base_url"] == "http://100.72.138.62:11434/v1"  # original row untouched


def test_cross_name_same_ip_updates_in_place(providers_fixture: Path) -> None:
    # the lung was renamed: the same endpoint IP must end up with ONE row
    # under the new name, not a duplicate row.
    text = providers_fixture.read_text(encoding="utf-8").replace("100.72.138.61", "100.72.138.62")
    providers_fixture.write_text(text, encoding="utf-8")
    card = _good_card()
    name = fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=_live_stub(card))
    assert name == "ollama-aspire"
    parsed = yaml.safe_load(providers_fixture.read_text(encoding="utf-8"))
    provs = parsed["providers"]
    assert "ollama-bob" not in provs  # replaced, not duplicated
    assert provs["ollama-aspire"]["base_url"] == "http://100.72.138.62:11434/v1"
    assert provs["ollama-aspire"]["default_model"] == "gemma:2b"
    assert "  mlx-cluster:" in providers_fixture.read_text(encoding="utf-8")  # anchor preserved


def test_live_probe_accepts_real_card_shape(tmp_path: Path) -> None:
    # smoke: the real aspire introduction card passes validation and the
    # stubbed probe, and the produced row carries the verbatim proof model.
    card = {
        "kind": "mag_lung_introduction.v1",
        "hostname": "Bob-2",
        "tailscale_ip": "100.72.138.62",
        "ollama": {
            "version": "0.40.13",
            "models": [
                "qwen2.5:3b-instruct",
                "gemma4:latest",
                "deepseek-r1:1.5b",
                "nomic-embed-text:latest",
                "gemma:2b",
            ],
        },
        "generation": {"ok": True, "model": "qwen2.5:3b-instruct", "seconds": 75.4, "reply": "READY"},
    }
    fixture = tmp_path / "providers.yaml"
    fixture.write_text(
        "providers:\n"
        "  mlx-cluster:\n"
        "    name: stub\n"
        "    kind: openai_compat\n",
        encoding="utf-8",
    )
    name = fleet_enroll.enroll_into_yaml(card, fixture, prober=_live_stub(card))
    assert name == "ollama-bob-2"
    row = yaml.safe_load(fixture.read_text(encoding="utf-8"))["providers"][name]
    assert row["default_model"] == "qwen2.5:3b-instruct"
    assert row["models"] == card["ollama"]["models"]


# --- cost-ledger producer (ractor_engine 2026-10-30 repair step 3) ---


def _ledger_rows(ledger: Path) -> list[dict]:
    return [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_proven_enrollment_lands_one_ledger_row(providers_fixture: Path, tmp_path: Path) -> None:
    card = _good_card()
    card["generated"] = "2026-10-31T12:00:00Z"  # the mag_introduce card timestamp
    ledger = tmp_path / "logs" / "fleet_cost_jobs.jsonl"
    name = fleet_enroll.enroll_into_yaml(card, providers_fixture,
                                         prober=_live_stub(card), ledger_path=ledger)
    rows = _ledger_rows(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == "fleet_cost_job.v1"
    assert row["lung"] == name == "ollama-aspire"
    assert row["model"] == "gemma:2b"           # the proof model, verbatim
    assert row["latency_s"] == 91.2             # the proof seconds, verbatim
    assert row["tokens"] is None                # the probe reports no token count
    assert row["ts"] == "2026-10-31T12:00:00Z"  # the card's generation time, not now
    assert row["src"] == "enroll-proof"
    assert row["class"] == "laptop"             # DEFAULT_ENROLL_CLASS pin
    assert row["watts"] == 25.0                 # laptop load row, fcl.DEVICE_CLASSES
    assert row["est_wh"] == round(25.0 * 91.2 / 3600.0, 4)


def test_no_proof_of_generation_means_no_row(providers_fixture: Path, tmp_path: Path) -> None:
    card = _good_card()
    card["generation"] = {"ok": False, "error": "skipped"}
    ledger = tmp_path / "fleet_cost_jobs.jsonl"
    with pytest.raises(ValueError, match="NO PROOF OF GENERATION"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture,
                                      prober=_live_stub(card), ledger_path=ledger)
    assert not ledger.exists()  # no proof, no membership, no ledger row


def test_refused_and_noop_enrollments_append_nothing(providers_fixture: Path, tmp_path: Path) -> None:
    ledger = tmp_path / "fleet_cost_jobs.jsonl"
    card = _good_card()
    fleet_enroll.enroll_into_yaml(card, providers_fixture,
                                  prober=_live_stub(card), ledger_path=ledger)
    # idempotent no-op: same name, same IP - no second row for one proof
    with pytest.raises(ValueError, match="already present"):
        fleet_enroll.enroll_into_yaml(card, providers_fixture,
                                      prober=_live_stub(card), ledger_path=ledger)
    assert len(_ledger_rows(ledger)) == 1
    # dead-lung refusal (A5) after the first row exists: still exactly one row
    with pytest.raises(ValueError, match="LIVE RE-PROBE failed"):
        other = _good_card()
        other["hostname"] = "aspire2"
        fleet_enroll.enroll_into_yaml(other, providers_fixture,
                                      prober=lambda ip: (_ for _ in ()).throw(ConnectionError("refused")),
                                      ledger_path=ledger)
    assert len(_ledger_rows(ledger)) == 1


def test_class_override_pins_other_watts_row(providers_fixture: Path, tmp_path: Path) -> None:
    card = _good_card()
    ledger = tmp_path / "fleet_cost_jobs.jsonl"
    fleet_enroll.enroll_into_yaml(card, providers_fixture, prober=_live_stub(card),
                                  ledger_path=ledger, cls_name="scavenger")
    row = _ledger_rows(ledger)[0]
    assert row["class"] == "scavenger"
    assert row["watts"] == 110.0                # scavenger load row, fcl.DEVICE_CLASSES
    assert row["est_wh"] == round(110.0 * 91.2 / 3600.0, 4)


def test_record_proof_row_refuses_non_numeric_seconds(tmp_path: Path) -> None:
    ledger = tmp_path / "fleet_cost_jobs.jsonl"
    for bad in ("91.2", True, None):
        card = _good_card()
        card["generation"]["seconds"] = bad
        with pytest.raises(ValueError, match="seconds"):
            fleet_enroll.record_proof_row(card, "ollama-aspire", ledger)
    assert not ledger.exists()  # a malformed proof is never ledgered


def test_record_proof_row_stamps_now_without_card_ts(tmp_path: Path) -> None:
    ledger = tmp_path / "fleet_cost_jobs.jsonl"
    card = _good_card()  # no "generated" key: the ts falls to build_record_row's now
    row = fleet_enroll.record_proof_row(card, "ollama-aspire", ledger)
    assert row["ts"].endswith("Z") and "T" in row["ts"]
    assert len(_ledger_rows(ledger)) == 1
