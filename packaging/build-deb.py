#!/usr/bin/env python3
"""Build a source-only DKMS .deb without root privileges."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
output = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "dist"
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="avell-deb-") as temp:
    stage = Path(temp)
    def copy(source, destination, mode=0o644):
        target = stage / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / source, target)
        target.chmod(mode)
    copy("packaging/control", "DEBIAN/control")
    for script in ("preinst", "postinst", "prerm", "postrm"):
        copy("packaging/" + script, "DEBIAN/" + script, 0o755)
    for filename in ("avell_dchu.c", "avell_dchu_ioctl.h", "Makefile", "dkms.conf"):
        copy("driver/" + filename, "usr/src/avell-dchu-1.0.0/" + filename)
    for file in (root / "avell_cc").glob("*.py"):
        copy(str(file.relative_to(root)), "usr/lib/avell-cc/avell_cc/" + file.name)
    for filename in ("avellctl", "avell-control-center"):
        copy("bin/" + filename, "usr/bin/" + filename, 0o755)
    for filename in ("avell-cc-helper", "avell-fan-curve"):
        copy("bin/" + filename, "usr/libexec/avell-cc/" + filename, 0o755)
    configs = []
    for filename in ("avell-cc-restore.service", "avell-fan-curve.service"):
        target = "etc/systemd/system/" + filename
        copy("data/" + filename, target)
        configs.append("/" + target)
    copy("data/avell-dchu.conf", "etc/modules-load.d/avell-dchu.conf")
    configs.append("/etc/modules-load.d/avell-dchu.conf")
    (stage / "DEBIAN/conffiles").write_text("\n".join(configs) + "\n")
    copy("data/br.com.avell.controlcenter.policy", "usr/share/polkit-1/actions/br.com.avell.controlcenter.policy")
    copy("data/avell-control-center.desktop", "usr/share/applications/avell-control-center.desktop")
    copy("LICENSE", "usr/share/doc/avell-control-center/copyright")
    copy("README.md", "usr/share/doc/avell-control-center/README.md")
    payload = [f for f in stage.rglob("*") if f.is_file() and "DEBIAN" not in f.relative_to(stage).parts]
    size = (sum(f.stat().st_size for f in payload) + 1023) // 1024
    with (stage / "DEBIAN/control").open("a") as control:
        control.write(f"Installed-Size: {size}\n")
    (stage / "DEBIAN/md5sums").write_text("".join(
        f"{hashlib.md5(f.read_bytes()).hexdigest()}  {f.relative_to(stage)}\n"
        for f in sorted(payload)))
    destination = output / "avell-control-center_1.1.0_amd64.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(destination)], check=True)
    print(destination)
