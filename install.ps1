# Handoff installer for Windows (PowerShell 5.1 or later).
#
#   Install or upgrade:
#     powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/install.ps1 | iex"
#   Uninstall (your memory database is kept):
#     powershell -ExecutionPolicy ByPass -c "$env:HANDOFF_UNINSTALL=1; irm https://raw.githubusercontent.com/kdev1966/Handoff-CLI/main/install.ps1 | iex"
#
# Environment variables:
#   HANDOFF_VERSION=v0.1.0     tag or branch to install (default: main)
#   HANDOFF_SOURCE=<pip spec>  install from this pip requirement instead (a folder, a wheel...)
#   HANDOFF_NO_SETUP=1         do not register the MCP server in Claude Code
#   HANDOFF_NO_MODIFY_PATH=1   do not add Handoff to your user PATH
#   HANDOFF_UNINSTALL=1        uninstall instead of installing
#
# Everything lives in a private virtual environment under %LOCALAPPDATA%\handoff;
# administrator rights are never needed.

function Invoke-HandoffInstaller {
    $ErrorActionPreference = 'Stop'  # scoped to this function: the caller's session is not changed
    $repo = 'kdev1966/Handoff-CLI'
    $root = Join-Path $env:LOCALAPPDATA 'handoff'
    $venvDir = Join-Path $root 'venv'
    $binDir = Join-Path $root 'bin'
    $venvHandoff = Join-Path $venvDir 'Scripts\handoff.exe'
    $binHandoff = Join-Path $binDir 'handoff.exe'

    function Find-Python {
        foreach ($candidate in @('py -3', 'python', 'python3')) {
            $parts = $candidate.Split(' ')
            if (-not (Get-Command $parts[0] -CommandType Application -ErrorAction SilentlyContinue)) { continue }
            $extra = @($parts | Select-Object -Skip 1)
            try {
                $exe = & $parts[0] @extra -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
            } catch {
                continue
            }
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        }
        return $null
    }

    function Get-UserPath {
        # Read the raw value so %VARIABLES% in the user PATH are preserved.
        $key = Get-Item 'HKCU:\Environment'
        return [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
    }

    function Set-UserPath([string]$value) {
        Set-ItemProperty -Path 'HKCU:\Environment' -Name 'Path' -Value $value -Type ExpandString
        # Setting a variable through .NET broadcasts the change to new terminals.
        [Environment]::SetEnvironmentVariable('HANDOFF_PATH_REFRESH', '1', 'User')
        [Environment]::SetEnvironmentVariable('HANDOFF_PATH_REFRESH', $null, 'User')
    }

    function Install-Handoff {
        $python = Find-Python
        if (-not $python) {
            throw 'Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ (or: winget install Python.Python.3.13), then run this installer again.'
        }
        $version = if ($env:HANDOFF_VERSION) { $env:HANDOFF_VERSION } else { 'main' }
        $source = if ($env:HANDOFF_SOURCE) { $env:HANDOFF_SOURCE } else { "https://github.com/$repo/archive/$version.tar.gz" }
        Write-Host "Installing Handoff with $(& $python --version) from $source"

        New-Item -ItemType Directory -Force -Path $root | Out-Null
        & $python -m venv --clear $venvDir
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the virtual environment in $venvDir. If an AI client is running Handoff, close it and run the installer again."
        }
        & (Join-Path $venvDir 'Scripts\python.exe') -m pip install --quiet --disable-pip-version-check $source
        if ($LASTEXITCODE -ne 0) { throw "pip could not install $source" }

        # Only handoff.exe is exposed on PATH, not the environment's python.exe and pip.exe.
        New-Item -ItemType Directory -Force -Path $binDir | Out-Null
        Copy-Item -Force $venvHandoff $binHandoff
        Write-Host "Installed $(& $binHandoff --version) in $binHandoff"

        if (-not $env:HANDOFF_NO_SETUP -and (Get-Command claude -ErrorAction SilentlyContinue)) {
            & $binHandoff setup
            if ($LASTEXITCODE -ne 0) { Write-Host "Run 'handoff setup' later to register Handoff in Claude Code." }
        } else {
            Write-Host "To connect an MCP client, run 'handoff setup' (Claude Code) or 'handoff setup --print-config'."
        }

        $userPath = Get-UserPath
        if (($userPath -split ';') -notcontains $binDir) {
            if ($env:HANDOFF_NO_MODIFY_PATH) {
                Write-Host "Add $binDir to your PATH to use the 'handoff' command."
            } else {
                Set-UserPath ((@($userPath.TrimEnd(';'), $binDir) | Where-Object { $_ }) -join ';')
                Write-Host "Added $binDir to your user PATH. Open a new terminal to use the 'handoff' command."
            }
        }
        $env:Path = "$env:Path;$binDir"
        Write-Host 'Done.'
    }

    function Uninstall-Handoff {
        $database = $null
        if (Test-Path $venvHandoff) {
            # Read all the output before filtering: stopping the pipeline early would close
            # the command's stdout while it is still writing.
            $output = @(& $venvHandoff where 2>$null)
            $line = $output | Where-Object { $_ -like 'Database:*' } | Select-Object -First 1
            if ($line) { $database = $line -replace '^Database:\s*', '' }
            if (Get-Command claude -ErrorAction SilentlyContinue) { & $venvHandoff setup --remove }
        }
        foreach ($dir in @($venvDir, $binDir)) {
            if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
        }
        $userPath = Get-UserPath
        $entries = @($userPath -split ';' | Where-Object { $_ -and $_ -ne $binDir })
        if ($entries.Count -ne @($userPath -split ';' | Where-Object { $_ }).Count) {
            Set-UserPath ($entries -join ';')
            Write-Host "Removed $binDir from your user PATH."
        }
        $kept = if ($database) { " in $database" } else { '' }
        Write-Host "Handoff is uninstalled. Your memory database was kept$kept; delete it by hand to erase it."
    }

    if ($env:HANDOFF_UNINSTALL) { Uninstall-Handoff } else { Install-Handoff }
}

Invoke-HandoffInstaller
