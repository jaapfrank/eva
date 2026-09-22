#Requires -RunAsAdministrator
<##
One narrow Hyper-V firewall rule; no port proxy or broad inbound exception.
Baseline is captured on Apply, never during software preparation.
##>
[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Apply', 'Rollback')][string]$Action = 'Plan',
    [string]$BaselinePath
)
$ErrorActionPreference = 'Stop'
$ruleName = 'Eva2-ContinuousSpeaker-Devices'
$creator = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
$sources = @('10.0.20.202', '10.0.20.53')
function Assert-ExactRule($rule) {
    if ($null -eq $rule) { return }
    $actualSources = @($rule.RemoteAddresses | ForEach-Object { [string]$_ } | Sort-Object)
    if (@($rule).Count -ne 1 -or
        [string]$rule.VMCreatorId -ne $creator -or
        [string]$rule.Direction -ne 'Inbound' -or
        [string]$rule.Protocol -notin @('TCP', '6') -or
        [string]$rule.LocalAddresses -ne '10.0.20.46' -or
        [string]$rule.LocalPorts -ne '44715' -or
        ($actualSources -join ',') -ne (($sources | Sort-Object) -join ',') -or
        [string]$rule.Action -ne 'Allow') {
        throw 'Existing rule differs from the reviewed binding; nothing changed.'
    }
}
$existing = Get-NetFirewallHyperVRule -Name $ruleName -ErrorAction SilentlyContinue
Assert-ExactRule $existing
if ($Action -eq 'Plan') {
    [pscustomobject]@{ Name=$ruleName; LocalAddress='10.0.20.46'; Port=44715; Sources=$sources; Exists=($null -ne $existing) }
    return
}
if ([string]::IsNullOrWhiteSpace($BaselinePath)) { throw 'A private baseline path is required.' }
$resolvedBaseline = [System.IO.Path]::GetFullPath($BaselinePath)
if ($Action -eq 'Apply') {
    if (Test-Path -LiteralPath $resolvedBaseline) { throw 'Refusing to overwrite a firewall baseline.' }
    $baseline = @{ Name=$ruleName; Existed=($null -ne $existing); Enabled=($null -ne $existing -and [string]$existing.Enabled -eq 'True') }
    # Exclusive creation prevents losing a prior rollback record.
    $file = [System.IO.File]::Open($resolvedBaseline, [System.IO.FileMode]::CreateNew)
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($baseline | ConvertTo-Json))
        $file.Write($bytes, 0, $bytes.Length)
    } finally { $file.Dispose() }
    if ($null -eq $existing) {
        New-NetFirewallHyperVRule -Name $ruleName -DisplayName 'Eva2 continuous voice from two fixed speakers' `
            -Direction Inbound -VMCreatorId $creator -Protocol TCP `
            -LocalAddresses '10.0.20.46' -LocalPorts 44715 -RemoteAddresses $sources `
            -Action Allow -Enabled True | Out-Null
    } elseif ([string]$existing.Enabled -ne 'True') {
        Enable-NetFirewallHyperVRule -Name $ruleName
    }
    $actual = Get-NetFirewallHyperVRule -Name $ruleName
    Assert-ExactRule $actual
    if ([string]$actual.Enabled -ne 'True') { throw 'Rule was not enabled; retained baseline is available.' }
} else {
    $baseline = Get-Content -LiteralPath $resolvedBaseline -Raw | ConvertFrom-Json
    if ($baseline.Name -ne $ruleName -or $baseline.Existed -isnot [bool] -or $baseline.Enabled -isnot [bool]) {
        throw 'Firewall baseline is invalid.'
    }
    if (-not $baseline.Existed) {
        if ($null -ne $existing) { Remove-NetFirewallHyperVRule -Name $ruleName }
    } elseif ($null -eq $existing) {
        throw 'Previously existing rule disappeared; reconcile before rollback.'
    } elseif (-not $baseline.Enabled) {
        Disable-NetFirewallHyperVRule -Name $ruleName
    } elseif ([string]$existing.Enabled -ne 'True') {
        Enable-NetFirewallHyperVRule -Name $ruleName
    }
    $restored = Get-NetFirewallHyperVRule -Name $ruleName -ErrorAction SilentlyContinue
    if (-not $baseline.Existed) {
        if ($null -ne $restored) { throw 'Rollback did not remove the new rule.' }
    } else {
        Assert-ExactRule $restored
        if ($null -eq $restored -or ([string]$restored.Enabled -eq 'True') -ne $baseline.Enabled) {
            throw 'Rollback readback does not match the captured rule state.'
        }
    }
}
[pscustomobject]@{ Action=$Action; Name=$ruleName; Baseline=$resolvedBaseline }
