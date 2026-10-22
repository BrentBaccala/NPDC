# PowerShell port of query-instances, for Microsoft Windows
#
# Arguments are either switches (start with '-') passed through to aws,
# or additional queries: NAME:EXPR, or a bare FIELD meaning FIELD:FIELD
#
# Switches that take a value must use '=' (--region=us-east-1), or the
# value will be taken as a query.
#
# Example:
#   .\query-instances.ps1 --output=table LaunchTime "AZ:Placement.AvailabilityZone"

# Find aws.exe: use PATH if it's there, otherwise the MSI install locations

$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) {
    $candidates = @(
        "$env:ProgramFiles\Amazon\AWSCLIV2\aws.exe"
        "${env:ProgramFiles(x86)}\Amazon\AWSCLIV2\aws.exe"
        "$env:ProgramFiles\Amazon\AWSCLI\bin\aws.exe"
        "$env:LOCALAPPDATA\Programs\Amazon\AWSCLIV2\aws.exe"
    )
    $aws = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $aws) {
    Write-Error "aws.exe not found in PATH or the standard install locations"
    exit 1
}

# Start with our standard queries

$switches = @()
$queries = @(
    'InstanceId:InstanceId'
    'InstanceType:InstanceType'
    'PrivateIpAddress:PrivateIpAddress'
    'PublicIpAddress:PublicIpAddress'
    'State:State.Name'
    "Name:(Tags[?Key=='Name'].Value | [0])"
)

foreach ($arg in $args) {
    $arg = "$arg"
    if ($arg -like '-*')      { $switches += $arg }
    elseif ($arg -like '*:*') { $queries  += $arg }
    else                      { $queries  += "${arg}:${arg}" }
}

# Queries are separated by commas

$query = 'Reservations[].Instances[].{' + ($queries -join ',') + '}'

# AWS CLI v2 pipes output through 'more' on Windows; turn that off.
# (Setting $env:AWS_PAGER = '' doesn't work: PowerShell deletes an
# environment variable assigned the empty string.)

& $aws --no-cli-pager @switches ec2 describe-instances "--query=$query"
