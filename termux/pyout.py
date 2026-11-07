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
import argparse, glob, json, os, signal, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~/.pyout")
FRAME = os.path.join(HOME, "frame.jpg")
EVENTS = os.path.join(HOME, "events.jsonl")


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

        def show(*a, **k):
            for n in plt.get_fignums():
                fig = plt.figure(n)
                fig.canvas.draw()
                import numpy as np
                buf = np.asarray(fig.canvas.buffer_rgba())[..., :3]
                publish_array(np.ascontiguousarray(buf))
                time.sleep(0.6)

        def pause(t=0.001):
            show(); time.sleep(max(t, 0.001))

        plt.show, plt.pause = show, pause

    # ---------------- OpenCV hook
    def hook_cv2():
        import cv2
        cv2.imshow = lambda name, img: publish_array(img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), rgb=False)
        cv2.namedWindow = lambda *a, **k: None
        cv2.destroyAllWindows = lambda *a, **k: None
        cv2.destroyWindow = lambda *a, **k: None

        def waitKey(ms=0):
            time.sleep(max(ms, 1) / 1000.0)
            k, state["lastkey"] = state["lastkey"], -1
            return k
        cv2.waitKey = waitKey

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
    table = {"pygame": hook_pygame, "matplotlib.pyplot": hook_mpl, "cv2": hook_cv2}

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
        if os.path.exists(FRAME): os.remove(FRAME)
        with self.lock: self.log = []
        self.name = rel
        self.proc = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__), "child", path], cwd=os.path.dirname(path),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        return True

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
            self._send(200, {"text": "".join(chunk), "n": n, "running": R.running()})
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
        try: d = json.loads(self.rfile.read(n) or b"{}")
        except Exception: d = {}
        if self.path == "/run": self._send(200, {"ok": R.run(d.get("file", ""))})
        elif self.path == "/stop": R.stop(); self._send(200, {"ok": True})
        elif self.path == "/stdin": R.stdin(d.get("text", "")); self._send(200, {"ok": True})
        elif self.path == "/event":
            with open(EVENTS, "ab") as f: f.write((json.dumps(d) + "\n").encode())
            self._send(200, {"ok": True})
        else: self._send(404, {"error": "not found"})


def serve(root, port):
    global R
    R = Runner(os.path.expanduser(root))
    os.makedirs(R.root, exist_ok=True); os.makedirs(HOME, exist_ok=True)
    print(f"PyOut runner on http://127.0.0.1:{port}   projects: {R.root}\nOpen the PyOut app. Ctrl+C to quit.")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


UI = r"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,user-scalable=no">
<title>PyOut</title><style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}html,body{margin:0;height:100%;background:#0d1117;color:#e6edf3;font:15px system-ui,sans-serif}
body{display:flex;flex-direction:column}header{display:flex;gap:6px;padding:8px;background:#161b22;align-items:center}
button,select{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:9px 12px;font:inherit}
button.go{background:#238636;border-color:#2ea043}button.stop{background:#da3633;border-color:#f85149}
select{flex:1;min-width:0}nav{display:flex;background:#161b22;border-top:1px solid #30363d}
nav button{flex:1;border:0;border-radius:0;background:transparent;padding:12px}nav button.on{color:#58a6ff;border-bottom:2px solid #58a6ff}
main{flex:1;position:relative;min-height:0}.tab{position:absolute;inset:0;display:none}.tab.on{display:flex;flex-direction:column}
#out{align-items:center;justify-content:center;background:#000;touch-action:none}#scr{max-width:100%;max-height:100%;image-rendering:auto;user-select:none;-webkit-user-drag:none}
#pad{position:absolute;bottom:10px;left:10px;display:grid;grid-template-columns:repeat(3,52px);gap:4px;opacity:.55}
#pad2{position:absolute;bottom:10px;right:10px;display:flex;gap:6px;opacity:.55}#pad button,#pad2 button{padding:0;height:52px;font-size:18px}
#con{flex:1;margin:0;padding:10px;overflow:auto;white-space:pre-wrap;word-break:break-word;font:13px ui-monospace,monospace;background:#010409}
#inrow{display:flex;gap:6px;padding:8px;background:#161b22}#inp{flex:1;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:9px;font:inherit}
</style></head><body>
<header><select id=sel></select><button class=go id=run>&#9654; Run</button><button class=stop id=stp>&#9632;</button></header>
<main>
<div class="tab on" id=out><img id=scr alt=""><div id=pad><span></span><button data-k=up>&#9650;</button><span></span><button data-k=left>&#9664;</button><button data-k=down>&#9660;</button><button data-k=right>&#9654;</button></div>
<div id=pad2><button data-k=space style="width:80px">SPACE</button><button data-k=return>&#9166;</button><button data-k=escape>ESC</button></div></div>
<div class=tab id=cons><pre id=con></pre><div id=inrow><input id=inp placeholder="input() text..."><button id=snd>Send</button></div></div>
</main>
<nav><button class=on data-t=out>Output</button><button data-t=cons>Console</button></nav>
<script>
const $=s=>document.querySelector(s),post=(u,d)=>fetch(u,{method:'POST',body:JSON.stringify(d||{})}).catch(()=>{});
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x==b));
 document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.id==b.dataset.t))});
async function files(){try{const r=await (await fetch('/files')).json();const s=$('#sel'),cur=s.value||r.name;
 s.innerHTML=r.files.map(f=>`<option>${f}</option>`).join('')||'<option>(no .py files in '+r.root+')</option>';if(cur)s.value=cur}catch(e){}}
files();setInterval(files,5000);
$('#run').onclick=()=>{$('#con').textContent='';since=0;$('#scr').src='/stream?'+Date.now();post('/run',{file:$('#sel').value})};
$('#stp').onclick=()=>post('/stop');
let since=0;setInterval(async()=>{try{const r=await (await fetch('/console?since='+since)).json();since=r.n;
 if(r.text){const c=$('#con');c.textContent+=r.text;c.scrollTop=c.scrollHeight}}catch(e){}},200);
$('#snd').onclick=()=>{post('/stdin',{text:$('#inp').value});$('#inp').value=''};$('#inp').onkeydown=e=>{if(e.key=='Enter')$('#snd').click()};
const img=$('#scr');function pt(e){const r=img.getBoundingClientRect(),t=e.touches?(e.touches[0]||e.changedTouches[0]):e;
 return {x:(t.clientX-r.left)/r.width*img.naturalWidth,y:(t.clientY-r.top)/r.height*img.naturalHeight}}
let dn=false,lm=0;
img.addEventListener('pointerdown',e=>{dn=true;img.setPointerCapture(e.pointerId);post('/event',{t:'down',...pt(e)})});
img.addEventListener('pointermove',e=>{if(dn&&Date.now()-lm>30){lm=Date.now();post('/event',{t:'move',...pt(e)})}});
img.addEventListener('pointerup',e=>{dn=false;post('/event',{t:'up',...pt(e)})});
img.ondragstart=e=>e.preventDefault();
document.querySelectorAll('[data-k]').forEach(b=>{const k=b.dataset.k;
 b.addEventListener('pointerdown',e=>{e.preventDefault();post('/event',{t:'kdown',key:k})});
 const up=e=>post('/event',{t:'kup',key:k});b.addEventListener('pointerup',up);b.addEventListener('pointerleave',up)});
fetch('/files').then(r=>r.json()).then(r=>{if(r.running)img.src='/stream?'+Date.now()}).catch(()=>{});
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--dir", default="~/drowsiness_lab"); s.add_argument("--port", type=int, default=8765)
    r = sub.add_parser("run"); r.add_argument("script")
    c = sub.add_parser("child"); c.add_argument("script")
    a = ap.parse_args()
    if a.cmd == "serve": serve(a.dir, a.port)
    elif a.cmd == "child": child_main(a.script)
    else: child_main(a.script)


if __name__ == "__main__":
    main()
