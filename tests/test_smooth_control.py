import json
from pathlib import Path
import sys
from types import SimpleNamespace
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import smooth_control as smooth


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.positions = dict.fromkeys(smooth.ARM_NAMES, 2048)
        self.planner = smooth.Planner(dict.fromkeys(smooth.ARM_NAMES, (100, 4000)))
        self.planner.reset(self.positions)

    def test_ramps_instead_of_jumping_to_fast_speed(self):
        base, targets = self.planner.step({"w", "r"}, "both", 3, self.positions, 1 / 30)
        self.assertAlmostEqual(base[0], 0.02)
        self.assertGreater(targets["arm_shoulder_pan"], 2048)
        self.assertLess(targets["arm_shoulder_pan"], 2048 + 90 * smooth.TICKS_PER_DEGREE / 30)

    def test_held_key_moves_continuously_without_repeat_events(self):
        previous = 2048
        for _ in range(30):
            base, targets = self.planner.step({"w", "r"}, "both", 1, self.positions, 1 / 30)
            self.assertGreater(targets["arm_shoulder_pan"], previous)
            previous = targets["arm_shoulder_pan"]
            self.positions = targets
        self.assertAlmostEqual(base[0], 0.1)
        self.assertGreater(previous - 2048, 140)

    def test_diagonal_speed_is_not_faster_than_selected_profile(self):
        for _ in range(60):
            base, _ = self.planner.step({"w", "a"}, "base", 3, self.positions, 1 / 30)
        self.assertAlmostEqual((base[0] ** 2 + base[1] ** 2) ** 0.5, 0.3)

    def test_multiple_arm_joints_and_base_can_move_together(self):
        base, targets = self.planner.step({"w", "r", "y", "i"}, "both", 2, self.positions, 1 / 30)
        self.assertGreater(base[0], 0)
        for name in ("arm_shoulder_pan", "arm_elbow_flex", "arm_wrist_roll"):
            self.assertGreater(targets[name], 2048)
        self.assertEqual(targets["arm_gripper"], 2048)

    def test_opposite_keys_cancel(self):
        base, targets = self.planner.step({"w", "s", "r", "f"}, "both", 3, self.positions, 1 / 30)
        self.assertEqual(base, (0, 0, 0))
        self.assertEqual(targets, self.positions)

    def test_release_arm_holds_measured_pose_instead_of_chasing_old_target(self):
        for _ in range(10):
            self.planner.step({"r"}, "arm", 3, self.positions, 1 / 30)
        self.assertGreater(self.planner.targets["arm_shoulder_pan"], 2048)
        _, targets = self.planner.step(set(), "arm", 3, self.positions, 1 / 30)
        self.assertEqual(targets["arm_shoulder_pan"], 2048)

    def test_stop_reset_immediately_zeros_velocity_and_rebases_targets(self):
        for _ in range(30):
            self.planner.step({"w", "r"}, "both", 3, self.positions, 1 / 30)
        self.planner.reset(self.positions)
        self.assertEqual(self.planner.base, [0, 0, 0])
        self.assertEqual(self.planner.targets, self.positions)
        self.assertFalse(any(self.planner.arm_velocity.values()))

    def test_lag_does_not_accumulate_a_long_queue_of_arm_movement(self):
        for _ in range(120):
            _, targets = self.planner.step({"r"}, "arm", 3, self.positions, 1 / 30)
        self.assertLessEqual(targets["arm_shoulder_pan"] - 2048, 83)

    def test_joint_limit_and_outside_calibration(self):
        self.positions["arm_shoulder_pan"] = 3999
        self.planner.reset(self.positions)
        _, targets = self.planner.step({"r"}, "arm", 3, self.positions, 1 / 30)
        self.assertEqual(targets["arm_shoulder_pan"], 4000)
        self.positions["arm_shoulder_pan"] = 4000 + smooth.FEEDBACK_TOLERANCE + 1
        with self.assertRaises(ValueError):
            self.planner.step({"r"}, "arm", 3, self.positions, 1 / 30)

    def test_reported_elbow_one_tick_over_limit_is_tolerated_without_widening_targets(self):
        self.planner.limits["arm_elbow_flex"] = (1181, 3389)
        self.positions["arm_elbow_flex"] = 3390
        self.planner.reset(self.positions)
        self.assertEqual(self.planner.targets["arm_elbow_flex"], 3389)
        _, targets = self.planner.step({"y"}, "both", 3, self.positions, 1 / 30)
        self.assertLessEqual(targets["arm_elbow_flex"], 3389)
        _, targets = self.planner.step(set(), "both", 3, self.positions, 1 / 30)
        self.assertLessEqual(targets["arm_elbow_flex"], 3389)
        self.assertEqual(self.planner.limits["arm_elbow_flex"], (1181, 3389))

    def test_small_endpoint_error_allows_motion_back_into_range(self):
        self.positions["arm_shoulder_pan"] = 4001
        self.planner.reset(self.positions)
        _, targets = self.planner.step({"f"}, "arm", 1, self.positions, 1 / 30)
        self.assertLess(targets["arm_shoulder_pan"], 4000)

    def test_lower_endpoint_tolerance_and_larger_error_still_rejected(self):
        self.positions["arm_gripper"] = 99
        self.planner.reset(self.positions)
        self.assertEqual(self.planner.targets["arm_gripper"], 100)
        self.positions["arm_gripper"] = 100 - smooth.FEEDBACK_TOLERANCE - 1
        with self.assertRaises(ValueError):
            self.planner.reset(self.positions)

    def test_hardware_stop_holds_are_clamped_before_any_velocity_write(self):
        device = smooth.SmoothHardware.__new__(smooth.SmoothHardware)
        device.limits = dict(self.planner.limits)
        events = []
        device.move_base = lambda *base: events.append(("base", base))
        device.bus = SimpleNamespace(sync_write=lambda register, targets, **kwargs:
                                     events.append((register, targets)))
        targets = dict(self.positions)
        targets["arm_elbow_flex"] = 4001
        device.send((0, 0, 0), targets)
        self.assertEqual(events[-1][1]["arm_elbow_flex"], 4000)
        events.clear()
        targets["arm_elbow_flex"] = 4009
        with self.assertRaises(ValueError):
            device.send((0.1, 0, 0), targets)
        self.assertEqual(events, [])

    def test_long_frame_cannot_create_large_target_jump(self):
        base, _ = self.planner.step({"w"}, "base", 3, self.positions, 5)
        self.assertAlmostEqual(base[0], 0.03)

    def test_separate_modes_filter_keys(self):
        base, targets = self.planner.step({"w", "r"}, "base", 3, self.positions, 1 / 30)
        self.assertGreater(base[0], 0)
        self.assertEqual(targets, self.positions)
        self.planner.reset(self.positions)
        base, targets = self.planner.step({"w", "r"}, "arm", 3, self.positions, 1 / 30)
        self.assertEqual(base, (0, 0, 0))
        self.assertGreater(targets["arm_shoulder_pan"], 2048)


class InputStateTests(unittest.TestCase):
    def setUp(self):
        self.state = smooth.InputState()

    def enable(self, now=0):
        self.state.apply({"action": "enable", "epoch": self.state.epoch}, now)
        self.state.ready = True

    def packet(self, sequence=1, **changes):
        return {"action": "input", "epoch": self.state.epoch, "sequence": sequence,
                "keys": ["w", "r"], **changes}

    def test_starts_disarmed(self):
        self.assertFalse(self.state.snapshot(0)["enabled"])

    def test_lost_heartbeat_stops_both_and_latches(self):
        self.enable()
        packet = self.packet()
        self.state.apply(packet, 0.05)
        snapshot = self.state.snapshot(0.36)
        self.assertFalse(snapshot["enabled"])
        self.assertFalse(snapshot["keys"])
        with self.assertRaises(ValueError):
            self.state.apply(packet, 0.37)

    def test_input_received_after_expiry_cannot_revive_motion(self):
        self.enable()
        with self.assertRaises(ValueError):
            self.state.apply(self.packet(), 0.31)
        self.assertFalse(self.state.enabled)

    def test_stop_invalidates_delayed_enable_and_input(self):
        stale_enable = {"action": "enable", "epoch": self.state.epoch}
        self.enable()
        stale_input = self.packet()
        self.state.apply({"action": "stop"}, 0.1)
        for message in (stale_enable, stale_input):
            with self.assertRaises(ValueError):
                self.state.apply(message, 0.11)

    def test_stale_sequence_does_not_refresh_watchdog(self):
        self.enable()
        self.state.apply(self.packet(2), 0.05)
        with self.assertRaises(ValueError):
            self.state.apply(self.packet(1), 0.2)
        self.assertEqual(self.state.last_seen, 0.05)

    def test_invalid_packets_do_not_refresh_watchdog(self):
        self.enable()
        for changes in ({"keys": ["!"]}, {"profile": 4}, {"mode": "unknown"}, {"keys": "w"}):
            with self.assertRaises(ValueError):
                self.state.apply(self.packet(**changes), 0.1)
        self.assertEqual(self.state.last_seen, 0)

    def test_preparing_does_not_accept_held_movement(self):
        self.state.apply({"action": "enable", "epoch": 0}, 0)
        self.state.apply(self.packet(), 0.05)
        self.assertEqual(self.state.keys, set())

    def test_release_stops_and_requests_torque_release(self):
        self.enable()
        self.state.apply({"action": "release"}, 0.1)
        self.assertFalse(self.state.enabled)
        self.assertTrue(self.state.release)

    def test_fault_blocks_enabling(self):
        self.state.error = "motor overheated"
        with self.assertRaisesRegex(ValueError, "overheated"):
            self.state.apply({"action": "enable", "epoch": 0}, 0)


class TorqueTests(unittest.TestCase):
    def test_full_torque_respects_existing_configured_ceiling(self):
        self.assertEqual(smooth.torque_limits({"a": 1000, "b": 700}), {"a": 1000, "b": 700})

    def test_invalid_torque_register_fails(self):
        for value in (0, -1, 1001):
            with self.assertRaises(ValueError):
                smooth.torque_limits({"a": value})


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.state = smooth.InputState()
        self.server = smooth.ThreadingHTTPServer(("127.0.0.1", 0), smooth.handler_type(self.state, "test-token", "TEST PAGE"))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def call(self, command=None, token="test-token"):
        headers = {"X-Control-Token": token, "Content-Type": "application/json"}
        request = Request(self.url + ("/control" if command is not None else "/status"),
                          data=json.dumps(command).encode() if command is not None else None,
                          headers=headers)
        with urlopen(request, timeout=2) as response:
            return json.load(response)

    def test_token_required_for_commands_and_status(self):
        for command in (None, {"action": "enable", "epoch": 0}):
            with self.assertRaises(HTTPError) as caught:
                self.call(command, token="wrong-token")
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()
        self.assertFalse(self.state.enabled)

    def test_http_enable_stop_and_stale_input(self):
        status = self.call()
        self.assertFalse(status["enabled"])
        enabled = self.call({"action": "enable", "epoch": status["epoch"]})
        self.assertTrue(enabled["enabled"])
        stopped = self.call({"action": "stop"})
        self.assertFalse(stopped["enabled"])
        with self.assertRaises(HTTPError) as caught:
            self.call({"action": "input", "epoch": enabled["epoch"], "sequence": 1, "keys": ["w"]})
        caught.exception.close()


class LoopTests(unittest.TestCase):
    def test_running_loop_stops_both_on_input_loss_and_keeps_arm_holding(self):
        hardware = smooth.SimulatedHardware()
        state = smooth.InputState()
        shutdown = threading.Event()
        worker = threading.Thread(target=smooth.control_loop, args=(hardware, state, shutdown))
        def wait_for(condition):
            deadline = time.monotonic() + 2
            while not condition():
                if time.monotonic() >= deadline:
                    self.fail("Control-loop condition timed out")
                time.sleep(.01)
        try:
            state.apply({"action": "enable", "epoch": 0}, time.monotonic())
            worker.start()
            wait_for(lambda: state.ready)
            for sequence in range(1, 4):
                state.apply({"action": "input", "epoch": state.epoch, "sequence": sequence,
                             "keys": ["w", "r"]}, time.monotonic())
                time.sleep(.05)
            self.assertGreater(hardware.base[0], 0)
            self.assertGreater(hardware.pose["arm_shoulder_pan"], 2048)
            wait_for(lambda: not state.enabled)
            wait_for(lambda: hardware.base == (0, 0, 0))
            self.assertTrue(state.powered)
            pose = dict(hardware.pose)
            time.sleep(.05)
            self.assertEqual(hardware.pose, pose)
        finally:
            shutdown.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_idle_server_does_not_prepare_motors(self):
        hardware = smooth.SimulatedHardware()
        hardware.prepare = lambda: self.fail("Must not enable motors on startup")
        shutdown = threading.Event()
        state = smooth.InputState()
        worker = threading.Thread(target=smooth.control_loop, args=(hardware, state, shutdown))
        worker.start()
        time.sleep(.08)
        shutdown.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(hardware.commands, 0)


if __name__ == "__main__":
    unittest.main()
