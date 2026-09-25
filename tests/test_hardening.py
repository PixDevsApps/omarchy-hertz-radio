"""Logo transcoding, record validation and local robustness.

Run from the repository root:  python3 -m unittest tests.test_hardening -v
"""

import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import zlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = SourceFileLoader("hertz_ctl", os.path.join(ROOT, "hertz-ctl"))
_spec = spec_from_loader("hertz_ctl", _loader)
hz = module_from_spec(_spec)
_loader.exec_module(hz)


def ffmpeg_image(path, size, extra=()):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}",
                    "-frames:v", "1", *extra, path], check=True)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("bwrap"), "needs ffmpeg and bubblewrap")
class LogoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        ffmpeg_image(os.path.join(cls.dir, "ok.png"), "300x200")
        for ext in ("jpg", "gif", "webp", "bmp"):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-i", os.path.join(cls.dir, "ok.png"),
                            os.path.join(cls.dir, f"ok.{ext}")], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", os.path.join(cls.dir, "ok.png"),
                        "-vf", "scale=64:64", os.path.join(cls.dir, "ok.ico")], check=True)
        for ext in ("png", "gif"):   # decompression bombs: tiny files, huge pictures
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=8000x8000",
                            "-frames:v", "1", os.path.join(cls.dir, f"bomb.{ext}")], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def read(self, name):
        with open(os.path.join(self.dir, name), "rb") as f:
            return f.read()

    def test_every_accepted_format_becomes_a_small_png(self):
        for name in ("ok.png", "ok.jpg", "ok.gif", "ok.webp", "ok.bmp", "ok.ico"):
            with self.subTest(name):
                png = hz.transcode_logo(self.read(name))
                self.assertIsNotNone(png)
                self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                w, h = struct.unpack(">II", png[16:24])
                self.assertLessEqual(max(w, h), hz.LOGO_SIZE)

    def test_decompression_bombs_are_refused(self):
        for name in ("bomb.png", "bomb.gif"):
            with self.subTest(name):
                data = self.read(name)
                self.assertLess(len(data), 400_000)
                start = time.monotonic()
                self.assertIsNone(hz.transcode_logo(data))
                self.assertLess(time.monotonic() - start, 5)

    def test_corrupt_and_unsupported_inputs_are_refused(self):
        self.assertIsNone(hz.transcode_logo(self.read("ok.png")[:3000]))
        self.assertIsNone(hz.Artwork.sniff(b'<svg xmlns="http://www.w3.org/2000/svg"/>'))
        self.assertIsNone(hz.transcode_logo(b"\x89PNG\r\n\x1a\n" + os.urandom(2000)))

    def test_downloaded_logo_is_stored_only_as_transcoded_png(self):
        served = self.read("ok.jpg")

        class Handler(__import__("http.server").server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(served)

            def log_message(self, *a):
                pass
        import http.server
        server = http.server.ThreadingHTTPServer(("127.0.0.2", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cache = tempfile.mkdtemp()
        saved = (hz.public_ip, hz.routes_to_this_machine, hz.ART_OPENER, hz.ART_DIR)
        hz.public_ip = lambda a: a == "127.0.0.2" or saved[0](a)
        hz.routes_to_this_machine = lambda a: a != "127.0.0.2" and saved[1](a)
        hz.ART_OPENER, hz.ART_DIR = hz.guarded_opener(), cache
        try:
            art = hz.Artwork.__new__(hz.Artwork)
            path = art.fetch("00000000-0000-0000-0000-00000000abcd",
                             f"http://127.0.0.2:{server.server_address[1]}/logo.jpg")
            self.assertTrue(path and path.endswith(".png"))
            with open(path, "rb") as f:
                stored = f.read()
            self.assertNotEqual(stored, served)
            self.assertTrue(stored.startswith(b"\x89PNG"))
            self.assertEqual([n for n in os.listdir(cache) if not n.endswith(".png")], [])
        finally:
            hz.public_ip, hz.routes_to_this_machine, hz.ART_OPENER, hz.ART_DIR = saved
            server.shutdown()
            server.server_close()
            shutil.rmtree(cache)


class RecordValidationTest(unittest.TestCase):
    GOOD = {"uuid": "9617a958-0601-11e8-ae97-52543be04c81", "name": "Radio Paradise",
            "url": "http://stream-uk1.radioparadise.com/aac-320"}

    def test_valid_record_passes(self):
        self.assertEqual(hz.clean_station(self.GOOD)["uuid"], self.GOOD["uuid"])

    def test_hostile_records_are_dropped(self):
        for bad in ({"name": "no uuid", "url": "http://x.example/"},
                    dict(self.GOOD, uuid="9617a958\nfav 1"),
                    dict(self.GOOD, uuid="../../etc"),
                    dict(self.GOOD, url="file:///etc/passwd"),
                    dict(self.GOOD, url="tcp://127.0.0.1:22"),
                    dict(self.GOOD, url="http://x.example/" + "a" * 3000),
                    "not a dict", None, 42):
            with self.subTest(repr(bad)[:40]):
                self.assertIsNone(hz.clean_station(bad))

    def test_text_fields_are_single_line_and_bounded(self):
        s = hz.clean_station(dict(self.GOOD, name="A\nB\x00C‮" + "x" * 500,
                                  favicon="javascript:alert(1)", tags=["jazz\n", 5, "x" * 99],
                                  countrycode="../", bitrate="9999999"))
        self.assertNotIn("\n", s["name"])
        self.assertTrue(all(c.isprintable() for c in s["name"]))
        self.assertLessEqual(len(s["name"]), 120)
        self.assertEqual(s["favicon"], "")
        self.assertEqual(s["countrycode"], "")
        self.assertLessEqual(s["bitrate"], 4000)
        self.assertTrue(all(isinstance(t, str) and "\n" not in t for t in s["tags"]))

    def test_corrupt_session_file_does_not_crash_the_daemon(self):
        runtime, data = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(runtime), exist_ok=True)
            with open(os.path.join(runtime, "session.json"), "w") as f:
                json.dump({"station": {"name": "evil"}, "queue": [{"uuid": "x\ny"}, 7], "mode": "playing"}, f)
            env = dict(os.environ, HERTZ_RADIO_RUNTIME=runtime, XDG_DATA_HOME=data, XDG_CACHE_HOME=data)
            out = subprocess.run(["python3", os.path.join(ROOT, "hertz-ctl"), "daemon"], input="hello\n",
                                 capture_output=True, text=True, timeout=20, env=env)
            self.assertNotIn("Traceback", out.stderr)
            states = [json.loads(l) for l in out.stdout.splitlines() if '"type":"state"' in l]
            self.assertTrue(states)
            self.assertIsNone(states[-1]["station"])
        finally:
            shutil.rmtree(runtime)
            shutil.rmtree(data)


class HostileMpvTest(unittest.TestCase):
    """The daemon treats whatever answers on the player socket as hostile."""

    def test_daemon_survives_and_cleans_hostile_player_messages(self):
        runtime, data = tempfile.mkdtemp(dir=f"/run/user/{os.getuid()}"), tempfile.mkdtemp()
        os.chmod(runtime, 0o700)
        sock_path = os.path.join(runtime, "mpv.sock")
        server = socket.socket(socket.AF_UNIX)
        server.bind(sock_path)
        server.listen(1)
        nested = "[" * 100000 + "]" * 100000
        evil = [
            {"event": "property-change", "name": "metadata", "data": {"icy-title": "A\ud800B \x1b]52;c;ZXZpbA==\x07 \u202eevil"}},
            {"event": "property-change", "name": "metadata", "data": ["not", "a", "dict"]},
            {"event": "property-change", "name": "volume", "data": "loud"},
            {"event": "property-change", "name": "audio-codec-name", "data": "x" * 2_000_000},
            {"event": "property-change", "name": "audio-codec-name", "data": "\x1b[31mAAC"},
            {"event": "property-change", "name": "pause", "data": "yes"},
            {"event": "property-change", "name": "audio-params/samplerate", "data": [1]},
        ]

        def fake_mpv():
            conn, _ = server.accept()
            conn.recv(65536)
            for msg in evil:
                conn.sendall((json.dumps(msg) + "\n").encode())
            conn.sendall(b'{"event":"property-change","name":"volume","data":Infinity}\n')
            conn.sendall(nested.encode() + b"\n")
            conn.sendall(b'{"event":"property-change","name":"metadata","data":{"icy-title":"Artist - Song"}}\n')
            time.sleep(1.5)
            conn.close()
        threading.Thread(target=fake_mpv, daemon=True).start()
        try:
            with open(os.path.join(runtime, "session.json"), "w") as f:
                json.dump({"station": {"uuid": "9617a958-0601-11e8-ae97-52543be04c81", "name": "RP",
                                       "url": "http://stream-uk1.radioparadise.com/aac-320"},
                           "mode": "playing"}, f)
            env = dict(os.environ, HERTZ_RADIO_RUNTIME=runtime, XDG_DATA_HOME=data, XDG_CACHE_HOME=data)
            proc = subprocess.Popen(["python3", os.path.join(ROOT, "hertz-ctl"), "daemon"], env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(4)
            proc.stdin.close()
            out, err = proc.communicate(timeout=20)
            self.assertNotIn(b"Traceback", err)
            states = [json.loads(l) for l in out.splitlines() if b'"type":"state"' in l]
            self.assertTrue(states, "daemon produced no state")
            for st in states:
                for field in ("title", "codec"):
                    value = st[field]
                    self.assertTrue(all(c.isprintable() for c in value), (field, value[:40]))
                    self.assertLessEqual(len(value), 200)
                self.assertIsInstance(st["volume"], int)
            self.assertIn("Artist - Song", [st["title"] for st in states])
        finally:
            server.close()
            shutil.rmtree(runtime, ignore_errors=True)
            shutil.rmtree(data, ignore_errors=True)


class PlayerKillTest(unittest.TestCase):
    def test_player_that_ignores_quit_and_sigterm_is_killed(self):
        runtime = tempfile.mkdtemp()
        saved = hz.PID_FILE
        hz.PID_FILE = os.path.join(runtime, "player.pid")
        stubborn = subprocess.Popen(["python3", "-c", "import signal,time\n"
                                     "signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)"],
                                    start_new_session=True)
        try:
            time.sleep(0.3)
            hz.write_json(hz.PID_FILE, {"pid": stubborn.pid, "start": hz.process_start(stubborn.pid)})
            hz.Mpv.kill_player(grace=0.5)
            self.assertEqual(stubborn.wait(timeout=5), -signal.SIGKILL)
            self.assertFalse(os.path.exists(hz.PID_FILE))
        finally:
            hz.PID_FILE = saved
            if stubborn.poll() is None:
                stubborn.kill()
            shutil.rmtree(runtime)

    def test_recycled_pid_is_left_alone(self):
        runtime = tempfile.mkdtemp()
        saved = hz.PID_FILE
        hz.PID_FILE = os.path.join(runtime, "player.pid")
        other = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            hz.write_json(hz.PID_FILE, {"pid": other.pid, "start": "1"})   # wrong start time
            hz.Mpv.kill_player(grace=0)
            self.assertIsNone(other.poll())
        finally:
            hz.PID_FILE = saved
            other.kill()
            shutil.rmtree(runtime)


class PngRebuildTest(unittest.TestCase):
    def png(self, w, h, color=6, depth=8, chunks=(), raw=None, crc_ok=True):
        rowlen = 1 + w * (4 if color == 6 else 1)
        raw = raw if raw is not None else b"".join(b"\x00" + b"\x7f" * (rowlen - 1) for _ in range(h))
        ihdr = struct.pack(">IIBBBBB", w, h, depth, color, 0, 0, 0)
        out = b"\x89PNG\r\n\x1a\n" + hz._png_chunk(b"IHDR", ihdr)
        for kind, body in chunks:
            out += hz._png_chunk(kind, body)
        idat = hz._png_chunk(b"IDAT", zlib.compress(raw))
        if not crc_ok:
            idat = idat[:-1] + bytes([idat[-1] ^ 1])
        return out + idat + hz._png_chunk(b"IEND", b"")

    def test_valid_rgba_is_rebuilt_not_passed_through(self):
        src = self.png(10, 5, chunks=[(b"tEXt", b"Comment\x00hello")])
        out = hz.rebuild_png(src)
        self.assertIsNotNone(out)
        self.assertNotEqual(out, src)
        self.assertNotIn(b"tEXt", out)

    def test_refusals(self):
        import zlib as z
        self.assertIsNone(hz.rebuild_png(self.png(200, 5)))                  # too large
        self.assertIsNone(hz.rebuild_png(self.png(10, 5, color=3, chunks=[(b"PLTE", b"\0" * 3)])))
        self.assertIsNone(hz.rebuild_png(self.png(10, 5, crc_ok=False)))
        self.assertIsNone(hz.rebuild_png(self.png(10, 5, raw=b"\x09" * (41 * 5))))   # bad filter byte
        bomb = b"\x89PNG\r\n\x1a\n" + hz._png_chunk(b"IHDR", struct.pack(">IIBBBBB", 10, 5, 8, 6, 0, 0, 0)) \
            + hz._png_chunk(b"IDAT", z.compress(b"\0" * 50_000_000)) + hz._png_chunk(b"IEND", b"")
        start = time.monotonic()
        self.assertIsNone(hz.rebuild_png(bomb))
        self.assertLess(time.monotonic() - start, 1)
        self.assertIsNone(hz.rebuild_png(b"\x89PNG\r\n\x1a\n\0\0"))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("bwrap"), "needs ffmpeg and bubblewrap")
    def test_transcoder_output_is_our_rebuilt_png(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "x.jpg")
            ffmpeg_image(path, "200x120")
            with open(path, "rb") as f:
                png = hz.transcode_logo(f.read())
            self.assertIsNotNone(png)
            self.assertEqual(png, hz.rebuild_png(png), "output must be rebuild_png's canonical form")
        finally:
            shutil.rmtree(d)

    def test_ffmpeg_is_told_the_input_format(self):
        seen = []
        real_run = hz.subprocess.run

        def capture(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, b"", b"")
        hz.subprocess.run = capture
        try:
            samples = {".png": b"\x89PNG\r\n\x1a\n" + b"0" * 32, ".jpg": b"\xff\xd8\xff" + b"0" * 32,
                       ".gif": b"GIF89a" + b"0" * 32, ".webp": b"RIFF0000WEBP" + b"0" * 32,
                       ".bmp": b"BM" + b"0" * 32, ".ico": b"\x00\x00\x01\x00" + b"0" * 32}
            for ext, data in samples.items():
                seen.clear()
                hz.transcode_logo(data)
                cmd = seen[0]
                self.assertEqual(cmd[cmd.index("-i") - 1], hz.LOGO_DEMUXERS[ext], ext)
                self.assertEqual(cmd[cmd.index("-i") - 2], "-f", ext)
        finally:
            hz.subprocess.run = real_run

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("bwrap"), "needs ffmpeg and bubblewrap")
    def test_decoder_is_pinned_to_the_sniffed_format(self):
        # PNG magic followed by a JPEG: ffmpeg must not be allowed to probe its way to it.
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "x.jpg")
            ffmpeg_image(path, "64x64")
            with open(path, "rb") as f:
                jpeg = f.read()
            self.assertIsNotNone(hz.transcode_logo(jpeg))
            self.assertIsNone(hz.transcode_logo(b"\x89PNG\r\n\x1a\n" + jpeg))
        finally:
            shutil.rmtree(d)


class RobustnessTest(unittest.TestCase):
    def test_overlong_mpv_lines_are_dropped(self):
        a, b = socket.socketpair()
        got = []
        m = hz.Mpv(got.append)
        t = threading.Thread(target=m._reader, args=(a,), daemon=True)
        t.start()
        b.sendall(b'{"event":"x","data":"' + b"a" * (hz.MAX_IPC_LINE + 10) + b'"}\n{"event":"ok"}\n')
        b.close()
        t.join(10)
        a.close()
        self.assertEqual([e.get("event") for e in got], ["ok", "hertz-disconnected"])

    def test_json_writes_do_not_follow_planted_symlinks(self):
        d = tempfile.mkdtemp()
        try:
            victim = os.path.join(d, "victim")
            with open(victim, "w") as f:
                f.write("keep")
            target = os.path.join(d, "state.json")
            # the old code wrote through a predictable "<path>.<pid>.tmp"
            os.symlink(victim, f"{target}.{os.getpid()}.tmp")
            hz.write_json(target, {"ok": True})
            with open(victim) as f:
                self.assertEqual(f.read(), "keep")
            with open(target) as f:
                self.assertEqual(json.load(f), {"ok": True})
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
