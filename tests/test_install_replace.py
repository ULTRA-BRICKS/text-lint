"""install.sh replaces a 0.1 install (distribution hyper-slop-check) with hyper-text-lint.

The real installer runs from this checkout against a fake pipx or uv that keeps its
"installed" set in its own state dir. PATH holds only the fake, the bin dir and links to the few
system tools the installer uses, so a real pipx or uv on the machine is never reached. The old
install must survive a failed replacement; a successful one removes it without losing the
`slop-check` shim the new install provides.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_BASH = shutil.which("bash")
SYSTEM_TOOLS = ("uname", "dirname", "mkdir", "grep", "sh", "python3")

# One fake for both managers: `pipx list --short` / `uv tool list`, `install --force
# --editable SRC`, `uninstall NAME`. Every call is logged. Entry points go to FAKE_BIN_DIR
# (default ~/.local/bin, the installer's own guess). FAKE_INSTALL_FAIL_ON=N fails the Nth
# install (N counts from 1, this call included); with FAKE_FAILED_INSTALL_DROPS=1 that failed
# `--force` install also drops the tool it was replacing, as a venv rebuilt and then aborted
# would. FAKE_INSTALL_NOOP_ON=N lets the Nth install succeed without writing anything;
# FAKE_UNINSTALL_FAIL=1 deletes the `slop-check` entry point, then fails. An uninstall takes
# `slop-check` from the bin dir whoever owns it (the worst case).
FAKE_MANAGER = r"""#!/usr/bin/env python3
import os, sys
from pathlib import Path

manager = Path(sys.argv[0]).name
args = sys.argv[1:]
if manager == "uv" and args[:1] == ["tool"]:
    args = args[1:]
state = Path(os.environ["FAKE_STATE"]) / manager
state.mkdir(parents=True, exist_ok=True)
bin_dir = Path(os.environ.get("FAKE_BIN_DIR") or Path(os.environ["HOME"]) / ".local" / "bin")
log_path = Path(os.environ["FAKE_LOG"])
with log_path.open("a", encoding="utf-8") as log:
    log.write(" ".join([manager, *args]) + "\n")

def script(path, owner):
    path.write_text(f"#!/bin/sh\n# owner: {owner}\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)

if args[:1] == ["list"]:
    for dist in sorted(p.name for p in state.iterdir()):
        print(f"{dist} 0.1.0" if manager == "pipx" else f"{dist} v0.1.0\n- slop-check")
elif args[:1] == ["install"]:
    installs = sum(1 for line in log_path.read_text(encoding="utf-8").splitlines() if " install " in f" {line} ")
    if os.environ.get("FAKE_INSTALL_FAIL_ON") == str(installs):
        if os.environ.get("FAKE_FAILED_INSTALL_DROPS") == "1":
            (state / "hyper-text-lint").unlink(missing_ok=True)
            for name in ("text-lint", "slop-check"):
                (bin_dir / name).unlink(missing_ok=True)
        print("fake: build failed", file=sys.stderr)
        sys.exit(1)
    if os.environ.get("FAKE_INSTALL_NOOP_ON") == str(installs):
        sys.exit(0)
    (state / "hyper-text-lint").touch()
    bin_dir.mkdir(parents=True, exist_ok=True)
    script(bin_dir / "text-lint", "hyper-text-lint")
    script(bin_dir / "slop-check", "hyper-text-lint")
elif args[:1] == ["uninstall"]:
    if os.environ.get("FAKE_UNINSTALL_KEEP_SHIM") != "1":
        (bin_dir / "slop-check").unlink(missing_ok=True)
    if os.environ.get("FAKE_UNINSTALL_FAIL") == "1":
        print("fake: permission denied", file=sys.stderr)
        sys.exit(1)
    (state / args[1]).unlink()
else:
    sys.exit(f"fake {manager}: unexpected {args}")
"""


@pytest.fixture
def machine(tmp_path):
    home, fakes, system, state = (tmp_path / name for name in ("home", "fakes", "system", "state"))
    bin_dir = home / ".local" / "bin"
    for directory in (bin_dir, fakes, system, state):
        directory.mkdir(parents=True)
    for tool in SYSTEM_TOOLS:
        if shutil.which(tool) is None:
            pytest.skip(f"needs {tool}")
        (system / tool).symlink_to(shutil.which(tool))
    return {"home": home, "bin": bin_dir, "fakes": fakes, "system": system, "state": state,
            "log": tmp_path / "calls.log"}


def with_0_1_installed(machine, manager, bin_dir=None):
    (machine["state"] / manager).mkdir(exist_ok=True)
    (machine["state"] / manager / "hyper-slop-check").touch()
    shim = (bin_dir or machine["bin"]) / "slop-check"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("#!/bin/sh\n# owner: hyper-slop-check\nexit 0\n", encoding="utf-8")
    shim.chmod(0o755)


def run_install(machine, manager, path_extra=(), also=(), **fake_env):
    """Run install.sh with the fake ``manager`` (and the fakes in ``also``) on PATH."""
    for name in (manager, *also):
        fake = machine["fakes"] / name
        fake.write_text(FAKE_MANAGER, encoding="utf-8")
        fake.chmod(0o755)
    path = [str(machine["fakes"]), *map(str, path_extra), str(machine["bin"]), str(machine["system"])]
    env = {
        "HOME": str(machine["home"]),
        "PATH": os.pathsep.join(path),
        "FAKE_STATE": str(machine["state"]),
        "FAKE_LOG": str(machine["log"]),
        **fake_env,
    }
    return subprocess.run([_BASH, str(ROOT / "install.sh")], env=env, capture_output=True,
                          text=True, timeout=60, check=False)


def installed(machine):
    """Every distribution any fake manager holds, as "manager:name"."""
    return sorted(f"{p.parent.name}:{p.name}" for p in machine["state"].glob("*/*"))


def calls(machine):
    return machine["log"].read_text(encoding="utf-8").splitlines()


def install_calls(machine):
    return [c for c in calls(machine) if " install " in f" {c} "]


def owner(path):
    return path.read_text(encoding="utf-8").splitlines()[1].removeprefix("# owner: ")


pytestmark = [
    pytest.mark.skipif(_BASH is None, reason="needs bash"),
    pytest.mark.parametrize("manager", ["pipx", "uv"]),
]


def test_failed_replacement_keeps_the_old_install(machine, manager):
    with_0_1_installed(machine, manager)
    out = run_install(machine, manager, FAKE_INSTALL_FAIL_ON="1")
    assert out.returncode != 0
    assert "text-lint is not installed" in out.stderr and "hyper-slop-check install" in out.stderr
    assert "left in place" in out.stderr
    assert installed(machine) == [f"{manager}:hyper-slop-check"]
    assert owner(machine["bin"] / "slop-check") == "hyper-slop-check"
    assert not [c for c in calls(machine) if "uninstall" in c]


def test_successful_replacement_removes_the_old_install(machine, manager):
    with_0_1_installed(machine, manager)
    out = run_install(machine, manager)
    assert out.returncode == 0, out.stderr
    assert installed(machine) == [f"{manager}:hyper-text-lint"]
    log = calls(machine)
    first_install = log.index(install_calls(machine)[0])
    uninstall = next(i for i, c in enumerate(log) if c.endswith("uninstall hyper-slop-check"))
    assert first_install < uninstall
    # The old uninstall took `slop-check` with it; the installer puts the new shim back.
    assert "restoring the slop-check shim" in out.stdout
    assert owner(machine["bin"] / "slop-check") == "hyper-text-lint"
    assert owner(machine["bin"] / "text-lint") == "hyper-text-lint"


def test_failed_shim_restore_says_what_is_missing(machine, manager):
    with_0_1_installed(machine, manager)
    out = run_install(machine, manager, FAKE_INSTALL_FAIL_ON="2")
    assert out.returncode != 0
    assert "while restoring 'slop-check'" in out.stderr and "re-run this installer" in out.stderr
    assert "left in place" not in out.stderr
    assert installed(machine) == [f"{manager}:hyper-text-lint"]
    assert "text-lint is installed" in out.stderr


def test_failed_restore_that_drops_text_lint_says_so(machine, manager):
    with_0_1_installed(machine, manager)
    out = run_install(machine, manager, FAKE_INSTALL_FAIL_ON="2", FAKE_FAILED_INSTALL_DROPS="1")
    assert out.returncode != 0
    assert "text-lint is installed" not in out.stderr
    assert "'text-lint' and 'slop-check' are both missing" in out.stderr
    assert "re-run this installer" in out.stderr


def test_failed_uninstall_warns_and_keeps_the_shim(machine, manager):
    with_0_1_installed(machine, manager)
    out = run_install(machine, manager, FAKE_UNINSTALL_FAIL="1")
    assert out.returncode == 0, out.stderr
    assert "WARNING" in out.stderr and "uninstall hyper-slop-check' failed" in out.stderr
    assert owner(machine["bin"] / "slop-check") == "hyper-text-lint"


def test_restore_that_writes_no_shim_warns(machine, manager):
    # text-lint itself is installed by then, so the run still succeeds, and says so.
    with_0_1_installed(machine, manager)
    out = run_install(machine, manager, FAKE_INSTALL_NOOP_ON="2")
    assert out.returncode == 0, out.stderr
    assert "WARNING" in out.stderr and "'slop-check' is still missing" in out.stderr


def test_uninstall_that_keeps_the_shim_installs_once(machine, manager):
    with_0_1_installed(machine, manager)
    out = run_install(machine, manager, FAKE_UNINSTALL_KEEP_SHIM="1")
    assert out.returncode == 0, out.stderr
    assert installed(machine) == [f"{manager}:hyper-text-lint"]
    assert len(install_calls(machine)) == 1
    assert "restoring" not in out.stdout


def test_fresh_machine_installs_once(machine, manager, tmp_path):
    # The manager writes entry points to its own bin dir, not the installer's guess.
    own_bin = tmp_path / "manager-bin"
    out = run_install(machine, manager, path_extra=[own_bin], FAKE_BIN_DIR=str(own_bin))
    assert out.returncode == 0, out.stderr
    assert installed(machine) == [f"{manager}:hyper-text-lint"]
    assert len(install_calls(machine)) == 1
    assert not [c for c in calls(machine) if "uninstall" in c]
    assert "restoring" not in out.stdout


def test_replacement_in_the_managers_own_bin_dir(machine, manager, tmp_path):
    own_bin = tmp_path / "manager-bin"
    with_0_1_installed(machine, manager, bin_dir=own_bin)
    out = run_install(machine, manager, path_extra=[own_bin], FAKE_BIN_DIR=str(own_bin))
    assert out.returncode == 0, out.stderr
    assert "still missing" not in out.stderr
    assert installed(machine) == [f"{manager}:hyper-text-lint"]
    assert owner(own_bin / "slop-check") == "hyper-text-lint"


def test_old_install_under_either_manager_when_both_exist(machine, manager):
    # With pipx and uv both present the installer uses pipx; 0.1 may sit under either one.
    with_0_1_installed(machine, manager)
    out = run_install(machine, "pipx", also=["uv"])
    assert out.returncode == 0, out.stderr
    assert installed(machine) == ["pipx:hyper-text-lint"]
    assert f"{manager} uninstall hyper-slop-check" in calls(machine)
    assert owner(machine["bin"] / "slop-check") == "hyper-text-lint"
