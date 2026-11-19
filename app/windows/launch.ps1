# PowerShell fallback. Installed shortcuts invoke pythonw directly.
$ErrorActionPreference = 'Stop'
# Do not let the modern py manager download a missing runtime during detection.
$env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
Add-Type -AssemblyName System.Windows.Forms
try {
    $root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $pythonw = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $result = & py -3 -c 'import sys; sys.exit(1) if sys.version_info < (3,9) else print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $result) {
                $candidate = Join-Path (Split-Path ([string]$result) -Parent) 'pythonw.exe'
                if (Test-Path -LiteralPath $candidate) { $pythonw = $candidate }
            }
        } catch { $pythonw = $null }
    }
    if (-not $pythonw) {
        foreach ($base in @('miniforge3','miniconda3','anaconda3','mambaforge')) {
            $candidate = Join-Path $env:USERPROFILE "$base\envs\learn\pythonw.exe"
            $console = Join-Path $env:USERPROFILE "$base\envs\learn\python.exe"
            if (Test-Path -LiteralPath $candidate) {
                & $console -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>$null
                if ($LASTEXITCODE -eq 0) { $pythonw = $candidate; break }
            }
        }
    }
    if (-not $pythonw) {
        $envFile = Join-Path $env:USERPROFILE '.conda\environments.txt'
        if (Test-Path -LiteralPath $envFile) {
            foreach ($envPath in (Get-Content -LiteralPath $envFile -Encoding UTF8)) {
                $envPath = $envPath.Trim()
                if (-not $envPath) { continue }
                if ((Split-Path $envPath -Leaf) -eq 'learn') {
                    $candidate = Join-Path $envPath 'pythonw.exe'
                    $console = Join-Path $envPath 'python.exe'
                    if ((Test-Path -LiteralPath $candidate) -and (Test-Path -LiteralPath $console)) {
                        & $console -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>$null
                        if ($LASTEXITCODE -eq 0) { $pythonw = $candidate; break }
                    }
                }
            }
        }
    }
    if (-not $pythonw) { throw '没有找到 Python 3.9 或更高版本。请先安装 Python（包含 py 启动器），或创建 conda learn 环境，再重新运行。' }
    $script = Join-Path $PSScriptRoot 'launch.pyw'
    Start-Process -FilePath $pythonw -ArgumentList ('"' + $script + '"') -WorkingDirectory $root -WindowStyle Hidden
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '学习台无法打开', 'OK', 'Error') | Out-Null
    exit 1
}
