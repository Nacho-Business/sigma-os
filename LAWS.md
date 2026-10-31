# LAWS - the ten pitfalls and the four meta-laws

Distilled from this fleet's own failures, not from theory. Parent document:
`watch/inbox/SIGMA_PITFALLS_18_TAXONOMY_20261030.md` (estate repo); the
user-facing translation lives at estate `docs/KNOWN_PITFALLS.md`. Every pitfall
below names the one-command detection that would have caught it.

## The ten pitfalls

**P1. Unsized transfer.** A disk that fills to 100 percent mid-copy was never
sized. Size first, transfer second: over 80 percent used warns, under 10G free
stops. Detect: `df -h .` before every transfer.

**P2. Unverified claim, wrong constant.** Two documents quoting different
numbers for the same thing means the number traveled without its instrument
and moment. A constant without instrument, moment, and delta is a rumor. One
level down: a byte counter typed too small silently truncates (an Int32 dies
at exactly 2,147,483,647 bytes, half of one 6.2GB ISO). Detect: re-run the
measured rows on the box before quoting its numbers.

**P3. Unexercised recovery path.** Recovery code only runs when things are
already broken, so green runs never test it; the first crash is the first
test, and it fails. Force-fire every heal and rollback path once, supervised,
before trusting it. Detect: count heal attempts in the keepalive log; zero
means never fired.

**P4. No byte verification.** A process exit is not a result. Done means
write, read back, hash, compare against the official source (the 6.2 billion
bytes verified twice, because the first verification had the overflow bug).
Detect: `sha256sum` of the written device against the official hash.

**P5. Confident read without the rulebook.** A plan built on a spec nobody
probed (the GPU that was never on the box) is fiction with citations. Probe
the hardware; spot-check the three load-bearing claims against primary
sources. Detect: run the introduction card - it reads cores, memory, disk,
and GPU class from the machine, never from a document.

**P6. Pattern self-match.** A kill that matched a phrase instead of a binary
kills the wrong process, or the killer's own session (`pkill -f` matches any
command line containing the string). Match the exact binary path, or use a
pidfile. Detect: `pgrep -af` - read the kill list before firing it.

**P7. Non-detached long ops.** A long write parented to your shell dies when
your shell dies; elevation makes the loss more expensive (the 4,352-of-5,961MB
mid-write kill). Long ops launch detached, watched by a log tail. Detect:
`ps -eo pid,ppid,etime,cmd` - PPID 1 survives your shell.

**P8. Environment adapters.** Green on one OS, red on the other: a path
hardcoded for one platform (`.venv/Scripts/python.exe` on Linux, MSYS paths on
Windows interpreters, UTF-16 under plain grep). Silent by construction.
Detect: know which venv layout exists before invoking it; gate on both OSes.

**P9. Secondhand facts.** Provenance decays one hop per retelling: fetched
citations rot, pasted numbers can be wrong at birth. Fetch the primary source
and bank the fetch verdict next to the claim. Detect: `curl -sI <url> | head
-1`, banked beside the citation.

**P10. Convenient default, insecure state.** An endpoint answering on 0.0.0.0
is a convenience default that became the security posture, faithfully restored
by the watchdog after every manual correction. Exposed inference endpoints are
the botnet shape (PoeLLM: 3,400+ servers). Bind tailnet IP or loopback, never
wildcard; secrets stay stripped from lung transfers by design. Detect:
`ss -ltn | grep 11434` - 0.0.0.0 or :: is exposure.

## The four meta-laws

1. **Bytes over reports** (P4, P5, P9): a claim is what a readback, an
   inventory, or a primary fetch says at a named moment.
2. **Numbers carry instruments** (P1, P2): size before transfer; instrument,
   moment, and delta before any comparison.
3. **Recovery code is code** (P3, P6, P7): forced fires, exact kill targets,
   detached parents.
4. **Adapters are part of the artifact** (P8, P10): platform paths and
   defaults are designed and gated, not assumed.

## Falsifier

If one of these incidents recurs on a box where this document was present, the
warnings were not load-bearing: amend this file with the new story and re-point
the guard that should have caught it.
