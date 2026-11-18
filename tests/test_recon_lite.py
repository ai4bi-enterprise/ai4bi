"""Bản Lite: công cụ đối soát chạy trên dữ liệu mẫu, chỉ kiểm phân hệ có trong Lite, không có ❌. Chạy: pytest -q"""
import subprocess
import sys
from pathlib import Path

from app.core import recon
from app.core.periods import make

P = make(2026, 9)


def test_recon_lite_no_fail():
    checks = recon.layer_table() + recon.layer_dashboard(P, "region") + recon.layer_semantic(P)
    assert recon.worst(checks) != recon.FAIL
    assert [c.name for c in checks if c.layer == "5. Dashboard"] == [f"sales – {P.label}"]


def test_crosscheck_cli_exit_code():
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "tools" / "crosscheck.py"), "--scope", "total", "--show", "0"],
                       capture_output=True, text=True, encoding="utf-8", cwd=root, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ĐỐI SOÁT" in r.stdout and "❌" not in r.stdout
