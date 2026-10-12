# Network Scanner Windows Portable

这是 `networksweep.py` 的独立 Windows 便携包。它会自动寻找已连接的物理以太网接口，根据接口 IPv4 地址和子网掩码计算网段，然后执行 Ping、ARP 和 IPv6 NDP 扫描。

## 运行要求

- Windows 10、Windows 11 或 Windows Server
- Python 3.8 或更高版本
- Windows PowerShell 5.1
- Windows 内置命令：`ping.exe`、`arp.exe`
- PowerShell NetTCPIP/NetAdapter cmdlet：
  - `Get-NetAdapter`
  - `Get-NetIPAddress`
  - `Get-NetNeighbor`

Python 代码仅使用标准库，不需要安装第三方 Python 包。因此 `requirements.txt` 为空。

## 快速使用

先检查环境：

```powershell
powershell -ExecutionPolicy Bypass -File .\check_environment.ps1
```

自动扫描已连接的物理有线网段：

```powershell
.\scan.cmd
```

手动扫描指定网段：

```powershell
.\scan.cmd 192.168.1.0/24
.\scan.cmd 169.254.0.0/16
```

调整并发和超时：

```powershell
.\scan.cmd 192.168.1.0/24 --workers 96 --timeout 500
```

也可以直接运行：

```powershell
python .\networksweep.py
```

## 当前操作系统绑定

这个版本是 Windows 专用实现，绑定点不是 Python 本身，而是它调用的系统网络工具：

| 功能 | Windows 实现 |
|---|---|
| 自动识别物理有线网卡 | `Get-NetAdapter -Physical` |
| 读取接口 IP 和前缀 | `Get-NetIPAddress` |
| 读取 IPv6 邻居 | `Get-NetNeighbor` |
| 主动存活探测 | `ping.exe` 的 Windows 参数格式 |
| 读取 IPv4 ARP 缓存 | `arp.exe -a` |
| JSON 桥接 | Windows PowerShell 5.1 |

因此，将这个文件直接复制到 Linux 或 macOS 后不能原样运行。跨平台版本需要为每个系统实现独立后端，例如：

- Linux：`ip -json address`、`ip neigh`、Linux `ping`
- macOS：`ifconfig`、`arp -an`、macOS `ping`
- 更完整的实现可使用 `psutil` 枚举接口，但 ARP/NDP 和原始 ICMP 仍需要平台适配或额外权限

## 容器化结论

不建议把当前扫描器的自动有线发现功能放入普通 Docker 容器。

原因：

1. 容器默认使用虚拟网卡和 NAT，看不到宿主机物理网卡对象。
2. ARP、NDP、SADP、ONVIF 等二层或本地广播流量通常不会穿过 Docker NAT。
3. Windows 容器无法直接复用 Linux 容器的 `--network host` 行为。
4. 容器内的 PowerShell 看不到宿主机的 `Get-NetAdapter` 状态。
5. ICMP 或原始套接字扫描通常需要额外网络能力和管理员权限。

Linux 主机可以用 host network 和额外 capability 运行重新实现的 Linux 版，例如：

```text
docker run --network host --cap-add NET_RAW --cap-add NET_ADMIN ...
```

但这种方式仍然依赖 Linux 宿主机网络栈，并没有消除操作系统绑定。对于当前 Windows 直连 IPC、交换机或另一台 PC 的用途，直接在 Windows 宿主机运行更可靠。

## 迁移建议

### 在另一台 Windows PC 上

1. 复制整个文件夹。
2. 安装 Python 3.8 或更高版本，并加入 PATH。
3. 运行 `check_environment.ps1`。
4. 连接网线并确认网卡状态为 `Up`。
5. 运行 `scan.cmd`。

不需要创建虚拟环境，也不需要执行 `pip install`。

### 做成无需安装 Python 的版本

可以使用 PyInstaller 生成单文件 EXE：

```powershell
python -m pip install pyinstaller
pyinstaller --onefile --name network-scanner networksweep.py
```

生成的 EXE 仍依赖 Windows 的 `ping.exe`、`arp.exe` 和 PowerShell cmdlet，因此它只是免 Python 安装，并不是跨操作系统版本。

## 扫描边界

- 默认自动模式只扫描已连接的物理以太网接口，不扫描 WLAN。
- 普通网段扫描只能主动扫描电脑当前所在的 CIDR。
- 与电脑不在同一 IP 网段的设备可能无法通过 Ping/ARP 找到。
- 海康未激活设备、APIPA 设备或跨网段 IPC 应结合 SADP、ONVIF 或厂商发现协议。
- Windows 防火墙或目标设备禁用 ICMP 时，Ping 扫描可能漏报。
