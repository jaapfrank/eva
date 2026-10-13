$ErrorActionPreference = 'Stop'
# Execute the real implementation with all firewall cmdlets replaced by local
# test doubles. No administrative API is invoked.
$script:rule = $null
$script:writes = 0
function Get-NetFirewallHyperVRule { param($Name, $ErrorAction) return $script:rule }
function New-NetFirewallHyperVRule {
    param($Name,$DisplayName,$Direction,$VMCreatorId,$Protocol,$LocalAddresses,$LocalPorts,$RemoteAddresses,$Action,$Enabled)
    $script:writes++
    $script:rule = [pscustomobject]@{Name=$Name;Direction=$Direction;VMCreatorId=$VMCreatorId;Protocol=$Protocol;LocalAddresses=$LocalAddresses;LocalPorts=$LocalPorts;RemoteAddresses=$RemoteAddresses;Action=$Action;Enabled=$Enabled}
}
function Enable-NetFirewallHyperVRule { param($Name) $script:writes++; $script:rule.Enabled=$true }
function Disable-NetFirewallHyperVRule { param($Name) $script:writes++; $script:rule.Enabled=$false }
function Remove-NetFirewallHyperVRule { param($Name) $script:writes++; $script:rule=$null }
$source = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'network-route.ps1') -Raw
$implementation = [scriptblock]::Create(($source -replace '(?m)^#Requires -RunAsAdministrator\r?\n',''))
$testDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ('eva-firewall-test-' + [guid]::NewGuid())
[System.IO.Directory]::CreateDirectory($testDirectory) | Out-Null
$baseline = Join-Path $testDirectory 'baseline.json'
& $implementation -Action Plan | Out-Null
if ($script:writes -ne 0) { throw 'Plan mutated firewall state' }
& $implementation -Action Apply -BaselinePath $baseline | Out-Null
if ($script:writes -ne 1 -or @($script:rule.RemoteAddresses).Count -ne 2) { throw 'Wrong source binding' }
& $implementation -Action Rollback -BaselinePath $baseline | Out-Null
if ($null -ne $script:rule) { throw 'Rollback did not remove newly created rule' }
& $implementation -Action Apply -BaselinePath (Join-Path $testDirectory 'second.json') | Out-Null
$script:rule.Enabled=$false
& $implementation -Action Apply -BaselinePath (Join-Path $testDirectory 'disabled.json') | Out-Null
& $implementation -Action Rollback -BaselinePath (Join-Path $testDirectory 'disabled.json') | Out-Null
if ($script:rule.Enabled) { throw 'Rollback did not preserve disabled baseline' }
$script:rule.RemoteAddresses=@('0.0.0.0/0')
$previousWrites = $script:writes
$rejected=$false
try { & $implementation -Action Apply -BaselinePath (Join-Path $testDirectory 'wrong.json') | Out-Null } catch { $rejected=$true }
if (-not $rejected -or $script:writes -ne $previousWrites) { throw 'Unexpected existing rule was not rejected' }
Write-Output '4 firewall scenarios passed; real firewall calls: 0'
