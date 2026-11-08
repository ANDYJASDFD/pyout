#!/usr/bin/env bash
# PyOut installer - run inside Termux (or Termux Ubuntu):  bash install.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "PyOut folder: $DIR"
if command -v apt >/dev/null 2>&1; then
  (apt install -y xvfb xdotool python3-tk python3-pil.imagetk 2>/dev/null || sudo apt install -y xvfb xdotool python3-tk python3-pil.imagetk 2>/dev/null) \
    || echo "(apt step skipped - only needed for tkinter/turtle programs)"
fi
python3 -m pip install --quiet pillow numpy matplotlib opencv-python-headless pygame 2>/dev/null \
  || python3 -m pip install --quiet --break-system-packages pillow numpy matplotlib opencv-python-headless pygame \
  || echo "(some pip packages failed - add them later from the Libs tab)"
mkdir -p "$HOME/pyout_projects"
LINE="pyout() { [ -n \"\$1\" ] && source ~/\$1/bin/activate; python \"$DIR/termux/pyout.py\" serve --dir \"\${PYOUT_DIR:-\$HOME/pyout_projects}\"; }"
grep -q "^pyout()" "$HOME/.bashrc" 2>/dev/null || echo "$LINE" >> "$HOME/.bashrc"
echo
echo "Done. Open a NEW terminal tab, type:  pyout"
echo "(or  pyout myvenv  to start it inside the venv ~/myvenv)"
