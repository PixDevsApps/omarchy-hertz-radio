"""End-to-end test: the sandboxed player cannot reach local or private addresses.

Starts the real `hertz-ctl player` session (guarded proxy + mpv in bubblewrap
with its own network namespace), then feeds mpv hostile URLs over its IPC
socket, as a malicious directory entry could: direct loopback and LAN
addresses, a public redirect to loopback, an .m3u playlist and an HLS playlist
whose entries point at loopback, and raw tcp://. A local HTTP server listening
on all interfaces must receive no request at all. A control run with plain
(unsandboxed) mpv shows the same server *is* reachable, so the test can detect
a leak. A public stream must still play.

Needs mpv, bubblewrap, internet access and httpbin.org; skipped otherwise.
Run from the repository root:  python3 -m unittest tests.test_player_sandbox -v
"""

import base64
import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTL = os.path.join(ROOT, "hertz-ctl")
PUBLIC_STREAM = "http://stream-uk1.radioparadise.com/aac-320"


def reachable(url):
    try:
        urllib.request.urlopen(url, timeout=5).close()
        return True
    except Exception:
        return False


def lan_address():
    try:
        out = subprocess.run(["ip", "-j", "route", "get", "1.1.1.1"], capture_output=True, text=True)
        return json.loads(out.stdout)[0].get("prefsrc")
    except Exception:
        return None


class Recorder(http.server.BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        Recorder.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.end_headers()

    def log_message(self, *args):
        pass


def ipc(sock_path, *command):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(3)
        s.connect(sock_path)
        s.sendall((json.dumps({"command": list(command)}) + "\n").encode())
        data = b""
        while b"\n" not in data:
            data += s.recv(65536)
    return json.loads(data.split(b"\n")[0])


@unittest.skipUnless(shutil.which("mpv") and shutil.which("bwrap"), "needs mpv and bubblewrap")
@unittest.skipUnless(reachable("https://httpbin.org/status/200"), "needs internet access and httpbin.org")
class PlayerSandboxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Recorder.hits = []
        cls.server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Recorder)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        # Short runtime path: Unix socket paths are limited to 108 bytes.
        cls.runtime = tempfile.mkdtemp(prefix="hzt-", dir=f"/run/user/{os.getuid()}")
        cls.data = tempfile.mkdtemp(prefix="hertz-radio-test-")
        env = dict(os.environ, HERTZ_RADIO_RUNTIME=cls.runtime, XDG_DATA_HOME=cls.data,
                   XDG_CACHE_HOME=cls.data)
        cls.player = subprocess.Popen(["python3", CTL, "player", "0"], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.mpv_sock = os.path.join(cls.runtime, "mpv.sock")
        for _ in range(200):
            if os.path.exists(cls.mpv_sock):
                break
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        try:
            ipc(cls.mpv_sock, "quit")
        except OSError:
            pass
        try:
            cls.player.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.player.kill()
        cls.server.shutdown()
        shutil.rmtree(cls.runtime, ignore_errors=True)
        shutil.rmtree(cls.data, ignore_errors=True)

    def load(self, url, wait=6):
        ipc(self.mpv_sock, "loadfile", url, "replace")
        time.sleep(wait)

    def hostile_urls(self):
        p = self.port
        local = f"http://127.0.0.1:{p}"
        m3u = base64.urlsafe_b64encode(f"#EXTM3U\n{local}/m3u-entry\n".encode()).decode()
        hls = base64.urlsafe_b64encode(
            f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:1\n"
            f"#EXTINF:10,\n{local}/hls-segment.ts\n".encode()).decode()
        urls = {
            "direct loopback": f"{local}/direct",
            "localhost name": f"http://localhost:{p}/localhost",
            "redirect to loopback": "https://httpbin.org/redirect-to?url="
                                    + urllib.parse.quote(f"{local}/redirect", safe=""),
            "m3u entry": f"https://httpbin.org/base64/{m3u}",
            "hls segment": f"https://httpbin.org/base64/{hls}",
            "raw tcp": f"tcp://127.0.0.1:{p}/tcp",
        }
        lan = lan_address()
        if lan:
            urls["own LAN address"] = f"http://{lan}:{p}/lan"
        return urls

    def test_1_control_unsandboxed_mpv_reaches_the_server(self):
        before = len(Recorder.hits)
        subprocess.run(["mpv", "--no-config", "--ao=null", "--no-video", "--really-quiet",
                        "--length=1", f"http://127.0.0.1:{self.port}/control"],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        self.assertGreater(len(Recorder.hits), before, "harness must detect a request")
        Recorder.hits.clear()

    def test_2_every_hostile_url_is_a_working_attack_without_the_sandbox(self):
        # Each URL must reach the local server from plain mpv; otherwise a
        # "no hits" result below would prove nothing. (raw tcp:// opens a bare
        # connection without an HTTP request, so it is checked as a connection.)
        for name, url in self.hostile_urls().items():
            if url.startswith("tcp://"):
                continue
            with self.subTest(name):
                Recorder.hits.clear()
                subprocess.run(["mpv", "--no-config", "--ao=null", "--no-video", "--really-quiet",
                                "--length=1", "--load-unsafe-playlists=no", url],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
                self.assertTrue(Recorder.hits, f"{name}: not a working attack, test would be vacuous")
        Recorder.hits.clear()

    def test_3_hostile_urls_never_reach_local_services(self):
        self.assertTrue(os.path.exists(self.mpv_sock), "sandboxed player did not start")
        for name, url in self.hostile_urls().items():
            with self.subTest(name):
                self.load(url)
                self.assertEqual(Recorder.hits, [], f"{name} leaked a request: {Recorder.hits}")

    def test_4_public_stream_still_plays(self):
        self.load(PUBLIC_STREAM, wait=8)
        self.assertFalse(ipc(self.mpv_sock, "get_property", "core-idle")["data"])
        self.assertEqual(ipc(self.mpv_sock, "get_property", "path")["data"], PUBLIC_STREAM)


if __name__ == "__main__":
    unittest.main()
