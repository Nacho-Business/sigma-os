# estate-bootstrap scripts carried on the kintsugi ISO

Consumer: the installed box lands this directory at
`/home/mark/estate-bootstrap` (autoinstall late-commands copy it from
`/cdrom/nocloud/sigma/scripts/`). The normal path is the curated chunked
transfer to `~/Documents/projects/local_sovereign_agent` and then the
`sigma-estate-wait` service runs the gate from the transferred repo; the
copies here are the fallback toolkit for re-runs after the tree lands.

Verifier: `boot/kintsugi_render.py selftest` asserts the build copied fresh
tracked versions of both tools next to this README; `boot/kintsugi_smoke.sh`
and the checklist in `watch/inbox/SIGMA_PATH_14_KINTSUGI_BUILD_20261030.md`
verify the ISO side.

Contents (copied in at build time from the repo, never hand-edited on the ISO):
- `mag_relight_gate.sh` - the idempotent relight core (venv via get-pip,
  ollama battery, record surface, offline-boot gate). Its exit is the verdict.
- `mag_introduce.py` - the self-introduction; `--json` writes the payload
  `fleet_enroll.py` consumes (proof-gated membership).

RAM law: the seed pulls only the bob-calibrated default battery
(gemma:2b, nomic-embed-text, deepseek-r1:1.5b). Heavier pulls stay logged
seat ops.
