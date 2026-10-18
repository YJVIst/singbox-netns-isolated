# sing-box 全机代理与物理网卡隔离管理器

面向 Debian / systemd 的交互管理器，支持 HTTPS、Shadowsocks、WireGuard、SOCKS5、分流 / 全局模式、TUN / TProxy 切换、Telegram 状态通知，以及把物理出口网卡移入独立网络命名空间。

## 安装与更新

以 root 执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/coexacx/singbox-netns-isolated/main/singbox.sh)
```

首次运行选择 **1** 安装，再填写节点并启用代理。更新时再次运行同一命令并选择 **1**；节点资料和运行状态会保留。

节点、Bot 配置和网络隔离配置保存在目标服务器 `/etc/sing-box/manager/`。仓库不保存真实节点、Bot Token 或服务器凭据。部署菜单和常用命令见 [管理器说明](singbox-manager/README.md)。

## 物理网卡隔离

菜单 **17** 自动识别主路由出口、当前 IPv4 / IPv6 地址、默认网关和 DNS，并生成开机配置。配置完成后需重启；开机服务会把物理网卡移入 `sb-uplink` 命名空间，在主机网络命名空间创建 `sbu0` / veth，并让主机侧 veth 沿用原网卡名称。桌面、SSH、sing-box 与主机应用继续在主机网络命名空间运行，只能通过该虚拟 veth 和 sing-box TUN 访问网络。沿用接口名称可保留多数按接口名匹配的防火墙规则；uplink 命名空间负责 NAT、端口转发和外网路由。菜单 **18** 会把物理网卡、地址和默认路由恢复到主机。

网络命名空间按 Linux 规则隔离网卡、路由及 `/sys/class/net` 视图；这可让普通应用通过常规网络接口查询看不到物理网卡。**它不是对主机 root 的硬件隐藏机制**：特权进程仍可能进入 uplink 命名空间，`lspci`、udev 或硬件总线信息也可能暴露网卡型号。物理网卡由 `sb-uplink` 中的网络控制面管理。[Linux 网络命名空间](https://man7.org/linux/man-pages/man7/network_namespaces.7.html)

首次启用和撤销都应从本地 / 云厂商控制台操作。服务在开机早期配置网络，遇到命令失败会尝试自动回滚；不能仅凭脚本检查保证所有云网络驱动、DHCP 和防火墙策略兼容。先在测试机验证 SSH 重连、桌面联网、代理出口和重启恢复，再用于其他机器。

当前隔离版本限定：

- 单一直接连接的 Ethernet 出口；不接受 bridge、bond、VLAN、Wi-Fi、多出口或 ECMP 默认路由。
- 支持 IPv4 DHCP / 静态地址及 IPv6 静态 / SLAAC；不支持 DHCPv6。
- 自动检测到服务绑定物理 IP 时会停止配置；服务应监听 `0.0.0.0` 或 `::`，由 uplink 转发外部入站连接。
- ifupdown 为物理接口配置了 `auto` / `allow-hotplug` 或 DHCP / 静态地址时，安装器会停止配置，避免它把公网地址重新配置到主机侧 veth。UFW / firewalld / nftables 的接口名规则通常因 veth 沿用原名而继续匹配；按原公网目标 IP 匹配的入站规则需要检查，因为转发到主机后目的地址会变成 veth 地址。
- 网卡隔离不替代 sing-box TUN。需要代理流量时，还需在菜单中启用代理和 TUN；关闭代理后，主机仍经 veth 直连 uplink，但不会看到物理网卡。

## 常用命令

```bash
# 打开菜单
bash /root/singbox.sh

# sing-box 状态和日志
systemctl status sing-box.service
journalctl -u sing-box.service -n 100 --no-pager

# 网卡隔离状态和启动日志
systemctl status singbox-uplink-isolation.service
journalctl -u singbox-uplink-isolation.service -b --no-pager

# 主机可见网卡与隔离命名空间网卡
ip -br link
ip netns exec sb-uplink ip -br link
```

## 仓库结构

- `singbox.sh`：菜单入口、管理器安装器。
- `singbox-manager/`：节点管理、规则模板、监测和网卡隔离程序。
- `packages/`：带 SHA256 校验的部署资源包。
- `tools/prepare_release.py`：生成资源包并更新下载摘要。
