"""Compile the actual driver fan-read branch and check protocol examples."""
from pathlib import Path
import subprocess
import tempfile

source = (Path(__file__).parent / "driver/avell_dchu.c").read_text()
branch = source.split("static int avell_hwmon_read(", 1)[1].split("case hwmon_fan:", 1)[1].split("case hwmon_pwm:", 1)[0]
program = r"""
#include <assert.h>
#define DIV_ROUND_CLOSEST(n, d) (((n) + (d) / 2) / (d))
static long read_rpm(unsigned count, int channel) {
    unsigned char buf[8] = {0};
    struct { int rpm_hi; } fan_map[] = {{2}, {4}};
    long result = -1, *val = &result;
    buf[fan_map[channel].rpm_hi] = count >> 8;
    buf[fan_map[channel].rpm_hi + 1] = count & 255;
    switch (0) { case 0: BRANCH }
    return result;
}
int main(void) {
    for (int c = 0; c < 2; c++) {
        assert(read_rpm(0, c) == 0);
        assert(read_rpm(357, c) == 6040);
        assert(read_rpm(900, c) == 2396);
        assert(read_rpm(337, c) == 6398);
        assert(read_rpm(65535, c) == 33);
        for (unsigned n = 2; n <= 65535; n++)
            assert(read_rpm(n, c) <= read_rpm(n - 1, c));
    }
}
""".replace("BRANCH", branch)
with tempfile.TemporaryDirectory() as tmp:
    code = Path(tmp) / "rpm.c"
    code.write_text(program)
    exe = str(Path(tmp) / "rpm")
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", str(code), "-o", exe], check=True)
    subprocess.run([exe], check=True)
print("RPM: zero, byte order, Windows examples and inverse monotonicity OK")
