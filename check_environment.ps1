$ErrorActionPreference = 'Stop'

Write-Host '=== Network Scanner Environment Check ==='

$python = Get-Command py.exe, python.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($python) {
    Write-Host "[OK] Python command: $($python.Source)"
    & $python.Source --version
} else {
    Write-Host '[FAIL] Python 3.8 or newer was not found in PATH.'
}

foreach ($command in 'ping.exe', 'arp.exe', 'powershell.exe') {
    $resolved = Get-Command $command -ErrorAction SilentlyContinue
    if ($resolved) {
        Write-Host "[OK] $command -> $($resolved.Source)"
    } else {
        Write-Host "[FAIL] Missing required command: $command"
    }
}

$requiredCmdlets = 'Get-NetAdapter', 'Get-NetIPAddress', 'Get-NetNeighbor'
foreach ($cmdlet in $requiredCmdlets) {
    if (Get-Command $cmdlet -ErrorAction SilentlyContinue) {
        Write-Host "[OK] PowerShell cmdlet: $cmdlet"
    } else {
        Write-Host "[FAIL] Missing PowerShell cmdlet: $cmdlet"
    }
}

Write-Host ''
Write-Host 'Connected physical Ethernet adapters:'
Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Status -eq 'Up' -and [int]$_.NdisPhysicalMedium -eq 14
    } |
    Select-Object Name, InterfaceDescription, Status, LinkSpeed, ifIndex |
    Format-Table -AutoSize

Write-Host ''
Write-Host 'Run scan.cmd to start the automatic wired-network scan.'
