#!/usr/bin/env python3
"""Inventory the host without opening hardware devices or importing drivers."""

import argparse
import glob
import grp
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import shutil
import struct
import sys


def read_text(path):
    try:
        return Path(path).read_text().strip().strip("\x00")
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "Unreadable: {}".format(exc)


def device_entries(patterns):
    paths = sorted({p for pattern in patterns for p in glob.glob(pattern)})
    return [
        {
            "path": path,
            "resolved": os.path.realpath(path),
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
        }
        for path in paths
    ]


def inspect():
    packages = {}
    for name in ("lerobot", "torch", "feetech-servo-sdk", "pyserial", "pyzmq", "pygame"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None

    memory = read_text("/proc/meminfo")
    memory_total = next(
        (line for line in (memory or "").splitlines() if line.startswith("MemTotal:")), None
    )
    os_release = read_text("/etc/os-release")
    os_name = next(
        (line.split("=", 1)[1].strip('"') for line in (os_release or "").splitlines()
         if line.startswith("PRETTY_NAME=")), platform.platform()
    )
    serial = device_entries(("/dev/serial/by-id/*", "/dev/ttyACM*", "/dev/ttyUSB*",
                             "/dev/cu.usbmodem*", "/dev/cu.usbserial*"))
    checks = []
    if platform.system() != "Linux":
        checks.append("This is not Linux. Run this script on the Ubuntu Pi for its hardware report.")
    if platform.machine() not in ("aarch64", "arm64") or struct.calcsize("P") != 8:
        checks.append("Expected a 64-bit ARM OS and Python on the Pi; verify before installing LeRobot.")
    if sys.version_info < (3, 12):
        checks.append("This Python is older than 3.12; LeRobot 0.6.1 needs a separate Python >=3.12 environment.")
    if not serial:
        checks.append("No USB serial controller found. Check its USB connection to this machine.")
    if any(not entry["writable"] for entry in serial):
        checks.append("Some serial devices are not writable. On Ubuntu check dialout membership and log in again.")
    return {
        "model": read_text("/proc/device-tree/model"),
        "os": os_name,
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "python_bits": struct.calcsize("P") * 8,
        "memory": memory_total,
        "disk_free_gib": round(shutil.disk_usage(Path.cwd()).free / 1024**3, 1),
        "hostname": platform.node(),
        "groups": sorted(grp.getgrgid(gid).gr_name for gid in os.getgroups()),
        "serial_devices": serial,
        "video_devices": device_entries(("/dev/video*", "/dev/v4l/by-id/*")),
        "input_devices": device_entries(("/dev/input/by-id/*", "/dev/input/js*")),
        "packages": packages,
        "notes": checks,
        "scope": "Inventory only. Motor IDs, baud rate, calibration, camera capture and gamepad input are untested.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a text report")
    args = parser.parse_args()
    report = inspect()
    if args.json:
        print(json.dumps(report, indent=2))
        return
    for key, value in report.items():
        print("{}: {}".format(key, json.dumps(value) if isinstance(value, (dict, list)) else value))


if __name__ == "__main__":
    main()
