"""Output from the sandboxed logo decoder is bounded while it is read.

ffmpeg decodes untrusted images, so a compromised decoder is assumed to
flood stdout, flood stderr or stall. `run_bounded()` holds at most the limit
of stdout (one byte more is overflow), drains stderr without keeping it,
applies one deadline to the whole run and kills the sandbox on overflow or
timeout, which ends every process inside it.

Run from the repository root:  python3 -m unittest tests.test_bounded_output -v
"""

import os
import random
import shutil
import subprocess
import sys
import time
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = SourceFileLoader("hertz_bounded", os.path.join(ROOT, "hertz-ctl"))
hz = module_from_spec(spec_from_loader("hertz_bounded", _loader))
_loader.exec_module(hz)

LIMIT = 256 * 1024


def leftovers(seconds):
    """PIDs whose exact argv is ["sleep", seconds] (a per-test random value)."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")[:-1]
        except OSError:
            continue
        if argv == [b"sleep", seconds.encode()]:
            found.append(pid)
    return found


def marker():
    return str(random.randint(100000, 999999))


class BoundedRunTest(unittest.TestCase):
    def test_normal_output_is_returned(self):
        self.assertEqual(hz.run_bounded(["cat"], b"hello", LIMIT, 5), (0, b"hello"))

    def test_exactly_the_limit_is_accepted_one_more_byte_is_not(self):
        exact = [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {LIMIT})"]
        over = [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x' * {LIMIT + 1})"]
        self.assertEqual(len(hz.run_bounded(exact, b"", LIMIT, 5)[1]), LIMIT)
        self.assertIsNone(hz.run_bounded(over, b"", LIMIT, 5))


@unittest.skipUnless(shutil.which("bwrap"), "needs bubblewrap")
class SandboxedFloodTest(unittest.TestCase):
    def sandboxed(self, script, timeout=3):
        cmd, fds = hz.sandbox_command()
        try:
            start = time.monotonic()
            result = hz.run_bounded([*cmd, "--", "sh", "-c", script], b"", LIMIT, timeout, pass_fds=fds)
            return result, time.monotonic() - start
        finally:
            for fd in fds:
                os.close(fd)

    def test_stdout_flood_is_cut_at_the_limit(self):
        stall = marker()
        result, elapsed = self.sandboxed(f"sleep {stall} & yes")
        self.assertIsNone(result)
        self.assertLess(elapsed, 2.5)
        time.sleep(0.3)
        self.assertEqual(leftovers(stall), [])

    def test_stderr_flood_and_stall_end_at_the_deadline(self):
        stall = marker()
        result, elapsed = self.sandboxed(f"sleep {stall} & exec yes >&2")
        self.assertIsNone(result)
        self.assertLess(elapsed, 5)
        time.sleep(0.3)
        self.assertEqual(leftovers(stall), [])

    def test_leftover_check_can_see_a_surviving_process(self):
        # Control: an unsandboxed background sleep outlives its shell, and the
        # check above must be able to see it.
        stall = marker()
        subprocess.run(["sh", "-c", f"sleep {stall} >/dev/null 2>&1 &"], timeout=5)
        try:
            self.assertNotEqual(leftovers(stall), [])
        finally:
            for pid in leftovers(stall):
                os.kill(int(pid), 9)

    def test_memory_stays_bounded_while_flooded(self):
        probe = (
            "import os, resource, sys\n"
            "from importlib.machinery import SourceFileLoader\n"
            f"hz = SourceFileLoader('h', {os.path.join(ROOT, 'hertz-ctl')!r}).load_module()\n"
            "cmd, fds = hz.sandbox_command()\n"
            "hz.run_bounded([*cmd, '--', 'sh', '-c', 'yes >&2 & yes'], b'', 256 * 1024, 3, pass_fds=fds)\n"
            "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)\n")
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=30)
        self.assertLess(int(out.stdout.strip().splitlines()[-1]), 100)   # MB


class TranscoderUsesBoundedRunTest(unittest.TestCase):
    def test_logo_decoder_output_goes_through_run_bounded(self):
        calls = []

        def fake(cmd, data, limit, timeout, **kwargs):
            calls.append((limit, timeout))
            return None
        with patch.object(hz, "run_bounded", side_effect=fake), \
             patch.object(hz.shutil, "which", return_value="/usr/bin/true"):
            self.assertIsNone(hz.transcode_logo(b"\x89PNG\r\n\x1a\n" + b"0" * 64))
        self.assertEqual(calls, [(hz.LOGO_OUTPUT_LIMIT, hz.LOGO_TIMEOUT)])


if __name__ == "__main__":
    unittest.main()
