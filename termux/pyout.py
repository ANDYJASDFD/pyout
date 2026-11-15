#!/usr/bin/env python3
"""PyOut - run Python from Termux, see the output in the PyOut app (like Pydroid, but yours).

  python pyout.py serve [--dir ~/projects] [--port 8765]    start the runner (leave it running)
  python pyout.py run myscript.py                            run one script from the shell

What it captures:  print()/input()  -> Console tab
                   pygame window    -> Output tab (touch = mouse, buttons = keys)
                   matplotlib show  -> Output tab
                   cv2.imshow       -> Output tab
Needs only the standard library. Your libs (pygame, tensorflow, cv2, matplotlib) come from the
Python you start it with, so start it inside your venv:  source ~/tfenv/bin/activate
"""
import argparse, glob, io, json, os, re, shutil, signal, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~/.pyout")
FRAME = os.path.join(HOME, "frame.jpg")
EVENTS = os.path.join(HOME, "events.jsonl")
CAMFILE = os.path.join(HOME, "cam.jpg")
WANT = os.path.join(HOME, "cam.want")
SNDDIR = os.path.join(HOME, "snd")   # PYSOUND

def snd_tail():
    try:
        return open(os.path.join(SNDDIR, "log")).read().split()[-10:]
    except Exception:
        return []


# =========================================================== child side: runs the user's script
def child_main(script):
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    os.environ["MPLBACKEND"] = "Agg"
    os.makedirs(HOME, exist_ok=True)
    sys.argv = [script]
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
    state = {"last": 0.0, "mouse": (0, 0), "btn": (0, 0, 0), "keys": set(), "lastkey": -1}

    def publish(bgr_or_jpg):
        tmp = FRAME + ".tmp"
        with open(tmp, "wb") as f:
            f.write(bgr_or_jpg)
        os.replace(tmp, FRAME)

    def publish_array(arr, rgb=True):
        import cv2
        a = arr if not rgb else arr[..., ::-1]
        ok, buf = cv2.imencode(".jpg", a, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            publish(buf.tobytes())

    # ---------------- pygame hooks
    def hook_pygame():
        import pygame
        real_flip, real_update = pygame.display.flip, pygame.display.update

        def grab():
            now = time.time()
            if now - state["last"] < 1 / 30:
                return
            state["last"] = now
            surf = pygame.display.get_surface()
            if surf is None:
                return
            import numpy as np
            w, h = surf.get_size()
            raw = pygame.image.tostring(surf, "RGB")
            publish_array(np.frombuffer(raw, np.uint8).reshape(h, w, 3))

        def flip():
            real_flip(); grab()

        def update(*a, **k):
            real_update(*a, **k); grab()

        pygame.display.flip, pygame.display.update = flip, update
        pygame.mouse.get_pos = lambda: state["mouse"]
        pygame.mouse.get_pressed = lambda *a, **k: state["btn"]
        pygame.key.get_pressed = lambda: _Keys(pygame, state["keys"])

    class _Keys:
        def __init__(self, pg, keys): self.pg, self.keys = pg, keys
        def __getitem__(self, k): return k in self.keys

    # ---------------- matplotlib hook
 def hook_mpl():
    import matplotlib
    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import io

    def show(*args, **kwargs):
        figures = plt.get_fignums()

        for number in figures:
            fig = plt.figure(number)

            # Render the figure directly as JPEG.
            # No OpenCV required!
            buf = io.BytesIO()
            fig.savefig(
                buf,
                format="jpeg",
                dpi=fig.dpi,
                bbox_inches="tight"
            )

            publish(buf.getvalue())
            time.sleep(0.6)

    def pause(seconds=0.001):
        show()
        time.sleep(max(seconds, 0.001))

    plt.show = show
    plt.pause = pause

    # ---------------- OpenCV hook
    def hook_cv2():
        import cv2
        cv2.imshow = lambda name, img: publish_array(img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), rgb=False)
        cv2.namedWindow = lambda *a, **k: None
        cv2.destroyAllWindows = lambda *a, **k: None
        cv2.destroyWindow = lambda *a, **k: None
        cam = ""
        try:
            cam = open(os.path.join(HOME, "camera.txt")).read().strip()
        except Exception:
            pass

        class PyOutCam:                           # the phone camera, sent by the PyOut app
            def __init__(self):
                open(WANT, "w").close()
                self.on, self.last, self.img = True, 0, None

            def isOpened(self): return self.on

            def _wait(self, timeout):
                end = time.time() + timeout
                while time.time() < end:
                    try:
                        m = os.stat(CAMFILE).st_mtime_ns
                        if m != self.last:
                            self.last = m
                            return True
                    except FileNotFoundError:
                        pass
                    time.sleep(0.01)
                return False

            def read(self):
                if not self.on: return False, None
                if not self._wait(10.0 if self.img is None else 3.0):
                    if self.img is None:
                        print("[pyout] no camera pictures yet - keep the PyOut app open and allow the camera", flush=True)
                    return False, None
                img = cv2.imread(CAMFILE)
                if img is None: return False, None
                self.img = img
                return True, img

            def grab(self): return self.read()[0]
            def retrieve(self): return (self.img is not None), self.img
            def set(self, *a): return True

            def get(self, p):
                h, w = self.img.shape[:2] if self.img is not None else (480, 640)
                return {3: float(w), 4: float(h), 5: 30.0}.get(int(p), 0.0)

            def release(self):
                self.on = False
                try: os.remove(WANT)
                except Exception: pass

        real_vc = cv2.VideoCapture

        def VideoCapture(src=0, *a, **k):
            if isinstance(src, int):
                if cam and cam != "phone":        # camera.txt holds an IP Webcam address
                    return real_vc(cam, *a, **k)
                return PyOutCam()
            return real_vc(src, *a, **k)
        cv2.VideoCapture = VideoCapture

        def waitKey(ms=0):
            time.sleep(max(ms, 1) / 1000.0)
            k, state["lastkey"] = state["lastkey"], -1
            return k
        cv2.waitKey = waitKey

    def hook_turtle():
        import turtle
        turtle._CFG["width"] = 1.0              # window = whole screen (no skinny window, no scrollbars)
        turtle._CFG["height"] = 1.0

    def hook_tk():
        import tkinter
        real_init = tkinter.Tk.__init__

        def init(self, *a, **k):
            real_init(self, *a, **k)
            try:
                self.geometry("%dx%d+0+0" % (self.winfo_screenwidth(), self.winfo_screenheight()))
            except Exception:
                pass
        tkinter.Tk.__init__ = init

    # ---------------- events from the app
    def pump():
        pos = 0
        while True:
            time.sleep(0.01)
            try:
                with open(EVENTS, "rb") as f:
                    f.seek(pos); data = f.read(); pos += len(data)
            except FileNotFoundError:
                continue
            for line in data.splitlines():
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                handle(e)

    def handle(e):
        t = e.get("t")
        if "pygame" in sys.modules:
            pg = sys.modules["pygame"]
            if t in ("down", "up", "move"):
                x, y = int(e["x"]), int(e["y"])
                state["mouse"] = (x, y)
                if t == "down": state["btn"] = (1, 0, 0)
                if t == "up": state["btn"] = (0, 0, 0)
                typ = {"down": pg.MOUSEBUTTONDOWN, "up": pg.MOUSEBUTTONUP, "move": pg.MOUSEMOTION}[t]
                d = {"pos": (x, y)}
                if t == "move": d.update(rel=(0, 0), buttons=state["btn"])
                else: d["button"] = 1
                pg.event.post(pg.event.Event(typ, d))
            elif t in ("kdown", "kup"):
                code = getattr(pg, "K_" + e["key"], None)
                if code is None: return
                (state["keys"].add if t == "kdown" else state["keys"].discard)(code)
                pg.event.post(pg.event.Event(pg.KEYDOWN if t == "kdown" else pg.KEYUP, key=code, mod=0, unicode=e["key"] if len(e["key"]) == 1 else ""))
        if t == "kdown":
            ch = e["key"]
            state["lastkey"] = ord(ch) if len(ch) == 1 else {"left": 81, "up": 82, "right": 83, "down": 84, "space": 32, "return": 13, "escape": 27}.get(ch, -1)

    threading.Thread(target=pump, daemon=True).start()

    # import hooks lazily: patch each library right after the script imports it
    import builtins
    real_import = builtins.__import__
    hooked = set()
    table = {"pygame": hook_pygame, "matplotlib.pyplot": hook_mpl, "cv2": hook_cv2, "turtle": hook_turtle, "tkinter": hook_tk}

    depth = [0]

    def imp(name, *a, **k):
        depth[0] += 1
        try:
            mod = real_import(name, *a, **k)
        finally:
            depth[0] -= 1
        if depth[0] == 0:                       # only after a whole top-level import finished
            for key, fn in table.items():
                if key not in hooked and key in sys.modules:
                    hooked.add(key)
                    try:
                        fn()
                    except Exception as ex:
                        print(f"[pyout] could not hook {key}: {ex}", file=sys.stderr)
        return mod
    builtins.__import__ = imp

    # ---------------- sound: sounddevice / winsound / playsound -> the PyOut app
    import types as _types
    snd = {"last": 0, "end": 0.0}

    def snd_put(data, ext):
        os.makedirs(SNDDIR, exist_ok=True)
        n = max(int(time.time() * 1000), snd["last"] + 1); snd["last"] = n
        name = "%d.%s" % (n, ext)
        if ext != "stop":
            with open(os.path.join(SNDDIR, name), "wb") as f: f.write(data)
        lg = os.path.join(SNDDIR, "log")
        with open(lg, "a") as f: f.write(name + "\n")
        try:
            lines = open(lg).read().split()
            if len(lines) > 200:
                open(lg, "w").write("\n".join(lines[-50:]) + "\n")
                keep = set(lines[-50:])
                for fn in os.listdir(SNDDIR):
                    if fn != "log" and fn not in keep:
                        try: os.remove(os.path.join(SNDDIR, fn))
                        except Exception: pass
        except Exception:
            pass
        return name

    def snd_wav(data, sr):
        import io, wave, numpy as np
        a = np.asarray(data)
        if a.dtype.kind == "f": a = np.clip(a, -1, 1) * 32767
        a = a.astype("<i2")
        ch = 1 if a.ndim == 1 else a.shape[1]
        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(ch); w.setsampwidth(2); w.setframerate(int(sr)); w.writeframes(a.tobytes())
        return b.getvalue(), len(a) / float(sr)

    def snd_play(data, sr=44100, blocking=False):
        raw, dur = snd_wav(data, sr)
        snd_put(raw, "wav")
        snd["end"] = time.time() + dur
        if blocking: time.sleep(dur)

    def snd_wait():
        time.sleep(max(0.0, snd["end"] - time.time()))

    def snd_stop():
        snd_put(b"", "stop"); snd["end"] = 0.0

    sdm = _types.ModuleType("sounddevice")
    sdm.play = lambda data, samplerate=44100, mapping=None, blocking=False, loop=False, **k: snd_play(data, samplerate, blocking)
    sdm.wait = lambda *a, **k: snd_wait()
    sdm.stop = lambda *a, **k: snd_stop()
    sdm.query_devices = lambda *a, **k: {"name": "PyOut speaker", "max_output_channels": 2, "max_input_channels": 0, "default_samplerate": 44100.0}
    sdm.default = _types.SimpleNamespace(samplerate=44100, channels=2, device=(0, 0))
    sdm.get_stream = lambda: None

    def _nomic(*a, **k): raise RuntimeError("PyOut has no microphone input in sounddevice yet")
    sdm.rec = sdm.InputStream = sdm.Stream = _nomic

    class _Out:
        def __init__(self, samplerate=44100, channels=1, **k): self.sr, self.ch = samplerate, channels; self.on = False
        def start(self): self.on = True
        def stop(self): self.on = False
        def close(self): self.on = False
        def write(self, data): snd_play(data, self.sr, True)
        def __enter__(self): self.start(); return self
        def __exit__(self, *a): self.close()
    sdm.OutputStream = _Out
    sys.modules["sounddevice"] = sdm

    def snd_file(path, block=True):
        raw = open(path, "rb").read()
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        ext = ext if ext in ("wav", "mp3", "ogg", "m4a", "flac") else "wav"
        snd_put(raw, ext)
        dur = 0.0
        try:
            import wave
            with wave.open(path) as w: dur = w.getnframes() / float(w.getframerate())
        except Exception:
            dur = len(raw) / 16000.0
        snd["end"] = time.time() + dur
        if block: time.sleep(dur)

    psm = _types.ModuleType("playsound")
    psm.playsound = lambda path, block=True: snd_file(path, block)
    sys.modules["playsound"] = psm

    if sys.platform != "win32":
        wsm = _types.ModuleType("winsound")
        def _beep(freq, dur):
            import numpy as np
            sr = 22050
            t = np.arange(int(sr * dur / 1000.0)) / sr
            snd_play(0.4 * np.sin(2 * np.pi * freq * t), sr, True)
        wsm.Beep = _beep
        wsm.MessageBeep = lambda *a: _beep(800, 150)
        wsm.SND_FILENAME, wsm.SND_ASYNC, wsm.SND_LOOP, wsm.SND_PURGE, wsm.SND_NODEFAULT = 0x20000, 1, 8, 0x40, 2
        wsm.PlaySound = lambda name, flags=0: snd_file(name, not (flags & 1)) if name else snd_stop()
        sys.modules["winsound"] = wsm

    code = compile(open(script, "rb").read(), script, "exec")
    try:
        exec(code, {"__name__": "__main__", "__file__": os.path.abspath(script)})
    except SystemExit:
        pass
    except BaseException:
        import traceback
        traceback.print_exc()
    print("\n[program finished]", flush=True)


# =========================================================== server side
class Runner:
    def __init__(self, root):
        self.root, self.proc, self.log, self.lock, self.name = root, None, [], threading.Lock(), ""
        self.xvfb, self.gui, self.warned = None, False, set()

    def say(self, s):
        with self.lock:
            self.log.append(s)

    def run(self, rel):
        self.stop()
        path = os.path.realpath(os.path.join(self.root, rel))
        if not path.startswith(os.path.realpath(self.root)) or not os.path.isfile(path):
            self.say(f"[pyout] file not found: {rel}\n"); return False
        os.makedirs(HOME, exist_ok=True)
        open(EVENTS, "wb").close()
        for _f in (WANT, CAMFILE):
            try: os.remove(_f)
            except Exception: pass
        if os.path.exists(FRAME): os.remove(FRAME)
        with self.lock: self.log = []
        self.name = rel
        env = dict(os.environ)
        self.gui = bool(re.search(r"\b[Tt]kinter\b|\bturtle\b", open(path, errors="ignore").read()))
        if self.gui:
            if not self.ensure_x():
                return False
            env["DISPLAY"] = ":99"
        self.proc = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__), "child", path], cwd=os.path.dirname(path),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, env=env)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        if self.gui:
            threading.Thread(target=self._grab, args=(self.proc,), daemon=True).start()
        return True

    def ensure_x(self):
        if self.xvfb and self.xvfb.poll() is None:
            return True
        exe = shutil.which("Xvfb")
        if not exe:
            self.say("[pyout] tkinter scripts need a virtual screen. Run once:\n   apt install -y xvfb xdotool python3-tk\n")
            return False
        self.xvfb = subprocess.Popen([exe, ":99", "-screen", "0", "540x960x24", "-nolisten", "tcp"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(1.0)
        return True

    def _grab(self, p):
        """copy the virtual screen into the Output tab"""
        try:
            from PIL import ImageGrab
        except Exception:
            self.say("[pyout] Pillow is missing: pip install pillow\n"); return
        while p.poll() is None:
            try:
                im = ImageGrab.grab(xdisplay=":99").convert("RGB")
                buf = io.BytesIO(); im.save(buf, "JPEG", quality=80)
                tmp = FRAME + ".tmp"
                with open(tmp, "wb") as f: f.write(buf.getvalue())
                os.replace(tmp, FRAME)
            except Exception as ex:
                if "grab" not in self.warned:
                    self.warned.add("grab"); self.say(f"[pyout] screen grab failed: {ex}\n")
            time.sleep(0.07)

    def xinput(self, e):
        """touch/keys -> the virtual screen (needs xdotool)"""
        exe = shutil.which("xdotool")
        if not exe:
            if "xdo" not in self.warned:
                self.warned.add("xdo"); self.say("[pyout] touch needs xdotool: apt install -y xdotool\n")
            return
        t, env = e.get("t"), dict(os.environ, DISPLAY=":99")
        keys = {"left": "Left", "up": "Up", "right": "Right", "down": "Down", "space": "space", "return": "Return", "escape": "Escape"}
        if t in ("down", "up", "move"):
            x, y = int(e["x"]), int(e["y"])
            cmd = ["mousemove", str(x), str(y)] + {"down": ["mousedown", "1"], "up": ["mouseup", "1"], "move": []}[t]
        elif t in ("kdown", "kup"):
            cmd = [("keydown" if t == "kdown" else "keyup"), keys.get(e["key"], e["key"])]
        else:
            return
        subprocess.Popen([exe] + cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _read(self, p):
        while True:
            b = p.stdout.read1(4096)
            if not b: break
            self.say(b.decode("utf-8", "replace"))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try: os.killpg(self.proc.pid, signal.SIGKILL)
            except Exception: pass
            self.say("\n[stopped]\n")
        self.proc = None

    def running(self): return bool(self.proc and self.proc.poll() is None)

    def stdin(self, text):
        if self.running():
            self.proc.stdin.write((text + "\n").encode()); self.proc.stdin.flush()
            self.say(text + "\n")


PIP = {"log": [], "lock": threading.Lock(), "busy": False}
_PKG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*(\[[A-Za-z0-9_,\-]+\])?(([=<>!~]=?|===)[A-Za-z0-9_.*+!]+)?$")
SAMPLES = [('hello.py', 'name = input("What is your name? ")\nprint("Hello,", name, "- PyOut works!")\n'), ('functions_loops.py', 'def grade(score):\n    if score >= 90:\n        return "A"\n    elif score >= 75:\n        return "B"\n    elif score >= 60:\n        return "C"\n    else:\n        return "try again"\n\nfor s in (95, 80, 65, 30):\n    print(s, "->", grade(s))\n\nsquares = [n * n for n in range(1, 11)]\nprint("squares:", squares)\n\nclass Dog:\n    def __init__(self, name):\n        self.name = name\n    def bark(self):\n        print(self.name, "says woof!")\n\nDog("Rex").bark()\n'), ('cowsay_demo.py', '# first: Libs tab -> type  cowsay  -> Install\nimport cowsay\ncowsay.cow("PyOut works! :)")\n'), ('turtle_colors.py', 'import turtle\nt = turtle.Turtle()\nt.speed(0)\nturtle.bgcolor("black")\ncolors = ["red", "orange", "yellow", "green", "cyan", "magenta"]\nfor i in range(120):\n    t.pencolor(colors[i % 6])\n    t.forward(i * 3)\n    t.left(59)\nturtle.done()\n'), ('opencv_bounce.py', 'import cv2, numpy as np, math, time\n\nW, H = 640, 480\nx, y, vx, vy = 100.0, 100.0, 5.0, 3.5\nt0 = time.time()\nfor n in range(300):\n    img = np.zeros((H, W, 3), np.uint8)\n    for i in range(0, W, 8):\n        c = int(127 + 127 * math.sin(i / 40 + n / 10))\n        img[:, i:i + 8] = (c, 255 - c, 128)\n    x += vx; y += vy\n    if x < 30 or x > W - 30: vx = -vx\n    if y < 30 or y > H - 30: vy = -vy\n    cv2.circle(img, (int(x), int(y)), 30, (255, 255, 255), -1)\n    cv2.putText(img, "OpenCV works!", (150, 250), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 4)\n    cv2.imshow("test", img)\n    cv2.waitKey(1)\nprint("fps:", round(300 / (time.time() - t0)))\n'), ('camera_edges.py', 'import cv2\n\ncap = cv2.VideoCapture(0)          # the phone camera, through the PyOut app\nprint("camera opened:", cap.isOpened())\nfor _ in range(900):\n    ok, frame = cap.read()\n    if not ok:\n        print("no picture - keep PyOut open and allow the camera")\n        break\n    edges = cv2.Canny(frame, 100, 200)\n    cv2.imshow("edges", edges)\n    cv2.waitKey(1)\ncap.release()\n'), ('matplotlib_plot.py', 'import numpy as np\nimport matplotlib.pyplot as plt\n\nx = np.linspace(0, 10, 200)\nplt.plot(x, np.sin(x), label="sin")\nplt.plot(x, np.cos(x), label="cos")\nplt.legend()\nplt.title("PyOut + matplotlib")\nplt.show()\n'), ('pygame_bounce.py', '# if pygame is missing: Libs tab -> pygame -> Install\nimport pygame\npygame.init()\nscreen = pygame.display.set_mode((540, 960))\nclock = pygame.time.Clock()\nx, y, vx, vy = 100, 100, 6, 5\nfor _ in range(900):\n    for e in pygame.event.get():\n        if e.type == pygame.MOUSEBUTTONDOWN:\n            x, y = e.pos                 # tap the screen to move the ball\n    x += vx; y += vy\n    if x < 30 or x > 510: vx = -vx\n    if y < 30 or y > 930: vy = -vy\n    screen.fill((20, 20, 40))\n    pygame.draw.circle(screen, (255, 200, 0), (int(x), int(y)), 30)\n    pygame.display.flip()\n    clock.tick(60)\n'), ('numpy_demo.py', 'import numpy as np\n\na = np.arange(12).reshape(3, 4)\nprint(a)\nprint("sum:", a.sum(), "mean:", a.mean())\nprint("transpose:")\nprint(a.T)\n'), ('tensorflow_hello.py', 'import tensorflow as tf      # the first import can take a while\nprint("TensorFlow", tf.__version__)\nprint(tf.reduce_sum(tf.random.normal((3, 3))))\n')]


def pip_say(t):
    with PIP["lock"]:
        PIP["log"].append(t)


def pip_run(action, pkgs, extra=()):
    if PIP["busy"]:
        pip_say("[busy - wait for the current job to finish]\n"); return
    PIP["busy"] = True

    def work():
        try:
            base = [sys.executable, "-m", "pip"]
            if action == "list": cmd = base + ["list", "--disable-pip-version-check"]
            elif action == "uninstall": cmd = base + ["uninstall", "-y"] + pkgs
            else: cmd = base + ["install", "--progress-bar", "off", "--disable-pip-version-check"] + (["--upgrade"] if action == "upgrade" else []) + list(extra) + pkgs
            pip_say("$ pip " + " ".join(cmd[3:]) + "\n")
            for attempt in (0, 1):
                p = subprocess.Popen(cmd + (["--break-system-packages"] if attempt else []),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                out = ""
                for line in p.stdout:
                    out += line; pip_say(line)
                p.wait()
                if p.returncode == 0:
                    if action in ("install", "upgrade"): pip_say("[done] installed - now use it in a script (import it)\n")
                    elif action == "uninstall": pip_say("[done] removed\n")
                    break
                if attempt == 0 and "externally-managed" in out and action != "list":
                    pip_say("[retrying with --break-system-packages]\n"); continue
                pip_say("[failed, code %d]\n" % p.returncode); break
        except Exception as ex:
            pip_say("[error] %s\n" % ex)
        finally:
            PIP["busy"] = False
    threading.Thread(target=work, daemon=True).start()


R = None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)): body = json.dumps(body)
        if isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        p, _, q = self.path.partition("?")
        if p == "/":
            self._send(200, UI, "text/html; charset=utf-8")
        elif p == "/files":
            files = sorted(os.path.relpath(f, R.root) for f in glob.glob(os.path.join(R.root, "**", "*.py"), recursive=True))
            self._send(200, {"files": files, "root": R.root, "running": R.running(), "name": R.name})
        elif p == "/console":
            since = int(dict(x.split("=") for x in q.split("&") if "=" in x).get("since", 0))
            with R.lock: chunk = R.log[since:]; n = len(R.log)
            self._send(200, {"text": "".join(chunk), "n": n, "running": R.running(), "cam": R.running() and os.path.exists(WANT), "snds": snd_tail()})
        elif p == "/snd":
            import re as _re, mimetypes
            f = dict(x.split("=", 1) for x in q.split("&") if "=" in x).get("f", "")
            if _re.fullmatch(r"\d+\.(wav|mp3|ogg|m4a|flac)", f) and os.path.isfile(os.path.join(SNDDIR, f)):
                self._send(200, open(os.path.join(SNDDIR, f), "rb").read(), mimetypes.guess_type(f)[0] or "audio/wav")
            else:
                self._send(404, b"", "text/plain")
        elif p == "/read":
            import urllib.parse
            f = urllib.parse.unquote(dict(x.split("=", 1) for x in q.split("&") if "=" in x).get("file", ""))
            path = os.path.realpath(os.path.join(R.root, f))
            if path.startswith(os.path.realpath(R.root)) and os.path.isfile(path):
                self._send(200, open(path, "rb").read(), "text/plain; charset=utf-8")
            else:
                self._send(404, b"", "text/plain")
        elif p == "/pkgs":
            try:
                out = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
                                     capture_output=True, text=True, timeout=90).stdout
                pk = json.loads(out)
            except Exception:
                pk = []
            self._send(200, {"pkgs": pk, "python": sys.version.split()[0], "env": sys.prefix})
        elif p == "/piplog":
            since = int(dict(x.split("=") for x in q.split("&") if "=" in x).get("since", 0))
            with PIP["lock"]: chunk = PIP["log"][since:]; n = len(PIP["log"])
            self._send(200, {"text": "".join(chunk), "n": n, "busy": PIP["busy"]})
        elif p == "/samples":
            self._send(200, [{"name": a, "code": b} for a, b in SAMPLES])
        elif p == "/frame":
            try: self._send(200, open(FRAME, "rb").read(), "image/jpeg")
            except FileNotFoundError: self._send(404, b"", "text/plain")
        elif p == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f"); self.end_headers()
            last = 0
            try:
                while True:
                    try: m = os.stat(FRAME).st_mtime_ns
                    except FileNotFoundError: m = 0
                    if m and m != last:
                        last = m
                        d = open(FRAME, "rb").read()
                        self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(d) + d + b"\r\n")
                        self.wfile.flush()
                    time.sleep(0.02)
            except Exception:
                pass
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        if self.path == "/cam":
            tmp = "%s.%d.tmp" % (CAMFILE, threading.get_ident())
            try:
                with open(tmp, "wb") as f: f.write(raw)
                os.replace(tmp, CAMFILE)
            except Exception:
                pass
            return self._send(200, {"ok": True})
        try: d = json.loads(raw or b"{}")
        except Exception: d = {}
        if self.path == "/run":
            f = d.get("file", "").strip()
            path = os.path.realpath(os.path.join(R.root, f))
            if "code" in d and f.endswith(".py") and path.startswith(os.path.realpath(R.root) + os.sep):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w").write(d["code"])
            self._send(200, {"ok": R.run(f)})
        elif self.path == "/save":
            f = d.get("file", "").strip()
            path = os.path.realpath(os.path.join(R.root, f))
            if f.endswith(".py") and path.startswith(os.path.realpath(R.root) + os.sep):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w").write(d.get("code", ""))
                self._send(200, {"ok": True})
            else:
                self._send(400, {"ok": False, "error": "file name must end in .py"})
        elif self.path == "/pip":
            action, toks = d.get("action", "install"), d.get("pkgs", "").split()
            if action in ("install", "upgrade", "uninstall", "list") and len(toks) <= 8 and all(_PKG.match(t) for t in toks) and (action == "list" or toks):
                pip_run(action, toks, ["--index-url", "https://download.pytorch.org/whl/cpu"] if d.get("cpu") else []); self._send(200, {"ok": True})
            else:
                pip_say("[that is not a valid package name]\n"); self._send(200, {"ok": False})
        elif self.path == "/stop": R.stop(); self._send(200, {"ok": True})
        elif self.path == "/stdin": R.stdin(d.get("text", "")); self._send(200, {"ok": True})
        elif self.path == "/event":
            with open(EVENTS, "ab") as f: f.write((json.dumps(d) + "\n").encode())
            if R.gui and R.running(): R.xinput(d)
            self._send(200, {"ok": True})
        else: self._send(404, {"error": "not found"})


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass                                      # a closed connection is not an error


def serve(root, port):
    global R
    R = Runner(os.path.expanduser(root))
    os.makedirs(R.root, exist_ok=True); os.makedirs(HOME, exist_ok=True)
    print(f"PyOut runner on http://127.0.0.1:{port}   projects: {R.root}\nOpen the PyOut app. Ctrl+C to quit.")
    QuietServer(("127.0.0.1", port), H).serve_forever()


UI = r"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,user-scalable=no">
<title>PyOut</title><style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{margin:0;height:100%;background:#0d1117;color:#e6edf3;font:15px system-ui,sans-serif}
body{display:flex;flex-direction:column}header{display:flex;gap:6px;padding:8px;background:#161b22;align-items:center}
button,select{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:9px 12px;font:inherit}
button.go{background:#238636;border-color:#2ea043}button.stop{background:#da3633;border-color:#f85149}
select{flex:1;min-width:0}nav{display:flex;background:#161b22;border-top:1px solid #30363d}
nav button{flex:1;border:0;border-radius:0;background:transparent;padding:12px}nav button.on{color:#58a6ff;border-bottom:2px solid #58a6ff}
main{flex:1;position:relative;min-height:0}.tab{position:absolute;inset:0;display:none}.tab.on{display:flex;flex-direction:column}
#out{align-items:center;justify-content:center;background:#000;touch-action:none}#scr{width:100%;height:100%;object-fit:contain;image-rendering:auto;user-select:none;-webkit-user-drag:none}
#out:not(.keys) #pad,#out:not(.keys) #pad2{display:none}
#ktog{position:absolute;top:8px;right:8px;z-index:5;padding:6px 10px;opacity:.6}
#camtog{position:absolute;top:8px;right:104px;z-index:5;padding:6px 10px;opacity:.6}#camflip{position:absolute;top:8px;right:196px;z-index:5;padding:6px 10px;opacity:.6}#camtog.on{background:#238636;opacity:1}
#pad{position:absolute;bottom:10px;left:10px;display:grid;grid-template-columns:repeat(3,52px);gap:4px;opacity:.55}
#pad2{position:absolute;bottom:10px;right:10px;display:flex;gap:6px;opacity:.55}#pad button,#pad2 button{padding:0;height:52px;font-size:18px}
#con{flex:1;margin:0;padding:10px;overflow:auto;white-space:pre-wrap;word-break:break-word;font:13px ui-monospace,monospace;background:#010409}
#inrow{display:flex;gap:6px;padding:8px;background:#161b22}#inp{flex:1;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:9px;font:inherit}
#erow{display:flex;gap:6px;padding:8px;background:#161b22}#fname{flex:1;min-width:0;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:9px;font:inherit}
#code{flex:1.4;min-height:0;margin:0;padding:10px;background:#0d1117;color:#e6edf3;border:0;outline:0;resize:none;font:14px ui-monospace,monospace;white-space:pre;overflow:auto;tab-size:4}
#con2{flex:1;min-height:0;margin:0;padding:10px;overflow:auto;white-space:pre-wrap;word-break:break-word;font:13px ui-monospace,monospace;background:#010409;border-top:2px solid #238636}
#prow,#prow2,#srow{display:flex;gap:6px;padding:8px;background:#161b22}#pkg{flex:1;min-width:0;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:10px;font:inherit}
#chips{display:flex;flex-wrap:wrap;gap:6px;padding:0 8px 8px;background:#161b22}#chips button{padding:6px 10px;font-size:13px}#prow2{padding-top:0}#srow{padding-top:0}#samp{flex:1}
#plog{flex:1;min-height:0;margin:0;padding:10px;overflow:auto;white-space:pre-wrap;word-break:break-word;font:13px ui-monospace,monospace;background:#010409;border-top:2px solid #238636}
#lhead{padding:8px 10px;font-size:12px;color:#8b949e;background:#161b22;word-break:break-all}
#lseg{display:flex;background:#161b22;border-top:1px solid #30363d}#lseg button{flex:1;border:0;border-radius:0;background:transparent;padding:10px}#lseg button.on{color:#58a6ff;border-bottom:2px solid #58a6ff}
#lbody{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}
#vinst{flex:1;min-height:0;flex-direction:column}#lfilter{margin:8px;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:9px;font:inherit}
#llist{flex:1;min-height:0;overflow:auto}#vquick{flex:1;min-height:0;overflow:auto}
.lrow{display:flex;align-items:center;gap:8px;padding:9px 10px;border-bottom:1px solid #21262d}.ln{flex:1;min-width:0;word-break:break-all}.ln small{display:block;color:#8b949e;font-size:12px}.lv{color:#8b949e;font-size:12px}
.lrow button{padding:7px 10px;font-size:13px}#lstat{padding:6px 10px;font-size:13px;color:#3fb950;background:#161b22;min-height:30px}#lcount{margin-left:auto;align-self:center;color:#8b949e;font-size:12px}
</style></head><body>
<header><select id=sel></select><button class=go id=run>&#9654; Run</button><button class=stop id=stp>&#9632;</button></header>
<main>
<div class="tab on" id=out><img id=scr alt=""><button id=ktog>&#9000; keys</button><button id=camtog>&#128247; cam</button><button id=camflip>&#8635;</button><div id=pad><span></span><button data-k=up>&#9650;</button><span></span><button data-k=left>&#9664;</button><button data-k=down>&#9660;</button><button data-k=right>&#9654;</button></div>
<div id=pad2><button data-k=space style="width:80px">SPACE</button><button data-k=return>&#9166;</button><button data-k=escape>ESC</button></div></div>
<div class=tab id=cons><pre id=con></pre><div id=inrow><input id=inp placeholder="input() text..."><button id=snd>Send</button></div></div>
<div class=tab id=edit><div id=erow><input id=fname placeholder="name.py" value="new.py"><button id=esave>Save</button><button class=go id=erun>&#9654; Run</button><button class=stop id=estop>&#9632;</button></div>
<div id=srow><select id=samp><option value="">Load a sample...</option></select></div><textarea id=code spellcheck=false autocapitalize=off autocomplete=off placeholder="write python here..."></textarea><pre id=con2></pre></div>
<div class=tab id=libs><div id=lhead>...</div>
<div id=prow><input id=pkg placeholder="any package, e.g. numpy or numpy==1.26.4" autocapitalize=off autocomplete=off spellcheck=false><button class=go id=pinst>Install</button></div>
<div id=prow2><button id=pupg>Upgrade</button><button id=prem>Remove</button><button id=plist>Refresh</button><span id=lcount></span></div>
<div id=lseg><button data-v=inst class=on>Installed</button><button data-v=quick>Quick install</button><button data-v=logv>Log</button></div>
<div id=lbody><div id=vinst><input id=lfilter placeholder="filter installed..." autocapitalize=off autocomplete=off spellcheck=false><div id=llist></div></div><div id=vquick><div id=qlist></div></div><pre id=plog></pre></div>
<div id=lstat></div></div>
</main>
<nav><button class=on data-t=out>Output</button><button data-t=cons>Console</button><button data-t=edit>Editor</button><button data-t=libs>Libs</button></nav>
<script>
const $=s=>document.querySelector(s),post=(u,d)=>fetch(u,{method:'POST',body:JSON.stringify(d||{})}).catch(()=>{});
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x==b));
 document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.id==b.dataset.t))});
async function files(){try{const r=await (await fetch('/files')).json();const s=$('#sel'),cur=s.value||r.name;
 s.innerHTML=r.files.map(f=>`<option>${f}</option>`).join('')||'<option>(no .py files in '+r.root+')</option>';if(cur)s.value=cur}catch(e){}}
files();setInterval(files,5000);
$('#run').onclick=()=>{$('#con').textContent='';since=0;post('/run',{file:$('#sel').value})};
$('#stp').onclick=()=>post('/stop');
let since=0;let cbusy=false;setInterval(async()=>{if(cbusy)return;cbusy=true;try{const r=await (await fetch('/console?since='+since)).json();since=r.n;sndPoll(r);if(r.cam&&!camOn){camAuto=true;startCam()}else if(!r.cam&&camOn&&camAuto){stopCam()};
 if(r.text){if(r.text.indexOf('\x07')>=0)pyBell();const c=$('#con');c.textContent+=r.text;c.scrollTop=c.scrollHeight;const c2=$('#con2');c2.textContent+=r.text;c2.scrollTop=c2.scrollHeight}}catch(e){}cbusy=false},200);
$('#snd').onclick=()=>{post('/stdin',{text:$('#inp').value});$('#inp').value=''};$('#inp').onkeydown=e=>{if(e.key=='Enter')$('#snd').click()};
const img=$('#scr');function pt(e){const r=img.getBoundingClientRect(),t=e.touches?(e.touches[0]||e.changedTouches[0]):e;const nw=img.naturalWidth||1,nh=img.naturalHeight||1,k=Math.min(r.width/nw,r.height/nh),ox=(r.width-nw*k)/2,oy=(r.height-nh*k)/2;return {x:(t.clientX-r.left-ox)/k,y:(t.clientY-r.top-oy)/k}}
let dn=false,lm=0;
img.addEventListener('pointerdown',e=>{dn=true;img.setPointerCapture(e.pointerId);post('/event',{t:'down',...pt(e)})});
img.addEventListener('pointermove',e=>{if(dn&&Date.now()-lm>30){lm=Date.now();post('/event',{t:'move',...pt(e)})}});
img.addEventListener('pointerup',e=>{dn=false;post('/event',{t:'up',...pt(e)})});
img.ondragstart=e=>e.preventDefault();
document.querySelectorAll('[data-k]').forEach(b=>{const k=b.dataset.k;
 b.addEventListener('pointerdown',e=>{e.preventDefault();post('/event',{t:'kdown',key:k})});
 const up=e=>post('/event',{t:'kup',key:k});b.addEventListener('pointerup',up);b.addEventListener('pointerleave',up)});


const code=$('#code'),fname=$('#fname');
code.addEventListener('keydown',e=>{if(e.key=='Tab'){e.preventDefault();const a=code.selectionStart;code.setRangeText('    ',a,code.selectionEnd,'end')}});
async function openFile(f){try{const r=await fetch('/read?file='+encodeURIComponent(f));if(r.ok){code.value=await r.text();fname.value=f}}catch(e){}}
$('#sel').addEventListener('change',()=>{if($('#sel').value.endsWith('.py'))openFile($('#sel').value)});
async function saveFile(){let f=fname.value.trim();if(!f.endsWith('.py'))f+='.py';fname.value=f;
 const r=await (await fetch('/save',{method:'POST',body:JSON.stringify({file:f,code:code.value})})).json();return r.ok?f:null}
$('#esave').onclick=async()=>{const f=await saveFile();$('#esave').textContent=f?'Saved':'Error';setTimeout(()=>$('#esave').textContent='Save',1200);files()};
$('#erun').onclick=async()=>{const out=$('#con2');
 let f=fname.value.trim();if(!f.endsWith('.py'))f+='.py';fname.value=f;
 out.textContent='[running '+f+']\n';$('#con').textContent='';since=0;
 const sel=$('#sel');if(![...sel.options].some(o=>o.value==f))sel.add(new Option(f,f));sel.value=f;
 const ac=new AbortController(),tm=setTimeout(()=>ac.abort(),4000);
 try{await fetch('/run',{method:'POST',body:JSON.stringify({file:f,code:code.value}),signal:ac.signal})}
 catch(e){out.textContent='[runner not answering - restart it in Termux]\n'}
 clearTimeout(tm)};
$('#estop').onclick=()=>post('/stop');
document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',()=>{document.querySelector('header').style.display=(b.dataset.t=='edit'||b.dataset.t=='libs')?'none':'flex'}));

/*PULL*/let lastUrl=null,busy=false;
setInterval(async()=>{if(busy||!$('#out').classList.contains('on'))return;busy=true;
 try{const r=await fetch('/frame?'+Date.now(),{cache:'no-store'});
  if(r.ok){const u=URL.createObjectURL(await r.blob());img.onload=()=>{if(lastUrl)URL.revokeObjectURL(lastUrl);lastUrl=u};img.src=u}}catch(e){}
 busy=false},80);
$('#ktog').onclick=()=>$('#out').classList.toggle('keys');

/*CAM*/let camStream=null,camOn=false,camAuto=false,camFacing=(function(){try{return localStorage.getItem('camface')||'user'}catch(e){return 'user'}})();/*CAMFRONT*/
const camVid=document.createElement('video'),camCv=document.createElement('canvas');camVid.muted=true;camVid.playsInline=true;
async function startCam(){if(camOn)return;camOn=true;
 try{camStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:camFacing,width:{ideal:640},height:{ideal:480}},audio:false});
  camVid.srcObject=camStream;await camVid.play();$('#camtog').classList.add('on');camLoop()}
 catch(e){camOn=false;camAuto=false;const o=$('#con2');o.textContent+='[camera error] '+e+'\n';$('#con').textContent+='[camera error] '+e+'\n'}}
function stopCam(){camOn=false;if(camStream)camStream.getTracks().forEach(t=>t.stop());camStream=null;$('#camtog').classList.remove('on')}
async function camLoop(){const x=camCv.getContext('2d');
 while(camOn){if(camVid.videoWidth){camCv.width=camVid.videoWidth;camCv.height=camVid.videoHeight;if(camFacing=='user'){x.save();x.translate(camCv.width,0);x.scale(-1,1);x.drawImage(camVid,0,0);x.restore()}else x.drawImage(camVid,0,0);
   const b=await new Promise(r=>camCv.toBlob(r,'image/jpeg',0.7));try{await fetch('/cam',{method:'POST',body:b})}catch(e){}}
  await new Promise(r=>setTimeout(r,50))}}
$('#camtog').onclick=()=>{camAuto=false;camOn?stopCam():startCam()};
$('#camflip').onclick=()=>{camFacing=camFacing=='environment'?'user':'environment';try{localStorage.setItem('camface',camFacing)}catch(e){}if(camOn){stopCam();startCam()}};

/*LIBS*/let psince=0;
setInterval(async()=>{if(!$('#libs').classList.contains('on'))return;
 try{const r=await (await fetch('/piplog?since='+psince)).json();psince=r.n;
  if(r.text){const p=$('#plog');p.textContent+=r.text;p.scrollTop=p.scrollHeight}$('#pinst').disabled=r.busy}catch(e){}},400);
const pipDo=a=>{const v=$('#pkg').value.trim();if(a!='list'&&!v){$('#pkg').focus();return}post('/pip',{action:a,pkgs:v})};
$('#pinst').onclick=()=>pipDo('install');$('#prem').onclick=()=>pipDo('uninstall');$('#plist').onclick=()=>pipDo('list');
$('#pkg').onkeydown=e=>{if(e.key=='Enter')pipDo('install')};
document.querySelectorAll('#chips button').forEach(b=>b.onclick=()=>{$('#pkg').value=b.dataset.p});
let samples=[];
fetch('/samples').then(r=>r.json()).then(r=>{samples=r;$('#samp').innerHTML='<option value="">Load a sample...</option>'+r.map((x,i)=>`<option value="${i}">${x.name}</option>`).join('')}).catch(()=>{});
$('#samp').onchange=()=>{const x=samples[$('#samp').value];if(x){$('#code').value=x.code;$('#fname').value=x.name}$('#samp').value=''};

/*LIBS2*/
const QUICK=[["numpy","Fast arrays and math"],["pandas","Tables and data analysis"],["matplotlib","Charts and plots"],["scipy","Science and math tools"],["scikit-learn","Machine learning"],["pillow","Images"],["opencv-python-headless","Computer vision (OpenCV)"],["requests","Web requests"],["flask","Make websites"],["pygame","Games"],["sympy","Algebra by computer"],["beautifulsoup4","Read web pages"],["tqdm","Progress bars"],["rich","Pretty text"],["cowsay","ASCII cow"],["pyserial","Talk to Arduino / ESP32"],["torch","PyTorch AI (CPU build, big download)",1]];
let inst={},armed=null,lastBusy=false;
const norm=n=>n.toLowerCase().replace(/[-_.]+/g,'-');
function mk(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
async function loadPkgs(){try{const r=await (await fetch('/pkgs')).json();inst={};r.pkgs.forEach(p=>inst[norm(p.name)]=p.version);
 $('#lhead').textContent='Python '+r.python+'  |  '+r.env;drawLibs()}catch(e){$('#lstat').textContent='could not read the package list'}}
function drawLibs(){const f=$('#lfilter').value.trim().toLowerCase(),L=$('#llist');L.textContent='';
 Object.keys(inst).sort().filter(n=>!f||n.includes(f)).forEach(n=>{const row=mk('div','lrow');row.appendChild(mk('span','ln',n));row.appendChild(mk('span','lv',inst[n]));
  const b=mk('button',null,'Remove');b.onclick=()=>{if(armed==n){armed=null;libJob('uninstall',n)}else{armed=n;b.textContent='Sure?';setTimeout(()=>{if(armed==n){armed=null;b.textContent='Remove'}},3000)}};
  row.appendChild(b);L.appendChild(row)});
 $('#lcount').textContent=Object.keys(inst).length+' installed';
 const Q=$('#qlist');Q.textContent='';
 QUICK.forEach(([n,d,cpu])=>{const row=mk('div','lrow'),nm=mk('span','ln',n);nm.appendChild(mk('small',null,d));row.appendChild(nm);
  const have=inst[norm(n)],b=mk('button',have?null:'go',have?'Installed '+have:'Install');
  if(have)b.disabled=true;else b.onclick=()=>libJob('install',n,cpu);row.appendChild(b);Q.appendChild(row)})}
async function libJob(action,pkgs,cpu){$('#lstat').textContent=({install:'Installing ',upgrade:'Upgrading ',uninstall:'Removing '})[action]+pkgs+'...';setLibView('logv');
 await post('/pip',{action,pkgs,cpu:!!cpu})}
function setLibView(v){document.querySelectorAll('#lseg button').forEach(b=>b.classList.toggle('on',b.dataset.v==v));
 $('#vinst').style.display=v=='inst'?'flex':'none';$('#vquick').style.display=v=='quick'?'block':'none';$('#plog').style.display=v=='logv'?'block':'none'}
document.querySelectorAll('#lseg button').forEach(b=>b.onclick=()=>setLibView(b.dataset.v));
$('#lfilter').oninput=drawLibs;
$('#pinst').onclick=()=>{const v=$('#pkg').value.trim();if(v)libJob('install',v)};
$('#pupg').onclick=()=>{const v=$('#pkg').value.trim();if(v)libJob('upgrade',v)};
$('#prem').onclick=()=>{const v=$('#pkg').value.trim();if(v)libJob('uninstall',v)};
$('#plist').onclick=loadPkgs;$('#pkg').onkeydown=e=>{if(e.key=='Enter')$('#pinst').click()};
$('nav button[data-t=libs]').addEventListener('click',loadPkgs);
setInterval(async()=>{if(!$('#libs').classList.contains('on'))return;
 try{const r=await (await fetch('/piplog?since=999999999')).json();if(lastBusy&&!r.busy){$('#lstat').textContent='Done';loadPkgs()}lastBusy=r.busy}catch(e){}},700);
setLibView('inst');
/*PYSOUND*/let sndSeen=null,sndAud=[];
function sndPoll(r){if(!r.snds)return;const ids=r.snds.map(x=>parseInt(x));
 if(sndSeen===null){sndSeen=ids.length?Math.max(...ids):0;return}
 r.snds.forEach((f,i)=>{if(ids[i]>sndSeen){sndSeen=ids[i];
  if(f.endsWith('.stop')){sndAud.forEach(a=>{try{a.pause()}catch(e){}});sndAud=[]}
  else{const a=new Audio('/snd?f='+f);sndAud.push(a);a.onended=()=>{sndAud=sndAud.filter(x=>x!==a)};a.play().catch(e=>{})}}})}
/*PYBELL*/let bellCtx=null;function pyBell(){try{bellCtx=bellCtx||new (window.AudioContext||window.webkitAudioContext)();bellCtx.resume();
 for(let i=0;i<4;i++){const o=bellCtx.createOscillator(),g=bellCtx.createGain();o.frequency.value=880;o.connect(g);g.connect(bellCtx.destination);
  const t=bellCtx.currentTime+i*0.45;g.gain.setValueAtTime(0.5,t);g.gain.setValueAtTime(0,t+0.28);o.start(t);o.stop(t+0.3)}}catch(e){}
 try{navigator.vibrate&&navigator.vibrate([300,100,300])}catch(e){}}
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--dir", default="~/drowsiness_lab"); s.add_argument("--port", type=int, default=8765)
    r = sub.add_parser("run"); r.add_argument("script")
    c = sub.add_parser("child"); c.add_argument("script")
    m = sub.add_parser("camera"); m.add_argument("url", help="IP Webcam address, or 'off'")
    a = ap.parse_args()
    if a.cmd == "serve": serve(a.dir, a.port)
    elif a.cmd == "child": child_main(a.script)
    elif a.cmd == "camera":
        os.makedirs(HOME, exist_ok=True)
        f = os.path.join(HOME, "camera.txt")
        if a.url == "off":
            os.path.exists(f) and os.remove(f); print("camera: off (VideoCapture(0) uses the real camera)")
        else:
            open(f, "w").write(a.url); print("camera: VideoCapture(0) now reads", a.url)
    else: child_main(a.script)


if __name__ == "__main__":
    main()
