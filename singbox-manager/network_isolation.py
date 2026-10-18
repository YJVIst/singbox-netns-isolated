#!/usr/bin/env python3
"""Move the host's uplink NIC to a private netns and connect it with veth."""

from __future__ import annotations

import ipaddress
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from typing import Any


CONFIG = pathlib.Path("/etc/sing-box/manager/network-isolation.json")
STATE = pathlib.Path("/var/lib/singbox-manager/network-isolation")
NETNS = "sb-uplink"
UPLINK_VETH = "sbu0"
NFT_TABLE = "singbox_uplink"
DHCP_PID = "/run/singbox-uplink-dhclient.pid"
DHCP_LEASES = "/var/lib/singbox-manager/network-isolation/dhclient.leases"
LIB = pathlib.Path("/usr/local/lib/singbox-manager")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")


class IsolationError(RuntimeError):
    pass


def command(*args: str, input_text: str | None = None, check: bool = True,
            timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, input=input_text, text=True, capture_output=True,
                              check=check, timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise IsolationError(f"命令失败：{' '.join(args)}；{str(detail).strip()[:500]}") from exc


def ip_json(*args: str, check: bool = True) -> list[dict[str, Any]]:
    result = command("ip", "-j", *args, check=check)
    if not result.stdout.strip():
        return []
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise IsolationError(f"iproute2 返回了无效 JSON：{exc}") from exc
    if not isinstance(parsed, list):
        raise IsolationError("iproute2 返回格式不正确。")
    return parsed


def valid_interface(name: str) -> str:
    if not IFACE_RE.fullmatch(name):
        raise IsolationError(f"不支持的网卡名称：{name!r}")
    return name


def addr_info(interface: str, family: str) -> list[dict[str, Any]]:
    rows = ip_json(family, "addr", "show", "dev", interface)
    if not rows:
        return []
    result = []
    for item in rows[0].get("addr_info", []):
        if item.get("scope") != "global":
            continue
        if item.get("family") not in ("inet", "inet6"):
            continue
        result.append({
            "local": item["local"],
            "prefixlen": int(item["prefixlen"]),
            "family": item["family"],
            "label": item.get("label"),
        })
    return result


def default_routes(interface: str, family: str) -> list[dict[str, Any]]:
    routes = ip_json(family, "route", "show", "table", "main", "default")
    selected = [row for row in routes if row.get("dev") == interface]
    if any("nexthops" in row for row in selected):
        raise IsolationError("多路径默认路由暂不支持；请先改成单一默认网关。")
    metrics = [int(row.get("metric", 0)) for row in selected]
    if len(metrics) != len(set(metrics)):
        raise IsolationError("同一网卡存在相同优先级的多条默认路由；请先消除 ECMP 配置。")
    result = []
    for row in selected:
        gateway = row.get("gateway")
        if gateway is not None:
            ipaddress.ip_address(gateway)
        result.append({
            "gateway": gateway,
            "metric": int(row.get("metric", 0)),
            "onlink": "onlink" in row.get("flags", []),
            "src": row.get("prefsrc"),
        })
    return result


def validate_extra_routes(interface: str, addresses: dict[str, list[dict[str, Any]]]) -> None:
    connected = []
    for family_addresses in addresses.values():
        for item in family_addresses:
            connected.append(ipaddress.ip_interface(as_cidr(item)).network)
    for family in ("-4", "-6"):
        for route in ip_json(family, "route", "show", "table", "all", "dev", interface):
            if (route.get("dst") == "default" or route.get("scope") in ("link", "host")
                    or route.get("type") in ("local", "broadcast", "anycast", "multicast")):
                continue
            try:
                network = ipaddress.ip_network(route.get("dst", ""), strict=False)
            except ValueError:
                network = None
            if network is not None and network.network_address.is_link_local:
                continue
            if route.get("protocol") == "kernel" and network is not None and any(
                    network.version == own.version
                    and (network.subnet_of(own) or own.subnet_of(network))
                    for own in connected):
                continue
            raise IsolationError(
                f"网卡 {interface} 还承载非默认路由 {route.get('dst', '未知目标')}；"
                "首版不会迁移自定义路由，请先移除或改为单一默认出口。"
            )


def find_default_interface() -> tuple[str, dict[str, Any]]:
    candidates = []
    for family in ("-4", "-6"):
        for row in ip_json(family, "route", "show", "table", "main", "default"):
            dev = row.get("dev")
            if dev and dev != "lo":
                candidates.append((int(row.get("metric", 0)), dev, family, row))
    if not candidates:
        raise IsolationError("找不到主路由表中的默认网卡；请先恢复直连网络后再配置。")
    candidates.sort(key=lambda entry: entry[0])
    _, interface, family, route = candidates[0]
    if any(entry[1] != interface for entry in candidates):
        raise IsolationError("检测到多张默认出口网卡；为避免断网，本版本要求单一出口网卡。")
    return valid_interface(interface), route


def capture() -> dict[str, Any]:
    interface, _ = find_default_interface()
    link_rows = ip_json("-d", "link", "show", "dev", interface)
    if not link_rows:
        raise IsolationError(f"找不到网卡 {interface}。")
    link = link_rows[0]
    kind = (link.get("linkinfo") or {}).get("info_kind")
    if kind:
        raise IsolationError(f"出口 {interface} 是 {kind} 设备；本版本只接受直接连接的 Ethernet 网卡。")
    if link.get("link_type") not in (None, "ether"):
        raise IsolationError(f"出口 {interface} 不是 Ethernet 网卡。")
    if link.get("master"):
        raise IsolationError(f"出口 {interface} 属于 {link['master']}；请先解除 bridge/bond/team 绑定。")
    if pathlib.Path(f"/sys/class/net/{interface}/wireless").exists():
        raise IsolationError("Wi-Fi 出口暂不支持；本版本只接受直接连接的 Ethernet 网卡。")
    extras = []
    for path in pathlib.Path("/sys/class/net").iterdir():
        if path.name in ("lo", interface):
            continue
        if (path / "device").exists() or (path / "wireless").exists():
            extras.append(path.name)
    if extras:
        raise IsolationError(f"检测到其他物理 / 无线网卡 {', '.join(extras)}；请先确认并处理，避免残留接口泄露。")
    addresses = {
        "ipv4": addr_info(interface, "-4"),
        "ipv6": addr_info(interface, "-6"),
    }
    validate_extra_routes(interface, addresses)
    routes = {
        "ipv4": default_routes(interface, "-4"),
        "ipv6": default_routes(interface, "-6"),
    }
    if not any(routes.values()):
        raise IsolationError(f"网卡 {interface} 没有可迁移的默认路由。")
    if not any(addresses.values()):
        raise IsolationError(f"网卡 {interface} 尚未获得全局 IP 地址。")
    validate_listeners(addresses)
    validate_host_firewall(interface)
    dhcp6 = detect_dhcp6(interface)
    if dhcp6:
        raise IsolationError("检测到 DHCPv6 地址配置；本版本目前支持 IPv6 SLAAC / 静态地址，不支持 DHCPv6。")
    return {
        "schema": 1,
        "interface": interface,
        "addresses": addresses,
        "routes": routes,
        # Reuse the old name in the host namespace so interface-based firewall
        # rules still match the virtual uplink. The physical device lives in
        # sb-uplink and the two same-named links are namespace-local.
        "host_veth": interface,
        "uplink_veth": "sbu1" if interface == UPLINK_VETH else UPLINK_VETH,
        "namespace": NETNS,
        "dhcp": detect_dhcp(interface),
        "dns": detect_dns(interface),
        "created_at": int(time.time()),
    }


def validate_listeners(addresses: dict[str, list[dict[str, Any]]]) -> None:
    public = {item["local"] for family in addresses.values() for item in family}
    result = command("ss", "-H", "-lntup", check=False)
    for line in result.stdout.splitlines():
        columns = line.split()
        if len(columns) < 5:
            continue
        endpoint = columns[3]
        host = endpoint.rsplit(":", 1)[0].strip("[]")
        try:
            address = str(ipaddress.ip_address(host))
        except ValueError:
            continue
        if address in public:
            program = columns[-1] if columns[-1].startswith("users:") else "未知服务"
            raise IsolationError(
                f"{program} 正在绑定物理网卡地址 {address}；隔离后该地址只存在于 uplink 命名空间。"
                "请先将服务改为监听 0.0.0.0 或 ::，再配置隔离。"
            )


def validate_host_firewall(interface: str) -> None:
    # The host-side veth intentionally reuses this name, so interface-based
    # UFW/firewalld/nftables rules continue to see the expected device name.
    # ifupdown is different: it can configure the veth with the physical
    # address and replace the namespace-managed default route.
    roots = [pathlib.Path("/etc/network/interfaces")]
    roots.extend(pathlib.Path("/etc/network/interfaces.d").glob("*")
                 if pathlib.Path("/etc/network/interfaces.d").exists() else [])
    pattern = re.compile(rf"^\s*(?:auto|allow-hotplug)\s+.*\b{re.escape(interface)}\b|"
                         rf"^\s*iface\s+{re.escape(interface)}\s+inet6?\s+(?!manual\b)\S+", re.M)
    for path in roots:
        if path.is_file() and pattern.search(path.read_text(errors="replace")):
            raise IsolationError(
                f"ifupdown 配置会在主机命名空间重新配置虚拟网卡 {interface}；"
                "请先迁移该接口配置到 NetworkManager/systemd-networkd，或移除 ifupdown stanza。"
            )


def detect_dhcp(interface: str) -> bool:
    """Best-effort detection; the user confirms the result in the setup menu."""
    if shutil.which("nmcli") and command("systemctl", "is-active", "NetworkManager.service", check=False).returncode == 0:
        active = command("nmcli", "-g", "GENERAL.CONNECTION", "device", "show", interface, check=False).stdout.strip()
        if active and active != "--":
            method = command("nmcli", "-g", "ipv4.method", "connection", "show", active, check=False).stdout.strip()
            if method:
                return method == "auto"
    for lease in pathlib.Path("/run/systemd/netif/leases").glob("*"):
        try:
            text = lease.read_text(errors="replace")
        except OSError:
            continue
        if f"IFNAME={interface}\n" in text:
            return True
    interfaces = pathlib.Path("/etc/network/interfaces")
    if interfaces.exists():
        text = interfaces.read_text(errors="replace")
        match = re.search(rf"(?ms)^\s*iface\s+{re.escape(interface)}\s+inet\s+(\S+)", text)
        if match:
            return match.group(1) == "dhcp"
    return False


def detect_dhcp6(interface: str) -> bool:
    if shutil.which("nmcli") and command("systemctl", "is-active", "NetworkManager.service", check=False).returncode == 0:
        active = command("nmcli", "-g", "GENERAL.CONNECTION", "device", "show", interface, check=False).stdout.strip()
        if active and active != "--":
            method = command("nmcli", "-g", "ipv6.method", "connection", "show", active, check=False).stdout.strip()
            dhcp = command("nmcli", "-g", "ipv6.dhcp", "connection", "show", active, check=False).stdout.strip().lower()
            if method == "dhcp" or dhcp in ("yes", "true", "1"):
                return True
    for lease in pathlib.Path("/run/systemd/netif/leases").glob("*"):
        try:
            text = lease.read_text(errors="replace")
        except OSError:
            continue
        if f"IFNAME={interface}\n" in text and (
                re.search(r"(?m)^DHCP6_", text) or re.search(r"(?m)^ADDRESS=.*:", text)):
            return True
    return False


def detect_dns(interface: str) -> list[str]:
    servers: list[str] = []
    if shutil.which("resolvectl"):
        result = command("resolvectl", "dns", interface, check=False)
        for token in result.stdout.split(":", 1)[-1].split():
            try:
                servers.append(str(ipaddress.ip_address(token)))
            except ValueError:
                continue
    if not servers:
        try:
            for line in pathlib.Path("/etc/resolv.conf").read_text().splitlines():
                if line.strip().startswith("nameserver "):
                    token = line.split()[1]
                    try:
                        value = str(ipaddress.ip_address(token))
                    except ValueError:
                        continue
                    if not value.startswith("127.") and value != "::1":
                        servers.append(value)
        except OSError:
            pass
    return list(dict.fromkeys(servers)) or ["1.1.1.1", "2606:4700:4700::1111"]


def candidates_for_networks() -> tuple[str, str]:
    used4: list[ipaddress._BaseNetwork] = []
    used6: list[ipaddress._BaseNetwork] = []
    for family, destination in (("-4", used4), ("-6", used6)):
        for row in ip_json(family, "route", "show", "table", "all", check=False):
            dst = row.get("dst")
            if not dst or dst == "default" or dst.startswith("broadcast"):
                continue
            try:
                network = ipaddress.ip_network(dst, strict=False)
            except ValueError:
                continue
            destination.append(network)
        for row in ip_json(family, "addr", "show", check=False):
            for info in row.get("addr_info", []):
                try:
                    destination.append(ipaddress.ip_interface(
                        f"{info['local']}/{info['prefixlen']}"
                    ).network)
                except (KeyError, ValueError):
                    pass
    choices4 = ["10.254.254.0/30", "10.253.253.0/30", "172.29.254.0/30", "100.127.255.252/30"]
    choices6 = ["fd42:7362:7570:1::/64", "fd42:7362:7570:2::/64", "fd42:7362:7570:3::/64"]
    subnet4 = next((ipaddress.ip_network(item) for item in choices4
                   if not any(ipaddress.ip_network(item).overlaps(net) for net in used4)), None)
    subnet6 = next((ipaddress.ip_network(item) for item in choices6
                   if not any(ipaddress.ip_network(item).overlaps(net) for net in used6)), None)
    if subnet4 is None or subnet6 is None:
        raise IsolationError("找不到不冲突的 veth 地址段；请移除冲突路由后重试。")
    return str(subnet4), str(subnet6)


def write_config(config: dict[str, Any]) -> None:
    subnet4, subnet6 = candidates_for_networks()
    net4, net6 = ipaddress.ip_network(subnet4), ipaddress.ip_network(subnet6)
    config["subnet4"] = str(net4)
    config["host4"] = str(net4.network_address + 2)
    config["uplink4"] = str(net4.network_address + 1)
    config["subnet6"] = str(net6)
    config["host6"] = str(net6.network_address + 2)
    config["uplink6"] = str(net6.network_address + 1)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG)


def load_config(path: pathlib.Path = CONFIG) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise IsolationError(f"读取网卡隔离配置失败：{exc}") from exc
    if config.get("schema") != 1:
        raise IsolationError("网卡隔离配置版本不受支持。")
    valid_interface(config.get("interface", ""))
    if config.get("host_veth") != config["interface"]:
        raise IsolationError("隔离配置中的主机 veth 名称与原网卡不一致；拒绝自动修改网络。")
    valid_interface(config.get("uplink_veth", ""))
    return config


def as_cidr(row: dict[str, Any]) -> str:
    address = ipaddress.ip_address(row["local"])
    prefix = int(row["prefixlen"])
    return f"{address}/{prefix}"


def run_ipns(config: dict[str, Any], *args: str, check: bool = True,
             input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return command("ip", "netns", "exec", config["namespace"], *args,
                   check=check, input_text=input_text)


def ns_exists(config: dict[str, Any]) -> bool:
    return any(line.split()[0] == config["namespace"]
               for line in command("ip", "netns", "list", check=False).stdout.splitlines() if line.split())


def host_link_exists(name: str) -> bool:
    return command("ip", "link", "show", "dev", name, check=False).returncode == 0


def host_link_kind(name: str) -> str | None:
    rows = ip_json("-d", "link", "show", "dev", name, check=False)
    return (rows[0].get("linkinfo") or {}).get("info_kind") if rows else None


def namespace_link_exists(config: dict[str, Any], name: str) -> bool:
    return run_ipns(config, "ip", "link", "show", "dev", name, check=False).returncode == 0


def apply_routes(config: dict[str, Any]) -> None:
    interface = config["interface"]
    for family, key in (("-4", "ipv4"), ("-6", "ipv6")):
        for address in config["addresses"][key]:
            command("ip", "netns", "exec", config["namespace"], "ip", family,
                    "addr", "replace", as_cidr(address), "dev", interface)
        for route in config["routes"][key]:
            args = ["ip", family, "route", "replace", "default"]
            if route.get("gateway"):
                args += ["via", route["gateway"]]
                if route.get("onlink"):
                    args.append("onlink")
                else:
                    # VPS gateways are often outside a /32 address prefix.
                    args.append("onlink")
            args += ["dev", interface]
            if route.get("metric", 0):
                args += ["metric", str(route["metric"])]
            command("ip", "netns", "exec", config["namespace"], *args)


def nft_rules(config: dict[str, Any]) -> str:
    interface = config["interface"]
    host_veth = config["uplink_veth"]
    lines = [
        f"table inet {NFT_TABLE} {{",
        "  chain prerouting {",
        "    type nat hook prerouting priority dstnat; policy accept;",
        f'    iifname "{interface}" ip daddr 255.255.255.255 return',
        f'    iifname "{interface}" ip daddr 224.0.0.0/4 return',
        f'    iifname "{interface}" udp sport 67 udp dport 68 return',
        f'    iifname "{interface}" dnat ip to {config["host4"]}',
        f'    iifname "{interface}" ip6 daddr {{ fe80::/10, ff00::/8 }} return',
    ]
    if config["routes"]["ipv6"]:
        lines.append(f'    iifname "{interface}" dnat ip6 to {config["host6"]}')
    lines += [
        "  }",
        "  chain input {",
        "    type filter hook input priority filter; policy drop;",
        '    iifname "lo" accept',
        "    ct state established,related accept",
        f'    iifname "{interface}" udp sport 67 udp dport 68 accept',
        f'    iifname "{interface}" udp sport 547 udp dport 546 accept',
        f'    iifname "{interface}" meta l4proto ipv6-icmp icmpv6 type {{ nd-router-solicit, nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert, nd-redirect }} accept',
        "  }",
        "  chain forward {",
        "    type filter hook forward priority filter; policy drop;",
        "    ct state established,related accept",
        f'    iifname "{host_veth}" oifname "{interface}" accept',
        f'    iifname "{interface}" oifname "{host_veth}" ct status dnat accept',
        "  }",
        "  chain postrouting {",
        "    type nat hook postrouting priority srcnat; policy accept;",
        f'    ip saddr {config["subnet4"]} oifname "{interface}" masquerade',
    ]
    if config["routes"]["ipv6"]:
        lines.append(f'    ip6 saddr {config["subnet6"]} oifname "{interface}" masquerade')
    lines += ["  }", "}", ""]
    return "\n".join(lines)


def configure_sysctls(config: dict[str, Any]) -> None:
    run_ipns(config, "sysctl", "-qw", "net.ipv4.ip_forward=1")
    run_ipns(config, "sysctl", "-qw", "net.ipv4.conf.all.rp_filter=0")
    run_ipns(config, "sysctl", "-qw", "net.ipv4.conf.default.rp_filter=0")
    if config["routes"]["ipv6"]:
        run_ipns(config, "sysctl", "-qw", "net.ipv6.conf.all.forwarding=1")
        run_ipns(config, "sysctl", "-qw", "net.ipv6.conf.default.forwarding=1")
        run_ipns(config, "sysctl", "-qw", f"net.ipv6.conf.{config['interface']}.accept_ra=2")


def configure_host_veth(config: dict[str, Any]) -> None:
    command("ip", "link", "set", "dev", config["host_veth"], "up")
    command("ip", "addr", "replace", f"{config['host4']}/30", "dev", config["host_veth"])
    if config["routes"]["ipv6"]:
        command("ip", "-6", "addr", "replace", f"{config['host6']}/64", "dev", config["host_veth"])
    for family, key, uplink in (("-4", "ipv4", config["uplink4"]),
                                ("-6", "ipv6", config["uplink6"])):
        if not config["routes"][key]:
            continue
        route = min(config["routes"][key], key=lambda item: item.get("metric", 0))
        args = ["ip", family, "route", "replace", "default", "via", uplink,
                "dev", config["host_veth"]]
        if route.get("metric", 0):
            args += ["metric", str(route["metric"])]
        command(*args)
    if shutil.which("resolvectl") and config.get("dns"):
        command("resolvectl", "dns", config["host_veth"], *config["dns"], check=False)
        command("resolvectl", "domain", config["host_veth"], "~.", check=False)
        command("resolvectl", "default-route", config["host_veth"], "yes", check=False)


def configure_network_manager(config: dict[str, Any]) -> None:
    # These runtime snippets are recreated before the network managers at boot.
    nm_dir = pathlib.Path("/run/NetworkManager/conf.d")
    if nm_dir.exists() or pathlib.Path("/usr/sbin/NetworkManager").exists():
        nm_dir.mkdir(parents=True, exist_ok=True)
        (nm_dir / "90-singbox-uplink-isolation.conf").write_text(
            "[keyfile]\n"
            f"unmanaged-devices=interface-name:{config['host_veth']}\n"
        )
    networkd_dir = pathlib.Path("/run/systemd/network")
    if networkd_dir.exists() or pathlib.Path("/usr/lib/systemd/systemd-networkd").exists():
        networkd_dir.mkdir(parents=True, exist_ok=True)
        (networkd_dir / "00-singbox-uplink-isolation.network").write_text(
            "[Match]\n"
            f"Name={config['host_veth']}\n\n"
            "[Link]\nUnmanaged=yes\n"
        )


def start_dhcp(config: dict[str, Any]) -> None:
    if not config.get("dhcp"):
        return
    client = shutil.which("dhclient")
    if not client:
        raise IsolationError("此网卡使用 DHCP，但找不到 dhclient；请安装 isc-dhcp-client。")
    pathlib.Path(STATE).mkdir(parents=True, exist_ok=True)
    hook = LIB / "dhclient-hook.sh"
    if not hook.is_file():
        raise IsolationError(f"找不到 DHCP 更新脚本：{hook}")
    command("ip", "netns", "exec", config["namespace"], client,
            "-nw", "-pf", DHCP_PID, "-lf", DHCP_LEASES, "-sf", str(hook),
            config["interface"], check=False)


def namespace_setup(config: dict[str, Any]) -> None:
    if not shutil.which("nft"):
        raise IsolationError("找不到 nft；请安装 nftables 后重试。")
    if not ns_exists(config):
        command("ip", "netns", "add", config["namespace"])
    # Move the physical link first to free its name. The host-side veth then
    # reuses that name to keep existing interface-based firewall rules valid.
    physical_in_namespace = namespace_link_exists(config, config["interface"])
    if host_link_exists(config["interface"]) and not physical_in_namespace:
        if host_link_kind(config["interface"]) == "veth":
            raise IsolationError(
                f"主机接口 {config['interface']} 是残留 veth，但物理网卡不在 uplink 命名空间；"
                "拒绝把虚拟接口误当成物理网卡移动。请在本地控制台检查。"
            )
        command("ip", "link", "set", "dev", config["interface"], "netns", config["namespace"])
    elif not physical_in_namespace:
        raise IsolationError(f"物理网卡 {config['interface']} 在主机和 uplink 命名空间中都不存在。")
    if host_link_exists(config["host_veth"]) and host_link_kind(config["host_veth"]) != "veth":
        raise IsolationError(
            f"主机接口 {config['host_veth']} 已被非 veth 设备占用；拒绝改动该接口。"
        )
    if not host_link_exists(config["host_veth"]):
        command("ip", "link", "add", config["host_veth"], "type", "veth",
                "peer", "name", config["uplink_veth"])
    if not namespace_link_exists(config, config["uplink_veth"]):
        command("ip", "link", "set", config["uplink_veth"], "netns", config["namespace"])
    run_ipns(config, "ip", "link", "set", "lo", "up")
    run_ipns(config, "ip", "link", "set", config["uplink_veth"], "up")
    run_ipns(config, "ip", "addr", "replace", f"{config['uplink4']}/30", "dev", config["uplink_veth"])
    if config["routes"]["ipv6"]:
        run_ipns(config, "ip", "-6", "addr", "replace", f"{config['uplink6']}/64", "dev", config["uplink_veth"])
    run_ipns(config, "ip", "link", "set", "dev", config["interface"], "up")
    apply_routes(config)
    configure_host_veth(config)
    configure_sysctls(config)
    run_ipns(config, "nft", "delete", "table", "inet", NFT_TABLE, check=False)
    rules = nft_rules(config)
    run_ipns(config, "nft", "-c", "-f", "-", input_text=rules)
    run_ipns(config, "nft", "-f", "-", input_text=rules)
    start_dhcp(config)
    configure_network_manager(config)
    pathlib.Path(STATE).mkdir(parents=True, exist_ok=True)
    (STATE / "active.json").write_text(json.dumps({"interface": config["interface"], "namespace": config["namespace"]}) + "\n")
    os.chmod(STATE / "active.json", 0o600)


def rollback(config: dict[str, Any], tolerate_missing: bool = False) -> None:
    interface = config["interface"]
    namespace = config["namespace"]
    if ns_exists(config):
        command("ip", "netns", "exec", namespace, "dhclient", "-x", "-pf", DHCP_PID,
                check=False)
        physical_in_namespace = namespace_link_exists(config, interface)
        if physical_in_namespace and host_link_exists(config["host_veth"]):
            # The host veth has the physical interface's old name.
            if host_link_kind(config["host_veth"]) != "veth":
                raise IsolationError(
                    f"主机接口 {config['host_veth']} 不是预期的 veth；拒绝删除它，"
                    "请在本地控制台检查网络设备。"
                )
            command("ip", "link", "del", config["host_veth"], check=False)
        if physical_in_namespace:
            command("ip", "-n", namespace, "link", "set", "dev", interface, "netns", "1")
    if not host_link_exists(interface):
        if tolerate_missing and ns_exists(config):
            command("ip", "netns", "del", namespace, check=False)
    if not host_link_exists(interface):
        raise IsolationError(f"物理网卡 {interface} 没有回到主机命名空间；请在本地控制台检查。")
    command("ip", "link", "set", "dev", interface, "up", check=False)
    for family, key in (("-4", "ipv4"), ("-6", "ipv6")):
        for address in config["addresses"][key]:
            command("ip", family, "addr", "replace", as_cidr(address), "dev", interface, check=False)
        for route in config["routes"][key]:
            args = ["ip", family, "route", "replace", "default"]
            if route.get("gateway"):
                args += ["via", route["gateway"], "onlink"]
            args += ["dev", interface]
            if route.get("metric", 0):
                args += ["metric", str(route["metric"])]
            command(*args, check=False)
    if ns_exists(config):
        command("ip", "netns", "del", namespace, check=False)
    command("resolvectl", "revert", config["host_veth"], check=False)
    for path in (pathlib.Path("/run/NetworkManager/conf.d/90-singbox-uplink-isolation.conf"),
                 pathlib.Path("/run/systemd/network/00-singbox-uplink-isolation.network")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    command("systemctl", "try-restart", "NetworkManager.service", check=False)
    command("systemctl", "try-restart", "systemd-networkd.service", check=False)
    if tolerate_missing:
        shutil.rmtree(STATE, ignore_errors=True)


def boot() -> None:
    if os.geteuid() != 0:
        raise IsolationError("启动网络隔离需要 root。")
    config = load_config()
    try:
        namespace_setup(config)
    except Exception as exc:
        try:
            rollback(config, tolerate_missing=True)
        except Exception as rollback_error:
            raise IsolationError(f"隔离启动失败：{exc}；自动回滚也失败：{rollback_error}。请使用本地控制台恢复网络。") from exc
        raise IsolationError(f"隔离启动失败，原网卡配置已自动回滚：{exc}") from exc


def disable() -> None:
    if not CONFIG.exists():
        raise IsolationError("没有已保存的网卡隔离配置。")
    config = load_config()
    rollback(config)
    command("systemctl", "disable", "singbox-uplink-isolation.service", check=False)
    command("systemctl", "stop", "singbox-uplink-isolation.service", check=False)
    try:
        CONFIG.unlink()
    except FileNotFoundError:
        pass
    shutil.rmtree(STATE, ignore_errors=True)
    try:
        pathlib.Path(DHCP_PID).unlink()
    except FileNotFoundError:
        pass


def show_status() -> None:
    try:
        config = load_config()
    except IsolationError:
        print("网卡隔离：未配置")
        return
    active = ns_exists(config) and namespace_link_exists(config, config["interface"])
    state = "运行中" if active else "已配置，重启后启用"
    print(f"网卡隔离：{state}")
    print(f"物理网卡：{config['interface']}（仅在 {config['namespace']} 命名空间）")
    print(f"主机可见接口：{config['host_veth']}（veth 虚拟接口）" + (f"、sb-split0（代理 TUN）" if active else ""))


def configure_menu() -> None:
    if os.geteuid() != 0:
        raise IsolationError("请使用 root 打开管理菜单。")
    if CONFIG.exists():
        print("网卡隔离已配置。")
        show_status()
        print("如需撤销，请先使用菜单中的关闭网卡隔离功能。")
        return
    config = capture()
    print("检测到的出口配置：")
    print(f"  网卡：{config['interface']}")
    print(f"  IPv4：{', '.join(as_cidr(x) for x in config['addresses']['ipv4']) or '无'}")
    print(f"  IPv4 默认路由：{json.dumps(config['routes']['ipv4'], ensure_ascii=False)}")
    print(f"  IPv6：{', '.join(as_cidr(x) for x in config['addresses']['ipv6']) or '无'}")
    print(f"  IPv6 默认路由：{json.dumps(config['routes']['ipv6'], ensure_ascii=False)}")
    detected_mode = "DHCP" if config["dhcp"] else "静态"
    print(f"  IPv4 配置方式自动检测：{detected_mode}")
    mode = input("IPv4 使用 DHCP 更新吗？输入 y/n；回车接受自动检测结果：").strip().lower()
    if mode in ("y", "yes"):
        config["dhcp"] = True
    elif mode in ("n", "no"):
        config["dhcp"] = False
    elif mode:
        print("输入无效，已取消。")
        return
    print("隔离后物理网卡会移入专用网络命名空间，桌面、SSH 与主机应用通过 veth/NAT 联网。")
    confirm = input("输入 ENABLE 确认配置；其他输入取消：").strip()
    if confirm != "ENABLE":
        print("已取消。")
        return
    write_config(config)
    unit = pathlib.Path("/etc/systemd/system/singbox-uplink-isolation.service")
    source = pathlib.Path(__file__).parent / "systemd" / "singbox-uplink-isolation.service"
    if not source.is_file():
        raise IsolationError(f"找不到 systemd 服务模板：{source}")
    shutil.copyfile(source, unit)
    command("systemctl", "daemon-reload")
    command("systemctl", "enable", "singbox-uplink-isolation.service")
    print("配置已保存并设为开机启用；请从本地控制台重启测试。")
    print("停用菜单会把物理网卡和原 IPv4/IPv6 地址、默认路由恢复到主机。")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("menu", "boot", "disable", "status", "capture", "dhcp-hook"):
        print("用法：network_isolation.py menu|boot|disable|status|capture|dhcp-hook", file=sys.stderr)
        return 2
    try:
        action = argv[1]
        if action == "menu":
            configure_menu()
        elif action == "boot":
            boot()
        elif action == "disable":
            disable()
        elif action == "status":
            show_status()
        elif action == "dhcp-hook":
            config = load_config()
            interface = os.environ.get("interface", "")
            if interface != config["interface"]:
                raise IsolationError("DHCP 更新脚本收到不匹配的网卡名称。")
            reason = os.environ.get("reason", "")
            if reason in ("BOUND", "RENEW", "REBIND", "REBOOT"):
                address = os.environ.get("new_ip_address", "")
                mask = os.environ.get("new_subnet_mask", "")
                if address and mask:
                    prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
                    command("ip", "-4", "addr", "flush", "dev", interface, "scope", "global", check=False)
                    command("ip", "-4", "addr", "replace", f"{address}/{prefix}", "dev", interface)
                routers = os.environ.get("new_routers", "").split()
                if routers:
                    gateway = str(ipaddress.IPv4Address(routers[0]))
                    command("ip", "-4", "route", "replace", "default", "via", gateway, "dev", interface, "onlink")
                dns = []
                for token in os.environ.get("new_domain_name_servers", "").split():
                    try:
                        dns.append(str(ipaddress.IPv4Address(token)))
                    except ipaddress.AddressValueError:
                        continue
                if dns and shutil.which("resolvectl"):
                    command("resolvectl", "dns", config["host_veth"], *dns, check=False)
                    command("resolvectl", "domain", config["host_veth"], "~.", check=False)
                    command("resolvectl", "default-route", config["host_veth"], "yes", check=False)
        else:
            print(json.dumps(capture(), ensure_ascii=False, indent=2))
        return 0
    except (IsolationError, KeyboardInterrupt) as exc:
        print(f"网卡隔离错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
