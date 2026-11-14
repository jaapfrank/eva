#Requires -RunAsAdministrator
<##
Allow only devices on the household LAN (10.0.20.0/24, such as Chromecasts and UniFi screens)
to reach Eva2's scoped HoloStage port (44712).
Does not expose Core (44710) or Owner Cockpit (44711).
Run with -Remove to remove this exact rule after checking its binding.
##>
[CmdletBinding()]
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$ruleName = 'Eva2-HoloStage-LAN'
$creator = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
$existing = Get-NetFirewallHyperVRule -Name $ruleName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    if (@($existing).Count -ne 1 -or
        [string]$existing.VMCreatorId -ne $creator -or
        [string]$existing.Direction -ne 'Inbound' -or
        [string]$existing.Protocol -notin @('TCP', '6') -or
        [string]$existing.LocalAddresses -ne '10.0.20.46' -or
        [string]$existing.LocalPorts -ne '44712' -or
        [string]$existing.RemoteAddresses -ne '10.0.20.0/24' -or
        [string]$existing.Action -ne 'Allow') {
        throw 'An existing rule with this name has a different binding; nothing changed.'
    }
}
if ($Remove) {
    if ($null -ne $existing) {
        Remove-NetFirewallHyperVRule -Name $ruleName
    }
    Write-Output 'Eva2 HoloStage LAN firewall rule removed.'
    return
}
if ($null -eq $existing) {
    New-NetFirewallHyperVRule -Name $ruleName `
        -DisplayName 'Eva2 HoloStage from Home LAN only (Chromecasts & Screens)' `
        -Direction Inbound -VMCreatorId $creator -Protocol TCP `
        -LocalAddresses '10.0.20.46' -LocalPorts 44712 `
        -RemoteAddresses '10.0.20.0/24' -Action Allow -Enabled True | Out-Null
} elseif ([string]$existing.Enabled -ne 'True') {
    Enable-NetFirewallHyperVRule -Name $ruleName
}
Get-NetFirewallHyperVRule -Name $ruleName |
    Select-Object Name, Enabled, Direction, LocalAddresses, LocalPorts, RemoteAddresses, Action
