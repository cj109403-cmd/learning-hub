# Run once as the current user; never changes machine-wide policy or settings.
$ErrorActionPreference = 'Stop'
# Do not let the modern py manager download a missing runtime during detection.
$env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
Add-Type -AssemblyName System.Windows.Forms
try {
    $root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    if (-not (Test-Path -LiteralPath (Join-Path $root 'content\course.yaml'))) {
        throw '请先完整解压分享包，再运行 app\windows\install.ps1。'
    }
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
    if (-not $pythonw) { throw '没有找到 Python 3.9 或更高版本。请先安装 Python（包含 py 启动器），或创建 conda learn 环境，再重新安装快捷方式。' }
    $shell = New-Object -ComObject WScript.Shell
    $places = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
    foreach ($place in $places) {
        $shortcut = $shell.CreateShortcut((Join-Path $place '学习台.lnk'))
        $shortcut.TargetPath = $pythonw
        $shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'launch.pyw') + '"'
        $shortcut.WorkingDirectory = $root
        $shortcut.IconLocation = (Join-Path $root 'app\assets\icons\orange\learning-hub.ico') + ',0'
        $shortcut.Description = '打开本地学习台'
        $shortcut.WindowStyle = 7
        $shortcut.Save()
    }
    [System.Windows.Forms.MessageBox]::Show('已创建桌面和开始菜单快捷方式“学习台”。以后双击快捷方式即可。请保留当前项目文件夹；搬家后重新运行本脚本。', '安装完成') | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, '学习台安装未完成', 'OK', 'Error') | Out-Null
    exit 1
}
