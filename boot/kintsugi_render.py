#!/usr/bin/env python
"""kintsugi_render.py - the deterministic render stage of the kintsugi image build.

Renders the nocloud seed templates (placeholders to operator-provided values),
generates boot/grub/grub.cfg (the four-entry consent menu), and filters
`xorriso -report_el_torito as_mkisofs` output into the repack flag string.
All three steps are pure functions so the build (tools/kintsugi_build.sh),
the smoke test (tools/kintsugi_smoke.sh) and the selftest
(tools/kintsugi_selftest.py) share one implementation instead of three
drifting copies.

Consumer: tools/kintsugi_build.sh (subcommands render / grub / torito).
Verifier: `python3 tools/kintsugi_selftest.py` (yaml round-trip, placeholder
scan, embedded-script bash -n, grub menu assertions, torito filter fixtures);
plus the qemu boot smoke and the checklist in
watch/inbox/SIGMA_PATH_14_KINTSUGI_BUILD_20261030.md.

Stdlib only; pyyaml is required (render verification is the point).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - selftest/render both die on this
    yaml = None

REPO = Path(__file__).resolve().parents[1]
SEED_DIR_DEFAULT = REPO / "boot" / "seeds"
PLACEHOLDER = re.compile(r"@[A-Z_]+@")
SSH_KEYS_LINE = re.compile(r"^[ \t]*-[ \t]*\"@SSH_KEYS@\"[ \t]*$", re.M)
SIGMA_DS = r"ds=nocloud\;s=/cdrom/nocloud/sigma/"
PINNED_DS = r"ds=nocloud\;s=/cdrom/nocloud/sigma-pinned/"
WIPE_DS = r"ds=nocloud\;s=/cdrom/nocloud/wipe/"


def die(msg: str) -> "None":
    print(f"kintsugi_render: FATAL: {msg}", file=sys.stderr)
    sys.exit(2)


def render_text(text: str, subs: dict) -> str:
    out = text
    for key, value in subs.items():
        out = out.replace(f"@{key}@", value)
    return out


def render_seed_file(src: Path, dst: Path, password_hash: str, ssh_keys: list,
                     extra_subs: dict | None = None) -> dict:
    """Render one seed template, verify no placeholder survived, parse the yaml."""
    if yaml is None:
        die("pyyaml not importable; refusing to render an unverified seed (apt install python3-yaml)")
    text = src.read_text(encoding="utf-8")
    if "@SSH_KEYS@" in text:
        keys_block = "\n".join(f'      - "{k}"' for k in ssh_keys)
        text, n = SSH_KEYS_LINE.subn(lambda _m: keys_block, text)
        if n != 1:
            die(f"{src.name}: expected exactly one @SSH_KEYS@ line, found {n}")
    text = render_text(text, {"PASSWORD_HASH": password_hash, **(extra_subs or {})})
    left = PLACEHOLDER.search(text)
    if left:
        die(f"{src.name}: unresolved placeholder {left.group(0)}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        die(f"{dst}: yaml did not parse to a mapping")
    return data


def copy_scripts(out_scripts: Path, repo_root: Path, seed_scripts: Path) -> None:
    """Carry the fresh tracked SIGMA-OS tools + seed README onto the ISO tree."""
    out_scripts.mkdir(parents=True, exist_ok=True)
    # Release layout: the relight gate lives in boot/, the introducer in the
    # consent-gated-join organ. The ISO-side names stay stable for the seeds.
    for rel, name in (("boot/mag_relight_gate.sh", "mag_relight_gate.sh"),
                      ("organs/consent-gated-join/mag_introduce.py", "mag_introduce.py")):
        src = repo_root / rel
        if not src.is_file():
            die(f"estate tool missing: {src}")
        shutil.copy2(src, out_scripts / name)
    for item in sorted(seed_scripts.iterdir()):
        if item.is_file():
            shutil.copy2(item, out_scripts / item.name)


def render_seeds(seed_dir: Path, repo_root: Path, out_root: Path, password_hash: str,
                 ssh_keys: list, pinned_serial: str | None) -> None:
    """Render the full nocloud tree: sigma (+ optional pinned variant) + wipe."""
    nocloud = out_root / "nocloud"

    sigma = nocloud / "sigma"
    data = render_seed_file(seed_dir / "sigma" / "user-data", sigma / "user-data",
                            password_hash, ssh_keys)
    a = data.get("autoinstall") or {}
    if a.get("version") != 1:
        die(f"sigma user-data: autoinstall.version != 1")
    if a.get("interactive-sections") != ["storage"]:
        die("sigma user-data: interactive-sections must be exactly [storage] (the consent screen)")
    if a.get("ssh", {}).get("allow-pw") is not False:
        die("sigma user-data: ssh.allow-pw must be false (keys only)")
    shutil.copy2(seed_dir / "sigma" / "meta-data", sigma / "meta-data")
    copy_scripts(sigma / "scripts", repo_root, seed_dir / "scripts")

    if pinned_serial:
        if not pinned_serial.strip() or any(c.isspace() for c in pinned_serial):
            die("--pin-serial must be the disk serial token, no whitespace")
        pinned = nocloud / "sigma-pinned"
        pdata = render_seed_file(seed_dir / "sigma" / "user-data.pinned",
                                 pinned / "user-data", password_hash, ssh_keys,
                                 extra_subs={"PINNED_SERIAL": pinned_serial})
        pa = pdata.get("autoinstall") or {}
        serial = ((pa.get("storage") or {}).get("layout") or {}).get("match", {}).get("serial")
        if serial != pinned_serial:
            die(f"pinned user-data: storage.layout.match.serial is {serial!r}, wanted {pinned_serial!r}")
        if "interactive-sections" in pa:
            die("pinned user-data must not carry interactive-sections (the pin is the ask)")
        shutil.copy2(seed_dir / "sigma" / "meta-data", pinned / "meta-data")
        copy_scripts(pinned / "scripts", repo_root, seed_dir / "scripts")

    wipe = nocloud / "wipe"
    wdata = render_seed_file(seed_dir / "wipe" / "user-data", wipe / "user-data",
                             password_hash="", ssh_keys=[])
    for banned in ("runcmd", "bootcmd"):
        if banned in wdata:
            die(f"wipe user-data carries {banned}: nothing may auto-run (consent law)")
    paths = {w.get("path") for w in wdata.get("write_files", [])}
    if "/usr/local/sbin/sigma-wipe" not in paths:
        die("wipe user-data must stage /usr/local/sbin/sigma-wipe")
    shutil.copy2(seed_dir / "wipe" / "meta-data", wipe / "meta-data")
    print(f"rendered seeds under {nocloud}"
          + (" (+ sigma-pinned)" if pinned_serial else ""))


GRUB_HEADER = """\
# kintsugi consent menu (generated by tools/kintsugi_render.py; stock cfg kept
# as grub.cfg.orig). Default is the harmless live entry: a stick left in a
# machine at reboot does nothing. Booting the stick and picking an entry IS the ask.
set timeout={timeout}
set default={default}

if loadfont /boot/grub/font.pf2 ; then
	set gfxmode=auto
	insmod all_video
	insmod gfxterm
	terminal_output gfxterm
fi

"""


def gen_grub_cfg(stock_text: str, pinned: bool = False, timeout: int = 30, default: int = 1):
    """Return (cfg_text, warnings) for the four-entry consent menu."""
    warnings: list[str] = []
    sigma_ds = PINNED_DS if pinned else SIGMA_DS
    entry_re = re.compile(
        r"menuentry\s+[\"'](?P<title>[^\"']*Try or install[^\"']*)[\"'][^{}]*\{(?P<body>.*?)\n\}",
        re.S)
    m = entry_re.search(stock_text)
    linux = initrd = None
    if m:
        lm = re.search(r"^\s*linux\s+(.+?)\s*$", m.group("body"), re.M)
        im = re.search(r"^\s*initrd\s+(.+?)\s*$", m.group("body"), re.M)
        if lm:
            linux = lm.group(1).strip()
        if im:
            initrd = im.group(1).strip()
    if not (linux and initrd):
        warnings.append("stock 'Try or install' entry not parsed; falling back to "
                        "/casper/vmlinuz + /casper/initrd (VERIFY at the smoke test)")
        linux, initrd = "/casper/vmlinuz", "/casper/initrd"
    base = linux
    if base.endswith("---"):
        base = base[:-3].rstrip()

    pin_note = "PINNED disk, wrong machine aborts" if pinned else "erases disk, asks once on the storage screen"
    lines = [GRUB_HEADER.format(timeout=timeout, default=default)]
    lines.append(f'menuentry "SIGMA: absorb this machine ({pin_note})" {{\n')
    lines.append("\tset gfxpayload=keep\n")
    lines.append(f"\tlinux\t{base} autoinstall {sigma_ds} ---\n")
    lines.append(f"\tinitrd\t{initrd}\n")
    lines.append("}\n")
    lines.append(f'menuentry "Try or install Ubuntu (live, no changes)" {{\n')
    lines.append("\tset gfxpayload=keep\n")
    lines.append(f"\tlinux\t{base} ---\n")
    lines.append(f"\tinitrd\t{initrd}\n")
    lines.append("}\n")
    lines.append(f'menuentry "SIGMA: wipe a disk (decommission, console two-man rule)" {{\n')
    lines.append("\tset gfxpayload=keep\n")
    lines.append(f"\tlinux\t{base} {WIPE_DS} ---\n")
    lines.append(f"\tinitrd\t{initrd}\n")
    lines.append("}\n")
    lines.append('if [ "${grub_platform}" == "efi" ]; then\n')
    lines.append('menuentry "UEFI Firmware Settings" {\n')
    lines.append("\tfwsetup\n")
    lines.append("}\n")
    lines.append("fi\n")
    return "".join(lines), warnings


def torito_flags(report_text: str) -> str:
    """Filter `-report_el_torito as_mkisofs` output down to the option flags."""
    parts: list[str] = []
    for raw in report_text.splitlines():
        line = raw.strip()
        if line.startswith("-"):
            parts.append(line)
        elif line.startswith("xorriso -as mkisofs"):
            rest = line[len("xorriso -as mkisofs"):].strip()
            if rest:
                parts.append(rest)
    flags = " ".join(parts)
    if not flags:
        raise ValueError("empty torito report: no mkisofs options found")
    for must in ("--grub2-mbr", "-append_partition"):
        if must not in flags:
            raise ValueError(f"torito report is missing {must}; refusing a generic repack")
    return flags


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="kintsugi render stage (seeds, grub, torito flags)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="render the nocloud seed tree")
    r.add_argument("--seed-dir", type=Path, default=SEED_DIR_DEFAULT)
    r.add_argument("--out", type=Path, required=True, help="isofiles root (nocloud/ is created inside)")
    r.add_argument("--repo-root", type=Path, default=REPO)
    r.add_argument("--password-hash", required=True, help="sha512crypt hash ($6$...) for mark")
    r.add_argument("--ssh-key", action="append", default=[], help="public key line (repeatable)")
    r.add_argument("--pin-serial", default=None, help="pin entry 1 to this disk serial (wrong-machine guard)")

    g = sub.add_parser("grub", help="generate boot/grub/grub.cfg from the stock cfg")
    g.add_argument("--stock-cfg", type=Path, required=True)
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("--pinned", action="store_true")
    g.add_argument("--timeout", type=int, default=30)
    g.add_argument("--default", type=int, default=1)

    t = sub.add_parser("torito", help="filter a -report_el_torito as_mkisofs report to repack flags")
    t.add_argument("--report-file", type=Path, required=True)
    t.add_argument("--out", type=Path, required=True)

    args = ap.parse_args(argv)
    if args.cmd == "render":
        if not args.ssh_key:
            die("no --ssh-key given; the seed must carry at least one key (keys-only ssh)")
        render_seeds(args.seed_dir, args.repo_root, args.out, args.password_hash,
                     args.ssh_key, args.pin_serial)
        return 0
    if args.cmd == "grub":
        cfg, warns = gen_grub_cfg(args.stock_cfg.read_text(encoding="utf-8"),
                                  pinned=args.pinned, timeout=args.timeout, default=args.default)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(cfg, encoding="utf-8")
        for w in warns:
            print(f"WARNING: {w}", file=sys.stderr)
        print(f"wrote {args.out}")
        return 0
    if args.cmd == "torito":
        flags = torito_flags(args.report_file.read_text(encoding="utf-8"))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(flags + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
