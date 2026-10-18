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

## 4. 启用远程桌面并从 Windows 连接

GNOME 内置远程桌面使用 RDP。Windows 无需另装客户端，使用系统自带的“远程桌面连接”（`mstsc.exe`）即可。GNOME 安装完成不代表远程桌面已经启用；需要在图形会话中单独配置。

### 4.1 检查并安装远程桌面组件

先在 Debian 图形桌面中登录普通用户，打开“终端”执行：

```bash
sudo apt update
sudo apt install gnome-remote-desktop
```

安装后关闭并重新打开“设置”。如果远程桌面选项仍未出现，注销 GNOME 后重新登录再检查。

### 4.2 在 GNOME 设置中启用

打开路径：**活动 → 搜索“设置”并打开 → 系统 → 远程桌面**。也可以点击桌面右上角系统菜单中的齿轮图标打开“设置”。

按需要启用以下模式之一；也可以同时启用，但两种模式的端口可能不同：

- **共享当前桌面：**打开“桌面共享”（有些界面也称“屏幕共享”），再打开“远程控制”。这会共享当前已登录用户的 GNOME 桌面。按页面“登录详细信息”设置或查看 RDP 用户名和密码；它们可能与 Debian 用户的系统登录密码不同。
- **远程登录账户：**打开“远程登录”。若开关不可修改，先点击“解锁”并进行系统认证。此模式用于从远程登录界面进入用户账户，连接时使用 Debian 用户账户凭据。

页面中的“如何连接”会显示主机名和 RDP 端口。启用“远程登录”时通常使用 `3389`；若同时启用桌面共享，桌面共享可能使用另一个端口（例如 `3390`）。请以本机设置页面显示的端口为准。

### 4.3 从 Windows 连接

推荐通过 SSH 隧道连接，不要把 RDP 端口直接开放到公网。在 Windows PowerShell 中运行以下命令，将 `desktopuser`、`SERVER_IP` 和端口替换为实际值：

```powershell
ssh -N -L 13389:127.0.0.1:3389 desktopuser@SERVER_IP
```

保持该 PowerShell 窗口打开。若 GNOME 设置页面显示远程桌面端口是 `3390`，对应使用：

```powershell
ssh -N -L 13390:127.0.0.1:3390 desktopuser@SERVER_IP
```

然后在 Windows 按 `Win + R`，输入 `mstsc` 并回车。在“计算机”栏填写隧道左侧的本机地址，例如 `127.0.0.1:13389` 或 `127.0.0.1:13390`，连接后按所选模式输入对应凭据。若 SSH 使用非默认端口，例如 `2222`，隧道命令中加入 `-p 2222`：

```powershell
ssh -p 2222 -N -L 13389:127.0.0.1:3389 desktopuser@SERVER_IP
```

在可信任的局域网内也可以直接用 `mstsc` 连接 Debian 的 IP 和 GNOME 页面显示的端口，例如 `192.168.1.20:3389`；此时需确保局域网防火墙允许该端口。不要为方便连接而在公网防火墙中向所有地址开放 RDP 端口。

### 4.4 常见连接问题

| 现象 | 检查与处理 |
| --- | --- |
| “设置”里找不到“远程桌面” | 确认当前使用 GNOME 桌面；安装 `gnome-remote-desktop`，关闭并重开“设置”，必要时注销后重新登录。 |
| Windows 连接超时或拒绝连接 | 确认 GNOME 中相应模式已开启；使用页面显示的端口；检查 Debian 和云平台防火墙。使用 SSH 隧道时，先确认 PowerShell 中 SSH 登录成功且窗口保持打开，再连接 `127.0.0.1:本地转发端口`。 |
| 用户名或密码被拒绝 | “桌面共享”使用“登录详细信息”中的 RDP 凭据；“远程登录”使用 Debian 用户账户凭据。不要默认认为二者相同。 |
| 连接成功但看不到当前桌面 | 若要控制已打开的 GNOME 会话，启用“桌面共享”和“远程控制”，并确保普通用户已经登录图形桌面；“远程登录”用于进入账户登录会话。 |
| SSH 隧道提示无法建立转发 | 确认 SSH 账户、服务器地址和 SSH 端口正确；检查远程桌面当前启用的端口，并相应修改 `-L 本地端口:127.0.0.1:远程端口`。 |

仍无法连接时，在 Debian 图形会话的终端中检查监听端口和日志：

```bash
sudo ss -lntp | grep -E ':(3389|3390)\b' || true
journalctl --user -b -u gnome-remote-desktop.service -n 80 --no-pager
sudo journalctl -b -u gnome-remote-desktop.service -n 80 --no-pager
```

将 `ss` 命令中的端口按 GNOME“如何连接”页面显示的端口调整。桌面共享通常查看当前用户日志；远程登录通常查看系统服务日志。

## 5. 设置简体中文拼音输入法

Debian GNOME 默认使用 IBus。仅添加“中文”键盘布局不会把拼音转换成汉字；需要安装 IBus 拼音引擎，并在 GNOME 的“输入源”中添加拼音输入法。

### 5.1 安装 IBus 拼音引擎

在 GNOME 桌面中登录普通用户，打开终端执行：

```bash
sudo apt update
sudo apt install ibus ibus-libpinyin
```

`ibus-libpinyin` 提供基于拼音的中文输入引擎。安装后建议注销当前桌面用户并重新登录，让 GNOME 和 IBus 加载新引擎。

### 5.2 在 GNOME 中添加拼音输入源

打开路径：**活动 → 设置 → 键盘 → 输入源 → `+` → 中文（中国）→ 中文（智能拼音）**，再点击“添加”。不同 GNOME 版本的名称可能略有差异，请选带有“拼音”或“智能拼音”的输入法引擎。

保留 **英语（美国）** 键盘布局，以便继续输入英文。不要只添加名为“中文”或“Chinese”的普通键盘布局；普通键盘布局本身不会执行拼音到汉字的转换。

如果“中文（中国）”或拼音引擎没有出现在列表中，可安装 GNOME Tweaks 并启用扩展输入源：

```bash
sudo apt install gnome-tweaks
```

打开 Tweaks（优化）→ **键盘和鼠标 → 其他布局选项 → 输入源**，启用 **显示扩展输入源**，再回到“设置 → 键盘 → 输入源”添加“中文（智能拼音）”。

### 5.3 切换并测试

1. 在桌面右上角打开输入源菜单，选择“中文（智能拼音）”。在 Debian 本机桌面可按 **Super（Windows 键）+ 空格**在输入源之间切换。
2. 打开文本编辑器，确认输入源菜单已选中拼音输入法；如果输入法状态显示英文模式（例如 `A`），按 **Shift** 切换到中文模式（例如 `中`）。这是 IBus 智能拼音默认的中英文切换键。
3. 输入 `nihao`，按空格键选择候选词，应出现“你好”；再输入 `zhongwen` 并选择候选词，应能输入“中文”。按数字键或空格键可选候选字词。

智能拼音默认以简体中文启动。若候选词变成繁体，按 **Ctrl + Shift + F** 切换回简体；该快捷键用于切换简体/繁体。

通过 Windows RDP 远程桌面操作时，Windows 可能先截获 **Win + 空格**，只切换 Windows 本机输入法。此时直接点击远程 GNOME 桌面右上角的输入源菜单，选择“中文（智能拼音）”。

### 5.4 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 打出来仍是英文 | 点击 GNOME 右上角输入源菜单，确认选中“中文（智能拼音）”；再确认输入法处于中文模式（`中`），而不是英文模式（`A`）。仅安装软件包或添加普通“中文”键盘布局都不等于已选中拼音引擎。 |
| 远程桌面按 Win + 空格只切换 Windows 输入法 | 这是 Windows 截获了本机快捷键。改为点击远程 GNOME 顶栏的输入源菜单切换。 |
| 候选词显示为繁体 | 按默认快捷键 **Ctrl + Shift + F** 切回简体中文。 |
| 输入源列表没有智能拼音 | 检查 `ibus-libpinyin` 是否安装；GNOME Tweaks 中启用“显示扩展输入源”后注销并重新登录，再回到“设置 → 键盘 → 输入源”添加。 |
| 输入法已切换，但中文显示为方框 | 安装中文字库并重新登录：`sudo apt install fonts-noto-cjk`。 |

若上述设置完成后仍不能输入中文，可在终端确认 IBus 引擎已安装并运行：

```bash
dpkg -l ibus ibus-libpinyin
ibus engine
ibus list-engine | grep -A1 -B1 'Intelligent Pinyin'
```

`ibus engine` 应显示 `libpinyin`（或当前选中的输入源）；引擎列表应包含 `libpinyin - Intelligent Pinyin`。安装或更新输入法引擎后，注销 GNOME 并重新登录再测试。系统界面语言是否为中文不会决定拼音输入源是否启用。

## 6. 配置代理和物理网卡隔离

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

如果这台机器已经启用了物理网卡隔离，再安装 GNOME 前先确认可以使用云厂商控制台。GNOME 可能安装并启动 NetworkManager；本仓库的隔离服务会在下次启动时把主机侧 veth 标记为 unmanaged，并安排在 NetworkManager 之前运行。安装完成后通过控制台重启，再按第 7 节确认隔离服务和路由正常。不要在没有备用控制台时从 SSH 直接重启 NetworkManager。

如果用户只需要隐藏桌面程序看到的真实出口网卡、继续使用直连网络，可以不启用代理；物理网卡隔离与 sing-box 代理是两个独立功能。如果要求桌面流量经代理，则还需要启用节点、TUN 和相应流量模式。

## 7. 重启后验证

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

## 8. 故障恢复

### 8.1 GNOME 无法启动，改回命令行启动

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

### 8.2 节点失联，离线关闭代理

无需访问 GitHub 即可通过 systemd 停止代理。此操作按当前网络规则恢复普通上游直连，流量会暴露服务器公网出口 IP：

```bash
sudo systemctl disable --now singbox-manager-telegram.timer 2>/dev/null || true
sudo systemctl stop singbox-manager-telegram.service 2>/dev/null || true
sudo systemctl disable --now sing-box.service
```

更多应急命令见[常用命令与离线应急恢复](常用命令与离线应急恢复.md)。

### 8.3 撤销物理网卡隔离

在本地或云厂商控制台使用管理菜单 **18**，或执行本机安装的恢复程序：

```bash
sudo /usr/bin/python3 /usr/local/lib/singbox-manager/network_isolation.py disable
```

该操作会把物理网卡、地址和默认路由恢复到主机，可能中断 SSH。不要只运行 `systemctl stop singbox-uplink-isolation.service`；该一次性服务没有 `ExecStop`，停服务不会自动把网卡移回主机。

## 参考资料

- [Debian GNOME 安装说明](https://wiki.debian.org/Gnome)
- [Debian 桌面环境与安装器选择说明](https://wiki.debian.org/DesktopEnvironment)
- [Debian `task-gnome-desktop` 稳定版软件包](https://packages.debian.org/stable/task-gnome-desktop)
- [Debian IBus 说明](https://wiki.debian.org/I18n/ibus)
- [Debian 中文输入法说明](https://wiki.debian.org/ChineseInputMethod)
- [Debian `ibus-libpinyin` 稳定版软件包](https://packages.debian.org/stable/ibus-libpinyin)
- [Debian GNOME 中文输入法说明](https://wiki.debian.org/gnome-chinese-input)
- [Debian GNOME 远程桌面设置说明](https://wiki.debian.org/RemoteDesktop/Gnome/Trixie)
- [GNOME 桌面共享帮助](https://help.gnome.org/gnome-help/sharing-desktop.html)
- [GNOME 远程登录帮助](https://help.gnome.org/gnome-help/remote-login.html)
- [Microsoft `mstsc` 命令说明](https://learn.microsoft.com/windows-server/administration/windows-commands/mstsc)
