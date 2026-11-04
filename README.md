# Le-Kiwi-Research

Bring-up workspace for an assembled LeKiwi: Ubuntu on a Raspberry Pi, an
SO-101 follower arm, three omni wheels, and gamepad teleoperation.

The MacBook is the development and SSH machine. The Pi runs the robot software.
Ubuntu is supported by the [official LeKiwi guide](https://huggingface.co/docs/lerobot/v0.6.1/en/lekiwi);
there is no custom Raspberry Pi firmware to compile for the standard setup.

Start with [the Pi setup guide](docs/pi-setup.md). First collect a hardware report
**on the Pi**, using its existing Python installation:

```bash
python3 scripts/inspect_pi.py
```

This uses only Python's standard library. It lists hardware and software details
without opening serial ports, importing robot drivers, or commanding motors.
Use `--json` for a machine-readable report.

Current status: Ubuntu and LeRobot are installed on the physical Pi; all nine
motors respond, calibration is saved as `lekiwi01`, and both cameras passed a
simultaneous capture test on the user-reported battery supply. See
[verified hardware status](docs/hardware-status.md) for results and limits.
Gamepad control and commanded robot movement remain untested. This repository
does not yet contain a gamepad motion controller.

[Keyboard control](docs/keyboard-control.md) is available for an SSH terminal:
short base movement pulses or a separate arm-jog mode. Start with the read-only
check: `python scripts/keyboard_control.py --check` inside the Pi environment.

The adjacent `SoARM-Research` project is reference material for SO-101 work.
Its Python environment, Mac device paths, and calibration files are not imported
automatically into this project.
