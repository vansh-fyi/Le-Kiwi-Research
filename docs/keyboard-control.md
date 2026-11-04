# Keyboard control over SSH

Run the controller in an interactive SSH terminal on the Pi. The program uses
terminal input directly and needs no desktop, X server, or keyboard attached to
the Pi. Cameras are not opened by this program.

## First check

With the motor board powered, run from the Pi repository with its venv active:

```bash
python scripts/keyboard_control.py --check
```

This verifies the nine motor models, compares the six arm entries in calibration
`lekiwi01` to motor registers, and prints current arm positions and limits. Wheel
position-calibration differences are reported separately: positional offsets and
ranges are not used for wheel velocity commands. It does not
configure motors, enable/disable torque, or command a position/velocity.
If an arm position is outside the range, inspect and reposition it before arm
control; the arm controller refuses to enable torque outside the saved range.

## Base control

Raise the wheels securely for the first test and keep motor power cutoff
accessible. Confirm that wheel directions match the labels before floor driving.
Base mode operates only the wheels and leaves arm torque unchanged; keep the arm
in a supported position.

```bash
python scripts/keyboard_control.py --mode base
```

The program waits for Enter before configuring motors. Base setup disables
wheel torque, selects velocity mode, writes zero velocity, and then enables
wheel torque. It does not run LeRobot's whole-robot `connect()` routine.

| Key | Action |
| --- | --- |
| W / S | Forward / backward |
| A / D | Left / right |
| Z / X | Rotate left / right |
| Space | Stop base immediately when input is received |
| Q, Esc, Ctrl-C | Stop and exit |

Tap a movement key once to check direction. Each pulse lasts 0.12 seconds at
0.03 m/s or 8 degrees/s by default. The base is commanded to stop at the deadline
without requiring further keyboard input. Incoming movement keys do not extend
an active pulse. Holding a key may trigger additional pulses through keyboard
repeat. Batched input is reduced to one key, with stop/quit taking priority;
queued movement is not replayed one key at a time. SSH cannot reliably report
key release, so this is tap-to-jog control rather than a hold-to-move interface.

On normal exit, Ctrl-C, SIGHUP or SIGTERM, cleanup attempts to stop the base,
disable the selected motors' torque, and close the serial port. This is a
software stop, not a motor-controller watchdog: power failure, SIGKILL, process
freeze, or serial communication loss can prevent stopping. The pulse timeout
does not depend on network input, but it depends on the Pi process continuing
to execute and being able to write to the bus.

Optional tuning is capped for this first controller:

```bash
python scripts/keyboard_control.py --mode base --speed 0.02 --rotation 5 --pulse 0.10
```

Maximum accepted settings are 0.05 m/s, 15 degrees/s and 0.25 seconds per pulse.
Actual travel depends on wheel dimensions, mounting and traction.

## Arm control

Exit base control first. Support the arm before starting and before quitting:
arm-mode initialization and cleanup release arm torque. Initialization sets
position mode, a limited position-profile speed, and the current measured pose
as the goal before enabling arm torque. It does not command a home pose.

```bash
python scripts/keyboard_control.py --mode arm
```

| Keys | Joint (+ / − raw position) |
| --- | --- |
| R / F | Shoulder pan |
| T / G | Shoulder lift |
| Y / H | Elbow flex |
| U / J | Wrist flex |
| I / K | Wrist roll |
| O / L | Gripper |

Each accepted key changes the measured position by up to 11 ticks (approximately
one motor degree), clamped to its recorded calibration range. Commands are
limited to four per second. The position-profile velocity register is set to
100, approximately 8.8 motor degrees/s. Test each direction visually, especially
the gripper; raw plus/minus is not a promise of a particular physical direction.
These are motor-space limits, not collision avoidance.

The arm holds the commanded pose between keys. Space only stops the base; it
does not cancel an arm target. Q / Esc / Ctrl-C exits and releases arm torque.

## Test without hardware

```bash
python scripts/keyboard_control.py --dry-run
python scripts/keyboard_control.py --dry-run --mode arm
python -m unittest discover -s tests -v
```

Dry run does not import LeRobot or open serial ports. The tests cover pulse
expiry, repeat handling, stop/quit priority, target limits, initialization order
and cleanup failure handling. A real-terminal dry-run check also verified
interactive startup, a W pulse, automatic stop, and Q exit on the Mac.

Physical keyboard-driven motion has not yet been verified. Run the read-only
check on the Pi, then perform the first movement test while attending the robot.
