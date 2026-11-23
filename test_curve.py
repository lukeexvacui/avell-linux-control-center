"""Run with python3 test_curve.py; no hardware writes."""
from unittest.mock import patch
from avell_cc import curve, helper

assert curve.validate(curve.DEFAULT) == curve.DEFAULT
for invalid in ({}, {"CPU": [45, 80, 35, 100]},
                {"CPU": [80, 45, 35, 100], "GPU": [45, 80, 35, 100]},
                {"CPU": [45, 80, 0, 100], "GPU": [45, 80, 35, 100]},
                {"CPU": [45, 80, True, 100], "GPU": [45, 80, 35, 100]}):
    try: curve.validate(invalid)
    except ValueError: pass
    else: raise AssertionError(invalid)
values = [40, 80, 30, 70]
assert [curve.target(t, values) for t in (20, 40, 60, 80, 89, 90, 100)] == [30, 30, 50, 70, 70, 100, 100]
for invalid in (None, float("nan"), -1, 0, 126):
    try: curve.target(invalid, values)
    except ValueError: pass
    else: raise AssertionError(invalid)
with patch.object(curve, "raw_call") as raw:
    curve.write_duties([30, 100])
    assert raw.call_args_list[0].args == (4, bytes([1, 1, 77, 0, 0, 0, 193]))
    assert raw.call_args_list[1].args == (4, bytes([1, 2, 255, 0, 0, 0, 193]))
    curve.release()
    assert raw.call_args.args == (105, bytes([3, 0, 0, 0]))
with patch.object(curve, "release", side_effect=OSError), patch.object(curve.hw, "apply") as apply:
    try: curve.failsafe()
    except OSError: pass
    assert apply.call_args.args == ("fan", "max")
# Missing sensor must never issue a manual duty command; cleanup is mandatory.
with patch.object(curve, "check_model"), patch.object(curve.hw, "load_state", return_value={"curve": curve.DEFAULT}), patch.object(curve.signal, "signal"), patch.object(curve, "release"), patch.object(curve.hw, "apply"), patch.object(curve.hw, "sensors", return_value=[]), patch.object(curve, "write_duties") as write, patch.object(curve, "save_runtime"), patch.object(curve, "failsafe") as safe, patch.object(curve, "RUNTIME") as runtime:
    runtime.exists.return_value = False
    try: curve.main()
    except RuntimeError: pass
    else: raise AssertionError("missing sensor accepted")
    write.assert_not_called()
    safe.assert_called_once()
print("Curves, boundaries, emergency override, EC packets and failure cleanup OK")
