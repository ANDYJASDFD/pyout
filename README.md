# PyOut

<img src="docs/icon.png" width="96" align="right">

**Write Python in Termux. See the output in your own Android app.**
A free, open Pydroid-style output window: pygame, matplotlib, OpenCV (with your phone camera), tkinter/turtle, PyTorch, TensorFlow, and a pip library manager.

PyOut is two parts that talk over `127.0.0.1` (your own phone, no internet needed):

| Part | What it is |
|---|---|
| `termux/pyout.py` | the runner. It runs in Termux and uses *your* Python packages |
| `android/` | the app: a window that shows the runner's screen |

## Install (3 steps)

1. **App:** open the **Actions** tab of this repo, open the latest green build, download **PyOut-apk**, unzip it and install `app-debug.apk` (Android will warn about apps from outside the Play Store: this is normal, the source code is right here).
2. **Runner:** in Termux:
   ```
   git clone https://github.com/ANDYJASDFD/pyout
   cd pyout && bash install.sh
   ```
3. **Start:** open a new terminal tab and type
   ```
   pyout
   ```
   Then open the **PyOut** app. Write code in the **Editor** tab (or put `.py` files in `~/pyout_projects`) and press Run.

No patch files, no extra commands. Everything is already in `termux/pyout.py`.

To use a virtualenv, type `pyout myvenv` (it uses `~/myvenv`). To use another projects folder, set `PYOUT_DIR` first.

## What it supports
- print / input / errors in a console
- pygame (touch = mouse, on-screen keys), matplotlib (`plt.show()` becomes a picture)
- OpenCV: `cv2.imshow`, and `cv2.VideoCapture(0)` uses the phone camera through the app
- tkinter and turtle on a virtual screen (needs the `apt` packages the installer adds)
- **Libs tab:** install, upgrade and remove pip packages with one tap, plus a quick list (numpy, torch CPU, ...)
- 10 sample programs in the Samples menu

## Permissions: what and why
| Permission | Why |
|---|---|
| Internet | only to talk to the runner on your own phone (`127.0.0.1`) |
| Camera | only when a script opens `cv2.VideoCapture(0)`. Frames never leave your phone |

PyOut has no accounts, no ads and no tracking. The icon, app and runner are all in this repo, and the APK is built by GitHub from this exact code.

## Good to know
- Runs while the PyOut app is open. The camera only works while PyOut is on screen (a floating window is perfect).
- Keep Termux alive: `termux-wake-lock`, and set Termux battery to "No restrictions".
- Big libraries (torch, tensorflow) go in separate venvs, because they need different numpy versions.
- Torch on a phone: `pip install torch --index-url https://download.pytorch.org/whl/cpu`

Made by Andy, with Claude.
