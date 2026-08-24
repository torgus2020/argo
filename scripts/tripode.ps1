# tripode.ps1 - Ritual de re-arranque de Argo en un solo comando.  (v2)
#
# Corre el tripode completo (local + VPS) y deja TODA la salida en un unico
# archivo de texto bajo logs\, para que Claude lo lea de una sin que Gus
# tenga que copiar y pegar outputs de a uno.
#
# Uso (desde C:\Argo, con o sin venv activo):
#     .\scripts\tripode.ps1
#     .\scripts\tripode.ps1 -Vps mi-alias-ssh      # forzar destino/alias
#     .\scripts\tripode.ps1 -SinVps                # solo chequeos locales
#     .\scripts\tripode.ps1 -SinInteractivo        # no intentar pedir passphrase
#
# Es READ-ONLY salvo por dos cosas: hace `git fetch` (no modifica el arbol de
# trabajo, solo actualiza refs remotas) y escribe el log en logs\.
# No commitea, no pushea, no toca la base ni el VPS mas alla de leer estado.
#
# NUNCA lee el contenido de claves privadas ni de secrets.json. Del directorio
# .ssh solo lee `config` (para descubrir el alias del VPS) y lista NOMBRES de
# archivo. Las claves no se abren, no se copian y no van al log.
#
# v2 (2026-08-21): descubre el alias del VPS leyendo ~/.ssh/config, diagnostica
#                  por que falla el SSH, y limpia el ruido de stderr nativo.

param(
    [string]$Vps = "",
    [switch]$SinVps,
    [switch]$SinInteractivo
)

$ErrorActionPreference = "Continue"
$env:GIT_TERMINAL_PROMPT = "0"   # que git falle en vez de colgarse pidiendo credenciales

$IP_VPS  = "167.99.57.224"
$raiz    = Split-Path -Parent $PSScriptRoot
$py      = Join-Path $raiz ".venv\Scripts\python.exe"
$stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$dirLogs = Join-Path $raiz "logs"
if (-not (Test-Path $dirLogs)) { New-Item -ItemType Directory -Path $dirLogs | Out-Null }
$salida  = Join-Path $dirLogs "tripode_$stamp.txt"

$lineas = New-Object System.Collections.Generic.List[string]

function Titulo($texto) {
    $lineas.Add("")
    $lineas.Add("=" * 78)
    $lineas.Add("  $texto")
    $lineas.Add("=" * 78)
}

function Agregar($texto) { $lineas.Add($texto) }

# Corre un bloque y captura stdout + stderr SIN la decoracion de PowerShell
# (los .exe nativos escriben INFO a stderr y PS5.1 lo envuelve como
# NativeCommandError con el fragmento de codigo; eso ensuciaba el log en v1).
function Correr($etiqueta, $bloque) {
    $lineas.Add("")
    $lineas.Add("--- $etiqueta")
    try {
        $r = & $bloque 2>&1 | ForEach-Object {
                 if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message }
                 else { $_ }
             } | Out-String
        if ([string]::IsNullOrWhiteSpace($r)) { $lineas.Add("(salida vacia)") }
        else { $lineas.Add($r.TrimEnd()) }
        return $r
    } catch {
        $lineas.Add("ERROR: $($_.Exception.Message)")
        return ""
    }
}

# ---------------------------------------------------------------- encabezado
Agregar "TRIPODE ARGO (v2)"
Agregar "Fecha local : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss K')"
Agregar "Fecha UTC   : $((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss')) UTC"
Agregar "Maquina     : $env:COMPUTERNAME"
Agregar "Raiz repo   : $raiz"
Agregar "Python venv : $py  (existe: $(Test-Path $py))"

# ------------------------------------------------------------------- LOCAL
Titulo "LOCAL - git (ANTES del fetch)"
Push-Location $raiz
Correr "git status --short --branch"    { git status --short --branch } | Out-Null
Correr "git log -1 --oneline"           { git log -1 --oneline } | Out-Null
Correr "git rev-parse HEAD origin/main" { git rev-parse HEAD origin/main } | Out-Null

Titulo "LOCAL - git fetch"
Correr "git fetch --prune origin" { git fetch --prune origin } | Out-Null

Titulo "LOCAL - git (DESPUES del fetch)"
Correr "git status --short --branch"    { git status --short --branch } | Out-Null
Correr "git rev-parse HEAD origin/main" { git rev-parse HEAD origin/main } | Out-Null
Correr "git log --oneline -3 HEAD..origin/main (commits remotos sin traer)" { git log --oneline -3 HEAD..origin/main } | Out-Null

Titulo "LOCAL - alembic"
if (Test-Path $py) {
    Correr "alembic current" { & $py -m alembic current } | Out-Null
    Correr "alembic heads"   { & $py -m alembic heads }   | Out-Null
} else {
    Agregar "(no se encontro $py - salteado)"
}

Titulo "LOCAL - archivos clave"
Correr "data\argo.sqlite" { Get-Item (Join-Path $raiz "data\argo.sqlite") | Select-Object Name,Length,LastWriteTime | Format-List } | Out-Null
Pop-Location

# --------------------------------------------------------------------- VPS
if ($SinVps) {
    Titulo "VPS - salteado (-SinVps)"
} else {

    # --- Paso 1: descubrir como se llega al VPS ---------------------------
    Titulo "VPS - descubrimiento de acceso SSH"

    $dirSsh = Join-Path $env:USERPROFILE ".ssh"
    $cfgSsh = Join-Path $dirSsh "config"

    Agregar ""
    Agregar "--- archivos en $dirSsh (solo nombres, no se lee contenido de claves)"
    if (Test-Path $dirSsh) {
        Get-ChildItem $dirSsh -Force | ForEach-Object { Agregar ("  " + $_.Name) }
    } else { Agregar "  (no existe)" }

    # Alias definidos en ~/.ssh/config que apunten a la IP del VPS.
    $aliases = @()
    Agregar ""
    Agregar "--- alias en ~/.ssh/config que apuntan a $IP_VPS"
    if (Test-Path $cfgSsh) {
        $hostActual = ""
        foreach ($linea in (Get-Content $cfgSsh)) {
            $l = $linea.Trim()
            if ($l -match '^(?i)Host\s+(.+)$')          { $hostActual = $Matches[1].Trim() }
            elseif ($l -match '^(?i)HostName\s+(.+)$')  {
                if ($Matches[1].Trim() -eq $IP_VPS -and $hostActual -ne "") {
                    $aliases += $hostActual
                    Agregar "  encontrado: Host $hostActual"
                }
            }
        }
        if ($aliases.Count -eq 0) { Agregar "  (ninguno - el config existe pero no mapea esa IP)" }
    } else { Agregar "  (no hay ~/.ssh/config)" }

    Correr "ssh-add -l (claves cargadas en el agente)" { ssh-add -l } | Out-Null

    # --- Paso 2: intentar conectar ----------------------------------------
    Titulo "VPS - conexion SSH"

    $candidatos = @()
    if ($Vps -ne "") { $candidatos += $Vps }
    $candidatos += $aliases
    $candidatos += @("argo@$IP_VPS", "root@$IP_VPS")
    $candidatos = $candidatos | Select-Object -Unique

    $destino = ""
    foreach ($c in $candidatos) {
        Agregar ""
        Agregar "probando (sin interaccion): $c"
        $p = & ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new $c "echo TRIPODE_SSH_OK" 2>&1 |
             ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_ } } |
             Out-String
        Agregar ("  " + $p.Trim())
        if ($p -match "TRIPODE_SSH_OK") { $destino = $c; break }
    }

    # Fallback interactivo: si todo fallo, puede ser una clave con passphrase.
    # BatchMode la rechaza sin preguntar; sin BatchMode, la consola te la pide.
    if ($destino -eq "" -and -not $SinInteractivo -and $candidatos.Count -gt 0) {
        $primero = $candidatos[0]
        Write-Host ""
        Write-Host "Ningun intento automatico funciono." -ForegroundColor Yellow
        Write-Host "Probando de forma interactiva contra: $primero" -ForegroundColor Yellow
        Write-Host "Si tu clave tiene passphrase, te la va a pedir ACA ABAJO. Escribila." -ForegroundColor Yellow
        Write-Host "(Si no queres, Ctrl+C y corre con -SinInteractivo)" -ForegroundColor Yellow
        Write-Host ""
        $p = & ssh -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new $primero "echo TRIPODE_SSH_OK" 2>&1 |
             ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_ } } |
             Out-String
        Agregar ""
        Agregar "--- intento interactivo contra $primero"
        Agregar ("  " + $p.Trim())
        if ($p -match "TRIPODE_SSH_OK") { $destino = $primero }
    }

    # --- Paso 3: si hay destino, traer todo el estado en un solo viaje -----
    if ($destino -eq "") {
        Agregar ""
        Agregar "NO SE PUDO ESTABLECER SSH."
        Agregar "Diagnostico detallado (ssh -v) del primer candidato, para ver que clave ofrece:"
        if ($candidatos.Count -gt 0) {
            Correr "ssh -v $($candidatos[0])" { & ssh -v -o BatchMode=yes -o ConnectTimeout=10 $candidatos[0] "echo TRIPODE_SSH_OK" } | Out-Null
        }
        Agregar ""
        Agregar "Si sabes el comando exacto con el que entras a mano, corre:"
        Agregar "   .\scripts\tripode.ps1 -Vps <eso-mismo>"
    } else {
        Agregar ""
        Agregar "SSH OK contra $destino"

        # Un solo viaje: todo el estado del VPS en una corrida.
        # Se encadena con ';' (no '&&') para que un paso que falle no aborte el resto.
        $remoto = "cd /home/argo/argo; echo [1-git-status-antes]; git status --short --branch; echo [2-log]; git log -1 --oneline; echo [3-fetch]; GIT_TERMINAL_PROMPT=0 git fetch --prune origin 2>&1; echo [4-git-status-despues]; git status --short --branch; echo [5-revs]; git rev-parse HEAD origin/main; echo [6-alembic-current]; ./.venv/bin/python -m alembic current 2>&1; echo [7-alembic-heads]; ./.venv/bin/python -m alembic heads 2>&1; echo [8-service]; systemctl status argo-collector --no-pager -l 2>&1 | head -25; echo [9-timers]; systemctl list-timers --no-pager 2>&1 | head -15; echo [10-disco]; df -h / | tail -1; echo [11-db]; ls -la /home/argo/argo/data/argo.sqlite; echo [12-logs]; ls -lt /home/argo/argo/logs | head -8; echo [FIN]"

        Titulo "VPS - estado completo"
        Correr "ssh $destino (bloque unico)" { & ssh -o ConnectTimeout=20 $destino $remoto } | Out-Null
    }
}

# ------------------------------------------------------------------- cierre
Titulo "FIN"
Agregar "Log escrito en: $salida"

$texto = ($lineas -join "`r`n")
$utf8NoBOM = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($salida, $texto, $utf8NoBOM)

Write-Host ""
Write-Host "Tripode terminado. Salida completa en:" -ForegroundColor Green
Write-Host "  $salida" -ForegroundColor Green
Write-Host ""
Write-Host "Deciles a Claude: 'corri el tripode' y que lea ese archivo." -ForegroundColor Yellow
