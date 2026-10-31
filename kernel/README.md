# kernel - C99 native organs

**Invariant:** zero runtime dependencies beyond libc/POSIX: each organ is a
single C99 file that compiles standalone and proves itself with a built-in
selftest (--selftest), printing deterministic receipts (byte offsets, hashes)
instead of claims.

**Falsifier:** a build that needs anything beyond `cc -std=c99`, or a selftest
that exits nonzero, or nondeterministic output between two runs on the same
input.

**Consumer / build:**
    cc -std=c99 -O2 -o sovereign_rib_kernel sovereign_rib_kernel.c && ./sovereign_rib_kernel --selftest
    cc -std=c99 -O2 -o mmap_estate_arena mmap_estate_arena.c     # POSIX mmap (Linux/WSL)
    cc -std=c99 -O2 -o bare_gguf_loader bare_gguf_loader.c       # POSIX mmap
    cc -std=c99 -O2 -o bare_ractor_runner bare_ractor_runner.c   # POSIX sockets

Files: sovereign_rib_kernel.c (kinematic stream filter: strips reasoning tags
across chunk boundaries, enforces the zero-em-dash invariant at the byte
stream level, validates rib_swarm.law_row.v1 rows), mmap_estate_arena.c
(zero-copy memory-mapped state arena), bare_gguf_loader.c (GGUF v2/v3 header
inspector over mmap), bare_ractor_runner.c (bare-metal task runner talking
straight to loopback ollama).

Verifier: each binary's own --selftest. These are the estate's Bare-Metal
Mountain Climb organs (Mag v2 phases 2.5-4), carried verbatim with only the
estate-internal prior-art comment lines dropped.
