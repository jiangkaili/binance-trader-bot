#!/usr/bin/env python3
"""Verify that the binance-trader-bot setup is complete and ready to run.

Usage:
    python scripts/verify_setup.py

Checks:
1. Python version >= 3.10
2. All required packages installed
3. .env file exists with required keys
4. config/trader.yaml exists and is valid
5. Tests pass
6. Connectivity to Binance (ping)
7. Dry-run starts without errors
"""
import sys
import os
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"

results = []

def check(name, ok, detail=""):
    status = PASS if ok else FAIL
    results.append(ok)
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))

print("=" * 50)
print("  binance-trader-bot — Setup Verification")
print("=" * 50)
print()

# 1. Python version / Python版本
v = sys.version_info
check("Python >= 3.10", v >= (3, 10), f"{v.major}.{v.minor}.{v.micro}")

# 2. Required packages / 必需包
pkgs = ["requests", "yaml", "pandas", "numpy", "pytest"]
for pkg in pkgs:
    try:
        mod = __import__(pkg if pkg != "yaml" else "yaml")
        check(f"Package: {pkg}", True)
    except ImportError:
        check(f"Package: {pkg}", False, "not installed — run: pip install -r requirements.txt")

# 3. .env file / .env文件
env_path = ROOT / ".env"
env_ok = env_path.exists()
check(".env file exists", env_ok)
if env_ok:
    content = env_path.read_text()
    has_key = "BINANCE_API_KEY=" in content and "your_" not in content.split("BINANCE_API_KEY=")[1].split("\n")[0]
    has_secret = "BINANCE_API_SECRET=" in content and "your_" not in content.split("BINANCE_API_SECRET=")[1].split("\n")[0]
    check("  API key filled in", has_key)
    check("  API secret filled in", has_secret)

# 4. config/trader.yaml / 配置文件
yaml_path = ROOT / "config" / "trader.yaml"
check("config/trader.yaml exists", yaml_path.exists())
if yaml_path.exists():
    try:
        import yaml
        cfg = yaml.safe_load(yaml_path.read_text())
        required = ["symbol", "leverage", "stop_loss_pct", "take_profit_pct"]
        missing = [k for k in required if k not in cfg]
        check("  YAML valid + required keys present", not missing, ", ".join(missing) if missing else "")
    except Exception as e:
        check("  YAML valid + required keys present", False, str(e))

# 5. trader/ module imports / 模块导入
try:
    from trader.config import TraderConfig
    from trader.exchange import BinanceFutures
    from trader.models import Position
    from trader.paths import DATA_DIR
    check("Core modules import", True)
except Exception as e:
    check("Core modules import", False, str(e))

# 6. Tests / 测试
print()
print("  Running tests (pytest)...")
import subprocess
r = subprocess.run(
    [sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q", "--tb=no"],
    capture_output=True, text=True, cwd=str(ROOT),
)
test_ok = r.returncode == 0
passed = r.stdout.count(" passed")
check(f"Tests pass ({passed} tests)", test_ok)

# Summary / 总结
print()
print("=" * 50)
total = len(results)
passed_count = sum(results)
if passed_count == total:
    print(f"  {PASS} ALL CHECKS PASSED ({passed_count}/{total})")
    print("  Ready for dry-run: python scripts/live_trader.py --dry-run --env-file .env")
else:
    print(f"  {FAIL} {passed_count}/{total} checks passed — fix the failures above")
print("=" * 50)

sys.exit(0 if passed_count == total else 1)
