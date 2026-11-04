#!/usr/bin/env python3
"""Jog LeKiwi from a local or SSH terminal. Requires LeRobot 0.6.1."""

import argparse
import importlib.metadata
import math
import os
import select
import signal
import sys
import termios
import time
import tty


BASE_KEYS = {
    "w": (1, 0, 0), "s": (-1, 0, 0),
    "a": (0, 1, 0), "d": (0, -1, 0),
    "z": (0, 0, 1), "x": (0, 0, -1),
}
ARM_KEYS = {
    "r": ("arm_shoulder_pan", 1), "f": ("arm_shoulder_pan", -1),
    "t": ("arm_shoulder_lift", 1), "g": ("arm_shoulder_lift", -1),
    "y": ("arm_elbow_flex", 1), "h": ("arm_elbow_flex", -1),
    "u": ("arm_wrist_flex", 1), "j": ("arm_wrist_flex", -1),
    "i": ("arm_wrist_roll", 1), "k": ("arm_wrist_roll", -1),
    "o": ("arm_gripper", 1), "l": ("arm_gripper", -1),
}


def newest_key(data):
    """Discard queued movement; stopping and quitting take priority."""
    if not data:
        return "q"
    data = data.lower()
    if any(key in data for key in ("q", "\x03", "\x04", "\x1b")):
        return "q"
    if " " in data:
        return " "
    return data[-1]


def bounded_target(current, direction, step, low, high):
    if not low <= current <= high:
        raise ValueError(f"Present position {current} is outside calibration [{low}, {high}].")
    return max(low, min(high, current + direction * step))


def calibration_differences(saved, actual, names):
    fields = ("id", "homing_offset", "range_min", "range_max")
    return [name for name in names if name not in actual or any(
        getattr(saved[name], field) != getattr(actual[name], field) for field in fields
    )]


class Hardware:
    def __init__(self, port, robot_id):
        version = importlib.metadata.version("lerobot")
        if version != "0.6.1":
            raise RuntimeError(f"This controller was checked against LeRobot 0.6.1; found {version}.")
        from lerobot.robots.lekiwi import LeKiwi, LeKiwiConfig

        self.robot = LeKiwi(LeKiwiConfig(port=port, id=robot_id, cameras={}))
        self.bus = self.robot.bus
        self.mode = None
        self.touched_base = False
        self.touched_arm = False
        self.wheel_calibration_notes = []

    def connect(self):
        if not self.robot.calibration:
            raise RuntimeError(f"Missing calibration: {self.robot.calibration_fpath}")
        if set(self.robot.calibration) != set(self.bus.motors):
            raise RuntimeError("Calibration must contain exactly the nine LeKiwi motors.")
        for name, cal in self.robot.calibration.items():
            if cal.id != self.bus.motors[name].id or not 0 <= cal.range_min < cal.range_max <= 4095:
                raise RuntimeError(f"Invalid calibration for {name}.")
        self.bus.connect(handshake=False)
        self.bus.set_baudrate(1_000_000)
        self.bus.port_handler.ser.write_timeout = 0.25
        for name in self.bus.motors:
            expected = self.bus.model_number_table[self.bus.motors[name].model]
            if self.bus.ping(name, num_retry=2) != expected:
                raise RuntimeError(f"Expected motor did not respond: {name}")
        actual = self.bus.read_calibration()
        differences = calibration_differences(self.robot.calibration, actual, self.robot.arm_motors)
        if differences:
            raise RuntimeError(f"Arm calibration differs from the motors: {', '.join(differences)}")
        self.wheel_calibration_notes = calibration_differences(
            self.robot.calibration, actual, self.robot.base_motors)

    def positions(self):
        return self.bus.sync_read("Present_Position", self.robot.arm_motors,
                                  normalize=False, num_retry=2)

    def check(self):
        print(f"Calibration: {self.robot.calibration_fpath}")
        print("All nine motor IDs respond; all six arm calibrations match.")
        if self.wheel_calibration_notes:
            print("Wheel position-calibration differences (not used for velocity control): "
                  + ", ".join(self.wheel_calibration_notes))
        for name, pos in self.positions().items():
            cal = self.robot.calibration[name]
            state = "in range" if cal.range_min <= pos <= cal.range_max else "OUTSIDE RANGE"
            print(f"{name}: {pos} [{cal.range_min}, {cal.range_max}] {state}")
        print("Read-only check finished; no motor settings or torque changed.")

    def prepare(self, mode):
        self.mode = mode
        self.touched_base = True
        self.bus.disable_torque(self.robot.base_motors)
        for name in self.robot.base_motors:
            self.bus.write("Operating_Mode", name, 1)  # STS3215 velocity mode
        self.stop()
        if mode == "base":
            self.bus.enable_torque(self.robot.base_motors)
            return

        self.touched_arm = True
        self.bus.disable_torque(self.robot.arm_motors)
        for name in self.robot.arm_motors:
            self.bus.write("Operating_Mode", name, 0)  # STS3215 position mode
            self.bus.write("Goal_Velocity", name, 100, normalize=False)
        positions = self.positions()
        for name, pos in positions.items():
            cal = self.robot.calibration[name]
            bounded_target(pos, 0, 0, cal.range_min, cal.range_max)
        self.bus.sync_write("Goal_Position", positions, normalize=False)
        self.bus.enable_torque(self.robot.arm_motors)

    def move_base(self, x, y, theta):
        wheels = self.robot._body_to_wheel_raw(x, y, theta)
        self.bus.sync_write("Goal_Velocity", wheels, normalize=False)

    def stop(self):
        self.bus.sync_write("Goal_Velocity", dict.fromkeys(self.robot.base_motors, 0),
                            normalize=False, num_retry=2)

    def jog_arm(self, name, direction):
        pos = self.bus.read("Present_Position", name, normalize=False, num_retry=2)
        cal = self.robot.calibration[name]
        target = bounded_target(pos, direction, 11, cal.range_min, cal.range_max)
        self.bus.write("Goal_Position", name, target, normalize=False)

    def close(self):
        if not self.bus.is_connected:
            return
        errors = []
        if self.touched_base:
            try:
                self.stop()
            except Exception as exc:
                errors.append(f"Base stop failed: {exc}")
        groups = []
        if self.touched_base:
            groups.extend(self.robot.base_motors)
        if self.touched_arm:
            groups.extend(self.robot.arm_motors)
        if groups:
            try:
                self.bus.disable_torque(groups, num_retry=2)
            except Exception as exc:
                errors.append(f"Torque release failed: {exc}")
        self.bus.disconnect(disable_torque=False)
        if errors:
            raise RuntimeError("; ".join(errors))


class DryRun:
    def prepare(self, mode):
        print(f"DRY RUN: {mode}; no hardware connection.")

    def move_base(self, x, y, theta):
        print(f"DRY RUN: base x={x:.3f} m/s y={y:.3f} m/s rotation={theta:.1f} deg/s")

    def stop(self):
        print("DRY RUN: STOP")

    def jog_arm(self, name, direction):
        print(f"DRY RUN: {name}: {direction * 11:+d} ticks")

    def close(self):
        pass


class Jogger:
    def __init__(self, hardware, mode, speed=0.03, rotation=8.0, pulse=0.12):
        self.hardware = hardware
        self.mode = mode
        self.speed = speed
        self.rotation = rotation
        self.pulse = pulse
        self.deadline = None
        self.next_arm_jog = 0.0

    def tick(self, now):
        if self.deadline is not None and now >= self.deadline:
            self.hardware.stop()
            self.deadline = None

    def key(self, key, now):
        if key in ("q", " "):
            self.hardware.stop()
            self.deadline = None
            return key != "q"
        if self.mode == "base" and key in BASE_KEYS and self.deadline is None:
            x, y, theta = BASE_KEYS[key]
            self.hardware.move_base(x * self.speed, y * self.speed, theta * self.rotation)
            self.deadline = now + self.pulse
        elif self.mode == "arm" and key in ARM_KEYS and now >= self.next_arm_jog:
            self.hardware.jog_arm(*ARM_KEYS[key])
            self.next_arm_jog = now + 0.25
        return True


def interrupted(signum, frame):
    raise KeyboardInterrupt


def run_terminal(hardware, jogger):
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        hardware.prepare(jogger.mode)
        termios.tcflush(fd, termios.TCIFLUSH)
        while True:
            jogger.tick(time.monotonic())
            ready, _, _ = select.select([fd], [], [], 0.02)
            if ready:
                data = os.read(fd, 4096).decode("ascii", errors="ignore")
                if not jogger.key(newest_key(data), time.monotonic()):
                    break
    finally:
        try:
            hardware.close()
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old_settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--id", default="lekiwi01")
    parser.add_argument("--mode", choices=("base", "arm"), default="base")
    parser.add_argument("--speed", type=float, default=0.03, help="base m/s, maximum 0.05")
    parser.add_argument("--rotation", type=float, default=8, help="degrees/s, maximum 15")
    parser.add_argument("--pulse", type=float, default=0.12, help="seconds per base jog, maximum 0.25")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="read-only hardware/calibration check")
    actions.add_argument("--dry-run", action="store_true", help="test keyboard without robot drivers")
    args = parser.parse_args()
    for name, limit in (("speed", 0.05), ("rotation", 15), ("pulse", 0.25)):
        value = getattr(args, name)
        if not math.isfinite(value) or not 0 < value <= limit:
            parser.error(f"--{name} must be greater than zero and at most {limit}")
    if not args.check and not sys.stdin.isatty():
        parser.error("Use an interactive terminal (ssh -t when launching remotely).")

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)

    hardware = DryRun() if args.dry_run else Hardware(args.port, args.id)
    try:
        if not args.dry_run:
            hardware.connect()
        if args.check:
            hardware.check()
            return
        print("Space: stop base. Q / Esc / Ctrl-C: quit. Keyboard repeats can issue more jogs.")
        print("Base: W/S forward/back, A/D left/right, Z/X rotate. Each pulse stops automatically.")
        print("Arm mode: R/F pan, T/G lift, Y/H elbow, U/J wrist flex, I/K roll, O/L gripper.")
        print("Arm steps: 11 ticks (about 1 degree); gripper direction must be checked visually.")
        print("Arm mode holds position between jogs; SPACE stops only the base.")
        print("Support the arm: arm mode releases torque at startup and on exit.")
        print(f"Mode: {args.mode}. Press Enter when the robot is ready; Ctrl-C cancels.")
        input()
        run_terminal(hardware, Jogger(hardware, args.mode, args.speed, args.rotation, args.pulse))
    finally:
        hardware.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nKeyboard control ended.")
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
