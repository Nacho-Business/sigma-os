"""Verifier for organs/consent-gated-join/mag_introduce.py (the lung self-introduction organ).

Named in the tool's docstring as its verifier (Consumer Law). Covers the
loopback SSRF fence, card structure, and readable render. The fleet-enrollment
side lives in organs/consent-gated-join/test_fleet_enroll.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

organ = str(Path(__file__).resolve().parent)
if organ not in __import__("sys").path:
    __import__("sys").path.insert(0, organ)

import mag_introduce  # noqa: E402


def test_script_compiles() -> None:
    import py_compile

    src = Path(__file__).resolve().parent / "mag_introduce.py"
    py_compile.compile(str(src), doraise=True)


def test_fence_rejects_non_http() -> None:
    with pytest.raises(ValueError):
        mag_introduce.guard_loopback("https://127.0.0.1:11434")


def test_fence_rejects_non_loopback_host() -> None:
    with pytest.raises(ValueError):
        mag_introduce.guard_loopback("http://169.254.169.254:11434")
    with pytest.raises(ValueError):
        mag_introduce.guard_loopback("http://10.0.0.5:11434")


def test_fence_accepts_loopback() -> None:
    assert mag_introduce.guard_loopback("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"
    assert mag_introduce.guard_loopback("http://localhost:11434") == "http://localhost:11434"


def test_render_card_contains_proof_and_hostname() -> None:
    card = {
        "hostname": "aspire",
        "platform": "Linux 7.0.0-42",
        "cpu_count": 8,
        "mem_total": "3.3G",
        "disk_free_gb": 84.0,
        "tailscale_ip": "100.72.138.62",
        "ollama": {"version": "0.40.13", "models": ["gemma:2b", "deepseek-r1:1.5b"]},
        "generation": {"ok": True, "model": "gemma:2b", "seconds": 91.2, "reply": "READY"},
    }
    text = mag_introduce.render(card)
    assert "aspire" in text and "100.72.138.62" in text and "READY" in text


def test_render_handles_failed_probe() -> None:
    card = {
        "hostname": "x",
        "platform": "Linux",
        "cpu_count": 1,
        "mem_total": "1G",
        "disk_free_gb": 1.0,
        "tailscale_ip": "not on tailnet",
        "ollama": {"version": None, "models": []},
        "generation": {"ok": False, "error": "no models pulled"},
    }
    text = mag_introduce.render(card)
    assert "NONE" in text and "not demonstrated" in text
