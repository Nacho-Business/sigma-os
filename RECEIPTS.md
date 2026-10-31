# RECEIPTS - every number, every claim, one table

Trust bytes, not reports. Every number this repo states, where it was born,
and where it lives here. Receipt paths under `watch/inbox/` and `memory/`
point into the parent estate repo (local_sovereign_agent); this release ships
no raw dump of them. A number with no instrument and moment is a rumor
(LAWS.md P2); a number with no row on this table does not belong in this repo.

| # | Claim / number | Receipt (estate repo unless noted) | Lives here |
|---|---|---|---|
| 1 | ISO write verified twice: 6.2 billion bytes, readback hashed; first verification had an Int32 counter that silently corrupts at exactly 2,147,483,647 bytes | THE_TERRIBLE_ALLOCATION_ARTICLE_V2_20261031.md sec II; .zcode_scratch/flash_usb.ps1 pin | README.md (story); boot/kintsugi_build.sh (pinned source sha256 4da4a0c9...) |
| 2 | Kintsugi source ISO pinned sha256 4da4a0c9035da8e68a59a838674f403f0a54472c78a83b4fb7f78d03588f85a7 (readback-verified twice 2026-10-29) | kintsugi_build.sh pin + SIGMA_PATH_14_KINTSUGI_BUILD_20261030.md | boot/kintsugi_build.sh ISO_SHA256 |
| 3 | Security scanner self-accretion: 249GB, 18.5M files in one directory, 8,353 unique files snapshotted 2,220x each, 6,400:1 cache-to-finding ratio | THE_TERRIBLE_ALLOCATION_ARTICLE_V2_20261031.md sec II; THE_429G_DISCOVERY_20261030.md | README.md (story); organs/retention-bounded (the cure) |
| 4 | Total 429G accretion: .mimosa 271G + logs 158G, unwatched for months | THE_429G_DISCOVERY_20261030.md; SIGMA_CONSTITUTION_20_20261030.md Art 3 | CONSTITUTION.md Art 3 |
| 5 | Diagnosis buried unreceipted: symptom recorded 24,864x vs cause 1,624x; estate produced ~30,000 receipts and read 8 percent | THE_TERRIBLE_ALLOCATION_ARTICLE_V2_20261031.md sec II | README.md (story) |
| 6 | 542G tree; blind tar took a 117G laptop to 100 percent in an hour; census: 1,033 top-level entries; two instruments disagreed by 22GB (cluster slack); 407G deleted after mapping | THE_TERRIBLE_ALLOCATION_ARTICLE_V2_20261031.md sec II; SIGMA_CONSTITUTION_20_20261030.md Art 4 | CONSTITUTION.md Art 4; organs/retention-bounded README |
| 7 | Disk floor: over 80 percent used warns, under 10G free hard-stops; bootstrap fails under 60G free | SIGMA_BURN_LANE_T_OPS_20261030.md G1 | boot/mag_relight_gate.sh; boot/seeds |
| 8 | Marginal cost of the already-on laptop: $0.075/1M tokens (lane C constants); lane O anchor 5W idle / 25W inferring; 104-second generations measured on bob | SIGMA_BURN_LANE_C_ECONOMICS_20261030.md; SIGMA_BURN_LANE_O_POWER_20261030.md; BOB_RELIGHT_NIGHT_20261030.md | organs/cost-priced/cost_model.py DEVICE_CLASSES + marginal_usd_per_m; organs/cost-priced/README |
| 9 | Below about 9 percent duty the idle floor wins; the v2 target band is 10-30 percent duty | SIGMA_TRIANGULATION_20261030.md C2, PART V | fleet/sigma_fleet_envelopes.yaml defaults |
| 10 | ROUTE-1 experiment banked $0.0051 for 90 generations, marginal watts only | SIGMA_ROUTE1_VERDICT_20261030.md; SIGMA_HANDOFF_EXECUTION_20261030.md | organs/cost-priced/README |
| 11 | Routing thesis FALSIFIED: clean desktop:bob ratio 110.55:1; 10.4x same-day tok/s contention drift (6.94 vs 71.86 tok/s desktop; 0.65 tok/s bob; 30 rows per arm per window) | memory/runs/route1/summary.json + summary_env_matched.json; SIGMA_ROUTE1_VERDICT_20261030.md | fleet/providers.yaml ollama-bob-2 (verbatim rows + demotion) |
| 12 | Bob-2 enrollment proof: qwen2.5:3b-instruct generated in 75.4s, live at join time | SIGMA_HANDOFF_EXECUTION_20261030.md; fleet_enroll row | fleet/providers.yaml ollama-bob-2 name field |
| 13 | Bob measured ~8 tok/s on qwen2.5:3b - the one yield calibration point | BOB_RELIGHT_NIGHT_20261030.md | organs/cost-priced (honesty limits) |
| 14 | Cloud price lines per 1M (2026 posted): gpt5-nano blended $0.225, haiku5.5 $0.30, gpt4o-mini $0.375, gpt5-nano output $0.40, glm-4.6 $1.09; lane C break-even d* = 9.1 percent duty at their constants | SIGMA_BURN_LANE_C_ECONOMICS_20261030.md | organs/cost-priced/cost_model.py REFERENCE_LINES; pinned by test_fleet_cost_ledger.py |
| 15 | Desktop envelope: RX 5600 XT ~6GB VRAM, 64GB RAM, AMD (no CUDA); qwen-desk 69 t/s probe winner; never two 7B+ models resident; context 8k not 65k | fleet/lanes.yaml hardware_envelope (2026-08-06) | fleet/lanes.yaml; fleet/sigma_fleet_envelopes.yaml |
| 16 | Device-class watt rows: laptop 5/25W, nuc 8/22W, desktop 60/180W, scavenger 60/110W (class surveys, not wall meters; the real instrument is a $15 smart plug) | SIGMA_BURN_LANE_O_POWER_20261030.md; SIGMA_BURN_LANE_C_ECONOMICS_20261030.md | organs/cost-priced/cost_model.py + report honesty limits |
| 17 | Fleet census 5/5 hardware nodes live, 2026-10-30: 2 lungs (desktop, bob-2) + 3 spores (t90, pixel, moto-g); first dual-witnessed C2SP checkpoint 94e84024f66dac51e01a7c15f48cddcea989ebfb7964046e3f5d8b3ee6ddeaf1 (verkle n_leaves 18886), min_signers=2 | SIGMA_HANDOFF_EXECUTION_20261030.md; HANDOFF_SIGMA_OS_PITCH5_SUMMIT_TO_ZAI_20261030.md | fleet/seat_allowlist.json (3 keys receipted here; node:bob-2 + node:moto-g pointed at, on the aspire surface); fleet/sigma_fleet_envelopes.yaml census |
| 18 | Node public keys minted on this surface's receipts: desktop cf96516d..., t90 fc985d16..., pixel 10eee246... (spore doors live on :8768, challenges verified cross-machine) | SIGMA_HANDOFF_EXECUTION_20261030.md | fleet/seat_allowlist.json nodes |
| 19 | Dispatch taint: arm C double-dispatch produced 55 rows over 30 ids; fixed by the cross-process claim registry (claims, append, release = one atomic step) | SIGMA_W4_11_DISPATCHER_FIX_20261030.md | organs/dispatch-exactly-once (+ the race replayed in test_route1_dispatch.py) |
| 20 | Queue determinism: seed 20261030, 30 items, arm results byte-derivable (same seed, same corpus state, byte-identical queue) | SIGMA wave 4 lane 11 | organs/dispatch-exactly-once/route1_queue.py + route1_corpus.py |
| 21 | Heartbeat plane: 15-minute beat, alive grace 20 min, DARK at 45 min (3 missed beats), proof stale at 150 min, ledger self-prunes at 7 days | SIGMA_MAP_08_HEALTH_20261030.md (G6); SIGMA_MAP_06_COCKPIT_20261030.md; SIGMA_BURN_LANE_T_OPS_20261030.md (f) | organs/fleet-visible/fleet_heartbeat_collect.py constants |
| 22 | Retention bounds: logs compress >14d, delete >30d, own receipt bounded at 2000 rows; scan caches age at 30d while findings never age; claim log rotates at 8 MiB | SIGMA_BURN_LANE_T_OPS_20261030.md; SIGMA_W4_05_ROOM_RETENTION_20261030.md; SIGMA_W4_20_ROOM_ROTATE_20261030.md | organs/retention-bounded (scripts + constants) |
| 23 | The scanner's immune response fired at its own fix: the bootstrap script was blocked four times by the anti-mask law | THE_TERRIBLE_ALLOCATION_ARTICLE_V2_20261031.md sec IV | README.md (story) |
| 24 | PoeLLM worm: 3,400+ servers via exposed inference endpoints - the case for the bind law (never 0.0.0.0) | SIGMA_BURN_LANE_F_SECURITY_20261030.md | boot/seeds (bind law); LAWS.md P10 |
| 25 | Red-team wave: 19 attacks (A1-A8 fences landed in enrollment; D-K join resolution: typed join, no authkey on sticks) | SIGMA_REDTEAM_19_ATTACKS_20261030.md | organs/consent-gated-join; boot/seeds headers |
| 26 | Parallel arrival: FreeLattice (local-first, sovereign, mesh, Merkle-chained, no cloud) built independently - the paradigm check | THE_TERRIBLE_ALLOCATION_ARTICLE_V2_20261031.md sec V | README.md (story) |
| 27 | Aspire-seat pitch receipts: native AVX2 mesh door 2.316ms / 26,975 vectors (272x), 397-line stdlib spore capsule, autonomic governor census, GHOST=0 | SIGMA_TRIANGULATION_20261030.md PART I | README.md (story); boot/sigma_spore_capsule.py is this tree's re-landing of the capsule contract |
| 28 | Convergences (independent seats, same conclusion): consent-as-architecture x4, junk-tier honesty x3, receipts-as-nervous-system universal, bob=seats-not-tokens x3 | SIGMA_TRIANGULATION_20261030.md PART II | CONSTITUTION.md Articles 1, 2, 5 |
| 29 | Blind spots named and still open: watt meter (B1), real third lung (B2), stranger UX (B3), n=1 operator (B5), ~476G unnamed mass (B6), intake retention (B7) | SIGMA_TRIANGULATION_20261030.md PART IV | organs/thermal-decay-guard README (B4 closed by this organ) |
| 30 | Money is the one uncut chain: local settle now (the verkle chain), GSTD rail deferred dark; Article 5 excludes money-form from v1 by law | THE_ROOT_FRAME_20261031.md | README.md (story) |

## The tree's own verification receipts (2026-10-31 restructure)

| # | Claim | Instrument |
|---|---|---|
| 31 | Every organ test suite runs green in this tree | `python -m pytest organs -q` (run recorded in watch/inbox/SIGMA_W5_DOGFOOD_REPO_20261031.md) |
| 32 | The spore capsule passes the RFC 8032 TEST 1 vectors, sign/verify round trip, tamper rejection, and the independent cross-check against the `cryptography` package | `python boot/sigma_spore_capsule.py --selftest` |
| 33 | The spore door serves health, identity, challenge, and rejects a tampered signature live | live-check on an ephemeral port, 2026-10-31 (receipt 31's document) |
| 34 | No file exceeds the 400-line ratchet cap | line-count sweep, receipt 31's document |
| 35 | The kintsugi render selftest passes on the re-rooted seeds | `python boot/kintsugi_selftest.py` (needs pyyaml) |
