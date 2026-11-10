# /// script
# requires-python = ">=3.11"
# dependencies = ["websocket-client>=1.8"]
# ///
"""What the face costs to keep alive on the RPi: Chromium CPU per variant.

    uv run scripts/face-bench/bench.py --runs gpuinfo bench_static_gpu bench_svg_attr_gpu ...
    uv run scripts/face-bench/bench.py --runs all

For each run: start the page's server (once per group of runs that share it),
launch Chromium the way rpi-up.sh does (kiosk, Wayland) with DevTools on
:9222, let it settle, then over a fixed window read

- CPU per Chromium process type (browser / renderer / gpu-process / utility),
  from /proc/<pid>/stat, as % of one core
- the whole machine's busy %, from /proc/stat
- the page's requestAnimationFrame rate (CDP Runtime.evaluate)
- the bench page's own counters (frames, attribute writes, blinks, glances)
- SoC temperature

and the GPU feature status once per GPU configuration (CDP SystemInfo.getInfo
- the programmatic chrome://gpu). Rows go to scripts/face-bench/out/*.jsonl and
a table to stdout.

The display must stay on: swayidle is stopped for the duration and restarted
after, with the same line rpi-up.sh's `down` uses. Chromium is killed by the
pid this script launched - never by pkill -f on a string, which on this box has
killed the calling ssh session before.
"""

import argparse
import base64
import json
import os
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

import websocket

repo = Path(__file__).resolve().parents[2]
branch_repo = Path.home() / "face-idle-branch"
profile = Path.home() / ".config" / "claw-face-bench"
out_dir = repo / "scripts" / "face-bench" / "out"
devtools = "http://127.0.0.1:9222"
clock_ticks = os.sysconf("SC_CLK_TCK")
insecure = ssl._create_unverified_context()

env = dict(
    os.environ,
    PATH=f"{Path.home() / '.local' / 'bin'}:{os.environ['PATH']}",
    WAYLAND_DISPLAY=os.environ.get("WAYLAND_DISPLAY", "wayland-0"),
    XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
)

# rpi-up.sh's kiosk line, minus --force-prefers-reduced-motion (a per-run flag
# here) and plus DevTools. --remote-allow-origins lets a non-browser client
# open the websocket; DevTools binds to 127.0.0.1 only.
chromium_base = [
    "chromium", "--kiosk", "--ozone-platform=wayland", "--lang=ko", "--password-store=basic",
    "--ignore-certificate-errors", "--autoplay-policy=no-user-gesture-required",
    "--use-fake-ui-for-media-stream", "--noerrdialogs", "--disable-infobars", "--no-first-run",
    f"--user-data-dir={profile}", "--remote-debugging-port=9222", "--remote-allow-origins=*",
]
reduced_motion = ["--force-prefers-reduced-motion"]
no_gpu = ["--disable-gpu"]


@dataclass(frozen=True)
class server_spec:
    cwd: Path
    argv: list[str]
    # Probed in order; the first that answers is the page's origin.
    origins: list[str]


servers = {
    "bench": server_spec(repo / "scripts" / "face-bench", ["python3", "-m", "http.server", "8765", "--bind", "127.0.0.1"], ["http://127.0.0.1:8765"]),
    "main_dev": server_spec(repo / "frontend", ["npx", "vite", "--host", "127.0.0.1", "--port", "5173", "--strictPort"], ["https://127.0.0.1:5173"]),
    "main_prod": server_spec(repo / "frontend", ["npx", "vite", "preview", "--host", "127.0.0.1", "--port", "4173", "--strictPort"], ["https://127.0.0.1:4173", "http://127.0.0.1:4173"]),
    "branch_dev": server_spec(branch_repo / "frontend", ["npx", "vite", "--host", "127.0.0.1", "--port", "5173", "--strictPort"], ["https://127.0.0.1:5173"]),
    "branch_prod": server_spec(branch_repo / "frontend", ["npx", "vite", "preview", "--host", "127.0.0.1", "--port", "4173", "--strictPort"], ["https://127.0.0.1:4173", "http://127.0.0.1:4173"]),
}


@dataclass(frozen=True)
class run_spec:
    name: str
    server: str
    path: str
    flags: list[str]
    note: str


bench_modes = ["static", "svg_attr", "svg_hybrid", "layers_css", "layers_img", "canvas", "events_img", "events_svg", "breathe10_img"]

plan: dict[str, run_spec] = {}

for gpu_name, gpu_flags in [("gpu", []), ("nogpu", no_gpu)]:
    for mode in bench_modes:
        plan[f"bench_{mode}_{gpu_name}"] = run_spec(
            f"bench_{mode}_{gpu_name}", "bench", f"/index.html?mode={mode}", gpu_flags, f"bench {mode}, {gpu_name}"
        )

for code, layout in [("main", "#/face?role=viewer"), ("branch", "#/kiosk")]:
    for build in ["dev", "prod"]:
        plan[f"{code}_{build}"] = run_spec(f"{code}_{build}", f"{code}_{build}", f"/{layout}", [], f"{code} {layout} {build}, motion on")
        plan[f"{code}_{build}_rm"] = run_spec(
            f"{code}_{build}_rm", f"{code}_{build}", f"/{layout}", reduced_motion, f"{code} {layout} {build}, reduced-motion (rpi-up.sh)"
        )
        plan[f"{code}_{build}_nogpu"] = run_spec(
            f"{code}_{build}_nogpu", f"{code}_{build}", f"/{layout}", no_gpu, f"{code} {layout} {build}, motion on, --disable-gpu"
        )

plan["blank_gpu"] = run_spec("blank_gpu", "bench", "/blank", [], "about:blank-like floor (404 page)")

# Day-12 implementation on main: the kiosk (event idle, no debug chrome) and the
# debug viewer, both from the production build.
for build in ["dev", "prod"]:
    plan[f"main_{build}_kiosk"] = run_spec(f"main_{build}_kiosk", f"main_{build}", "/#/kiosk", [], f"main #/kiosk {build}, event idle")
    plan[f"main_{build}_kiosk_rm"] = run_spec(
        f"main_{build}_kiosk_rm", f"main_{build}", "/#/kiosk", reduced_motion, f"main #/kiosk {build}, reduced-motion"
    )


def log(message: str) -> None:
    print(f"[bench] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


# --- processes ----------------------------------------------------------------


def answers(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2, context=insecure)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def start_server(name: str) -> tuple[subprocess.Popen, str]:
    spec = servers[name]
    log_file = open(out_dir / f"server-{name}.log", "w")
    process = subprocess.Popen(spec.argv, cwd=spec.cwd, env=env, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.time() + 90

    while time.time() < deadline:
        for origin in spec.origins:
            if answers(origin + "/"):
                log(f"server {name} up at {origin}")
                return process, origin

        if process.poll() is not None:
            raise RuntimeError(f"server {name} exited with {process.returncode}; see {log_file.name}")

        time.sleep(1)

    stop(process)
    raise RuntimeError(f"server {name} did not answer within 90s")


def stop(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)


def parent_map() -> dict[int, int]:
    parents: dict[int, int] = {}

    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue

        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except OSError:
            continue

        parents[int(entry.name)] = int(fields[1])

    return parents


def process_kind(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return "gone"

    # Children rewrite their argv (one space-joined string), so search the raw
    # bytes rather than splitting on NUL.
    marker = raw.find(b"--type=")

    if marker < 0:
        return "browser"

    end = min(index for index in (raw.find(b" ", marker), raw.find(b"\0", marker), len(raw)) if index >= 0)
    return raw[marker + len(b"--type="):end].decode()


def chromium_pids(root: int) -> dict[int, str]:
    """pid -> process type for the browser we launched and all its descendants.

    By process tree, not by a flag in the command line: the GPU and utility
    processes do not carry --user-data-dir (measured on Chromium 142).
    """
    parents = parent_map()
    tree = {root}
    grew = True

    while grew:
        grew = False

        for pid, parent in parents.items():
            if parent in tree and pid not in tree:
                tree.add(pid)
                grew = True

    return {pid: process_kind(pid) for pid in tree if pid in parents}


def cpu_ticks(pid: int) -> int | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except OSError:
        return None

    # utime and stime are fields 14 and 15; after the ")" split they are 11, 12.
    return int(fields[11]) + int(fields[12])


def system_ticks() -> tuple[int, int]:
    values = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    idle = values[3] + values[4]
    return sum(values), idle


def temperature() -> float:
    text = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True).stdout
    return float(text.strip().removeprefix("temp=").removesuffix("'C"))


def throttled() -> str:
    return subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True).stdout.strip()


# --- devtools -----------------------------------------------------------------


def devtools_json(path: str):
    with urllib.request.urlopen(devtools + path, timeout=3) as response:
        return json.loads(response.read())


def cdp(ws_url: str, method: str, params: dict, timeout: float):
    socket = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)

    try:
        socket.send(json.dumps({"id": 1, "method": method, "params": params}))

        while True:
            message = json.loads(socket.recv())

            if message.get("id") == 1:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")

                return message["result"]
    finally:
        socket.close()


def page_ws() -> str:
    pages = [target for target in devtools_json("/json") if target["type"] == "page"]

    if len(pages) == 0:
        raise RuntimeError("no page target")

    return pages[0]["webSocketDebuggerUrl"]


def evaluate(expression: str, timeout: float):
    result = cdp(page_ws(), "Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True}, timeout)

    if "exceptionDetails" in result:
        raise RuntimeError(f"page threw: {result['exceptionDetails'].get('text')}")

    return result["result"].get("value")


# Counts the page's own requestAnimationFrame calls without asking for any
# frames: a face that sleeps between events shows a handful per second, a
# running loop ~60. Installed at the start of the window, read at the end.
raf_counter_install_js = """(() => {
  window.__raf_calls = 0;
  const original = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (callback) => { window.__raf_calls++; return original(callback); };
  return true;
})()"""

# The probe asks for frames itself, so it is run only at the end of the window
# - inside it, it would wake an event-only page that is otherwise asleep.
raf_rate_js = """new Promise((resolve) => {
  let n = 0; const t0 = performance.now();
  const f = () => { n++; if (performance.now() - t0 < 5000) requestAnimationFrame(f); else resolve(n / ((performance.now() - t0) / 1000)); };
  requestAnimationFrame(f);
})"""


def gpu_info() -> dict:
    browser_ws = devtools_json("/json/version")["webSocketDebuggerUrl"]
    info = cdp(browser_ws, "SystemInfo.getInfo", {}, 10)["gpu"]
    aux = info.get("auxAttributes", {})

    return {
        "feature_status": info.get("featureStatus", {}),
        "devices": [
            {key: device.get(key) for key in ("vendorString", "deviceString", "driverVendor", "driverVersion")}
            for device in info.get("devices", [])
        ],
        "gl": {key: aux.get(key) for key in ("glRenderer", "glVersion", "glImplementationParts", "displayType", "passthroughCmdDecoder") if key in aux},
        "workarounds": info.get("driverBugWorkarounds", [])[:20],
    }


# --- one run ------------------------------------------------------------------


@dataclass
class run_result:
    name: str
    note: str
    url: str
    flags: list[str]
    cpu_by_type: dict[str, float]
    chromium_cpu: float
    system_busy: float
    raf_hz: float
    # The page's own rAF calls per second during the window (0 = asleep).
    page_raf_per_second: float
    page_counters: dict | None
    temp_start: float
    temp_end: float
    throttled: str
    processes: int


def launch_chromium(url: str, flags: list[str]) -> subprocess.Popen:
    argv = chromium_base + flags + [url]
    process = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.time() + 40

    while time.time() < deadline:
        if answers(devtools + "/json/version"):
            return process

        time.sleep(0.5)

    kill_chromium(process)
    raise RuntimeError("chromium devtools did not come up")


def kill_chromium(process: subprocess.Popen) -> None:
    tree = chromium_pids(process.pid)
    process.send_signal(signal.SIGTERM)

    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()

    # Anything of the tree that outlived the browser.
    for pid in tree:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    time.sleep(2)


def measure(spec: run_spec, origin: str, settle: float, window: float) -> tuple[run_result, dict | None]:
    url = origin + spec.path
    log(f"{spec.name}: {url} {' '.join(spec.flags)}")
    process = launch_chromium(url, spec.flags)

    try:
        time.sleep(settle)
        gpu = gpu_info()
        temp_start = temperature()
        pids = chromium_pids(process.pid)
        start = {pid: cpu_ticks(pid) for pid in pids}
        evaluate(raf_counter_install_js, 5)
        sys_total_0, sys_idle_0 = system_ticks()
        t0 = time.monotonic()
        time.sleep(window)
        elapsed = time.monotonic() - t0
        end = {pid: cpu_ticks(pid) for pid in pids}
        sys_total_1, sys_idle_1 = system_ticks()
        raf_calls = float(evaluate("window.__raf_calls", 5))
        counters = evaluate("window.__bench ? {...window.__bench} : null", 5)
        shot = cdp(page_ws(), "Page.captureScreenshot", {"format": "png"}, 15)["data"]
        (out_dir / f"{spec.name}.png").write_bytes(base64.b64decode(shot))
        raf_hz = float(evaluate(raf_rate_js, 15))
        temp_end = temperature()
    finally:
        kill_chromium(process)

    by_type: dict[str, float] = {}

    for pid, kind in pids.items():
        if start[pid] is None or end[pid] is None:
            continue

        by_type[kind] = by_type.get(kind, 0.0) + (end[pid] - start[pid]) / clock_ticks / elapsed * 100

    busy = 1 - (sys_idle_1 - sys_idle_0) / (sys_total_1 - sys_total_0)
    result = run_result(
        name=spec.name, note=spec.note, url=url, flags=spec.flags,
        cpu_by_type={kind: round(value, 1) for kind, value in sorted(by_type.items())},
        chromium_cpu=round(sum(by_type.values()), 1),
        # Of all four cores, expressed like the others as % of one core.
        system_busy=round(busy * 400, 1),
        raf_hz=round(raf_hz, 1), page_raf_per_second=round(raf_calls / elapsed, 1), page_counters=counters,
        temp_start=temp_start, temp_end=temp_end, throttled=throttled(), processes=len(pids),
    )
    log(f"{spec.name}: chromium {result.chromium_cpu}% {result.cpu_by_type} | system {result.system_busy}% | page rAF {result.page_raf_per_second}/s | display {result.raf_hz} Hz | {temp_end}'C")

    return result, gpu


# --- main ---------------------------------------------------------------------


def screen_keep_on() -> None:
    subprocess.run(["pkill", "-x", "swayidle"], env=env)

    for line in subprocess.run(["wlopm"], env=env, capture_output=True, text=True).stdout.splitlines():
        subprocess.run(["wlopm", "--on", line.split()[0]], env=env)


def screen_restore_blanking() -> None:
    subprocess.Popen(
        ["setsid", "swayidle", "-w", "timeout", "600", "wlopm --off *", "resume", "wlopm --on *"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True,
    )


def prepare_profile() -> None:
    # The same first-run suppressions rpi-up.sh writes for the kiosk profile.
    preferences = profile / "Default" / "Preferences"

    if not preferences.exists():
        preferences.parent.mkdir(parents=True, exist_ok=True)
        preferences.write_text('{"translate":{"enabled":false},"intl":{"accept_languages":"ko-KR,ko"}}')


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", nargs="+", required=True, help=f"run names, or 'all'. Known: {' '.join(plan)}")
    parser.add_argument("--settle", type=float, default=15.0)
    parser.add_argument("--window", type=float, default=45.0)
    parser.add_argument("--backend", action="store_true", help="also run the backend (the app pages open /api/events)")
    arguments = parser.parse_args(argv)

    names = list(plan) if arguments.runs == ["all"] else arguments.runs
    specs = [plan[name] for name in names]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / time.strftime("run-%Y%m%d-%H%M%S.jsonl")
    prepare_profile()

    if answers(devtools + "/json/version"):
        raise RuntimeError("something already answers on the DevTools port 9222")

    backend = None

    if arguments.backend:
        backend = subprocess.Popen(
            ["uv", "run", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=repo / "backend",
            env=dict(env, AGENT_GATEWAY_URL="http://127.0.0.1:3001", AGENT_PAIRING_CODE="000000", PUSH_TOKEN="day8-push"),
            stdout=open(out_dir / "server-backend.log", "w"), stderr=subprocess.STDOUT, start_new_session=True,
        )

    screen_keep_on()
    results: list[run_result] = []
    gpu_reports: dict[str, dict] = {}
    current: tuple[str, subprocess.Popen, str] | None = None

    try:
        for spec in specs:
            if current is None or current[0] != spec.server:
                if current is not None:
                    stop(current[1])

                process, origin = start_server(spec.server)
                current = (spec.server, process, origin)
                # A dev server compiles on first request; let it finish before measuring.
                time.sleep(3)

            result, gpu = measure(spec, current[2], arguments.settle, arguments.window)
            gpu_key = "nogpu" if no_gpu[0] in spec.flags else "gpu"

            if gpu_key not in gpu_reports:
                gpu_reports[gpu_key] = gpu

                with out_path.open("a") as out:
                    out.write(json.dumps({"gpu_config": gpu_key, **gpu}) + "\n")

            results.append(result)

            with out_path.open("a") as out:
                out.write(json.dumps(asdict(result)) + "\n")
    finally:
        if current is not None:
            stop(current[1])

        if backend is not None:
            stop(backend)

        screen_restore_blanking()

    for key, report in gpu_reports.items():
        print(f"\n## GPU ({key})")
        print("feature_status:", json.dumps(report["feature_status"], ensure_ascii=False))
        print("gl:", json.dumps(report["gl"], ensure_ascii=False))
        print("devices:", json.dumps(report["devices"], ensure_ascii=False))

    print("\n## CPU (% of one core, mean over the window)\n")
    print(f"{'run':24} {'chromium':>8} {'renderer':>8} {'gpu':>6} {'browser':>7} {'system':>7} {'pageRAF':>7} {'°C':>5}  note")

    for row in results:
        print(
            f"{row.name:24} {row.chromium_cpu:8.1f} {row.cpu_by_type.get('renderer', 0):8.1f} "
            f"{row.cpu_by_type.get('gpu-process', 0):6.1f} {row.cpu_by_type.get('browser', 0):7.1f} "
            f"{row.system_busy:7.1f} {row.page_raf_per_second:7.1f} {row.temp_end:5.1f}  {row.note}"
        )

    log(f"wrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
