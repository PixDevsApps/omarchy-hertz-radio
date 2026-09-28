"""Route verdicts: the LAN stays refused behind a TUN, and addresses without
any route (issue #1: IPv6 on an IPv4-only network) are skipped, never used.

All routing tables here are simulated; nothing touches the host's routes or
the network. Run from the repository root:
    python3 -m unittest tests.test_route_verdicts -v
"""

import os
import socket
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from unittest.mock import patch

_loader = SourceFileLoader("hertz_verdicts", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hertz-ctl"))
hz = module_from_spec(spec_from_loader("hertz_verdicts", _loader))
_loader.exec_module(hz)

WIFI_V6 = [{"dst": "default", "dev": "wlo1", "gateway": "fe80::1"},
           {"dst": "2001:db8:4646::/64", "dev": "wlo1"},          # the LAN, public-style IPv6
           {"dst": "fe80::/64", "dev": "wlo1"}]
WIFI_V4 = [{"dst": "default", "dev": "wlo1", "gateway": "10.0.0.1"},
           {"dst": "10.0.0.0/24", "dev": "wlo1"}]
SINGBOX_V6 = [{"dst": "::/1", "dev": "tun0"}, {"dst": "8000::/1", "dev": "tun0"}]
SINGBOX_V4 = [{"dst": "0.0.0.0/1", "dev": "tun0"}, {"dst": "128.0.0.0/1", "dev": "tun0"}]


def routing(tables, selected):
    """A fake `ip -j` for the given tables. `selected(address)` is what
    `ip route get` returns for an address: a route dict, or None when the
    kernel has no route (e.g. "Network is unreachable")."""
    def ip_json(*args):
        if args[1:3] == ("route", "get"):
            route = selected(args[3])
            return [route] if route else []
        if args[1:3] == ("route", "show"):
            return tables.get(str(args[args.index("table") + 1]), [])
        raise AssertionError(args)
    return ip_json


class VerdictTestCase(unittest.TestCase):
    def setUp(self):
        hz._route_cache.clear()

    def tearDown(self):
        hz._route_cache.clear()

    def verdict(self, address, tables, selected):
        with patch.object(hz, "_ip_json", side_effect=routing(tables, selected)):
            return hz.route_verdict(address)


class LanGuardTest(VerdictTestCase):
    def test_tun_capturing_a_public_ipv6_lan_still_refuses_the_lan(self):
        tables = {"main": WIFI_V6, "2022": SINGBOX_V6}
        via_tun = lambda a: {"dst": a, "dev": "tun0", "table": "2022"}
        self.assertEqual(self.verdict("2001:db8:4646::1", tables, via_tun), hz.ROUTE_FORBIDDEN)
        self.assertEqual(self.verdict("2001:db8:4646::abcd", tables, via_tun), hz.ROUTE_FORBIDDEN)
        self.assertEqual(self.verdict("2606:4700:4700::1111", tables, via_tun), hz.ROUTE_INTERNET)

    def test_tun_capturing_a_public_ipv4_lan_still_refuses_the_lan(self):
        tables = {"main": WIFI_V4 + [{"dst": "1.2.3.0/24", "dev": "eth1"}], "2022": SINGBOX_V4}
        via_tun = lambda a: {"dst": a, "dev": "tun0", "table": "2022"}
        self.assertEqual(self.verdict("1.2.3.4", tables, via_tun), hz.ROUTE_FORBIDDEN)
        self.assertEqual(self.verdict("1.1.1.1", tables, via_tun), hz.ROUTE_INTERNET)

    def test_public_lan_on_a_second_interface_is_refused(self):
        tables = {"main": WIFI_V6 + [{"dst": "2001:db8:77::/64", "dev": "eth1"}]}
        on_eth1 = lambda a: {"dst": a, "dev": "eth1"}
        self.assertEqual(self.verdict("2001:db8:77::5", tables, on_eth1), hz.ROUTE_FORBIDDEN)

    def test_point_to_point_internet_link_is_not_over_blocked(self):
        tables = {"main": [{"dst": "default", "dev": "ppp0"},
                           {"dst": "1.1.1.0/24", "dev": "ppp0"},     # gateway-free peer route
                           {"dst": "192.168.1.0/24", "dev": "eth0"}]}
        via_ppp = lambda a: {"dst": a, "dev": "ppp0"}
        self.assertEqual(self.verdict("1.1.1.1", tables, via_ppp), hz.ROUTE_INTERNET)
        self.assertEqual(self.verdict("8.8.8.8", tables, via_ppp), hz.ROUTE_INTERNET)

    def test_multipath_gateway_route_is_not_on_the_link(self):
        tables = {"main": WIFI_V4 + [{"dst": "1.1.1.0/24", "nexthops": [
            {"gateway": "10.0.0.1", "dev": "wlo1"}, {"gateway": "10.0.0.2", "dev": "wlo1"}]}]}
        via_gateway = lambda a: {"dst": a, "dev": "wlo1", "gateway": "10.0.0.1"}
        self.assertEqual(self.verdict("1.1.1.1", tables, via_gateway), hz.ROUTE_INTERNET)

    def test_ordinary_lan_and_internet_unchanged(self):
        tables = {"main": WIFI_V6}
        lan = lambda a: {"dst": a, "dev": "wlo1"}
        internet = lambda a: {"dst": a, "dev": "wlo1", "gateway": "fe80::1"}
        self.assertEqual(self.verdict("2001:db8:4646::1", tables, lan), hz.ROUTE_FORBIDDEN)
        self.assertEqual(self.verdict("2606:4700:4700::1111", tables, internet), hz.ROUTE_INTERNET)


class UnroutableFamilyTest(VerdictTestCase):
    """Issue #1: on an IPv4-only network the IPv6 address of a dual-stack host
    has no route. It must be skipped, not make the whole host fail."""

    V4, V6 = "91.98.4.78", "2a01:4f8:1c1d:699::1"
    TABLES = {"main": WIFI_V4}

    @staticmethod
    def ipv4_only(address):
        if ":" in address:
            return None                                   # Network is unreachable
        return {"dst": address, "dev": "wlo1", "gateway": "10.0.0.1"}

    def addrinfo(self, *addresses):
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (a, 443, 0, 0) if ":" in a else (a, 443)) for a in addresses]

    def resolve(self, addresses, selected):
        with patch.object(hz, "_ip_json", side_effect=routing(self.TABLES, selected)), \
             patch.object(hz.socket, "getaddrinfo", return_value=self.addrinfo(*addresses)):
            return hz.resolve_public("de1.api.radio-browser.info", 443)

    def test_unroutable_ipv6_is_skipped_and_ipv4_used(self):
        infos = self.resolve([self.V4, self.V6], self.ipv4_only)
        self.assertEqual([i[4][0] for i in infos], [self.V4])

    def test_a_forbidden_address_still_refuses_the_whole_host(self):
        lan = "2001:db8:4646::1"

        def selected(address):
            if address == lan:
                return {"dst": address, "dev": "wlo1"}            # directly on the LAN
            return self.ipv4_only(address)
        with self.assertRaises(hz.BlockedAddress):
            self.resolve([self.V4, lan], selected)

    def test_non_public_address_still_refuses_the_whole_host(self):
        with self.assertRaises(hz.BlockedAddress):
            self.resolve([self.V4, "192.168.1.10"], self.ipv4_only)

    def test_no_usable_address_is_a_plain_network_error(self):
        with self.assertRaises(OSError) as caught:
            self.resolve([self.V6], self.ipv4_only)
        self.assertNotIsInstance(caught.exception, hz.BlockedAddress)

    def test_unroutable_address_is_never_connected_to(self):
        attempts = []

        class FakeSocket:
            def __init__(self, family, kind, proto):
                self.family = family

            def settimeout(self, _):
                pass

            def connect(self, sockaddr):
                attempts.append(sockaddr[0])

            def close(self):
                pass

        with patch.object(hz, "_ip_json", side_effect=routing(self.TABLES, self.ipv4_only)), \
             patch.object(hz.socket, "getaddrinfo", return_value=self.addrinfo(self.V6, self.V4)), \
             patch.object(hz.socket, "socket", FakeSocket):
            hz.public_connection((443,))(("de1.api.radio-browser.info", 443), timeout=1)
        self.assertEqual(attempts, [self.V4])

    def test_unreadable_routing_is_never_used(self):
        with patch.object(hz, "_ip_json", side_effect=OSError("ip missing")), \
             patch.object(hz.socket, "getaddrinfo", return_value=self.addrinfo(self.V4)):
            with self.assertRaises(OSError):
                hz.resolve_public("de1.api.radio-browser.info", 443)


if __name__ == "__main__":
    unittest.main()
