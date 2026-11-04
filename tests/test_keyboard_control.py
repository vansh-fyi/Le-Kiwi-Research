import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


spec = importlib.util.spec_from_file_location(
    "keyboard_control", Path(__file__).resolve().parents[1] / "scripts/keyboard_control.py"
)
keyboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keyboard)


class FakeBus:
    def __init__(self):
        self.events = []
        self.is_connected = True

    def disable_torque(self, names, **kwargs):
        self.events.append(("disable", tuple(names)))

    def enable_torque(self, names):
        self.events.append(("enable", tuple(names)))

    def write(self, register, name, value, **kwargs):
        self.events.append(("write", register, name, value))

    def sync_write(self, register, values, **kwargs):
        self.events.append(("sync_write", register, dict(values)))

    def disconnect(self, disable_torque=True):
        self.events.append(("disconnect", disable_torque))
        self.is_connected = False


class FakeMotion:
    def __init__(self):
        self.events = []

    def move_base(self, *values):
        self.events.append(("move", values))

    def stop(self):
        self.events.append(("stop",))

    def jog_arm(self, *values):
        self.events.append(("arm", values))


def hardware():
    instance = keyboard.Hardware.__new__(keyboard.Hardware)
    instance.bus = FakeBus()
    instance.robot = SimpleNamespace(
        base_motors=["left", "back", "right"], arm_motors=["arm"],
        calibration={"arm": SimpleNamespace(range_min=100, range_max=300)},
    )
    instance.mode = None
    instance.touched_base = False
    instance.touched_arm = False
    instance.positions = lambda: {"arm": 200}
    return instance


class KeyboardControlTests(unittest.TestCase):
    def test_pulse_stops_without_more_keyboard_input(self):
        motion = FakeMotion()
        jogger = keyboard.Jogger(motion, "base", pulse=0.12)
        jogger.key("w", 0)
        jogger.tick(0.1)
        self.assertEqual(len(motion.events), 1)
        jogger.tick(0.13)
        self.assertEqual(motion.events, [("move", (0.03, 0, 0)), ("stop",)])

    def test_repeats_do_not_extend_active_pulse(self):
        motion = FakeMotion()
        jogger = keyboard.Jogger(motion, "base", pulse=0.12)
        jogger.key("w", 0)
        jogger.key("w", 0.11)
        jogger.key("d", 0.115)
        jogger.tick(0.13)
        self.assertEqual(len(motion.events), 2)
        self.assertEqual(motion.events[-1], ("stop",))

    def test_stop_and_quit_stop_immediately(self):
        motion = FakeMotion()
        jogger = keyboard.Jogger(motion, "base")
        jogger.key("w", 0)
        self.assertTrue(jogger.key(" ", 0.01))
        self.assertIsNone(jogger.deadline)
        self.assertFalse(jogger.key("q", 0.02))
        self.assertEqual(motion.events[-1], ("stop",))

    def test_queued_keys_do_not_replay_and_stop_has_priority(self):
        self.assertEqual(keyboard.newest_key("wwwwd"), "d")
        self.assertEqual(keyboard.newest_key("w wd"), " ")
        self.assertEqual(keyboard.newest_key("wqwd"), "q")
        self.assertEqual(keyboard.newest_key("wQwd"), "q")
        self.assertEqual(keyboard.newest_key(""), "q")
        self.assertEqual(keyboard.newest_key("\x1b[A"), "q")

    def test_arm_steps_are_clamped_and_bad_current_position_rejected(self):
        self.assertEqual(keyboard.bounded_target(295, 1, 11, 100, 300), 300)
        self.assertEqual(keyboard.bounded_target(105, -1, 11, 100, 300), 100)
        with self.assertRaises(ValueError):
            keyboard.bounded_target(99, 1, 11, 100, 300)

    def test_wheel_position_offset_is_not_an_arm_calibration_failure(self):
        def cal(offset):
            return SimpleNamespace(id=1, homing_offset=offset, range_min=0, range_max=4095)
        saved = {"arm": cal(-100), "wheel": cal(0)}
        actual = {"arm": cal(-100), "wheel": cal(85)}
        self.assertEqual(keyboard.calibration_differences(saved, actual, ["arm"]), [])
        self.assertEqual(keyboard.calibration_differences(saved, actual, ["wheel"]), ["wheel"])
        actual["arm"] = cal(0)
        self.assertEqual(keyboard.calibration_differences(saved, actual, ["arm"]), ["arm"])

    def test_arm_key_rate_limit_and_separate_modes(self):
        motion = FakeMotion()
        jogger = keyboard.Jogger(motion, "arm")
        jogger.key("w", 0)
        jogger.key("r", 0)
        jogger.key("r", 0.1)
        jogger.key("f", 0.26)
        self.assertEqual(motion.events, [
            ("arm", ("arm_shoulder_pan", 1)), ("arm", ("arm_shoulder_pan", -1))])

    def test_base_startup_zeros_wheels_before_enabling_and_leaves_arm_alone(self):
        device = hardware()
        device.prepare("base")
        events = device.bus.events
        self.assertEqual(events[0], ("disable", ("left", "back", "right")))
        zero = ("sync_write", "Goal_Velocity", {"left": 0, "back": 0, "right": 0})
        self.assertLess(events.index(zero), events.index(("enable", ("left", "back", "right"))))
        self.assertFalse(device.touched_arm)

    def test_arm_startup_sets_present_pose_before_enabling(self):
        device = hardware()
        device.prepare("arm")
        events = device.bus.events
        self.assertLess(events.index(("sync_write", "Goal_Position", {"arm": 200})),
                        events.index(("enable", ("arm",))))
        self.assertNotIn(("enable", ("left", "back", "right")), events)

    def test_arm_out_of_range_does_not_enable_torque(self):
        device = hardware()
        device.positions = lambda: {"arm": 350}
        with self.assertRaises(ValueError):
            device.prepare("arm")
        self.assertNotIn(("enable", ("arm",)), device.bus.events)

    def test_readonly_cleanup_does_not_write_or_change_torque(self):
        device = hardware()
        device.close()
        self.assertEqual(device.bus.events, [("disconnect", False)])

    def test_cleanup_attempts_torque_release_even_if_stop_fails(self):
        device = hardware()
        device.touched_base = True
        device.stop = lambda: (_ for _ in ()).throw(OSError("USB lost"))
        with self.assertRaisesRegex(RuntimeError, "Base stop failed"):
            device.close()
        self.assertIn(("disable", ("left", "back", "right")), device.bus.events)
        self.assertFalse(device.bus.is_connected)


if __name__ == "__main__":
    unittest.main()
