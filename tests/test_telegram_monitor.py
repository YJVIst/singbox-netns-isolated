import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "singbox-manager"))
SPEC = importlib.util.spec_from_file_location(
    "telegram_monitor", ROOT / "singbox-manager" / "telegram_monitor.py"
)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


def result(args, stdout="", returncode=0):
    return subprocess.CompletedProcess(args, returncode, stdout, "")


class TelegramMonitorIsolationTests(unittest.TestCase):
    def test_reports_uplink_address_from_isolated_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "network-isolation.json"
            config_path.write_text(json.dumps({
                "schema": 1, "namespace": "sb-uplink", "interface": "ens3"
            }))

            def run(*args):
                if args[:3] == ("ip", "netns", "list"):
                    return result(args, "sb-uplink\n")
                if "-4" in args:
                    body = [{"addr_info": [{"scope": "global", "local": "198.51.100.7"}]}]
                else:
                    body = [{"addr_info": [{"scope": "global", "local": "2001:db8::7"}]}]
                return result(args, json.dumps(body))

            with mock.patch.object(monitor, "ISOLATION_CONFIG", config_path), \
                 mock.patch.object(monitor, "command", side_effect=run), \
                 mock.patch.object(monitor, "local_ip", side_effect=AssertionError("host veth leaked")):
                self.assertEqual(monitor.server_ip(), "198.51.100.7 / 2001:db8::7")

    def test_uses_host_address_before_isolation_is_active(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "network-isolation.json"
            config_path.write_text(json.dumps({
                "schema": 1, "namespace": "sb-uplink", "interface": "ens3"
            }))
            with mock.patch.object(monitor, "ISOLATION_CONFIG", config_path), \
                 mock.patch.object(monitor, "command", return_value=result(("ip",), "")), \
                 mock.patch.object(monitor, "physical_interface", return_value="ens3"), \
                 mock.patch.object(monitor, "local_ip", return_value="192.0.2.7"):
                self.assertEqual(monitor.server_ip(), "192.0.2.7")

    def test_invalid_isolation_config_does_not_report_host_veth_as_public_ip(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = pathlib.Path(directory) / "network-isolation.json"
            config_path.write_text("not json")
            with mock.patch.object(monitor, "ISOLATION_CONFIG", config_path), \
                 mock.patch.object(monitor, "local_ip", side_effect=AssertionError("host veth leaked")):
                self.assertEqual(monitor.server_ip(), "未知")


if __name__ == "__main__":
    unittest.main()
