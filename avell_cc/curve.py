"""Curvas CPU/GPU para o A50i; proteção térmica tem prioridade."""
import json
import math
import os
import signal
import socket
import sys
import time
from pathlib import Path

from . import hw
from .cli import raw_call

RUNTIME = Path("/run/avell-fan-curve.json")
DEFAULT = {"CPU": [45, 80, 35, 100], "GPU": [45, 80, 35, 100]}
CRITICAL = 90


def validate(value):
    if not isinstance(value, dict) or set(value) != {"CPU", "GPU"}:
        raise ValueError("Informe curvas para CPU e GPU")
    for values in value.values():
        if not isinstance(values, list) or len(values) != 4 or any(type(v) is not int for v in values):
            raise ValueError("Cada curva precisa de quatro números inteiros")
        low, high, minimum, maximum = values
        if not (30 <= low < high <= 85 and 30 <= minimum <= maximum <= 100):
            raise ValueError("Temperaturas: 30 ≤ início < fim ≤ 85 °C; ventilação: 30 ≤ mínimo ≤ máximo ≤ 100%")
    return value


def target(temp, values):
    if not isinstance(temp, (int, float)) or not math.isfinite(temp) or not 1 <= temp <= 125:
        raise ValueError("Temperatura indisponível ou inválida")
    if temp >= CRITICAL:
        return 100
    low, high, minimum, maximum = values
    fraction = max(0, min(1, (temp - low) / (high - low)))
    return round(minimum + fraction * (maximum - minimum))


def check_model():
    if Path("/sys/class/dmi/id/product_name").read_text().strip() != "A50i":
        raise RuntimeError("Curva validada somente para Avell A50i")
    _, info = raw_call(0x0d)
    if len(info) < 16 or info[12] != 2:
        raise RuntimeError("O firmware não confirmou duas ventoinhas")


def release():
    # Clevo Windows: 0x69 bit mask returns the two channels to EC control.
    raw_call(0x69, (3).to_bytes(4, "little"))


def failsafe():
    try:
        release()
    finally:
        hw.apply("fan", "max")


def write_duties(duties):
    for channel, duty in enumerate(duties, 1):
        if type(duty) is not int or not 30 <= duty <= 100:
            raise ValueError("Ventilação fora dos limites")
        # Same per-channel PK04(1)/EC C1 packet as the Windows implementation.
        raw_call(4, bytes([1, channel, (duty * 255 + 50) // 100, 0, 0, 0, 0xc1]))


def notify(text):
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(text.encode(), address)


def save_runtime(value):
    tmp = RUNTIME.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    tmp.replace(RUNTIME)


def main():
    if sys.argv[1:] == ["failsafe"]:
        failsafe()
        save_runtime({"active": False, "time": time.time(), "error": "Curva encerrada; ventilação máxima de proteção"})
        return
    check_model()
    config = validate(hw.load_state().get("curve"))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    previous = None
    last_tick = time.clock_gettime(time.CLOCK_BOOTTIME)
    try:
        release()
        hw.apply("fan", "auto")
        while True:
            # Do not continue a stale override after suspend or a stalled loop.
            if time.clock_gettime(time.CLOCK_BOOTTIME) - last_tick > 8:
                raise RuntimeError("Monitoramento interrompido; proteção ativada")
            last_tick = time.clock_gettime(time.CLOCK_BOOTTIME)
            sensors = {s["label"]: s for s in hw.sensors()}
            if not all(label in sensors for label in ("CPU", "GPU")):
                raise RuntimeError("Sensores de ventilação indisponíveis")
            duties = [target(sensors[label]["temp"], config[label]) for label in ("CPU", "GPU")]
            # One hot component triggers full cooling on both fans.
            if any(sensors[label]["temp"] >= CRITICAL for label in ("CPU", "GPU")):
                duties = [100, 100]
            if previous is not None:
                for label, expected in zip(("CPU", "GPU"), previous):
                    actual = sensors[label]["duty"]
                    if sensors[label]["rpm"] is None or sensors[label]["rpm"] <= 0:
                        raise RuntimeError("Rotação indisponível ou ventoinha parada")
                    if actual is None or abs(actual - expected) > 8:
                        raise RuntimeError("Firmware não confirmou a ventilação solicitada")
            # Slow only the descent, never the response to rising heat.
            if previous is not None:
                duties = [max(v, p - 5) for v, p in zip(duties, previous)]
            write_duties(duties)
            if previous is None:
                notify("READY=1")
            previous = duties
            save_runtime({"active": True, "time": time.time(), "duties": duties})
            notify("WATCHDOG=1")
            time.sleep(2)
    except Exception as exc:
        save_runtime({"active": False, "time": time.time(), "error": str(exc)})
        raise
    finally:
        failsafe()
        if RUNTIME.exists():
            status = json.loads(RUNTIME.read_text())
            status["active"] = False
            save_runtime(status)


if __name__ == "__main__":
    main()
