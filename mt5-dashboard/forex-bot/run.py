import argparse, os, sys, subprocess, platform
from dotenv import load_dotenv
load_dotenv()
ROOT   = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(ROOT, "engine")
BUILD  = os.path.join(ENGINE, "build")

def banner(msg): print(f"\n{'='*60}\n  {msg}\n{'='*60}")
def ok(msg):     print(f"  OK   {msg}")
def warn(msg):   print(f"  WARN {msg}")
def err(msg):    print(f"  ERR  {msg}")

def build_engine():
    banner("Building C++ signal engine")
    os.makedirs(BUILD, exist_ok=True)
    cmake_cmd = ["cmake", "..", "-DCMAKE_BUILD_TYPE=Release"]
    if platform.system() == "Windows":
        cmake_cmd += ["-G", "Visual Studio 17 2022", "-A", "x64"]
    r = subprocess.run(cmake_cmd, cwd=BUILD)
    if r.returncode != 0: err("CMake configure failed"); sys.exit(1)
    r = subprocess.run(["cmake", "--build", ".", "--config", "Release"], cwd=BUILD)
    if r.returncode != 0: err("Build failed"); sys.exit(1)
    ok("Engine built")

def engine_exists():
    candidates = [
        os.path.join(BUILD, "Release", "forex_engine.dll"),
        os.path.join(BUILD, "forex_engine.dll"),
        os.path.join(ROOT,  "forex_engine.dll"),
        os.path.join(BUILD, "libforex_engine.so"),
    ]
    return any(os.path.exists(p) for p in candidates)

def check_env():
    banner("Environment check")
    for pkg in ["fastapi","uvicorn","sqlalchemy","yaml","numpy","pandas","requests"]:
        try: __import__(pkg); ok(pkg)
        except ImportError: err(f"{pkg} not installed")
    try: import MetaTrader5; ok("MetaTrader5")
    except ImportError: warn("MetaTrader5 not found - DEMO mode")
    if engine_exists(): ok("forex_engine.dll found")
    else: warn("forex_engine.dll NOT found - signals will not fire until built")
    cfg = os.path.join(ROOT, "config.yaml")
    if os.path.exists(cfg): ok("config.yaml found")
    else: err("config.yaml missing")

def start_api_only():
    banner("API server only (no MT5, no engine required)")
    sys.path.insert(0, ROOT)
    from db.journal import init_db; init_db()
    import uvicorn, yaml
    with open(os.path.join(ROOT, "config.yaml")) as f: cfg = yaml.safe_load(f)
    api = cfg.get("api", {})
    port = api.get("port", 8000)
    print(f"\n  Dashboard API: http://localhost:{port}")
    print(f"  WebSocket:     ws://localhost:{port}/ws\n")
    uvicorn.run("bridge.api_server:app", host=api.get("host","0.0.0.0"),
                port=port, log_level="info")

def start_bot():
    banner("Starting Vestro Bot")
    sys.path.insert(0, ROOT)
    from bridge.bot import main; main()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build",    action="store_true")
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--check",    action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)

    if args.check:
        check_env()
        sys.exit(0)

    if args.build:
        build_engine()
        sys.exit(0)

    # --api-only: skip engine check entirely
    if args.api_only:
        check_env()
        start_api_only()
        sys.exit(0)

    # Full bot: need engine

    check_env()
    start_bot()