"""The sandbox (player and logo decoder) exposes nothing from the user's session.

Runs a probe inside exactly the sandbox hertz-ctl builds (`sandbox_command()`)
and checks, against what really exists on this machine, that the probe can't:
connect to any Unix socket or named pipe from the session or system (Docker,
D-Bus, systemd, Hyprland, Wayland, X11, agents, PipeWire...), see the home
directory or any file in it, read the parent's environment, reach the network,
hold capabilities, create user namespaces, or use syscalls the seccomp filter
blocks. A control run of the same probe without the sandbox shows it would
find those sockets, so an empty result is meaningful.

Run from the repository root:  python3 -m unittest tests.test_sandbox_escape -v
"""

import glob
import json
import os
import shutil
import subprocess
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = SourceFileLoader("hertz_ctl", os.path.join(ROOT, "hertz-ctl"))
_spec = spec_from_loader("hertz_ctl", _loader)
hz = module_from_spec(_spec)
_loader.exec_module(hz)

PROBE = r'''
import ctypes, errno, json, os, socket, stat, sys
targets = json.loads(sys.argv[1])
out = {"reachable": [], "visible": [], "env": sorted(os.environ), "errors": {}}
for path in targets["sockets"]:
    try:
        st = os.stat(path)
    except OSError:
        continue
    out["visible"].append(path)
    if stat.S_ISSOCK(st.st_mode):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(1); s.connect(path); s.close()
            out["reachable"].append(path)
        except OSError:
            pass
    elif stat.S_ISFIFO(st.st_mode):
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK)); out["reachable"].append(path)
        except OSError:
            pass
for path in targets["files"]:
    if os.path.exists(path):
        out["visible"].append(path)
try:
    socket.create_connection(("1.1.1.1", 443), timeout=1); out["reachable"].append("internet")
except OSError:
    pass
status = open("/proc/self/status").read()
out["caps"] = {l.split(":")[0]: l.split()[1] for l in status.splitlines() if l.startswith("Cap")}
libc = ctypes.CDLL(None, use_errno=True)
def call(nr, *args):
    r = libc.syscall(nr, *args)
    return 0 if r >= 0 else ctypes.get_errno()
out["errors"]["unshare_user"] = call(272, 0x10000000)       # CLONE_NEWUSER
out["errors"]["unshare_ns"] = call(272, 0x00020000)         # CLONE_NEWNS
out["errors"]["io_uring_setup"] = call(425, 1, 0)
out["errors"]["keyctl"] = call(250, 0)
out["errors"]["bpf"] = call(321, 0, 0, 0)
out["errors"]["ptrace"] = call(101, 0, 1, 0, 0)
out["errors"]["mount"] = call(165, 0, 0, 0, 0, 0)
out["errors"]["userfaultfd"] = call(323, 0)
print(json.dumps(out))
'''


def targets():
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    sockets = set()
    for pattern in (f"{runtime}/*", f"{runtime}/*/*", f"{runtime}/*/*/*", "/tmp/.X11-unix/*",
                    "/run/*.sock", "/run/*/*.sock", "/run/dbus/*", "/var/run/*.sock"):
        for path in glob.glob(pattern):
            try:
                mode = os.stat(path).st_mode
            except OSError:
                continue
            if stat_is_ipc(mode):
                sockets.add(path)
    home = os.path.expanduser("~")
    files = [home, os.path.join(home, ".bash_history"), os.path.join(home, ".config"),
             os.path.join(home, ".ssh"), os.path.join(home, ".local/share/keyrings"), ROOT]
    return {"sockets": sorted(sockets), "files": files}


def stat_is_ipc(mode):
    import stat
    return stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode)


@unittest.skipUnless(shutil.which("bwrap"), "needs bubblewrap")
class SandboxEscapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = targets()
        cmd, fds = hz.sandbox_command()
        try:
            result = subprocess.run([*cmd, "--", "python3", "-c", PROBE, json.dumps(cls.targets)],
                                    capture_output=True, text=True, timeout=60, pass_fds=fds)
        finally:
            for fd in fds:
                os.close(fd)
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        cls.inside = json.loads(result.stdout)
        control = subprocess.run(["python3", "-c", PROBE, json.dumps(cls.targets)],
                                 capture_output=True, text=True, timeout=60)
        cls.outside = json.loads(control.stdout)

    def test_control_probe_finds_session_sockets_outside(self):
        self.assertTrue(self.targets["sockets"], "no session sockets found on this machine to test")
        self.assertTrue(self.outside["reachable"], "the probe must detect reachable sockets without the sandbox")

    def test_no_session_or_system_socket_is_reachable(self):
        self.assertEqual(self.inside["reachable"], [])

    def test_no_session_socket_or_personal_file_is_even_visible(self):
        self.assertEqual(self.inside["visible"], [])

    def test_environment_is_cleared(self):
        self.assertEqual(set(self.inside["env"]) - {"PATH", "HOME", "LANG", "PWD", "LC_CTYPE"}, set())

    def test_no_capabilities(self):
        for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
            self.assertEqual(int(self.inside["caps"][key], 16), 0, key)

    def test_namespace_and_kernel_interfaces_blocked(self):
        errors = self.inside["errors"]
        # nested user namespaces are disabled by bubblewrap (ENOSPC) or refused by seccomp
        self.assertIn(errors["unshare_user"], (errno_value("EPERM"), errno_value("ENOSPC")))
        for name in ("unshare_ns", "io_uring_setup", "keyctl", "bpf", "ptrace", "mount", "userfaultfd"):
            self.assertEqual(errors[name], errno_value("EPERM"), name)


def errno_value(name):
    import errno
    return getattr(errno, name)


if __name__ == "__main__":
    unittest.main()
