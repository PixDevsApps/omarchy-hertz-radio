"""Logo transcoding, record validation and local robustness.

Run from the repository root:  python3 -m unittest tests.test_hardening -v
"""

import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
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
