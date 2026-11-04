#!/usr/bin/env python3
"""Smooth browser keyboard control for LeKiwi, served over an SSH tunnel."""

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
import signal
import threading
import time
from urllib.parse import urlsplit

from keyboard_control import ARM_KEYS, BASE_KEYS, Hardware


ARM_NAMES = tuple(dict.fromkeys(name for name, _ in ARM_KEYS.values()))
ALLOWED_KEYS = set(BASE_KEYS) | set(ARM_KEYS)
PROFILES = {
    1: {"name": "Slow", "linear": 0.1, "rotation": 30, "arm": 15},
    2: {"name": "Medium", "linear": 0.2, "rotation": 60, "arm": 35},
    3: {"name": "Fast", "linear": 0.3, "rotation": 90, "arm": 90},
}
INPUT_TIMEOUT = 0.3
TICKS_PER_DEGREE = 4096 / 360


def approach(value, target, delta):
    return value + max(-delta, min(delta, target - value))


def torque_limits(factory):
    if any(not 0 < value <= 1000 for value in factory.values()):
        raise ValueError("Invalid configured motor torque limit.")
    return {name: min(1000, value) for name, value in factory.items()}


class Planner:
    def __init__(self, limits):
        self.limits = limits
        self.base = [0.0, 0.0, 0.0]
        self.targets = {}
        self.arm_velocity = dict.fromkeys(ARM_NAMES, 0.0)

    def reset(self, positions):
        self.base = [0.0, 0.0, 0.0]
        self.targets = dict(positions)
        self.arm_velocity = dict.fromkeys(ARM_NAMES, 0.0)

    def step(self, keys, mode, profile, positions, dt):
        dt = max(0.0, min(dt, 0.05))
        speed = PROFILES[profile]
        desired = [0.0, 0.0, 0.0]
        if mode in ("base", "both"):
            for key in keys & BASE_KEYS.keys():
                for axis, direction in enumerate(BASE_KEYS[key]):
                    desired[axis] += direction
        length = math.hypot(desired[0], desired[1])
        if length > 1:
            desired[0] /= length
            desired[1] /= length
        desired = [desired[0] * speed["linear"], desired[1] * speed["linear"],
                   max(-1, min(1, desired[2])) * speed["rotation"]]
        for i in range(3):
            limit = 180 if i == 2 else (1.2 if desired[i] == 0 else 0.6)
            self.base[i] = approach(self.base[i], desired[i], limit * dt)

        directions = dict.fromkeys(ARM_NAMES, 0)
        if mode in ("arm", "both"):
            for key in keys & ARM_KEYS.keys():
                name, direction = ARM_KEYS[key]
                directions[name] += direction
        for name in ARM_NAMES:
            low, high = self.limits[name]
            current = positions[name]
            if not low <= current <= high:
                raise ValueError(f"{name}: current position {current} outside [{low}, {high}]")
            direction = max(-1, min(1, directions[name]))
            if direction == 0:
                if self.arm_velocity[name] != 0:
                    self.targets[name] = current
                self.arm_velocity[name] = 0
                continue
            if self.arm_velocity[name] == 0:
                self.targets[name] = current
            velocity = approach(self.arm_velocity[name], direction * speed["arm"], 360 * dt)
            proposed = self.targets[name] + velocity * TICKS_PER_DEGREE * dt
            lead = max(3, speed["arm"] * 0.08) * TICKS_PER_DEGREE
            self.targets[name] = max(low, min(high, max(current - lead, min(current + lead, proposed))))
            self.arm_velocity[name] = velocity
        return tuple(self.base), {name: round(value) for name, value in self.targets.items()}


class InputState:
    def __init__(self):
        self.lock = threading.Lock()
        self.epoch = 0
        self.sequence = -1
        self.enabled = False
        self.ready = False
        self.powered = False
        self.release = False
        self.keys = set()
        self.mode = "both"
        self.profile = 1
        self.last_seen = 0.0
        self.message = "Stopped. Support the arm before enabling or releasing torque."
        self.error = None
        self.telemetry = {}

    def _stop(self, message):
        self.enabled = False
        self.ready = False
        self.keys.clear()
        self.epoch += 1
        self.message = message

    def apply(self, command, now):
        if not isinstance(command, dict):
            raise ValueError("Expected a JSON object.")
        action = command.get("action")
        with self.lock:
            if action in ("stop", "release"):
                self._stop("Stopped; arm holds position." if action == "stop" else "Releasing torque. Support the arm.")
                self.release = action == "release"
                return
            if self.error:
                raise ValueError(self.error)
            mode = command.get("mode", self.mode)
            profile = command.get("profile", self.profile)
            if mode not in ("base", "arm", "both") or type(profile) is not int or profile not in PROFILES:
                raise ValueError("Invalid mode or speed profile.")
            if action == "enable":
                if command.get("epoch") != self.epoch:
                    raise ValueError("Stale enable request. Refresh status and try again.")
                self.epoch += 1
                self.sequence = -1
                self.keys.clear()
                self.enabled = True
                self.ready = False
                self.release = False
                self.last_seen = now
                self.mode, self.profile = mode, profile
                self.message = "Preparing motors; keep keys released."
                return
            if action != "input":
                raise ValueError("Unknown control action.")
            if self.enabled and now - self.last_seen > INPUT_TIMEOUT:
                self._stop("Input timed out. Click Enable control to resume.")
            if not self.enabled or command.get("epoch") != self.epoch:
                raise ValueError("Control stopped. Enable it again.")
            sequence = command.get("sequence")
            keys = command.get("keys")
            if type(sequence) is not int or sequence <= self.sequence:
                raise ValueError("Stale input packet.")
            if not isinstance(keys, list) or len(keys) > 32 or any(
                not isinstance(key, str) or key not in ALLOWED_KEYS for key in keys
            ):
                raise ValueError("Invalid keys.")
            self.sequence = sequence
            self.last_seen = now
            self.mode, self.profile = mode, profile
            self.keys = set(keys) if self.ready else set()

    def snapshot(self, now):
        with self.lock:
            if self.enabled and now - self.last_seen > INPUT_TIMEOUT:
                self._stop("Input timed out. Click Enable control to resume.")
            return {"epoch": self.epoch, "enabled": self.enabled, "ready": self.ready,
                    "powered": self.powered, "release": self.release, "keys": set(self.keys),
                    "mode": self.mode, "profile": self.profile, "message": self.message,
                    "error": self.error, "telemetry": dict(self.telemetry)}

    def public(self):
        result = self.snapshot(time.monotonic())
        result["keys"] = sorted(result["keys"])
        result["profiles"] = PROFILES
        return result


class SmoothHardware(Hardware):
    def connect(self):
        super().connect()
        self.original = {}
        for register in ("Torque_Limit", "Acceleration", "Goal_Velocity"):
            self.original[register] = self.bus.sync_read(register, normalize=False, num_retry=1)
        self.factory_torque = self.bus.sync_read("Max_Torque_Limit", normalize=False, num_retry=1)
        self.full_torque = torque_limits(self.factory_torque)
        phase = self.bus.sync_read("Phase", normalize=False, num_retry=1)
        if any(not value & 4 for value in phase.values()):
            raise RuntimeError("Motor speed units differ from the verified ticks/second configuration.")
        self.limits = {name: (cal.range_min, cal.range_max)
                       for name, cal in self.robot.calibration.items() if name in ARM_NAMES}

    def prepare(self):
        super().prepare("arm")
        self.bus.sync_write("Torque_Limit", self.full_torque, normalize=False)
        actual = self.bus.sync_read("Torque_Limit", normalize=False, num_retry=1)
        if actual != self.full_torque:
            raise RuntimeError("Torque-limit readback did not match the requested setting.")
        # Match LeRobot's 4096-ticks/turn velocity conversion, with a bounded profile.
        profile_speed = math.ceil(95 * TICKS_PER_DEGREE)
        self.bus.sync_write("Goal_Velocity", dict.fromkeys(ARM_NAMES, profile_speed), normalize=False)
        self.bus.sync_write("Acceleration", dict.fromkeys(self.bus.motors, 40), normalize=False)
        self.bus.enable_torque(self.robot.base_motors)

    def send(self, base, targets):
        self.move_base(*base)
        self.bus.sync_write("Goal_Position", targets, normalize=False)

    def disarm(self):
        self.stop()
        self.bus.disable_torque(list(self.bus.motors), num_retry=1)

    def telemetry(self):
        voltage = self.bus.sync_read("Present_Voltage", normalize=False, num_retry=1)
        temperature = self.bus.sync_read("Present_Temperature", normalize=False, num_retry=1)
        status = self.bus.sync_read("Status", normalize=False, num_retry=1)
        return {"min_voltage": min(voltage.values()) / 10,
                "max_temperature": max(temperature.values()),
                "motor_faults": {name: value for name, value in status.items() if value},
                "torque_limit_percent": min(self.full_torque.values()) / 10,
                "dry_run": False}

    def close(self):
        if self.bus.is_connected and (self.touched_arm or self.touched_base):
            try:
                self.disarm()
                for register, values in self.original.items():
                    self.bus.sync_write(register, values, normalize=False)
            finally:
                super().close()
        else:
            super().close()


class SimulatedHardware:
    def __init__(self):
        self.limits = dict.fromkeys(ARM_NAMES, (0, 4095))
        self.pose = dict.fromkeys(ARM_NAMES, 2048)
        self.base = (0, 0, 0)
        self.commands = 0

    def positions(self):
        return dict(self.pose)

    def prepare(self):
        pass

    def send(self, base, targets):
        self.base = base
        self.pose = dict(targets)
        self.commands += 1

    def disarm(self):
        pass

    def telemetry(self):
        return {"dry_run": True, "torque_limit_percent": 100, "motor_faults": {},
                "commands": self.commands, "positions": dict(self.pose), "base": list(self.base)}

    def close(self):
        pass


def control_loop(hardware, state, shutdown):
    planner = Planner(hardware.limits)
    powered = False
    active_epoch = None
    telemetry_due = 0.0
    previous = time.monotonic()
    try:
        planner.reset(hardware.positions())
        while not shutdown.is_set():
            now = time.monotonic()
            dt, previous = now - previous, now
            snapshot = state.snapshot(now)
            if snapshot["release"] and powered:
                hardware.disarm()
                powered = False
                active_epoch = None
                with state.lock:
                    state.powered = False
                    state.release = False
            if snapshot["enabled"]:
                if not powered:
                    hardware.prepare()
                    powered = True
                    with state.lock:
                        state.powered = True
                positions = hardware.positions()
                if snapshot["epoch"] != active_epoch:
                    planner.reset(positions)
                    hardware.send((0, 0, 0), dict(positions))
                    active_epoch = snapshot["epoch"]
                    with state.lock:
                        if state.enabled and state.epoch == active_epoch:
                            state.ready = True
                            state.message = "Control enabled. Hold keys to move."
                else:
                    base, targets = planner.step(snapshot["keys"], snapshot["mode"],
                                                 snapshot["profile"], positions, dt)
                    # A stop arriving during a bus read takes priority over this frame.
                    current = state.snapshot(time.monotonic())
                    if current["enabled"] and current["epoch"] == active_epoch:
                        hardware.send(base, targets)
                    else:
                        planner.reset(positions)
                        hardware.send((0, 0, 0), dict(positions))
                        active_epoch = None
            elif powered and active_epoch is not None:
                positions = hardware.positions()
                planner.reset(positions)
                hardware.send((0, 0, 0), dict(positions))
                active_epoch = None
            if now >= telemetry_due:
                telemetry = hardware.telemetry()
                with state.lock:
                    state.telemetry = telemetry
                if telemetry.get("motor_faults"):
                    raise RuntimeError(f"Motor protection status: {telemetry['motor_faults']}")
                telemetry_due = now + 1
            shutdown.wait(max(0, 1 / 30 - (time.monotonic() - now)))
    except Exception as exc:
        with state.lock:
            state.error = str(exc)
            state._stop(f"Controller fault: {exc}. Restart after inspecting the robot.")
        print(f"Controller fault: {exc}", flush=True)
    finally:
        try:
            hardware.close()
        except Exception as exc:
            print(f"Motor cleanup failed: {exc}", flush=True)
        with state.lock:
            state.powered = False
            state._stop("Controller stopped." if not state.error else state.message)


def handler_type(state, token, page):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def respond(self, status, body, content_type="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authenticated(self):
            return hmac.compare_digest(self.headers.get("X-Control-Token", ""), token)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                self.respond(200, page, "text/html; charset=utf-8")
            elif path == "/status" and self.authenticated():
                self.respond(200, state.public())
            else:
                self.respond(403, {"error": "Open the complete control URL printed by the server."})

        def do_POST(self):
            if self.path != "/control" or not self.authenticated():
                self.respond(403, {"error": "Invalid control token."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("Invalid request size.")
                state.apply(json.loads(self.rfile.read(size)), time.monotonic())
                self.respond(200, state.public())
            except (ValueError, TypeError, TimeoutError) as exc:
                self.respond(400, {"error": str(exc), "state": state.public()})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyACM0", help="servo USB port")
    parser.add_argument("--id", default="lekiwi01")
    parser.add_argument("--http-port", default=8765, type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true", help="read settings without enabling motors")
    args = parser.parse_args()
    hardware = SimulatedHardware() if args.dry_run else SmoothHardware(args.port, args.id)
    try:
        if not args.dry_run:
            hardware.connect()
        if args.check:
            if not args.dry_run:
                hardware.check()
                print("Configured maximum torque:", hardware.factory_torque)
                print("Current torque limits:", hardware.original["Torque_Limit"])
            print(json.dumps(hardware.telemetry(), indent=2))
            return
        state = InputState()
        token = secrets.token_urlsafe(24)
        page = (Path(__file__).resolve().parent / "smooth_control.html").read_text()
        server = ThreadingHTTPServer(("127.0.0.1", args.http_port), handler_type(state, token, page))
        server.daemon_threads = True
        shutdown = threading.Event()
        worker = threading.Thread(target=control_loop, args=(hardware, state, shutdown), daemon=True)
        def stop(signum, frame):
            raise KeyboardInterrupt
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, stop)
        worker.start()
        print(f"Control URL: http://localhost:{args.http_port}/#token={token}", flush=True)
        print("Waiting for Enable control. No motors enabled by starting the server.", flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            shutdown.set()
            server.server_close()
            worker.join(timeout=5)
    finally:
        # During serving the worker owns all bus access, including cleanup.
        if 'worker' not in locals() or not worker.is_alive():
            hardware.close()


if __name__ == "__main__":
    main()
