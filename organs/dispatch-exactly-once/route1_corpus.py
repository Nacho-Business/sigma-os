#!/usr/bin/env python3
"""route1_corpus: deterministic SIGMA-ROUTE-1 queue derivation (seed -> corpus windows).

Split out of tools/route1_queue.py (SIGMA wave 4 lane 11, 2026-10-30) so the
orchestrator stays under the 400-line ratchet cap; the derivation contract is
unchanged: same seed, same corpus state, byte-identical queue (text_sha and
prompt_sha re-derivable).

Consumer: tools/route1_queue.py (the default no---run mode re-derives every
row's text_sha from seed+corpus and exits 1 on drift; --run resumes off the
queue file) and tools/route1_ledger.py's closure checks.
Verifier: `.venv/Scripts/python.exe -m py_compile tools/route1_corpus.py`; the
route1_queue default mode is the drift oracle (exit 1 on corpus drift);
tests/test_route1_queue_dispatch.py compiles this module and exercises the
dispatcher against synthetic queues in the same row shape.

Stdlib only. Repo venv only: `.venv/Scripts/python.exe tools/route1_queue.py ...`.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOTS = "corpus/library,corpus/canon,corpus/steal,docs"

# Quiet / junk / non-redistributable source dirs never enter the sample.
DENY_DIRS = {
    ".git", ".mimosa", ".venv", "node_modules", "__pycache__",
    "papers_reference_nonredistributable", ".zcode", ".agents", "apk",
}
MIN_FILE_BYTES = 8 * 1024
MAX_FILE_BYTES = 384 * 1024
MIN_WINDOW_CHARS = 2000
MAX_WINDOW_CHARS = 4000
MIN_WINDOW_WORDS = 150

PROMPT_TMPL = (
    "Summarize the following text in at most five sentences. "
    "Capture the main point and any key numbers or names.\n\n"
    "Text:\n{text}\n\nSummary:"
)


def sha16(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def iter_candidates(roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for r in roots:
        base = ROOT / r
        if not base.is_dir():
            print(f"WARN: root not found, skipped: {r}", file=sys.stderr)
            continue
        for p in base.rglob("*"):
            if p.suffix.lower() not in (".md", ".txt"):
                continue
            if any(part in DENY_DIRS for part in p.parts):
                continue
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if MIN_FILE_BYTES <= sz <= MAX_FILE_BYTES:
                files.append(p)
    files.sort()
    return files


def extract_window(text: str, path_str: str, idx: int, rng: random.Random) -> str | None:
    """Deterministic 2-4K char window seeded by (path, queue index, rng state)."""
    h = hashlib.sha256(f"{path_str}:{idx}:{rng.random():.17f}".encode()).digest()
    size = MIN_WINDOW_CHARS + (int.from_bytes(h[:4], "big") % (MAX_WINDOW_CHARS - MIN_WINDOW_CHARS + 1))
    if len(text) <= size:
        window = text
    else:
        start = int.from_bytes(h[4:8], "big") % (len(text) - size)
        window = text[start:start + size]
    words = re.findall(r"[A-Za-z]{3,}", window)
    tokens = window.split()
    # English-dominance: translated files pass the word count on link/code fragments
    # but summarize unevenly across arms; require >= 50% ASCII-word share.
    if len(words) < MIN_WINDOW_WORDS or len(words) < 0.5 * max(1, len(tokens)):
        return None
    # structural check: OCR column fragments (one word per line) summarize to noise
    lines = [ln for ln in window.splitlines() if ln.strip()]
    avg_line = sum(len(ln) for ln in lines) / max(1, len(lines))
    if len(lines) < 8 or avg_line < 25.0:
        return None
    return window


def build_queue(n: int, seed: int, roots: list[str]) -> list[dict]:
    candidates = iter_candidates(roots)
    if len(candidates) < n:
        raise SystemExit(f"only {len(candidates)} candidate files under {roots}; need {n}")
    rng = random.Random(seed)
    rng.shuffle(candidates)
    items: list[dict] = []
    for path in candidates:
        if len(items) >= n:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        window = extract_window(text, str(path), len(items), rng)
        if window is None:
            continue
        prompt = PROMPT_TMPL.format(text=window)
        items.append({
            "id": f"route1-{seed}-{len(items):02d}",
            "source": str(path.relative_to(ROOT)),
            "seed": seed,
            "chars": len(window),
            "text_sha": sha16(window),
            "prompt_sha": sha16(prompt),
            "prompt": prompt,
        })
    if len(items) < n:
        raise SystemExit(f"corpus yielded only {len(items)} usable windows of {n}")
    return items


def write_queue(items: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def load_queue(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def gen_and_check(args) -> int:
    items = build_queue(args.n, args.seed, args.roots.split(","))
    out = Path(args.out)
    if out.exists() and not args.force:
        old = load_queue(out)
        old_shas = {r["id"]: (r.get("text_sha"), r.get("prompt_sha")) for r in old}
        new_shas = {r["id"]: (r["text_sha"], r["prompt_sha"]) for r in items}
        if old_shas == new_shas:
            print(f"QUEUE OK: {len(items)} items, shas match seed {args.seed}: {out}")
            return 0
        print("QUEUE DRIFT: existing file differs from seed re-derivation.", file=sys.stderr)
        print("Corpus moved under a fixed seed. Refusing to overwrite without --force.", file=sys.stderr)
        print("If you accept a new queue, rerun ALL arms from scratch with --force.", file=sys.stderr)
        return 1
    write_queue(items, out)
    chars = [it["chars"] for it in items]
    print(f"QUEUE WRITTEN: {len(items)} items, seed {args.seed}, chars min/avg/max = "
          f"{min(chars)}/{sum(chars) // len(chars)}/{max(chars)}: {out}")
    return 0
