import ipaddress
import importlib.util
import os
import pathlib
import subprocess
import unittest
from unittest import mock

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "singbox-manager" / "network_isolation.py"
SPEC = importlib.util.spec_from_file_location("network_isolation", SOURCE)
netiso = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(netiso)


def sample_config(ipv6=True):
    return {
        "schema": 1,
        "interface": "ens3",
        "addresses": {"ipv4": [{"local": "192.0.2.10", "prefixlen": 24, "family": "inet"}],
                      "ipv6": ([{"local": "2001:db8::10", "prefixlen": 64, "family": "inet6"}] if ipv6 else [])},
        "routes": {"ipv4": [{"gateway": "192.0.2.1", "metric": 100, "onlink": False}],
                   "ipv6": ([{"gateway": "fe80::1", "metric": 100, "onlink": False}] if ipv6 else [])},
        "host_veth": "ens3",
        "uplink_veth": "sbu0",
        "namespace": "sb-uplink",
        "subnet4": "10.254.254.0/30",
        "host4": "10.254.254.2",
        "uplink4": "10.254.254.1",
        "subnet6": "fd42:7362:7570:1::/64",
        "host6": "fd42:7362:7570:1::2",
        "uplink6": "fd42:7362:7570:1::1",
        "dhcp": False,
        "dns": ["1.1.1.1"],
    }


class NetworkIsolationTests(unittest.TestCase):
    def test_default_route_capture_keeps_gateway_metric_and_onlink(self):
        with mock.patch.object(netiso, "ip_json", return_value=[
            {"dst": "default", "dev": "ens3", "gateway": "192.0.2.1", "metric": 42,
             "flags": ["onlink"]}
        ]):
            self.assertEqual(netiso.default_routes("ens3", "-4"), [
                {"gateway": "192.0.2.1", "metric": 42, "onlink": True, "src": None}
            ])

    def test_detects_multiple_default_egress_devices(self):
        def routes(family, *_args):
            if family == "-4":
                return [{"dev": "ens3", "metric": 10}]
            return [{"dev": "wlan0", "metric": 20}]

        with mock.patch.object(netiso, "ip_json", side_effect=routes):
            with self.assertRaisesRegex(netiso.IsolationError, "多张默认出口网卡"):
                netiso.find_default_interface()

    def test_rejects_additional_non_link_route_on_uplink(self):
        with mock.patch.object(netiso, "ip_json", return_value=[
            {"dst": "10.0.0.0/8", "dev": "ens3", "gateway": "192.0.2.1", "scope": "global"}
        ]):
            with self.assertRaisesRegex(netiso.IsolationError, "非默认路由"):
                netiso.validate_extra_routes("ens3", {"ipv4": [], "ipv6": []})

    def test_allows_connected_routes_regenerated_from_interface_addresses(self):
        with mock.patch.object(netiso, "ip_json", return_value=[
            {"dst": "192.0.2.0/24", "dev": "ens3", "scope": "link"},
            {"dst": "default", "dev": "ens3", "gateway": "192.0.2.1"},
            {"type": "local", "dst": "192.0.2.10", "table": "local", "scope": "host"},
            {"dst": "2001:db8::/64", "dev": "ens3", "protocol": "kernel"},
        ]):
            netiso.validate_extra_routes("ens3", {
                "ipv4": [{"local": "192.0.2.10", "prefixlen": 24}],
                "ipv6": [{"local": "2001:db8::10", "prefixlen": 64}],
            })

    def test_rejects_service_bound_to_physical_address(self):
        output = 'LISTEN 0 128 192.0.2.10:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'
        with mock.patch.object(netiso, "command", return_value=subprocess.CompletedProcess([], 0, output, "")):
            with self.assertRaisesRegex(netiso.IsolationError, "绑定物理网卡地址"):
                netiso.validate_listeners({
                    "ipv4": [{"local": "192.0.2.10"}], "ipv6": []
                })

    def test_accepts_wildcard_listeners(self):
        output = 'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'
        with mock.patch.object(netiso, "command", return_value=subprocess.CompletedProcess([], 0, output, "")):
            netiso.validate_listeners({"ipv4": [{"local": "192.0.2.10"}], "ipv6": []})

    def test_nft_rules_forward_all_ipv4_and_ipv6_but_keep_ndp_local(self):
        rules = netiso.nft_rules(sample_config(ipv6=True))
        self.assertIn('iifname "ens3" dnat ip to 10.254.254.2', rules)
        self.assertIn('iifname "ens3" dnat ip6 to fd42:7362:7570:1::2', rules)
        self.assertIn("ff00::/8", rules)
        self.assertIn('ct status dnat accept', rules)
        self.assertIn('ip6 saddr fd42:7362:7570:1::/64', rules)

    def test_ipv4_only_config_does_not_add_ipv6_dnat_or_nat(self):
        rules = netiso.nft_rules(sample_config(ipv6=False))
        self.assertNotIn("dnat ip6", rules)
        self.assertNotIn("ip6 saddr", rules)

    def test_interface_name_validation_prevents_rule_injection(self):
        with self.assertRaises(netiso.IsolationError):
            netiso.valid_interface('eth0"; flush ruleset')

    def test_cidr_conversion_uses_address_and_prefix(self):
        self.assertEqual(netiso.as_cidr({"local": "2001:db8::10", "prefixlen": 64}),
                         "2001:db8::10/64")

    def test_pending_disable_does_not_delete_physical_link(self):
        config = sample_config(ipv6=False)
        config["host_veth"] = config["interface"]
        calls = []
        with mock.patch.object(netiso, "ns_exists", return_value=False), \
             mock.patch.object(netiso, "host_link_exists", return_value=True), \
             mock.patch.object(netiso, "command", side_effect=lambda *args, **kwargs: calls.append(args) or
                               subprocess.CompletedProcess(args, 0, "", "")):
            netiso.rollback(config)
        self.assertFalse(any(args[:3] == ("ip", "link", "del") for args in calls))

    def test_rollback_removes_veth_before_returning_same_named_physical_link(self):
        config = sample_config(ipv6=False)
        config["host_veth"] = config["interface"]
        calls = []
        with mock.patch.object(netiso, "ns_exists", return_value=True), \
             mock.patch.object(netiso, "namespace_link_exists", return_value=True), \
             mock.patch.object(netiso, "host_link_exists", return_value=True), \
             mock.patch.object(netiso, "host_link_kind", return_value="veth"), \
             mock.patch.object(netiso, "command", side_effect=lambda *args, **kwargs: calls.append(args) or
                               subprocess.CompletedProcess(args, 0, "", "")):
            netiso.rollback(config)
        delete_index = next(i for i, args in enumerate(calls) if args[:4] == ("ip", "link", "del", "ens3"))
        move_index = next(i for i, args in enumerate(calls) if args[:4] == ("ip", "-n", "sb-uplink", "link"))
        self.assertLess(delete_index, move_index)

    def test_rollback_preserves_unexpected_host_device_name_collision(self):
        config = sample_config(ipv6=False)
        config["host_veth"] = config["interface"]
        with mock.patch.object(netiso, "ns_exists", return_value=True), \
             mock.patch.object(netiso, "namespace_link_exists", return_value=True), \
             mock.patch.object(netiso, "host_link_exists", return_value=True), \
             mock.patch.object(netiso, "host_link_kind", return_value="dummy"), \
             mock.patch.object(netiso, "command") as run:
            with self.assertRaisesRegex(netiso.IsolationError, "不是预期的 veth"):
                netiso.rollback(config)
        self.assertFalse(any(call.args[:4] == ("ip", "link", "del", "ens3")
                             for call in run.call_args_list))

    def test_dhcp_hook_updates_only_the_uplink_link_and_host_dns(self):
        config = sample_config(ipv6=False)
        config["host_veth"] = config["interface"]
        calls = []
        env = {
            "interface": "ens3", "reason": "RENEW", "new_ip_address": "192.0.2.20",
            "new_subnet_mask": "255.255.255.0", "new_routers": "192.0.2.1",
            "new_domain_name_servers": "1.1.1.1 8.8.8.8",
        }
        with mock.patch.object(netiso, "load_config", return_value=config), \
             mock.patch.object(netiso, "command", side_effect=lambda *args, **kwargs: calls.append(args) or
                               subprocess.CompletedProcess(args, 0, "", "")), \
             mock.patch.object(netiso.shutil, "which", return_value="/usr/bin/resolvectl"), \
             mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(netiso.main(["network_isolation.py", "dhcp-hook"]), 0)
        self.assertIn(("ip", "-4", "addr", "flush", "dev", "ens3", "scope", "global"), calls)
        self.assertIn(("ip", "-4", "route", "replace", "default", "via", "192.0.2.1",
                       "dev", "ens3", "onlink"), calls)
        self.assertIn(("resolvectl", "dns", "ens3", "1.1.1.1", "8.8.8.8"), calls)

    def test_candidate_networks_are_parsed(self):
        with mock.patch.object(netiso, "ip_json", side_effect=[[], [], [], []]):
            subnet4, subnet6 = netiso.candidates_for_networks()
        self.assertTrue(ipaddress.ip_network(subnet4).is_private)
        self.assertEqual(ipaddress.ip_network(subnet6).version, 6)


if __name__ == "__main__":
    unittest.main()
