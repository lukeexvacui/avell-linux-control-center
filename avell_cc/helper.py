"""Helper privilegiado (executado via pkexec ou pelo serviço de boot).

Valida estritamente os argumentos antes de tocar no sysfs e persiste o
estado em /var/lib/avell-cc/state.json para ser restaurado no boot.
"""

import fcntl
import json
import subprocess
import re
import sys

from . import hw, curve

ORDER = ["profile", "fan", "kbd", "color", "effect", "charge"]


def _parse(args):
    if not args:
        raise ValueError("uso: avell-cc-helper <op> [args]")
    op, rest = args[0], args[1:]
    if op == "curve" and len(rest) == 1:
        return op, curve.validate(json.loads(rest[0]))
    if op == "profile" and len(rest) == 1 and rest[0] in hw.PROFILES:
        return op, rest[0]
    if op == "fan" and len(rest) == 1 and rest[0] in hw.FAN_MODES:
        return op, rest[0]
    if op == "effect" and len(rest) == 1 and rest[0] in hw.KBD_EFFECTS:
        return op, rest[0]
    if op == "color" and len(rest) == 1 and re.fullmatch(r"[0-9a-fA-F]{6}", rest[0]):
        return op, rest[0].lower()
    if op == "kbd" and len(rest) == 1 and rest[0].isdigit() and int(rest[0]) <= 10:
        return op, int(rest[0])
    if op == "charge" and len(rest) == 2 and all(x.isdigit() for x in rest):
        start, end = map(int, rest)
        if 0 <= start < end <= 100 and end >= 50:
            return op, [start, end]
    if op == "antidust" and not rest:
        return op, None
    if op == "restore" and not rest:
        return op, None
    raise ValueError(f"argumentos inválidos: {' '.join(args)}")


def restore():
    state = hw.load_state()
    errors = []
    for key in ORDER:
        if key in state:
            try:
                if key == "fan" and state[key] == "curve":
                    curve.validate(state.get("curve"))
                    subprocess.run(["systemctl", "restart", "avell-fan-curve.service"], check=True)
                else:
                    hw.apply(key, state[key])
            except Exception as e:  # noqa: BLE001 - segue restaurando o resto
                errors.append(f"{key}: {e}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return "restaurado\n"


def _run(args):
    op, value = _parse(args)
    if op == "restore":
        return restore()
    if op == "curve":
        curve.check_model()
        state = hw.load_state()
        state.update(curve=value, fan="curve")
        hw.save_state(state)
        try:
            subprocess.run(["systemctl", "restart", "avell-fan-curve.service"], check=True)
        except Exception:
            curve.failsafe()
            raise
        return "Curva ativada\n"
    if op == "fan":
        subprocess.run(["systemctl", "stop", "avell-fan-curve.service"], check=True)
        curve.release()
        curve.RUNTIME.unlink(missing_ok=True)
    hw.apply(op, value)
    if op != "antidust":
        state = hw.load_state()
        state[op] = value
        if op in ("color", "effect"):
            # o driver decide se a cor mantém o efeito; salva o que ficou
            st = hw.status()
            state["color"] = st.get("kbd_color") or state.get("color")
            state["effect"] = st.get("kbd_effect") or "static"
        hw.save_state(state)
    return "ok\n"


def run(args):
    # Serialize hardware writes and read-modify-write of the shared state.
    with open("/run/avell-cc.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run(args)


def main():
    try:
        sys.stdout.write(run(sys.argv[1:]))
    except Exception as e:  # noqa: BLE001
        print(f"erro: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
