"""Network guard tests for hertz-ctl.

Station logo and stream URLs come from a public, community-edited directory,
so hertz-ctl must never let them reach loopback, private-network or other
non-public addresses, directly, through DNS names, or through redirects.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""

import http.server
import os
import shutil
import tempfile
import socket
import threading
import time
import unittest
import urllib.request
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = SourceFileLoader("hertz_ctl", os.path.join(ROOT, "hertz-ctl"))
_spec = spec_from_loader("hertz_ctl", _loader)
hz = module_from_spec(_spec)
_loader.exec_module(hz)

# The directory API client is HTTPS-only; the local test servers speak plain
# HTTP, so the address-policy tests use a guarded client that allows it.
HTTP_OPENER = hz.guarded_opener()


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
                   "::ffff:127.0.0.1", "::ffff:192.168.0.1", "ff02::1", "not-an-ip",
                   # IPv6 forms carrying a private IPv4: 6to4, NAT64, IPv4-compatible, Teredo
                   "2002:7f00:1::1", "2002:c0a8:101::1", "64:ff9b::7f00:1", "64:ff9b::a00:1",
                   "::127.0.0.1", "2001:0:4136:e378:8000:63bf:3fff:fdd2"]:
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
            self.assert_blocked(url, HTTP_OPENER)
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
        original, original_route = hz.public_ip, hz.routes_to_this_machine
        hz.public_ip = lambda a: a == "127.0.0.2" or original(a)
        hz.routes_to_this_machine = lambda a: a != "127.0.0.2" and original_route(a)
        try:
            front, front_hits = serve("127.0.0.2", redirect_to=f"http://127.0.0.1:{self.port}/secret")
            try:
                self.assert_blocked(f"http://127.0.0.2:{front.server_address[1]}/logo.png", HTTP_OPENER)
                self.assertEqual(len(front_hits), 1, "the allowed host is reached once")
                self.assertEqual(self.hits, [], "the redirect target must not be reached")
            finally:
                front.shutdown()
                front.server_close()
        finally:
            hz.public_ip, hz.routes_to_this_machine = original, original_route

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


class HttpsOnlyApiTest(unittest.TestCase):
    def test_api_client_refuses_plain_http(self):
        with self.assertRaises(OSError):
            hz.API_OPENER.open(urllib.request.Request("http://example.com/"), timeout=3)

    def test_api_client_refuses_https_to_http_redirect(self):
        handler = [h for h in hz.API_OPENER.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)][0]
        with self.assertRaises(hz.BlockedAddress):
            handler.redirect_request(urllib.request.Request("https://example.com/"), None, 302, "Found",
                                     {}, "http://example.com/downgraded")


class IPv6FormsTest(unittest.TestCase):
    def test_mapped_and_compatible_refused_nat64_follows_embedded(self):
        for a in ("::ffff:1.1.1.1", "::ffff:10.0.0.12", "::1.1.1.1", "64:ff9b::a00:1", "64:ff9b::7f00:1"):
            self.assertFalse(hz.public_ip(a), a)
        self.assertTrue(hz.public_ip("64:ff9b::808:808"))


class DefaultRouteTest(unittest.TestCase):
    """Route decisions against simulated routing tables."""

    def decide(self, route, defaults):
        saved = hz._ip_json
        hz._route_cache.clear()
        hz._ip_json = lambda *a: defaults if "show" in a else [route]
        try:
            return hz.routes_to_this_machine("203.0.113.9")
        finally:
            hz._ip_json = saved
            hz._route_cache.clear()

    def test_rules(self):
        wifi = [{"dst": "default", "gateway": "10.0.0.1", "dev": "wlo1"}]
        wg_full = [{"dst": "default", "dev": "wg0"}]
        ecmp = [{"dst": "default", "nexthops": [{"gateway": "10.0.0.1", "dev": "eth0"},
                                                {"gateway": "10.1.0.1", "dev": "eth1"}]}]
        self.assertFalse(self.decide({"dev": "wlo1", "gateway": "10.0.0.1"}, wifi))
        self.assertTrue(self.decide({"dev": "wlo1"}, wifi))                  # on the LAN
        self.assertTrue(self.decide({"dev": "tun0", "gateway": "10.8.0.1"}, wifi))   # VPN-specific route
        self.assertTrue(self.decide({"dev": "lo", "type": "local"}, wifi))
        self.assertFalse(self.decide({"dev": "wg0"}, wg_full))               # full-tunnel WireGuard
        self.assertTrue(self.decide({"dev": "wlo1"}, wg_full))               # LAN while on the VPN
        self.assertFalse(self.decide({"dev": "eth1", "gateway": "10.1.0.1"}, ecmp))
        self.assertTrue(self.decide({"dev": "wlo1", "gateway": "10.0.0.1"}, []))   # no default: closed


class PortTest(unittest.TestCase):
    def test_bad_ports_refused_before_any_lookup(self):
        for port in (22, 25, 53, 110, 143, 465, 587, 993, 6667, 0, 70000):
            with self.assertRaises(hz.BlockedAddress, msg=port):
                hz.public_connection(None)(("example.invalid", port), timeout=1)


class RouteTest(unittest.TestCase):
    def test_loopback_routes_locally(self):
        self.assertTrue(hz.routes_to_this_machine("127.0.0.1"))

    @unittest.skipUnless(shutil.which("ip"), "needs iproute2")
    def test_lan_and_own_addresses_are_refused_even_if_public(self):
        # This machine's own addresses and anything directly on its LAN are
        # refused, including LAN devices with global IPv6 addresses.
        import ipaddress, json, subprocess
        for family, probe in (("-4", "1.1.1.1"), ("-6", "2606:4700:4700::1111")):
            out = subprocess.run(["ip", "-j", family, "route", "get", probe], capture_output=True, text=True)
            try:
                own = json.loads(out.stdout)[0].get("prefsrc")
            except (ValueError, IndexError):
                continue
            if not own:
                continue
            self.assertTrue(hz.routes_to_this_machine(own), own)
            neighbour = str(ipaddress.ip_interface(own + ("/64" if ":" in own else "/24")).network.network_address + 1)
            if neighbour != own:
                self.assertTrue(hz.routes_to_this_machine(neighbour), neighbour)

    @unittest.skipUnless(shutil.which("ip"), "needs iproute2")
    def test_public_address_is_not_local(self):
        self.assertFalse(hz.routes_to_this_machine("1.1.1.1"))


class PrivateDirTest(unittest.TestCase):
    def test_refuses_symlink_and_fixes_mode(self):
        base = tempfile.mkdtemp()
        try:
            real = os.path.join(base, "real")
            os.mkdir(real, 0o755)
            self.assertEqual(hz.private_dir(real), real)
            self.assertEqual(os.stat(real).st_mode & 0o777, 0o700)
            link = os.path.join(base, "link")
            os.symlink(real, link)
            with self.assertRaises(PermissionError):
                hz.private_dir(link)
        finally:
            shutil.rmtree(base)


class SniffTest(unittest.TestCase):
    def test_only_known_raster_formats(self):
        self.assertEqual(hz.Artwork.sniff(b"\x89PNG\r\n\x1a\n" + b"0" * 20), ".png")
        self.assertEqual(hz.Artwork.sniff(b"\xff\xd8\xff" + b"0" * 20), ".jpg")
        self.assertIsNone(hz.Artwork.sniff(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'))
        self.assertIsNone(hz.Artwork.sniff(b"<html><body>not an image</body></html>"))


class ProxyTest(unittest.TestCase):
    """The player's proxy: every request is checked like any other connection."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "p.sock")
        self.proxy = hz.GuardedProxy(self.path)
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.target, self.hits = serve("127.0.0.1")
        self.port = self.target.server_address[1]

    def tearDown(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        self.target.shutdown()
        self.target.server_close()
        shutil.rmtree(self.dir)

    def ask(self, raw):
        with socket.socket(socket.AF_UNIX) as c:
            c.settimeout(5)
            c.connect(self.path)
            c.sendall(raw)
            return c.recv(4096).split(b"\r\n", 1)[0]

    def test_connect_to_loopback_refused(self):
        self.assertIn(b"403", self.ask(f"CONNECT 127.0.0.1:{self.port} HTTP/1.1\r\n\r\n".encode()))
        self.assertIn(b"403", self.ask(f"CONNECT localhost:{self.port} HTTP/1.1\r\n\r\n".encode()))
        self.assertEqual(self.hits, [])

    def test_absolute_get_to_loopback_refused(self):
        self.assertIn(b"403", self.ask(
            f"GET http://127.0.0.1:{self.port}/x HTTP/1.1\r\nHost: x\r\n\r\n".encode()))
        self.assertIn(b"403", self.ask(b"GET http://169.254.169.254/latest HTTP/1.1\r\n\r\n"))
        self.assertEqual(self.hits, [])

    def test_malformed_requests(self):
        self.assertIn(b"405", self.ask(b"POST http://example.com/ HTTP/1.1\r\n\r\n"))
        self.assertIn(b"400", self.ask(b"GET http://user:pw@example.com/ HTTP/1.1\r\n\r\n"))
        self.assertIn(b"400", self.ask(b"GET ftp://example.com/ HTTP/1.1\r\n\r\n"))
        self.assertIn(b"400", self.ask(b"CONNECT user@example.com:443 HTTP/1.1\r\n\r\n"))
        self.assertIn(b"431", self.ask(b"GET http://example.com/ HTTP/1.1\r\nX: " + b"a" * 70000 + b"\r\n\r\n"))

    def test_bad_port_refused(self):
        self.assertIn(b"403", self.ask(b"CONNECT example.com:25 HTTP/1.1\r\n\r\n"))


class Trickle(http.server.BaseHTTPRequestHandler):
    """Sends one byte every half second, forever (headers or body)."""
    in_headers = False

    def do_GET(self):
        try:
            head = b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 999999\r\n\r\n"
            if self.in_headers:
                for byte in head:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.5)
            self.wfile.write(head if not self.in_headers else b"")
            while True:
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.5)
        except OSError:
            pass

    def log_message(self, *args):
        pass


class DeadlineTest(unittest.TestCase):
    """A server trickling bytes (or a hanging DNS lookup) can't hold a request
    past its total deadline, although each read is well within the socket timeout."""

    def setUp(self):
        self.original = (hz.public_ip, hz.routes_to_this_machine, hz.ART_DEADLINE, hz.API_DEADLINE)
        hz.public_ip = lambda a: a == "127.0.0.2" or self.original[0](a)
        hz.routes_to_this_machine = lambda a: a != "127.0.0.2" and self.original[1](a)
        hz.ART_DEADLINE = hz.API_DEADLINE = 2.0
        self.servers = []

    def tearDown(self):
        hz.public_ip, hz.routes_to_this_machine, hz.ART_DEADLINE, hz.API_DEADLINE = self.original
        for server in self.servers:
            server.shutdown()
            server.server_close()

    def trickle(self, in_headers):
        handler = type("T", (Trickle,), {"in_headers": in_headers})
        server = http.server.ThreadingHTTPServer(("127.0.0.2", 0), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return f"http://127.0.0.2:{server.server_address[1]}/"

    def timed(self, fn):
        start = time.monotonic()
        result = fn()
        return result, time.monotonic() - start

    def test_trickled_body_is_cut_off(self):
        url = self.trickle(in_headers=False)
        with self.assertRaises(OSError):
            start = time.monotonic()
            try:
                hz.bounded_read(HTTP_OPENER, urllib.request.Request(url), 10**6, 2.0)
            finally:
                self.assertLess(time.monotonic() - start, 3.5)

    def test_trickled_headers_are_cut_off(self):
        url = self.trickle(in_headers=True)
        start = time.monotonic()
        with self.assertRaises((OSError, hz.http.client.HTTPException)):
            hz.bounded_read(HTTP_OPENER, urllib.request.Request(url), 10**6, 2.0)
        self.assertLess(time.monotonic() - start, 3.5)

    def test_artwork_worker_is_released(self):
        url = self.trickle(in_headers=False)
        art = hz.Artwork.__new__(hz.Artwork)
        # The trickle server can't listen on 80/443 here, so lift the logo port
        # limit for this test; otherwise the port check would refuse it first
        # and the test would prove nothing about the deadline.
        original = hz.ART_OPENER
        hz.ART_OPENER = hz.guarded_opener()
        try:
            result, elapsed = self.timed(lambda: art.fetch("00000000-0000-0000-0000-000000000001", url))
        finally:
            hz.ART_OPENER = original
        self.assertIsNone(result)
        self.assertGreater(elapsed, 1.5, "must have reached the slow server")
        self.assertLess(elapsed, 3.5)

    def test_hanging_dns_lookup_is_cut_off(self):
        real = socket.getaddrinfo
        hz.socket.getaddrinfo = lambda *a, **k: (time.sleep(30), real(*a, **k))[1]
        try:
            start = time.monotonic()
            with self.assertRaises(OSError):
                hz.bounded_read(HTTP_OPENER, urllib.request.Request("http://example.com/"), 1000, 2.0)
            self.assertLess(time.monotonic() - start, 3.5)
        finally:
            hz.socket.getaddrinfo = real

    def test_fast_response_is_unaffected(self):
        server, hits = serve("127.0.0.2")
        self.servers.append(server)
        body = hz.bounded_read(HTTP_OPENER, urllib.request.Request(
            f"http://127.0.0.2:{server.server_address[1]}/ok"), 10**6, 2.0)
        self.assertTrue(body.startswith(b"\x89PNG"))


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

    def test_proxy_reaches_public_https(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "p.sock")
            proxy = hz.GuardedProxy(path)
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            with socket.socket(socket.AF_UNIX) as c:
                c.settimeout(10)
                c.connect(path)
                c.sendall(b"CONNECT radioparadise.com:443 HTTP/1.1\r\n\r\n")
                self.assertIn(b"200", c.recv(4096))
            proxy.shutdown()
            proxy.server_close()
        finally:
            shutil.rmtree(d)

    def test_real_stream_host_is_public(self):
        self.assertTrue(hz.public_stream("http://stream-uk1.radioparadise.com/aac-320"))


if __name__ == "__main__":
    unittest.main()
