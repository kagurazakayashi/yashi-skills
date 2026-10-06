<#
.SYNOPSIS
识别对本机 HTTPS 做中间人替换的根证书（杀毒软件的加密连接扫描、企业代理等），
并把该根证书加入 Node 的信任列表，从而在保留证书校验的前提下修复 TLS 失败。

.DESCRIPTION
Node 使用内置 CA 列表，不认识本地拦截方的根证书，因此报
SELF_SIGNED_CERT_IN_CHAIN / UNABLE_TO_VERIFY_LEAF_SIGNATURE 之类的错误。
本脚本沿证书链走到自签根，并与 Node 的内置 CA 列表逐一比对：
只有「Node 不认识它」才说明它就是需要加入的那个根，避免盲目导入。

默认只做检测。加 -Export 导出 PEM，加 -Apply 写用户级环境变量，加 -Verify 实测验证。

.PARAMETER TargetHost
目标主机名，例如 api.deepseek.com。不要用 -Host，那是 PowerShell 的自动变量。

.PARAMETER PemPath
PEM 的写出位置。默认 %USERPROFILE%\.agent-certs\intercept-root-ca.pem。
不要放在 %TEMP%，那里会被清理，环境变量随之失效。

.PARAMETER Runs
-Verify 时每个主机的探测次数，默认 12。定位间歇性拦截必须跑多次。

.EXAMPLE
# 只看是否存在中间人，不修改任何东西
.\export-intercept-ca.ps1 -TargetHost api.deepseek.com

.EXAMPLE
# 导出根证书到指定位置
.\export-intercept-ca.ps1 -TargetHost api.deepseek.com -Export -PemPath "$env:USERPROFILE\.agent-certs\my-ca.pem"

.EXAMPLE
# 导出、写环境变量并实测验证（一步到位）
.\export-intercept-ca.ps1 -TargetHost api.deepseek.com -Export -Apply -Verify

.NOTES
退出码: 0 = 无中间人，或已修复并验证通过; 1 = 存在中间人但未修复; 2 = 参数或运行错误。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetHost,

    [int]$Port = 443,

    [string]$PemPath,

    [switch]$Export,

    [switch]$Apply,

    [switch]$Verify,

    [int]$Runs = 12,

    [int]$TimeoutMs = 15000
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Text) { Write-Host "`n== $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "   $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "   $Text" -ForegroundColor Yellow }

if (-not $PemPath) {
    $PemPath = Join-Path $env:USERPROFILE '.agent-certs\intercept-root-ca.pem'
}

# 取回对端证书链。RemoteCertificateValidationCallback 一律接受，
# 因为这里的目的正是要看到「被替换后的那张证书」，而不是判断它是否可信。
function Get-RemoteChain {
    param([string]$Name, [int]$PortNumber, [int]$Timeout)

    $tcp = New-Object System.Net.Sockets.TcpClient
    $task = $tcp.ConnectAsync($Name, $PortNumber)
    if (-not $task.Wait($Timeout)) {
        $tcp.Close()
        throw "连接 $Name`:$PortNumber 超时（$Timeout ms）"
    }

    $callback = [System.Net.Security.RemoteCertificateValidationCallback] { param($s, $c, $ch, $e) $true }
    $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false, $callback)
    try {
        $ssl.AuthenticateAsClient($Name)
        $leaf = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)

        $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
        $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $chain.ChainPolicy.VerificationFlags = [System.Security.Cryptography.X509Certificates.X509VerificationFlags]::AllowUnknownCertificateAuthority
        $null = $chain.Build($leaf)

        $elements = @()
        foreach ($el in $chain.ChainElements) { $elements += $el.Certificate }
        if ($elements.Count -eq 0) { $elements = @($leaf) }

        return [pscustomobject]@{ Leaf = $leaf; Elements = $elements; ChainOk = $elements.Count -gt 1 }
    } finally {
        $ssl.Close()
        $tcp.Close()
    }
}

# Node 内置 CA 列表的 SHA-256 指纹集合，用来回答「Node 是否已经信任这个根」。
# 走同级目录的 node-trusted-roots.mjs，而不是 node -e：Windows PowerShell 5.1
# 在向原生程序传含引号的参数时会吞掉引号，-e 形式的代码会被破坏。
function Get-NodeTrustedFingerprints {
    $node = (Get-Command node -ErrorAction SilentlyContinue).Source
    if (-not $node) { return $null }
    $helper = Join-Path $PSScriptRoot 'node-trusted-roots.mjs'
    if (-not (Test-Path -LiteralPath $helper)) {
        Write-Warn2 "找不到 $helper，跳过 Node 信任核对。"
        return $null
    }
    return @(& $node $helper)
}

# 读取一个 PEM 中每张证书的 SHA-256 指纹。同样交给 Node 解析，
# 避免 PowerShell 5.1 下 X509Certificate2 的构造重载问题。
function Get-PemFingerprints([string]$Path) {
    $node = (Get-Command node -ErrorAction SilentlyContinue).Source
    if (-not $node) { return @() }
    $helper = Join-Path $PSScriptRoot 'node-trusted-roots.mjs'
    if (-not (Test-Path -LiteralPath $helper)) { return @() }
    return @(& $node $helper --fingerprint $Path 2>$null)
}

# 本机 Node 是否支持 --use-system-ca（Node 22.15+ / 23.2+ 起提供）。
# 该标志让 Node 使用操作系统信任存储，是从另一条路修复同一问题的办法。
function Test-UseSystemCaSupport {
    $node = (Get-Command node -ErrorAction SilentlyContinue).Source
    if (-not $node) { return $false }
    $null = & $node --use-system-ca --version 2>&1
    return ($LASTEXITCODE -eq 0)
}

function Get-Sha256Hex([byte[]]$Bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return (($sha.ComputeHash($Bytes)) | ForEach-Object { $_.ToString('x2') }) -join '' }
    finally { $sha.Dispose() }
}

try {
    Write-Step "1. 取回 $TargetHost`:$Port 的证书链（系统信任存储视角）"
    $result = Get-RemoteChain -Name $TargetHost -PortNumber $Port -Timeout $TimeoutMs
    $leaf = $result.Leaf
    $root = $result.Elements[$result.Elements.Count - 1]

    Write-Host "   叶子: $($leaf.Subject)"
    Write-Host "         签发者 $($leaf.Issuer)"
    if ($result.Elements.Count -gt 2) {
        Write-Host "   链上一共 $($result.Elements.Count) 级:"
        foreach ($el in $result.Elements) { Write-Host "     - $($el.Subject)" }
    }
    Write-Host "   根  : $($root.Subject)"
    Write-Host "         指纹(SHA-1) $($root.Thumbprint)"
    Write-Host "         有效期至    $($root.NotAfter)"

    $selfSigned = ($root.Subject -eq $root.Issuer)
    if (-not $selfSigned) {
        Write-Warn2 "链顶不是自签根，可能只拿到了部分链。请人工核对后再导出。"
    }

    Write-Step '2. 核对 Node 是否已经信任这个根'
    $rootSha = Get-Sha256Hex -Bytes $root.RawData
    Write-Host "   根的 SHA-256: $rootSha"
    $nodeRoots = Get-NodeTrustedFingerprints
    if ($null -eq $nodeRoots) {
        Write-Warn2 '找不到 node 命令，跳过该检查。请自行确认 Node 是否信任上面的根。'
        $nodeTrusts = $null
    } else {
        Write-Host "   Node 内置 CA 数量: $($nodeRoots.Count)"
        $nodeTrusts = $nodeRoots -contains $rootSha
        if ($nodeTrusts) {
            Write-Ok 'Node 已经信任这个根 —— 说明对手不是证书信任问题，请回到诊断第 1 步重新判断。'
        } else {
            Write-Warn2 'Node 不信任这个根 —— 这就是 TLS 校验失败的原因。'
            if (Test-UseSystemCaSupport) {
                Write-Host '   备选方案: 本机 Node 支持 --use-system-ca，' -ForegroundColor DarkGray
                Write-Host '             可用 NODE_OPTIONS=--use-system-ca 让它直接采用系统信任存储，免去维护 PEM。' -ForegroundColor DarkGray
            }
        }
    }

    if ($nodeTrusts -eq $true) { exit 0 }

    # 常见误判来源：当前 shell 已经继承了 NODE_EXTRA_CA_CERTS，
    # 于是探测看着一切正常，但排查者以为「没问题」。这里显式指出。
    $alreadyFixed = $false
    $envPem = $env:NODE_EXTRA_CA_CERTS
    if ($envPem -and (Test-Path -LiteralPath $envPem)) {
        Write-Host "   当前进程已设 NODE_EXTRA_CA_CERTS = $envPem"
        if ((Get-PemFingerprints -Path $envPem) -contains $rootSha) {
            $alreadyFixed = $true
            Write-Ok '该 PEM 里正是这个根 —— 说明本机已经修好了，此处无需再动。'
            Write-Warn2 '注意: 这只说明变量在「当前进程」里存在。若换一个没继承它的终端仍失败，说明它没写进用户级环境。'
        }
    }

    if ($alreadyFixed -and -not ($Export -or $Apply -or $Verify)) { exit 0 }

    if ($Export) {
        Write-Step "3. 导出根证书到 $PemPath"
        $dir = Split-Path -Parent $PemPath
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
            Write-Host "   已创建目录 $dir"
        }
        $b64 = [Convert]::ToBase64String($root.RawData)
        $lines = [regex]::Matches($b64, '.{1,64}') | ForEach-Object { $_.Value }
        $pem = "-----BEGIN CERTIFICATE-----`n" + ($lines -join "`n") + "`n-----END CERTIFICATE-----`n"
        [System.IO.File]::WriteAllText($PemPath, $pem, [System.Text.Encoding]::ASCII)
        Write-Ok "已写入 $PemPath（$((Get-Item -LiteralPath $PemPath).Length) 字节）"
    }

    if ($Apply) {
        Write-Step '4. 写入用户级环境变量 NODE_EXTRA_CA_CERTS'
        if (-not (Test-Path -LiteralPath $PemPath)) {
            Write-Warn2 "PEM 不存在: $PemPath —— 请先加 -Export，或用 -PemPath 指向已有文件。"
            exit 2
        }
        [Environment]::SetEnvironmentVariable('NODE_EXTRA_CA_CERTS', $PemPath, 'User')
        Write-Ok "已设置（User 作用域）: $PemPath"
        Write-Warn2 '仅对之后新启动的进程生效，必须重启终端与目标应用。'
        Write-Warn2 '不要把这个变量写进 .env 文件：DSH 之类的宿主会拒绝启动。'
    }

    if ($Verify) {
        Write-Step "5. 实测验证（$Runs 次）"
        $probe = Join-Path $PSScriptRoot 'tls_probe.mjs'
        if (-not (Test-Path -LiteralPath $probe)) {
            Write-Warn2 "找不到探测脚本 $probe，跳过验证。"
            exit 2
        }
        $previous = $env:NODE_EXTRA_CA_CERTS
        $env:NODE_EXTRA_CA_CERTS = $PemPath
        try {
            # 先收进变量再逐行输出：原生程序的 stdout 与 Write-Host 的信息流
            # 在被管道捕获时顺序会错乱，结论会跑到明细前面。
            $probeOutput = & node $probe $TargetHost --runs $Runs 2>&1
            $probeExit = $LASTEXITCODE
        } finally {
            $env:NODE_EXTRA_CA_CERTS = $previous
        }
        foreach ($line in $probeOutput) { Write-Host $line }
        if ($probeExit -ne 0) {
            Write-Warn2 '验证未通过。检查是否重启了终端、PEM 是否对应正确的根、以及拦截是否换了证书。'
            exit 1
        }
        Write-Ok '验证通过。注意输出里的签发者：若仍能看到拦截方签发，说明扫描没有被削弱。'
        exit 0
    }

    if (-not ($Export -or $Apply)) {
        Write-Host ''
        Write-Warn2 '检测到中间人替换，但未做任何修改。加 -Export -Apply -Verify 执行修复。'
        exit 1
    }

    exit 0
} catch {
    Write-Host "`n错误: $($_.Exception.Message)" -ForegroundColor Red
    exit 2
}
