# Ubuntu Pi bring-up

## What runs where

| Machine | Role |
| --- | --- |
| MacBook | Edit this repository; connect to the Pi using SSH |
| Ubuntu Raspberry Pi | USB servo communication and robot control |
| Gamepad | Initially connect by USB to the Pi, if supported by the purchased controller |

The first target is direct gamepad control on the Pi. A later option is connecting
the gamepad to the Mac and sending commands over the local network. Direct control
avoids making Wi-Fi part of the first motion test.

## 1. Identify the Pi and Ubuntu installation

Boot the Pi with the already-flashed card. On the Pi's terminal run:

```bash
cat /proc/device-tree/model
free -h
uname -m
cat /etc/os-release
hostname -I
```

We expect `aarch64` for the 64-bit Ubuntu installation. Confirm the actual board
and RAM: the [official Pi 4 specification](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)
lists models up to 8 GB. A reported 16 GB could mean a different board or a mix-up
with storage. The report settles this without guessing.

The 256 GB card is ample for initial control software. Usable free space depends
on the partition layout; the inspection script reports space on its current filesystem.

## 2. Connect from the Mac

If SSH is not enabled, run on the **Pi**:

```bash
sudo apt update
sudo apt install openssh-server
sudo systemctl enable --now ssh
```

Put both machines on the same local network. On the **Mac**, replace the example
values with the Ubuntu username you created and the Pi's address:

```bash
ssh YOUR_UBUNTU_USERNAME@PI_IP_ADDRESS
```

Use the Ubuntu user's password when prompted. Do not put passwords in this repo.
If a firewall is active, SSH also needs to be allowed on that local network.

## 3. Get this repository onto the Pi

Run on the **Pi**, including through the SSH session:

```bash
sudo apt install git
git clone https://github.com/vansh-fyi/Le-Kiwi-Research.git
cd Le-Kiwi-Research
python3 scripts/inspect_pi.py
```

The new files must be pushed to GitHub before cloning will retrieve them. Until
then, copy the current local files from a **Mac** terminal (not inside SSH):

```bash
ssh YOUR_UBUNTU_USERNAME@PI_IP_ADDRESS 'mkdir -p ~/Le-Kiwi-Research/scripts'
scp scripts/inspect_pi.py YOUR_UBUNTU_USERNAME@PI_IP_ADDRESS:~/Le-Kiwi-Research/scripts/
```

Run those Mac commands from this repository's directory. Then, in SSH:

```bash
cd ~/Le-Kiwi-Research
python3 scripts/inspect_pi.py
```

This step requires no LeRobot installation and sends no motor commands. Device
names alone do not confirm motor IDs or controller baud rate.

## 4. Install the robot software after checking the report

The initial baseline is LeRobot **0.6.1**, the version also documented in the
SO-101 research project. Its
[package metadata](https://github.com/huggingface/lerobot/blob/v0.6.1/pyproject.toml)
requires Python >=3.12. Keep this environment separate from the SO-101 project.

For **Ubuntu 24.04 with Python 3.12**, run on the Pi from this repository:

```bash
sudo apt install python3-venv python3-dev build-essential cmake pkg-config libgl1 libglib2.0-0
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-pi.txt
python -c 'from lerobot.robots.lekiwi import LeKiwi, LeKiwiConfig; print("LeKiwi imports OK")'
python scripts/inspect_pi.py
```

If you only copied the inspection script, copy `requirements-pi.txt` from the Mac
as well before this step. On an older Ubuntu release, use a separate Python 3.12
environment following the [official installation guide](https://huggingface.co/docs/lerobot/v0.6.1/en/installation).
Do not replace Ubuntu's system Python. For a newer Ubuntu/Python version, check
wheel compatibility before using these commands.

This installation path is prepared but has not been tested on this Pi. If pip
reports missing ARM wheels or a build failure, retain the error and inspect it
before changing versions. LeRobot includes PyTorch even for manual control;
installing its `all` or training extras is unnecessary here.

For USB serial access on Ubuntu:

```bash
sudo usermod -aG dialout "$USER"
```

Log out and reconnect for group membership to take effect. Prefer stable
`/dev/serial/by-id/...` paths when available. Mac `/dev/cu.*` paths do not apply
on Ubuntu. Avoid running the robot process as root.

## 5. Confirm the motor layout before connecting a robot driver

The stock LeRobot LeKiwi implementation expects **one serial bus** with:

| ID | Motor |
| --- | --- |
| 1 | Shoulder pan |
| 2 | Shoulder lift |
| 3 | Elbow flex |
| 4 | Wrist flex |
| 5 | Wrist roll |
| 6 | Gripper |
| 7 | Left wheel |
| 8 | Back wheel |
| 9 | Right wheel |

Source: [LeKiwi motor map and connection code](https://github.com/huggingface/lerobot/blob/v0.6.1/src/lerobot/robots/lekiwi/lekiwi.py).
Already assigning IDs to the six arm motors does not confirm the three wheel
IDs, their positions, baud rates, or velocity mode. If the base and arm have
separate USB controllers, the stock one-bus configuration needs adapting.

Do not repeat motor-ID setup on the assembled chain just because a tutorial
includes it: setup expects individual motors to be connected in sequence.
First record what LeRobot Lab already configured.

Motor IDs and arm calibration are separate. The LeKiwi calibration uses
`arm_`-prefixed motor names and includes wheel entries; an SO-101 follower
calibration JSON is not a drop-in replacement. Preserve the existing calibration
files and identify whether they belong to this physical arm before migration.

## 6. Gamepad bring-up when it arrives

1. Confirm controller model, connection mode, and whether it will plug into the Pi
   or Mac. Start with USB if supported.
2. Test axes and buttons without connecting a robot driver. Map the actual
   controller rather than assuming Xbox button numbers.
3. Implement wheel control first, then arm joint jogging. End-effector movement
   needs an additional kinematics mapping.
4. Use a hold-to-move button, a stick dead zone, low initial speed, and stopping
   on release or controller disconnect. Test those behaviours with wheels raised
   before driving on the floor. Support the arm during initial connection and
   shutdown because driver configuration can release torque.

The upstream [gamepad teleoperator](https://github.com/huggingface/lerobot/blob/v0.6.1/src/lerobot/teleoperators/gamepad/teleop_gamepad.py)
outputs Cartesian deltas. LeKiwi expects named arm positions and base velocities;
these require an explicit adapter. There is no verified gamepad launch command
in this repository yet.

The upstream network host also connects and configures motors at startup, and
enables torque. It is not a read-only diagnostic. Its timeout stops the base;
it does not provide a complete arm emergency stop. Keep first motion tests
attended with the motor power cutoff accessible.

Next inputs: the Pi inspection output, gamepad model, one-bus/two-bus wiring,
wheel IDs, and the calibration performed in LeRobot Lab.
