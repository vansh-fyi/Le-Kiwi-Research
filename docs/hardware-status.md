# Hardware status — 2026-11-04

## Confirmed setup

- Raspberry Pi 4 Model B Rev 1.4, 4 GB RAM.
- Ubuntu Server 24.04.5 LTS, 64-bit ARM; kernel during remote test: 6.8.0-1065-raspi.
- Python 3.12.3 and LeRobot 0.6.1 installed in `~/Le-Kiwi-Research/.venv`.
- SSH user and hostname: `lekiwi`; robot calibration ID: `lekiwi01`.
- One Waveshare USB servo controller, currently `/dev/ttyACM0`, serial `5B91030878`.
- IDs 1–6: arm; IDs 7/8/9: left/back/right wheels, as confirmed by the owner.
- All nine IDs returned model 777 at 1,000,000 baud during the remote battery check.
- Owner completed interactive calibration; JSON contains all nine motors with
  ordered ranges within 0–4095. Physical calibration accuracy remains to be
  checked through controlled motion.
- Calibration on Pi:
  `~/.cache/huggingface/lerobot/calibration/robots/lekiwi/lekiwi01.json`.

## Camera and power test

Tested over SSH while the owner reported running the setup from battery.
Software telemetry does not independently confirm the supply wiring, battery
voltage, capacity, or runtime.

Both Xitech USB cameras (`0abd:8050`) were opened together for approximately
30 seconds using MJPEG and 640×480. Requested rate was 30 FPS; measured capture
loop throughput was approximately 19.65 FPS per camera.

| Current device | Inspected view | Frames received | Failed reads |
| --- | --- | --- | --- |
| `/dev/video0` | Wrist/gripper view | 590 | 0 |
| `/dev/video2` | Room/base view, upside down | 590 | 0 |

Device numbering may change after reconnecting. The current USB paths are
`/dev/v4l/by-path/platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.2:1.0-video-index0`
for the gripper camera and
`/dev/v4l/by-path/platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.4:1.0-video-index0`
for the room camera. These paths depend on keeping the same physical USB ports.

Initial capture failed because the SSH user lacked `video` group membership.
Added `lekiwi` to `video` with `usermod -aG video lekiwi`. The passing test ran
as `lekiwi` with refreshed supplementary groups, not as root. Existing SSH
sessions need to reconnect to inherit this membership. `dialout` access for
the serial controller was already working.

- Firmware power status after capture: `throttled=0x0`, with no current or
  historical undervoltage/throttling flags reported this boot.
- CPU temperature after capture: approximately 51.1°C.
- Current-boot kernel journal search found no matching undervoltage,
  overcurrent, USB disconnect/reset, or selected USB transport error messages.
- Snapshot JPEGs and JSON reports are saved under `/home/lekiwi/camera-check/`
  on the Pi. Copies inspected on the Mac are under
  `/tmp/lekiwi-battery-camera-check/` (temporary storage).

## Remaining checks

- Set camera orientation in the eventual control configuration; the room camera
  needs rotation. Do not assume LeRobot's default front/wrist device mapping.
- The owner subsequently confirmed low-speed base pulses and arm jogging work.
  Smooth combined control, stopping at higher speed and supply stability under
  combined motor load still require physical testing.
- Gamepad detection, mapping and control are pending delivery of the Kreo Surge XB.
- Battery endurance and long-duration simultaneous streaming have not been tested.

No motor movement commands or torque changes were sent during the remote check.

## Keyboard preparation

`scripts/keyboard_control.py` provides terminal-based base pulses and a separate
arm-jog mode. Twelve automated tests pass on the Mac; the first eleven also
passed on the Pi before the calibration-comparison refinement.

Read-only inspection after reboot confirmed that all six arm calibration entries
match their motor registers and current arm positions are within the saved ranges.
The three wheel homing offsets read back as 85, while their JSON entries are 0.
The controller reports these wheel-only position-calibration differences rather
than treating them as an arm calibration failure: wheel commands use velocity
mode, without position normalization. It does not rewrite those offsets.

The owner subsequently verified keyboard-driven base and arm jogging. The official LeKiwi example
uses a desktop keyboard listener on the laptop for base control, plus a leader
arm. Our terminal input replaces that desktop listener for headless SSH usage;
arm joint jogging is custom functionality. See the
[upstream example](https://github.com/huggingface/lerobot/blob/v0.6.1/examples/lekiwi/teleoperate.py)
and [headless keyboard limitation](https://github.com/huggingface/lerobot/blob/main/docs/source/lekiwi.mdx).
