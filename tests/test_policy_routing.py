"""Regression coverage for full internet TUNs in policy routing tables."""

import ipaddress
import os
import subprocess
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from unittest.mock import patch


_loader = SourceFileLoader("hertz_policy", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hertz-ctl"))
hz = module_from_spec(spec_from_loader("hertz_policy", _loader))
_loader.exec_module(hz)


def fragmented(family):
    """A full internet route with all private/link-local networks excluded."""
    whole, holes = (("::/0", ("fc00::/7", "fe80::/10")) if family == "-6" else
                    ("0.0.0.0/0", ("10.0.0.0/8", "172.16.0.0/12",
                                    "192.168.0.0/16", "169.254.0.0/16")))
    nets = [ipaddress.ip_network(whole)]
    for hole in map(ipaddress.ip_network, holes):
        nets = [part for net in nets for part in
                (net.address_exclude(hole) if hole.subnet_of(net) else [net])]
    return [{"dst": str(net), "dev": "tun0", "gateway": "fe80::2" if family == "-6"
             else "192.0.2.1"} for net in nets]


class PolicyRoutingTest(unittest.TestCase):
    def setUp(self):
        hz._route_cache.clear()

    def tearDown(self):
        hz._route_cache.clear()

    def decide(self, route, tables, address="1.1.1.1"):
        def ip_json(*args):
            if args[1:3] == ("route", "get"):
                return [route]
            if args[1:3] == ("route", "show"):
                table = str(args[args.index("table") + 1]) if "table" in args else "main"
                rows = tables.get(table, [])
                return [r for r in rows if r.get("dst") == "default"] if "default" in args else rows
            raise AssertionError(args)
        with patch.object(hz, "_ip_json", side_effect=ip_json):
            return hz.routes_to_this_machine(address)

    def test_fragmented_ipv4_tun_with_private_holes(self):
        self.assertFalse(self.decide(
            {"dev": "tun0", "table": 100, "gateway": "192.0.2.1"},
            {"100": fragmented("-4")}))

    def test_fragmented_ipv6_tun_with_private_holes(self):
        self.assertFalse(self.decide(
            {"dev": "tun0", "table": 100, "gateway": "fe80::1"},
            {"100": fragmented("-6")}, "2606:4700:4700::1111"))

    def test_point_to_point_policy_default(self):
        self.assertFalse(self.decide({"dev": "wg0", "table": 200},
                                    {"200": [{"dev": "wg0", "dst": "default"}]}))

    def test_point_to_point_default_with_narrow_gateway_route(self):
        self.assertFalse(self.decide({"dev": "ppp0"}, {"main": [
            {"dst": "default", "dev": "ppp0"},
            {"dst": "93.184.216.0/24", "dev": "ppp0", "gateway": "192.0.2.1"}]}))

    def test_on_link_subnet_does_not_make_gateway_optional(self):
        self.assertTrue(self.decide({"dev": "eth0"}, {"main": [
            {"dst": "default", "dev": "eth0", "gateway": "192.0.2.1"},
            {"dst": "1.1.1.0/24", "dev": "eth0"}]}))

    def test_public_hole_does_not_qualify_as_full_internet(self):
        rows = fragmented("-4")
        excluded = ipaddress.ip_network("1.1.1.0/24")
        limited = []
        for row in rows:
            net = ipaddress.ip_network(row["dst"])
            parts = net.address_exclude(excluded) if excluded.subnet_of(net) else [net]
            limited.extend(dict(row, dst=str(part)) for part in parts)
        self.assertTrue(self.decide(
            {"dev": "tun0", "table": 100, "gateway": "192.0.2.1"}, {"100": limited}, "8.8.8.8"))

    def test_limited_vpn_route_is_not_an_internet_route(self):
        self.assertTrue(self.decide({"dev": "tun0", "table": 100}, {
            "main": [{"dst": "default", "dev": "wlo1", "gateway": "10.0.0.1"}],
            "100": [{"dst": "1.1.1.0/24", "dev": "tun0"}]}))

    def test_coverage_cannot_be_combined_across_devices(self):
        self.assertTrue(self.decide({"dev": "tun0", "table": 100}, {"100": [
            {"dst": "0.0.0.0/1", "dev": "tun0"},
            {"dst": "128.0.0.0/1", "dev": "tun1"}]}))

    def test_coverage_cannot_be_combined_across_tables(self):
        self.assertTrue(self.decide({"dev": "tun0", "table": 100}, {
            "100": [{"dst": "0.0.0.0/1", "dev": "tun0"}],
            "main": [{"dst": "128.0.0.0/1", "dev": "tun0"}]}))

    def test_non_unicast_route_cannot_complete_coverage(self):
        self.assertTrue(self.decide({"dev": "tun0", "table": 100}, {"100": [
            {"dst": "0.0.0.0/1", "dev": "tun0"},
            {"dst": "128.0.0.0/1", "dev": "tun0", "type": "blackhole"}]}))

    def test_on_link_public_address_remains_blocked(self):
        self.assertTrue(self.decide({"dev": "tun0", "table": 100},
                                   {"100": fragmented("-6")}, "2606:4700:4700::1111"))

    def test_local_and_rejected_routes_remain_blocked(self):
        for kind in ("local", "broadcast", "multicast", "blackhole", "unreachable", "prohibit"):
            with self.subTest(kind=kind):
                hz._route_cache.clear()
                self.assertTrue(self.decide({"dev": "tun0", "table": 100, "type": kind},
                                           {"100": fragmented("-4")}))

    def test_route_read_failure_is_closed(self):
        for error in (OSError("unavailable"), ValueError("invalid JSON"),
                      subprocess.TimeoutExpired("ip", 2)):
            with self.subTest(error=type(error).__name__):
                hz._route_cache.clear()
                with patch.object(hz, "_ip_json", side_effect=error):
                    self.assertTrue(hz.routes_to_this_machine("1.1.1.1"))

    def test_non_public_destinations_remain_blocked_with_tun(self):
        for address in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.0.2.1", "192.168.1.1",
                        "169.254.169.254", "100.64.0.1", "::1", "fd00::1", "fe80::1"):
            with self.subTest(address=address):
                with patch.object(hz.socket, "getaddrinfo", return_value=[
                    (hz.socket.AF_INET6 if ":" in address else hz.socket.AF_INET,
                     hz.socket.SOCK_STREAM, 6, "", (address, 443))]):
                    with self.assertRaises(hz.BlockedAddress):
                        hz.resolve_public("untrusted.invalid", 443)


if __name__ == "__main__":
    unittest.main()
