#!/usr/bin/env python3
"""Validate the built archive without installing or running privileged scripts."""
import io
import subprocess
import sys
import tarfile
from pathlib import Path

package = Path(sys.argv[1]).resolve()
metadata = subprocess.check_output(["dpkg-deb", "-f", str(package)], text=True)
assert "Package: avell-control-center" in metadata
assert "Architecture: amd64" in metadata
assert "Installed-Size:" in metadata
assert "python3-gi-cairo" in metadata
payload = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(package)])
with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
    members = {m.name.removeprefix("./"): m for m in archive.getmembers()}
    for name in ("usr/bin/avellctl", "usr/bin/avell-control-center",
                 "usr/libexec/avell-cc/avell-fan-curve", "usr/libexec/avell-cc/avell-cc-helper"):
        assert members[name].mode == 0o755, name
    for name in ("usr/src/avell-dchu-1.0.0/avell_dchu.c", "usr/src/avell-dchu-1.0.0/dkms.conf",
                 "usr/lib/avell-cc/avell_cc/curve.py", "etc/systemd/system/avell-cc-restore.service",
                 "etc/systemd/system/avell-fan-curve.service", "usr/share/doc/avell-control-center/copyright"):
        assert name in members, name
    assert all(m.uid == 0 and m.gid == 0 for m in members.values())
    assert not any(n.endswith((".ko", ".pyc", ".priv", "state.json")) or "__pycache__" in n for n in members)
    for name, member in members.items():
        if member.isfile():
            assert b"/home/martins" not in archive.extractfile(member).read(), name
for script in ("preinst", "postinst", "prerm", "postrm"):
    source = subprocess.check_output(["dpkg-deb", "-I", str(package), script])
    subprocess.run(["sh", "-n"], input=source, check=True)
print("DEB: metadata, payload, root ownership, executable modes, privacy and script syntax OK")
