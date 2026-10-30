#!/usr/bin/env python3
"""Klipper Companion: a read-only helper that explains Klipper errors and
checks printer.cfg. Talks to Moonraker over HTTP. It never sends G-code and
never edits files. Python 3 standard library only."""
import base64, io, json, os, re, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MOONRAKER = os.environ.get("MOONRAKER_URL", "http://127.0.0.1:7125")
HOST = os.environ.get("COMPANION_HOST", "0.0.0.0")
PORT = int(os.environ.get("COMPANION_PORT", "8099"))
LLM_URL = os.environ.get("LLM_URL", "")   # OpenAI-compatible chat endpoint (optional)
LLM_MODEL = os.environ.get("LLM_MODEL", "")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "300"))            # cap reply length (small models are slow)
LLM_WHILE_PRINTING = os.environ.get("LLM_WHILE_PRINTING", "0") == "1"
HERE = Path(__file__).parent

VERSION = (HERE / "VERSION").read_text().strip() if (HERE / "VERSION").exists() else "dev"

def load_rules():
    """Error rules live in rules.json so they can be updated without code changes."""
    return [(r["pattern"], r["title"], r["fix"]) for r in json.loads((HERE / "rules.json").read_text())]

RULES = load_rules()

def fill(text, groups):
    for i, g in enumerate(groups):
        text = text.replace("{%d}" % i, g or "?")
    return text

def diagnose(text):
    found = []
    for pat, title, fix in RULES:
        m = re.search(pat, text)
        if m:
            found.append({"title": title, "fix": fill(fix, m.groups()), "match": m.group(0)[:120]})
    return found

def mr(path):
    with urllib.request.urlopen(MOONRAKER + path, timeout=5) as r:
        return json.loads(r.read())

def status():
    try:
        info = mr("/printer/info")["result"]
        state = info.get("state", "unknown")
        count = 100 if state != "ready" else 20   # when ready, ignore old history
        store = mr("/server/gcode_store?count=%d" % count)["result"]["gcode_store"]
    except Exception as e:
        return {"state": "unreachable", "message": "Can't reach Moonraker at %s (%s)" % (MOONRAKER, e), "issues": []}
    texts = [info.get("state_message", "")] + [s.get("message", "") for s in reversed(store) if s.get("type") == "response"]
    issues, seen = [], set()
    for t in texts:
        for d in diagnose(t):
            if d["title"] not in seen:
                seen.add(d["title"]); issues.append(d)
    return {"state": state, "message": info.get("state_message", ""), "issues": issues[:6]}

def fetch_cfg():
    with urllib.request.urlopen(MOONRAKER + "/server/files/config/printer.cfg", timeout=5) as r:
        return r.read().decode("utf-8", "replace")

def lint(cfg):
    out, seen, cur, opts = [], {}, None, {}
    for n, raw in enumerate(cfg.splitlines(), 1):
        if raw.startswith("#*#"):
            break                                    # SAVE_CONFIG block
        line = raw.rstrip()
        if not line or line.lstrip().startswith(("#", ";")) or raw[0] in " \t":
            continue                                 # blank, comment, or indented body
        m = re.match(r"\[(.+?)\]\s*(?:[#;].*)?$", line)
        if m:
            cur = m.group(1).strip()
            if cur in seen:
                out.append("Line %d: [%s] appears again (first at line %d). Duplicate sections usually cause a config error." % (n, cur, seen[cur]))
            seen.setdefault(cur, n); opts = {}
            continue
        m = re.match(r"([\w.]+)\s*[:=]", line)
        if not m:
            out.append("Line %d: not a section, option, or comment: %s" % (n, line[:60])); continue
        if cur is None:
            out.append("Line %d: option appears before any [section]." % n); continue
        if m.group(1) in opts:
            out.append("Line %d: `%s` repeated in [%s] (first at line %d)." % (n, m.group(1), cur, opts[m.group(1)]))
        opts.setdefault(m.group(1), n)
    return out

def lint_result():
    try:
        return {"issues": lint(fetch_cfg())}
    except Exception as e:
        return {"error": "Couldn't read printer.cfg: %s" % e, "issues": []}

SYSTEM = ("You are a Klipper 3D printer firmware assistant. Explain errors plainly and give concrete fixes "
          "in Klipper config syntax. You only advise and never claim to have changed anything. "
          "Warn before suggesting anything that moves axes or heats. If unsure, say so. Keep answers short and base them on the known issues when given.")

def printing():
    try:
        return mr("/printer/objects/query?print_stats=state")["result"]["status"]["print_stats"]["state"] == "printing"
    except Exception:
        return False

def excerpt(cfg, hints, limit=1500):
    """Only the printer.cfg sections the error or question mentions: small prompts are fast prompts."""
    parts, name = {}, None
    for line in cfg.splitlines():
        if line.startswith("#*#"):
            break
        m = re.match(r"\[(.+?)\]", line)
        if m:
            name = m.group(1); parts[name] = [line]
        elif name:
            parts[name].append(line)
    hint, out = " ".join(hints).lower(), ""
    for n, lines in parts.items():
        if n.lower() in hint or n.split()[-1].lower() in hint:
            block = "\n".join(lines).strip() + "\n\n"
            if len(out) + len(block) > limit:
                break
            out += block
    return out.strip() or "(no matching sections)"

def ask_stream(question):
    if not (LLM_URL and LLM_MODEL):
        yield "No language model is configured, so I can only match known errors (see Needs attention). Run setup-local-llm.sh to add a small local one."
        return
    if printing() and not LLM_WHILE_PRINTING:
        yield "Chat is paused during a print so the model can't disturb print timing. Known-error matching still works. Ask again after the print."
        return
    st = status()
    try:
        cfg = excerpt(fetch_cfg(), [question, st["message"]] + [i["match"] for i in st["issues"]])
    except Exception:
        cfg = "(printer.cfg unavailable)"
    ctx = "State: %s. Message: %s\nKnown issues:\n%s\nRelevant printer.cfg sections:\n%s\n\nQuestion: %s" % (
        st["state"], st["message"], "\n".join("- %s: %s" % (i["title"], i["fix"]) for i in st["issues"]) or "none", cfg, question)
    body = json.dumps({"model": LLM_MODEL, "stream": True, "max_tokens": MAX_TOKENS, "messages": [
        {"role": "system", "content": SYSTEM}, {"role": "user", "content": ctx}]}).encode()
    try:
        req = urllib.request.Request(LLM_URL, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:") or line.endswith("[DONE]"):
                    continue
                piece = json.loads(line[5:])["choices"][0].get("delta", {}).get("content")
                if piece:
                    yield piece
    except Exception as e:
        yield "\n(The language model stopped responding: %s)" % e

try:
    from PIL import Image          # sudo apt install python3-pil
except ImportError:
    Image = None
LAST_FRAME = {"px": None}

# ---- Webcam (advisory only: looks at snapshots, never controls the printer) ----
VISION_URL = os.environ.get("VISION_URL", LLM_URL)
VISION_MODEL = os.environ.get("VISION_MODEL", LLM_MODEL)
WEBCAM_BASE = os.environ.get("WEBCAM_BASE", "http://127.0.0.1")   # where relative snapshot URLs resolve
WATCH_MINUTES = float(os.environ.get("WATCH_MINUTES", "0"))        # 0 = no automatic checks
WATCH = {"time": None, "verdict": None}

VISION_SYSTEM = ("You inspect a photo from a 3D printer's camera. Look for common FDM failures: spaghetti, "
    "part detached from the bed, layer shift, heavy stringing or blobs, warping or lifting corners, signs of a clog. "
    "Begin with exactly OK, WARNING, or PROBLEM, then one or two plain sentences. You only advise and cannot "
    "control the printer. If the image is dark, empty, or unclear, say so instead of guessing.")

def snapshot():
    url = os.environ.get("CAMERA_SNAPSHOT_URL", "")
    if not url:
        cams = [w for w in mr("/server/webcams/list")["result"]["webcams"] if w.get("enabled", True)]
        if not cams:
            raise RuntimeError("no webcam is configured in Mainsail/Fluidd")
        url = cams[0].get("snapshot_url", "")
    if not url.startswith("http"):
        url = WEBCAM_BASE + (url if url.startswith("/") else "/" + url)
    with urllib.request.urlopen(url, timeout=8) as r:
        return r.read(), r.headers.get_content_type()

def basic_check():
    """Simple non-AI camera check. Weak, but fully local."""
    if Image is None:
        return "Basic camera checks need Pillow: sudo apt install python3-pil"
    try:
        img, _ = snapshot()
        px = list(Image.open(io.BytesIO(img)).convert("L").resize((32, 24)).getdata())
    except Exception as e:
        return "Couldn't read the camera image: %s" % e
    mean = sum(px) / len(px)
    prev, LAST_FRAME["px"] = LAST_FRAME["px"], px
    diff = sum(abs(a - b) for a, b in zip(px, prev)) / len(px) if prev else None
    notes = []
    if mean < 25:
        notes.append("the image is very dark or the camera is covered")
    if diff is not None and diff > 30:
        notes.append("the scene changed a lot since the last check (a failure, or someone in frame)")
    return "%s (basic check, no AI): brightness %d/255%s%s" % (
        "WARNING" if notes else "OK", mean, ", change %d" % diff if diff is not None else "",
        ("; " + "; ".join(notes)) if notes else "")

def look(question=""):
    if not (VISION_URL and VISION_MODEL):
        return basic_check()
    try:
        img, ctype = snapshot()
    except Exception as e:
        return "Couldn't get a camera image: %s" % e
    try:
        q = "/printer/objects/query?print_stats=state,filename&display_status=progress"
        st = mr(q)["result"]["status"]
        info = "Print state: %s, file: %s, progress: %d%%." % (
            st["print_stats"].get("state"), st["print_stats"].get("filename") or "none",
            int(st["display_status"].get("progress", 0) * 100))
    except Exception:
        info = "Print state unknown."
    uri = "data:%s;base64,%s" % (ctype, base64.b64encode(img).decode())
    body = json.dumps({"model": VISION_MODEL, "messages": [
        {"role": "system", "content": VISION_SYSTEM},
        {"role": "user", "content": [{"type": "text", "text": info + " " + question},
                                     {"type": "image_url", "image_url": {"url": uri}}]}]}).encode()
    try:
        req = urllib.request.Request(VISION_URL, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception as e:
        return "The vision model didn't respond (%s)." % e

def watch_loop():
    while True:
        time.sleep(max(WATCH_MINUTES, 0.5) * 60)
        try:
            if mr("/printer/objects/query?print_stats=state")["result"]["status"]["print_stats"]["state"] != "printing":
                continue
        except Exception:
            continue
        WATCH.update(time=time.strftime("%H:%M"), verdict=look())

class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/":
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            s = status(); s["version"] = VERSION; s["watch"] = WATCH
            self._send(200, s)
        elif self.path.startswith("/api/snapshot"):
            try:
                img, ctype = snapshot()
                self._send(200, img, ctype)
            except Exception as e:
                self._send(503, {"error": str(e)})
        elif self.path == "/api/lint":
            self._send(200, lint_result())
        else:
            self._send(404, {"error": "not found"})
    def do_POST(self):
        try:
            q = str(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))).get("question", ""))[:2000]
        except Exception:
            return self._send(400, {"error": "bad request"})
        if self.path == "/api/ask":
            self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache"); self.end_headers()
            try:
                for piece in ask_stream(q):
                    self.wfile.write(piece.encode()); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path == "/api/look":
            self._send(200, {"answer": look(q)})
        else:
            self._send(404, {"error": "not found"})
    def log_message(self, *a):
        pass

if __name__ == "__main__":
    print("Klipper Companion on http://%s:%d (Moonraker: %s)" % (HOST, PORT, MOONRAKER))
    if WATCH_MINUTES > 0:
        threading.Thread(target=watch_loop, daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
