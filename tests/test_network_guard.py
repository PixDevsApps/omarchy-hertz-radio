"""Network guard tests for hertz-ctl.

Station logo and stream URLs come from a public, community-edited directory,
so hertz-ctl must never let them reach loopback, private-network or other
non-public addresses, directly, through DNS names, or through redirects.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""

import http.server
import os
import socket
import threading
import unittest
import urllib.request
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = SourceFileLoader("hertz_ctl", os.path.join(ROOT, "hertz-ctl"))
_spec = spec_from_loader("hertz_ctl", _loader)
hz = module_from_spec(_spec)
_loader.exec_module(hz)


class Recorder(http.server.BaseHTTPRequestHandler):
    """Counts every request it receives; optionally redirects."""
    hits = None
    redirect_to = None

    def do_GET(self):
        type(self).hits.append(self.path)
        if self.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    def log_message(self, *args):
        pass


def serve(host, redirect_to=None):
    handler = type("H", (Recorder,), {"hits": [], "redirect_to": redirect_to})
    server = http.server.ThreadingHTTPServer((host, 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler.hits


class PublicIpTest(unittest.TestCase):
    def test_rejects_non_public(self):
        for ip in ["127.0.0.1", "127.8.9.10", "0.0.0.0", "10.0.0.1", "172.16.5.4",
                   "192.168.1.1", "169.254.169.254", "100.64.0.1", "224.0.0.1",
                   "255.255.255.255", "::1", "::", "fe80::1", "fc00::1", "fd12::1",
                   "::ffff:127.0.0.1", "::ffff:192.168.0.1", "ff02::1", "not-an-ip"]:
            self.assertFalse(hz.public_ip(ip), ip)

    def test_accepts_public(self):
        for ip in ["1.1.1.1", "8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"]:
            self.assertTrue(hz.public_ip(ip), ip)


class OpenerTest(unittest.TestCase):
    def setUp(self):
        self.server, self.hits = serve("127.0.0.1")
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def assert_blocked(self, url, opener):
        with self.assertRaises(OSError, msg=url):
            opener.open(urllib.request.Request(url), timeout=3)

    def test_loopback_spellings_never_connect(self):
        p = self.port
        for url in [f"http://127.0.0.1:{p}/", f"http://localhost:{p}/", f"http://[::1]:{p}/",
                    f"http://2130706433:{p}/", f"http://0x7f000001:{p}/", f"http://127.1:{p}/",
                    f"http://[::ffff:127.0.0.1]:{p}/", f"http://0.0.0.0:{p}/"]:
            self.assert_blocked(url, hz.API_OPENER)
            self.assert_blocked(url, hz.ART_OPENER)
        self.assertEqual(self.hits, [], "the local server must not receive any request")

    def test_private_and_metadata_addresses(self):
        for url in ["http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/",
                    "http://192.168.1.1/", "http://100.64.0.1/", "https://[fd00::1]/"]:
            self.assert_blocked(url, hz.ART_OPENER)

    def test_logo_ports_limited_to_80_and_443(self):
        with self.assertRaises(hz.BlockedAddress):
            hz.public_connection((80, 443))(("1.1.1.1", 8080), timeout=1)

    def test_redirect_hops_are_checked(self):
        # A "public" server (127.0.0.2, whitelisted only inside this test)
        # redirects to the loopback server. The hop must be refused.
        original = hz.public_ip
        hz.public_ip = lambda a: a == "127.0.0.2" or original(a)
        try:
            front, front_hits = serve("127.0.0.2", redirect_to=f"http://127.0.0.1:{self.port}/secret")
            try:
                self.assert_blocked(f"http://127.0.0.2:{front.server_address[1]}/logo.png", hz.API_OPENER)
                self.assertEqual(len(front_hits), 1, "the allowed host is reached once")
                self.assertEqual(self.hits, [], "the redirect target must not be reached")
            finally:
                front.shutdown()
                front.server_close()
        finally:
            hz.public_ip = original

    def test_artwork_fetch_refuses_local_logo(self):
        art = hz.Artwork.__new__(hz.Artwork)  # no worker threads needed
        self.assertIsNone(art.fetch("00000000-0000-0000-0000-000000000000",
                                    f"http://127.0.0.1:{self.port}/favicon.png"))
        self.assertIsNone(art.fetch("00000000-0000-0000-0000-000000000000",
                                    "http://localhost/favicon.png"))
        self.assertEqual(self.hits, [])

    def test_streams_must_be_public(self):
        self.assertFalse(hz.public_stream(f"http://127.0.0.1:{self.port}/stream"))
        self.assertFalse(hz.public_stream("http://192.168.0.10:8000/radio.mp3"))
        self.assertFalse(hz.public_stream("http://localhost:8000/"))


def online():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
        return True
    except OSError:
        return False


@unittest.skipUnless(online(), "needs internet access")
class PublicInternetTest(unittest.TestCase):
    def test_real_logo_still_downloads(self):
        art = hz.Artwork.__new__(hz.Artwork)
        os.makedirs(hz.ART_DIR, exist_ok=True)
        path = art.fetch("9617a958-0601-11e8-ae97-52543be04c81",
                         "https://radioparadise.com/apple-touch-icon.png")
        self.assertTrue(path and os.path.getsize(path) > 0)

    def test_directory_api_still_works(self):
        items, _more = hz.Directory().search(["jazz"], "", 0)
        self.assertTrue(items)

    def test_real_stream_host_is_public(self):
        self.assertTrue(hz.public_stream("http://stream-uk1.radioparadise.com/aac-320"))


if __name__ == "__main__":
    unittest.main()
