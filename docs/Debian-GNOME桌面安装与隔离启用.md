# Debian GNOME 桌面安装与隔离启用教程

本教程适用于已经安装 Debian、目前以命令行或 SSH 管理的服务器。安装后可在本地显示器或云厂商的图形控制台登录 GNOME；隔离服务把真实出口网卡放进 `sb-uplink` 网络命名空间，让桌面应用在主机网络命名空间中使用虚拟 veth。启用 sing-box TUN 后，桌面流量再按分流或全局模式经过代理。

> 首次启用或撤销物理网卡隔离时，请确保可以使用本地或云厂商控制台。网络配置变更可能中断 SSH。GNOME 安装也不会自动配置公网远程桌面；不要把 RDP/VNC 端口直接暴露到公网。

## 1. 安装 GNOME

### 方式 A：给已有的 Debian 命令行系统安装

以 root 登录，或在命令前使用 `sudo`。先确认根分区有足够空间，并更新 APT 索引：

```bash
df -h /
apt update
```

安装 Debian 默认的完整 GNOME 桌面任务：

```bash
apt install task-gnome-desktop
```

使用默认 APT 推荐依赖，不要添加 `--no-install-recommends`。该任务包含 Debian 桌面公共组件，并推荐完整 `gnome` 桌面组件。安装过程会下载多个桌面和应用包；APT 会先显示所需下载量和磁盘空间，检查后输入 `Y` 继续。

如果安装过程中出现“默认显示管理器”选择界面，使用方向键选择 **gdm3** 并回车。若没有看到选择界面，安装后可运行：

```bash
dpkg-reconfigure gdm3
```

在列表中选择 `gdm3`。GDM 是 GNOME 的登录界面服务。

### 方式 B：新装 Debian 时一并安装

在 Debian 安装器的“软件选择”页面，保留 **Debian 桌面环境**，并选择 **GNOME**。如果只保留“Debian 桌面环境”默认项，安装器会选择 GNOME。若只想安装命令行系统，可取消桌面选项，安装完成后按方式 A 执行 `apt install task-gnome-desktop`。

## 2. 创建普通桌面用户

不要用 root 登录 GNOME。若安装系统时已经创建了普通用户，直接使用该账户；否则创建一个：

```bash
apt install sudo
adduser desktopuser
usermod -aG sudo desktopuser
```

把 `desktopuser` 换成你自己的账户名。`adduser` 会提示设置密码和填写可选资料。重新登录后，新用户的 `sudo` 组权限才会生效。桌面会话应使用普通账户，只有管理操作才使用 `sudo`。

若账户已经存在，只需加入 sudo 组：

```bash
usermod -aG sudo desktopuser
```

## 3. 启用图形登录

将默认启动目标设为图形界面，并启用 GDM：

```bash
systemctl set-default graphical.target
systemctl enable gdm3.service
reboot
```

重启后，在本地显示器或云厂商图形控制台中应看到 GDM 登录画面。选择普通桌面用户登录。若出现语言和初始设置向导，按需选择中文、时区和键盘布局并完成设置。

没有本地显示器的 VPS 不会因此自动提供浏览器远程桌面；请使用服务商提供的 VNC/KVM/网页控制台。远程桌面访问方式需要单独部署并保护。

登录后可打开终端检查：

```bash
systemctl get-default
systemctl is-active gdm3.service
loginctl list-sessions
```

`graphical.target` 表示默认进入图形模式；GDM 状态应为 `active`。GNOME 网络设置可能显示有线连接由系统管理器控制或不可编辑，这是隔离服务将主机侧 veth 标记为 unmanaged 的预期行为。请勿在 GNOME 网络设置中重新配置该接口的地址、网关或 DNS。

## 4. 配置代理和物理网卡隔离

建议先完成 GNOME 安装和一次图形登录测试，再配置隔离。安装或更新仓库管理器后，以 root 打开本机菜单：

```bash
bash /root/singbox.sh
```

在菜单中按以下顺序操作：

1. 选择 **1** 安装或更新 sing-box 与管理模板。
2. 按使用的协议编辑节点资料，并启用对应协议：HTTPS **5**、Shadowsocks **6**、WireGuard **7**、SOCKS5 **15**。
3. 确认顶部显示 TUN 已开启。要让普通桌面流量都经过代理，选择 **16** 切换为“全局”；分流模式会按模板规则代理目标站点，其余流量仍会直连。
4. 先使用菜单 **10** 检测代理出口，确认节点可用。
5. 确认 SSH 和公开服务监听 `0.0.0.0` / `::` 等通配地址，并检查防火墙。菜单 **17** 配置物理网卡隔离，输入 `ENABLE` 确认。
6. 从本地或云厂商控制台重启。不要在没有备用控制台时远程启用隔离。

如果这台机器已经启用了物理网卡隔离，再安装 GNOME 前先确认可以使用云厂商控制台。GNOME 可能安装并启动 NetworkManager；本仓库的隔离服务会在下次启动时把主机侧 veth 标记为 unmanaged，并安排在 NetworkManager 之前运行。安装完成后通过控制台重启，再按第 5 节确认隔离服务和路由正常。不要在没有备用控制台时从 SSH 直接重启 NetworkManager。

如果用户只需要隐藏桌面程序看到的真实出口网卡、继续使用直连网络，可以不启用代理；物理网卡隔离与 sing-box 代理是两个独立功能。如果要求桌面流量经代理，则还需要启用节点、TUN 和相应流量模式。

## 5. 重启后验证

通过图形控制台登录桌面，在终端中检查服务和接口：

```bash
systemctl status singbox-uplink-isolation.service --no-pager
systemctl status sing-box.service --no-pager
ip -br address
ip route
ip netns list
sudo ip netns exec sb-uplink ip -br address
sudo ip netns exec sb-uplink ip route
```

隔离服务通常显示 `active (exited)`：它完成一次性网络配置后保持“已启动”状态。主机命名空间中的接口是 veth；真实出口网卡在 `sb-uplink` 命名空间中。用普通桌面应用访问网络时，应用会使用主机命名空间中的接口视图。

如启用了全局代理，可在桌面浏览器访问 `https://ipinfo.io/json` 检查代理出口 IP 与地理信息。分流模式下只有符合模板规则的站点走代理，所以应测试一个明确命中的目标站点。若浏览器无法联网，先分别检查代理服务日志、隔离服务日志和路由：

```bash
journalctl -u sing-box.service -n 100 --no-pager
journalctl -u singbox-uplink-isolation.service -b -n 100 --no-pager
```

物理网卡隔离调整的是网络命名空间，不是对主机管理员的硬件隐藏。主机 root 仍可进入 `sb-uplink` 查看真实网卡；PCI、udev 等硬件信息也不属于这个隔离边界。

## 6. 故障恢复

### GNOME 无法启动，改回命令行启动

在本地或云厂商控制台切换到文本终端（通常按 `Ctrl` + `Alt` + `F3`），用普通账户登录后执行：

```bash
sudo systemctl set-default multi-user.target
sudo systemctl disable --now gdm3.service
sudo reboot
```

这只关闭图形登录，不会撤销物理网卡隔离。要重新启用 GNOME：

```bash
sudo systemctl set-default graphical.target
sudo systemctl enable gdm3.service
sudo reboot
```

### 节点失联，离线关闭代理

无需访问 GitHub 即可通过 systemd 停止代理。此操作按当前网络规则恢复普通上游直连，流量会暴露服务器公网出口 IP：

```bash
sudo systemctl disable --now singbox-manager-telegram.timer 2>/dev/null || true
sudo systemctl stop singbox-manager-telegram.service 2>/dev/null || true
sudo systemctl disable --now sing-box.service
```

更多应急命令见[常用命令与离线应急恢复](常用命令与离线应急恢复.md)。

### 撤销物理网卡隔离

在本地或云厂商控制台使用管理菜单 **18**，或执行本机安装的恢复程序：

```bash
sudo /usr/bin/python3 /usr/local/lib/singbox-manager/network_isolation.py disable
```

该操作会把物理网卡、地址和默认路由恢复到主机，可能中断 SSH。不要只运行 `systemctl stop singbox-uplink-isolation.service`；该一次性服务没有 `ExecStop`，停服务不会自动把网卡移回主机。

## 参考资料

- [Debian GNOME 安装说明](https://wiki.debian.org/Gnome)
- [Debian 桌面环境与安装器选择说明](https://wiki.debian.org/DesktopEnvironment)
- [Debian `task-gnome-desktop` 稳定版软件包](https://packages.debian.org/stable/task-gnome-desktop)
