import argparse
import concurrent.futures
import ipaddress
import json
import os
import re
import subprocess
import time


ARP_PATTERN = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"([0-9a-f]{2}(?:[:-][0-9a-f]{2}){5})\s+",
    re.IGNORECASE,
)

MAC_PATTERN = re.compile(
    r"^[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}$",
    re.IGNORECASE,
)

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def ping_address(ip, timeout_ms, family, interface_index=None):
    """Ping IPv4 or IPv6 address."""
    target = ip

    if family == 6:
        address = ipaddress.ip_address(ip)
        if address.is_link_local and interface_index is not None:
            target = f"{ip}%{interface_index}"

    try:
        result = subprocess.run(
            [
                "ping.exe",
                f"-{family}",
                "-n",
                "1",
                "-w",
                str(timeout_ms),
                target,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(2, timeout_ms / 1000 + 1),
            creationflags=CREATE_NO_WINDOW,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def read_arp_table(network):
    """Read the Windows IPv4 ARP table."""
    try:
        result = subprocess.run(
            ["arp.exe", "-a"],
            capture_output=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}

    devices = {}

    for line in result.stdout.splitlines():
        match = ARP_PATTERN.search(line)
        if not match:
            continue

        ip_text = match.group(1)
        mac = match.group(2).replace("-", ":").upper()

        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue

        if ip not in network:
            continue
        if ip in (network.network_address, network.broadcast_address):
            continue
        if mac == "FF:FF:FF:FF:FF:FF":
            continue
        if int(mac[:2], 16) & 1:
            continue

        devices[ip_text] = mac

    return devices


def powershell_json(command):
    """Run Windows PowerShell and decode its JSON output."""
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "[Console]::OutputEncoding = "
                    "[System.Text.Encoding]::UTF8; "
                    + command
                ),
            ],
            capture_output=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []

    output = result.stdout.strip()
    if result.returncode != 0 or not output:
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []

    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def find_wired_ipv4_networks():
    """Find IPv4 networks on connected physical Ethernet adapters."""
    command = (
        "$wired = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | "
        "Where-Object {$_.Status -eq 'Up' -and "
        "[int]$_.NdisPhysicalMedium -eq 14}; "
        "$addresses = @(foreach ($adapter in $wired) { "
        "Get-NetIPAddress -InterfaceIndex $adapter.ifIndex "
        "-AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Where-Object {$_.AddressState -eq 'Preferred'} | "
        "Select-Object IPAddress,PrefixLength,InterfaceIndex,"
        "@{Name='InterfaceAlias';Expression={$adapter.Name}} "
        "}); $addresses | ConvertTo-Json -Compress"
    )
    networks = {}

    for row in powershell_json(command):
        try:
            address = ipaddress.ip_address(str(row["IPAddress"]))
            prefix_length = int(row["PrefixLength"])
            interface_index = int(row["InterfaceIndex"])
        except (ValueError, TypeError, KeyError):
            continue

        if address.version != 4 or address.is_loopback or address.is_unspecified:
            continue

        network = ipaddress.ip_network(
            f"{address}/{prefix_length}", strict=False
        )
        key = str(network)

        if key not in networks:
            networks[key] = {
                "network": network,
                "address": str(address),
                "interface_index": interface_index,
                "interface_alias": str(row.get("InterfaceAlias", "")),
            }

    return sorted(
        networks.values(),
        key=lambda item: (
            item["interface_index"],
            int(item["network"].network_address),
        ),
    )


def find_ipv6_interfaces(network):
    command = (
        "Get-NetIPAddress -AddressFamily IPv6 "
        "-ErrorAction SilentlyContinue | "
        "Where-Object {$_.AddressState -ne 'Tentative'} | "
        "Select-Object IPAddress,InterfaceIndex | "
        "ConvertTo-Json -Compress"
    )
    interfaces = set()

    for row in powershell_json(command):
        ip_text = str(row.get("IPAddress", "")).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_text)
            interface_index = int(row["InterfaceIndex"])
        except (ValueError, TypeError, KeyError):
            continue
        if ip in network:
            interfaces.add(interface_index)

    return sorted(interfaces)


def stimulate_ipv6_neighbors(interface_indexes, timeout_ms):
    for interface_index in interface_indexes:
        target = f"ff02::1%{interface_index}"
        try:
            subprocess.run(
                ["ping.exe", "-6", "-n", "1", "-w", str(timeout_ms), target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(2, timeout_ms / 1000 + 1),
                creationflags=CREATE_NO_WINDOW,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass


def read_ipv6_neighbors(network, interface_indexes=None):
    command = (
        "Get-NetNeighbor -AddressFamily IPv6 "
        "-ErrorAction SilentlyContinue | "
        "Select-Object IPAddress,LinkLayerAddress,"
        "@{Name='State';Expression={$_.State.ToString()}},"
        "InterfaceIndex | ConvertTo-Json -Compress"
    )
    allowed_interfaces = set(interface_indexes) if interface_indexes else None
    devices = {}

    for row in powershell_json(command):
        ip_text = str(row.get("IPAddress", "")).split("%", 1)[0]
        mac = str(row.get("LinkLayerAddress", "")).upper().replace("-", ":")
        state = str(row.get("State", ""))

        try:
            ip = ipaddress.ip_address(ip_text)
            interface_index = int(row["InterfaceIndex"])
        except (ValueError, TypeError, KeyError):
            continue

        if ip.version != 6 or ip not in network:
            continue
        if allowed_interfaces and interface_index not in allowed_interfaces:
            continue
        if ip.is_multicast or ip.is_unspecified or ip.is_loopback:
            continue
        if not MAC_PATTERN.fullmatch(mac):
            mac = "N/A"
        elif int(mac[:2], 16) & 1:
            continue

        devices[(ip_text, interface_index)] = {"mac": mac, "state": state}

    return devices


def scan_ipv4(network, workers, timeout_ms, max_hosts):
    if network.num_addresses > max_hosts:
        raise SystemExit(
            f"IPv4 scan range is too large; limit it to {max_hosts} addresses"
        )

    hosts = [str(ip) for ip in network.hosts()]
    worker_count = max(1, min(workers, 128, len(hosts)))
    ping_results = {}

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=worker_count
    ) as executor:
        tasks = {
            executor.submit(ping_address, ip, timeout_ms, 4): ip
            for ip in hosts
        }
        for task in concurrent.futures.as_completed(tasks):
            ip = tasks[task]
            try:
                ping_results[ip] = task.result()
            except Exception:
                ping_results[ip] = False

    arp_devices = read_arp_table(network)
    discovered = set(arp_devices)
    discovered.update(ip for ip, responded in ping_results.items() if responded)
    discovered = sorted(discovered, key=ipaddress.ip_address)

    print(f"\nFound {len(discovered)} IPv4 device(s)\n")
    print(f"{'IP address':<18} {'MAC address':<20} {'Status'}")
    print("-" * 60)

    for ip in discovered:
        mac = arp_devices.get(ip, "N/A")
        status = "Ping response" if ping_results.get(ip) else "ARP discovery"
        print(f"{ip:<18} {mac:<20} {status}")

    return len(discovered)


def scan_ipv6(network, workers, timeout_ms, max_hosts, selected_interface):
    interface_indexes = (
        [selected_interface]
        if selected_interface is not None
        else find_ipv6_interfaces(network)
    )

    if interface_indexes:
        print("IPv6 interface indexes: " + ", ".join(map(str, interface_indexes)))
        stimulate_ipv6_neighbors(interface_indexes, timeout_ms)
    else:
        print("Warning: no matching IPv6 interface was found.")

    ping_results = {}
    active_scan = network.num_addresses <= max_hosts

    if active_scan:
        hosts = [str(ip) for ip in network.hosts()]
        targets = []

        if network.network_address.is_link_local:
            if interface_indexes:
                targets = [
                    (ip, interface_index)
                    for ip in hosts
                    for interface_index in interface_indexes
                ]
        else:
            targets = [(ip, None) for ip in hosts]

        if targets:
            worker_count = max(1, min(workers, 128, len(targets)))
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=worker_count
            ) as executor:
                tasks = {
                    executor.submit(
                        ping_address, ip, timeout_ms, 6, interface_index
                    ): (ip, interface_index)
                    for ip, interface_index in targets
                }
                for task in concurrent.futures.as_completed(tasks):
                    target = tasks[task]
                    try:
                        ping_results[target] = task.result()
                    except Exception:
                        ping_results[target] = False
    else:
        print("IPv6 range is too large for per-address Ping; using NDP only.")

    neighbors = read_ipv6_neighbors(network, interface_indexes or None)
    responded_ips = {
        ip for (ip, _), responded in ping_results.items() if responded
    }
    known_ips = {ip for ip, _ in neighbors}

    for (ip, interface_index), responded in ping_results.items():
        if responded and ip not in known_ips:
            neighbors[(ip, interface_index or 0)] = {"mac": "N/A", "state": ""}

    discovered = sorted(
        neighbors.items(),
        key=lambda item: (
            int(ipaddress.ip_address(item[0][0])),
            item[0][1],
        ),
    )

    print(f"\nFound {len(discovered)} IPv6 device(s)\n")
    print(f"{'IPv6 address':<42} {'MAC address':<20} {'Interface':<10} Status")
    print("-" * 90)

    for (ip, interface_index), information in discovered:
        status = (
            "Ping response"
            if ip in responded_ips
            else f"NDP {information['state']}".strip()
        )
        print(
            f"{ip:<42} {information['mac']:<20} "
            f"{interface_index or '-':<10} {status}"
        )

    return len(discovered)


def scan_network(network, args):
    print("=" * 65)
    print(f"Network: {network}")
    print(f"Protocol: IPv{network.version}")
    print("=" * 65)

    if network.version == 4:
        return scan_ipv4(network, args.workers, args.timeout, args.max_hosts)
    return scan_ipv6(
        network,
        args.workers,
        args.timeout,
        args.max_hosts,
        args.interface,
    )


def main():
    if os.name != "nt":
        raise SystemExit("This package currently supports Windows only.")

    parser = argparse.ArgumentParser(
        description=(
            "Scan IPv4 networks on connected physical Ethernet adapters, "
            "or scan a manually specified IPv4/IPv6 CIDR"
        )
    )
    parser.add_argument("network", nargs="?", help="Optional IPv4/IPv6 CIDR")
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--timeout", type=int, default=800)
    parser.add_argument("--max-hosts", type=int, default=4096)
    parser.add_argument("--interface", type=int, help="IPv6 interface index")
    args = parser.parse_args()

    if args.network:
        try:
            networks = [ipaddress.ip_network(args.network, strict=False)]
        except ValueError as error:
            raise SystemExit(f"Invalid network: {error}")
    else:
        wired_networks = find_wired_ipv4_networks()
        if not wired_networks:
            raise SystemExit(
                "No connected physical Ethernet adapter with an IPv4 address was found."
            )

        print("Detected wired networks:")
        for information in wired_networks:
            print(
                f"  {information['interface_alias']} "
                f"(index {information['interface_index']}): "
                f"local {information['address']}, "
                f"network {information['network']}"
            )
        networks = [item["network"] for item in wired_networks]

    started = time.perf_counter()
    count = sum(scan_network(network, args) for network in networks)
    elapsed = time.perf_counter() - started
    print(f"\nFinished: found {count} device(s) in {elapsed:.1f} seconds")

    if count == 0:
        print("No devices found. Check the subnet, firewall, and adapter state.")


if __name__ == "__main__":
    main()
