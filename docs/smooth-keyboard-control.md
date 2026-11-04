# Smooth keyboard control

`scripts/smooth_control.py` runs a 30 Hz controller on the Pi and serves a small
browser page. The page sends the set of held keys every 50 ms, including key-up
events. This supports continuous motion, diagonals, multiple arm joints, and
simultaneous base/arm operation without depending on terminal key repeat.

## Start

Quit any other motor controller with Q before starting this one. Run on the Pi:

```bash
cd ~/Le-Kiwi-Research
source .venv/bin/activate
python scripts/smooth_control.py --check
python scripts/smooth_control.py
```

The check reads motor settings and telemetry without changing torque or motion.
The server prints a control URL with a session token, such as
`http://localhost:8765/#token=...`. Starting the server does not enable motors.

In another **Mac** terminal, create the tunnel:

```bash
ssh -6 -N -L 8765:127.0.0.1:8765 lekiwi@lekiwi.local
```

Leave that command running, then open the exact control URL in a Mac browser.
The Pi server binds only its loopback interface; it is accessed through the
encrypted SSH tunnel. The token authorizes control, so keep the URL local.
IPv6 through the hostname was verified when numeric IPv4 SSH logins were failing
on this network; this is a network-specific observation, not a general Pi rule.

Support the arm before clicking **Enable control**: initialization releases arm
torque briefly, reads its pose, initializes position targets to that pose, then
enables torque. Both base and arm are initialized, even when the page's input
mode selects only one of them. The arm holds position in base-only mode.

## Keys and modes

- **Base + arm** (default): both key groups work simultaneously.
- **Base only**: only the drive keys affect motion; the arm holds position.
- **Arm only**: only arm keys affect motion; the wheels stay stopped.

| Keys | Action |
| --- | --- |
| W / S | Forward / backward |
| A / D | Left / right |
| Z / X | Rotate left / right |
| R / F | Shoulder pan + / − |
| T / G | Shoulder lift + / − |
| Y / H | Elbow + / − |
| U / J | Wrist flex + / − |
| I / K | Wrist roll + / − |
| O / L | Gripper + / − |
| 1 / 2 / 3 | Slow / Medium / Fast |
| Space or Esc | Stop both base and arm; keep arm torque enabled |

Hold keys to move, release to stop. Base velocities ramp up/down; Space bypasses
the ramp. Arm trajectories ramp up and stop at the measured position on key
release. Focus loss, tab hiding and input timeout latch a stop. Click Enable
control again to resume. The **Release torque** button disables all nine motors;
support the arm first because it will no longer hold itself up.

## Speed and torque

| Profile | Base target | Turn target | Arm joint target |
| --- | --- | --- | --- |
| Slow | 0.10 m/s | 30°/s | 15°/s |
| Medium | 0.20 m/s | 60°/s | 35°/s |
| Fast | 0.30 m/s | 90°/s | 90°/s |

The base settings match the presets in the installed
[LeRobot 0.6.1 client](https://github.com/huggingface/lerobot/blob/v0.6.1/src/lerobot/robots/lekiwi/lekiwi_client.py).
These are controller targets, not a measured physical maximum for this build.
Combined translation/rotation can be scaled by LeRobot's wheel-speed clamp.
The arm speeds are this controller's selected limits, not manufacturer-rated
maximum joint speeds or a guaranteed rate under load.

Targets remain within each joint's saved calibration. Arm target lead over the
measured position is bounded to avoid accumulating a long movement queue if a
joint cannot keep up. Frame intervals are capped during integration, preventing
a long software pause from producing a large catch-up target.

This robot's nine motors were read remotely on 2026-11-04:

- `Torque_Limit` and `Max_Torque_Limit` were **1000 on every motor**, already 100%.
- `Phase` was 12 on every motor, including the ticks/second speed-unit bit.
- Supply readings were 12.0–12.2 V; internal temperatures were 33–34°C.
- Motor status was 0 on all motors.
- Existing unload protection setting was 44 and current-protection setting 310.
  The controller leaves these protection registers unchanged.

On enable, the runtime torque limit is set to the configured maximum, capped at
1000 and verified by readback. A lower configured maximum is respected. This
does not increase torque beyond the servo's design or raise its current limit.
For context, Waveshare lists 30 kg·cm **stall torque** and 10 kg·cm **rated load**
for its 12 V ST3215; stall torque is not a continuous operating load. See the
[servo specifications](https://docs.waveshare.com/ST3215_Servo) and
[torque/protection register definitions](https://docs.waveshare.com/Memory_Map_Explanation/ST_Servo_Memory_Map_Explanation).

Temporary runtime torque, acceleration and velocity settings are restored on
normal shutdown, with torque disabled. IDs, homing offsets and calibrated joint
limits are not rewritten. Arm position-profile speed is limited to approximately
95°/s and the servo acceleration register is set to 40 (documented unit 8.7°/s²).

## Stopping and limits

The Pi checks input freshness independently of the browser. After 300 ms without
valid input it commands zero base velocity, holds the measured arm pose and
requires re-enabling. Old packets and delayed enable requests cannot restart a
stopped session. Closing a page also attempts to send a stop. Browser background
timer throttling is expected to stop control rather than keep a robot moving.

Runtime motor fault flags stop the controller and trigger cleanup, which releases
torque. Stop, timeout and focus loss normally keep the arm holding its pose;
server shutdown or faults release it. These are software behaviours, not a
hardware emergency stop or collision avoidance. Pi power loss, process freeze,
SIGKILL or bus failure can prevent a stop command. The battery and structure have
not been validated at maximum combined motor load.

The page shows live minimum servo voltage, maximum servo temperature, speed
profile and torque setting. There is no camera viewer in this controller.

## Verification

```bash
python -m unittest discover -s tests -v
python scripts/smooth_control.py --dry-run
```

Simulation has no robot-driver imports or motor connections. With it running,
the actual page JavaScript can be tested against the simulation API using:

```bash
node tests/test_smooth_frontend.cjs 'http://localhost:8765/#token=TOKEN_FROM_SIMULATION'
```

That test refuses to enable a controller unless telemetry identifies it as a
simulation. It covers combined held keys, release, Space, focus loss and hidden
tabs. Browser rendering was not inspected because the browser automation host
was unavailable during development. Smooth motion on the physical robot still
requires operator observation after enabling.
