#!/usr/bin/env python
"""kintsugi_selftest.py - the parse-check instrument for the kintsugi image build.

Runs the render library's full selftest with fixtures, no ISO and no xorriso
needed: seed yaml round-trip, placeholder scans, the pinned variant render,
the wipe seed's no-auto-run law, bash -n on every embedded script, grub menu
assertions (entries, default, ds args, fwsetup guard, pinned swap) and torito
filter fixtures including the missing-flag refusal.

Consumer: tools/kintsugi_build.sh --selftest (first leg of every build) and
the operator's local parse checks; shares the pure functions it verifies with
tools/kintsugi_render.py.
Verifier: this file is the verifier leg of the kintsugi pipeline; its own
results are checked by running it (exit 0, PASS lines) and by the qemu boot
smoke in tools/kintsugi_smoke.sh. Checklist:
watch/inbox/SIGMA_PATH_14_KINTSUGI_BUILD_20261030.md.

Stdlib only; pyyaml required; bash must be on PATH for the embedded-script
syntax checks.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

from kintsugi_render import (
    PINNED_DS,
    PLACEHOLDER,
    REPO,
    SEED_DIR_DEFAULT,
    SIGMA_DS,
    WIPE_DS,
    die,
    gen_grub_cfg,
    render_seeds,
    torito_flags,
)

GRUB_FIXTURE = """\
if loadfont /boot/grub/font.pf2 ; then
  set gfxmode=auto
  insmod all_video
  insmod gfxterm
  terminal_output gfxterm
fi

menuentry "Try or install Ubuntu" {
\tset gfxpayload=keep
\tlinux\t/casper/vmlinuz ---
\tinitrd\t/casper/initrd
}
menuentry "Ubuntu (safe graphics)" {
\tset gfxpayload=keep
\tlinux\t/casper/vmlinuz nomodeset ---
\tinitrd\t/casper/initrd
}
"""

TORITO_FIXTURE = """\
xorriso -as mkisofs
  -V 'Ubuntu 24.04.5.1 LTS amd64'
  --grub2-mbr --interval:local_fs:0s-15s:zero_mbrpt,zero_gpt
  -append_partition 2 28732ac11ff8d211ba4b00a0c93ec93b local_fs:1s-9769s
  -appended_part_as_gpt
  -iso_mbr_part_type a2a0d0ebe5b9334487c068b6b72699c7
  -c /boot.catalog -b /boot/grub/i386-pc/eltorito.img -no-emul-boot
  -boot-load-size 4 -boot-info-table --grub2-boot-info
  -eltorito-alt-boot -e '--interval:appended_partition_2:::' -no-emul-boot
"""


def _bash_n(path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        die("bash not found on PATH; the embedded-script syntax check cannot run")
    r = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        die(f"bash -n failed on {path}:\n{r.stderr}")


def _extract_script(data: dict, path: str) -> str:
    """Pull one embedded script out of an autoinstall seed (nested under
    autoinstall.user-data.write_files)."""
    a = data.get("autoinstall") or {}
    for holder in (a.get("user-data") or {}, data.get("user-data") or {}):
        for w in holder.get("write_files", []):
            if w.get("path") == path:
                return w.get("content", "")
    die(f"embedded script not found in seed: {path}")
    raise AssertionError("unreachable")  # die() exits; keeps type checkers honest


def selftest() -> int:
    if yaml is None:
        die("pyyaml not importable; selftest cannot verify seeds (apt install python3-yaml)")
    checks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="kintsugi-selftest-") as td:
        td = Path(td)
        out = td / "nocloud"
        render_seeds(SEED_DIR_DEFAULT, REPO, td, "$6$TESTHASH$",
                     ["ssh-ed25519 AAA k1", "ssh-ed25519 BBB k2"], None)

        sigma = yaml.safe_load((out / "sigma" / "user-data").read_text(encoding="utf-8"))
        a = sigma["autoinstall"]
        assert a["version"] == 1, "autoinstall version"
        assert a["interactive-sections"] == ["storage"], "interactive storage screen"
        assert a["identity"]["username"] == "mark", "username mark"
        assert a["identity"]["password"] == "$6$TESTHASH$", "password hash rendered"
        assert a["ssh"]["allow-pw"] is False, "keys-only ssh"
        assert "zstd" in a["packages"] and "git" in a["packages"], "gate preconditions"
        assert a["late-commands"], "late-commands present"
        assert not PLACEHOLDER.search(repr(a)), "placeholder scan"
        keys = a["ssh"]["authorized-keys"]
        assert keys == ["ssh-ed25519 AAA k1", "ssh-ed25519 BBB k2"], f"keys block: {keys}"
        checks.append("sigma generic seed: schema keys, consent screen, keys block")

        for name in ("sigma_firstboot.sh", "sigma_estate_wait.sh"):
            body = _extract_script(sigma, f"/usr/local/sbin/{name}")
            p = td / name
            p.write_text(body, encoding="utf-8")
            _bash_n(p)
        unit = _extract_script(sigma, "/etc/systemd/system/sigma-estate-wait.service")
        assert "[Service]" in unit, "wait service unit has [Service]"
        assert "ExecStart=/usr/local/sbin/sigma_estate_wait.sh" in unit, "wait service ExecStart"
        assert "21600" in _extract_script(sigma, "/usr/local/sbin/sigma_estate_wait.sh"), "wait cap present"
        checks.append("embedded firstboot + estate-wait scripts: bash -n clean")

        # D-K join resolution (red-team A1/A2/A4, 2026-10-30): no authkey
        # material may ride any rendered seed and nothing may auto-run the
        # join; the operator types "sudo tailscale up" at the console.
        firstboot_body = _extract_script(sigma, "/usr/local/sbin/sigma_firstboot.sh")
        wait_body = _extract_script(sigma, "/usr/local/sbin/sigma_estate_wait.sh")
        assert "--auth-key" not in repr(sigma) and "tskey-" not in repr(sigma), \
            "no tailscale authkey material in the rendered seed"
        assert "tailscale up --qr" not in firstboot_body, "firstboot must not auto-run the join"
        assert "join the tailnet: sudo tailscale up" in firstboot_body, \
            "firstboot prints the typed-join instruction"
        assert "join the tailnet: sudo tailscale up" in wait_body, \
            "estate-wait prints the typed-join instruction"
        checks.append("D-K join law: no authkey material, no auto-join, typed-join instruction printed")

        pinned_serial = "TESTSERIAL_001"
        render_seeds(SEED_DIR_DEFAULT, REPO, td / "p", "$6$TESTHASH$",
                     ["ssh-ed25519 AAA k1"], pinned_serial)
        pdata = yaml.safe_load(((td / "p") / "nocloud" / "sigma-pinned" / "user-data").read_text(encoding="utf-8"))
        pa = pdata["autoinstall"]
        assert pa["storage"]["layout"]["match"]["serial"] == pinned_serial, "serial pin rendered"
        assert "interactive-sections" not in pa, "pinned has no interactive screen"
        assert "sigma-pinned/scripts" in str(pa["late-commands"]), "pinned late-command path"
        checks.append("sigma pinned seed: serial pin, no interactive screen")

        wipe = yaml.safe_load((out / "wipe" / "user-data").read_text(encoding="utf-8"))
        assert "runcmd" not in wipe and "bootcmd" not in wipe, "wipe: nothing auto-runs"
        assert "/usr/local/sbin/sigma-wipe" in {w["path"] for w in wipe["write_files"]}, "wipe script staged"
        wp = td / "sigma-wipe"
        wp.write_text(wipe["write_files"][0]["content"], encoding="utf-8")
        _bash_n(wp)
        checks.append("wipe seed: no runcmd/bootcmd, staged script bash -n clean")

        for sub, base in (("sigma", out / "sigma"),
                          ("sigma-pinned", (td / "p") / "nocloud" / "sigma-pinned")):
            sdir = base / "scripts"
            for name in ("mag_relight_gate.sh", "mag_introduce.py", "README.md"):
                assert (sdir / name).is_file(), f"scripts carry {name} in {sub}"
        checks.append("estate tools + README carried into seed script dirs")

        cfg, warns = gen_grub_cfg(GRUB_FIXTURE, pinned=False)
        assert not warns, f"unexpected grub warnings: {warns}"
        assert "set default=1" in cfg and "set timeout=30" in cfg, "default is the live entry"
        assert "SIGMA: absorb this machine" in cfg, "sigma install entry"
        assert "Try or install Ubuntu (live, no changes)" in cfg, "live entry kept"
        assert "SIGMA: wipe a disk (decommission, console two-man rule)" in cfg, "wipe entry"
        assert 'if [ "${grub_platform}" == "efi" ]; then' in cfg, "fwsetup guarded to efi"
        assert f"autoinstall {SIGMA_DS}" in cfg, "sigma ds arg"
        assert f"{WIPE_DS} ---" in cfg, "wipe ds arg"
        assert "autoinstall" not in cfg.split("wipe a disk")[1], "wipe entry has no autoinstall"
        assert "/casper/vmlinuz ---" in cfg, "stock linux line preserved"
        assert "nomodeset" not in cfg, "first stock entry used, not safe-graphics"
        cfg_p, _ = gen_grub_cfg(GRUB_FIXTURE, pinned=True)
        assert PINNED_DS in cfg_p and SIGMA_DS not in cfg_p, "pinned ds path swap"
        checks.append("grub menu: entries, default, ds args, fwsetup guard, pinned swap")

        flags = torito_flags(TORITO_FIXTURE)
        assert "--grub2-mbr" in flags and "-append_partition" in flags, "torito keeps boot flags"
        assert "-V 'Ubuntu 24.04.5.1 LTS amd64'" in flags, "torito preserves quoted args"
        try:
            torito_flags(TORITO_FIXTURE.replace("--grub2-mbr", ""))
        except ValueError as e:
            assert "--grub2-mbr" in str(e), f"wrong refusal reason: {e}"
        else:
            raise AssertionError("torito accepted a report without --grub2-mbr")
        checks.append("torito filter: flags kept, quoted args kept, missing-mbr refused")

    for c in checks:
        print(f"  PASS: {c}")
    print(f"selftest PASS ({len(checks)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(selftest())
