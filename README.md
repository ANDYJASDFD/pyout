# PyOut

Write Python in Termux, see the output in your own Android app (pygame, matplotlib, OpenCV, console).

termux/pyout.py   the runner (runs in Termux, uses your venv's pygame/tensorflow/cv2/matplotlib)
android/          the PyOut app (a thin window onto the runner)
.github/workflows/build.yml   builds the APK on GitHub for you (no Android Studio needed)
