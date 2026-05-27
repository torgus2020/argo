"""Script: validación de handshake productivo Primary vía BIND + snapshot.

Objetivo: validar que las credenciales productivas autentican contra
el endpoint productivo BIND, consultar el catálogo detallado, y guardar
un snapshot crudo para trabajo offline (parser de mapeo, debugging, etc).

NO opera, NO suscribe WebSocket, NO escribe a la base de datos.

Uso:
    python scripts/validar_handshake_primary_produccion.py

Salida:
- Output en consola con resultado del handshake y resumen del catálogo.
- data/raw/primary_produccion_catalogo_YYYYMMDD.json: snapshot crudo
  del catálogo detallado (lo que get_detailed_instruments devuelve).
  El nombre incluye fecha; corridas múltiples el mismo día se
  sobrescriben (un snapshot por día alcanza).

Smoke test reutilizable: correr cuando haya dudas de conexión, después
de cambios de password, o para refrescar el snapshot productivo.
"""
import json
import sys
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Inyectar raíz del proyecto al sys.path (patrón heartbeat.py)
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

import pyRofex


def cargar_credenciales():
    """Lee primary_produccion desde secrets.json. Valida claves requeridas."""
    ruta_secrets = RAIZ_PROYECTO / "config" / "secrets.json"
    with open(ruta_secrets, "r", encoding="utf-8") as f:
        secrets = json.load(f)

    bloque = secrets.get("primary_produccion")
    if not bloque:
        print("[ERROR] No existe el bloque 'primary_produccion' en secrets.json")
        sys.exit(1)

    claves_requeridas = ["user", "password", "account", "endpoint"]
    faltantes = [k for k in claves_requeridas if not bloque.get(k)]
    if faltantes:
        print(f"[ERROR] Faltan claves en primary_produccion: {faltantes}")
        sys.exit(1)

    return bloque


def configurar_environment_bind(endpoint_host):
    """Sobrescribe la URL REST del environment LIVE para apuntar a BIND.

    Nota técnica: pyRofex 0.5.0 expone esta función como
    _set_environment_parameter (con guión bajo, convención de 'API
    interna'). Es la única vía documentada en esa versión para redirigir
    el environment LIVE a un endpoint específico de broker. En versiones
    nuevas la función está expuesta sin guión bajo; cuando se actualice
    la dependencia, ajustar acá.

    Solo se toca 'url' (REST). NO se tocan 'ws' (WebSocket) ni
    'proprietary' porque este script no suscribe WebSocket y queremos
    minimizar interferencia con defaults: cualquier error que aparezca
    viene de credenciales o permisos, no de configuración nuestra.
    """
    url_rest = f"https://{endpoint_host}/"
    pyRofex._set_environment_parameter(
        "url", url_rest, pyRofex.Environment.LIVE
    )
    print(f"[INFO] Environment LIVE redirigido a: {url_rest}")


def handshake(user, password, account):
    """Inicializa pyRofex contra LIVE (ya redirigido) y autentica."""
    print(f"[INFO] Inicializando pyRofex contra LIVE para cuenta {account}...")
    pyRofex.initialize(
        user=user,
        password=password,
        account=account,
        environment=pyRofex.Environment.LIVE,
    )
    print("[OK] Inicialización completada sin excepción.")


def consultar_segmentos():
    """Llama get_segments. Primera lectura REST del catálogo."""
    print("[INFO] Consultando segmentos...")
    resp = pyRofex.get_segments()
    print(f"[OK] Respuesta de get_segments: {resp}")
    return resp


def consultar_catalogo_detallado():
    """Llama get_detailed_instruments y devuelve la respuesta cruda.

    Usa la versión detallada (no get_all_instruments) porque incluye
    los campos que el parser de mapeo necesita: currency,
    priceConvertionFactor, cficode, segment, instrumentSizes, etc.

    Puede tardar varios segundos: en producción son ~8K instrumentos
    con metadata por cada uno.
    """
    print("[INFO] Consultando catálogo detallado (puede tardar)...")
    resp = pyRofex.get_detailed_instruments()

    if not isinstance(resp, dict) or resp.get("status") != "OK":
        print(f"[WARN] Respuesta inesperada: {resp}")
        return resp

    instrumentos = resp.get("instruments", [])
    print(f"[OK] Catálogo detallado recibido: {len(instrumentos)} instrumentos")
    return resp


def resumen_catalogo(resp):
    """Imprime distribución por CFICode y muestra los primeros 3 símbolos."""
    if not isinstance(resp, dict) or "instruments" not in resp:
        return
    instrumentos = resp["instruments"]

    cficodes = Counter()
    for inst in instrumentos:
        cfi = inst.get("cficode") or inst.get("instrumentId", {}).get("cficode") or "SIN_CFI"
        cficodes[cfi[:6] if cfi != "SIN_CFI" else cfi] += 1

    print(f"[OK] Distribución por CFICode (top 10):")
    for cfi, count in cficodes.most_common(10):
        print(f"       {cfi}: {count}")

    print(f"[OK] Primeros 3 símbolos del catálogo (para inspección):")
    for inst in instrumentos[:3]:
        symbol = inst.get("instrumentId", {}).get("symbol", "?")
        cfi = inst.get("cficode", "?")
        print(f"       {symbol} | CFI={cfi}")


def guardar_snapshot(resp):
    """Guarda el catálogo crudo en data/raw/ con fecha en el nombre.

    Convención: una corrida por día sobrescribe el snapshot del día.
    Si necesitás granularidad horaria, cambiar el formato de fecha a
    YYYYMMDD_HHMMSS.
    """
    dir_raw = RAIZ_PROYECTO / "data" / "raw"
    dir_raw.mkdir(parents=True, exist_ok=True)

    fecha = datetime.now(timezone.utc).strftime("%Y%m%d")
    ruta = dir_raw / f"primary_produccion_catalogo_{fecha}.json"

    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(resp, f, ensure_ascii=False, indent=2)

    tam_mb = ruta.stat().st_size / (1024 * 1024)
    print(f"[OK] Snapshot guardado: {ruta.relative_to(RAIZ_PROYECTO)} ({tam_mb:.2f} MB)")


def main():
    print("=" * 70)
    print("VALIDACIÓN DE HANDSHAKE PRODUCTIVO BIND + SNAPSHOT")
    print("=" * 70)

    try:
        creds = cargar_credenciales()
        print(f"[OK] Credenciales cargadas para cuenta {creds['account']}")

        configurar_environment_bind(creds["endpoint"])
        handshake(creds["user"], creds["password"], creds["account"])
        consultar_segmentos()
        resp = consultar_catalogo_detallado()
        resumen_catalogo(resp)
        guardar_snapshot(resp)

        print("=" * 70)
        print("RESULTADO: handshake exitoso + snapshot guardado.")
        print("=" * 70)

    except Exception as e:
        print("=" * 70)
        print(f"RESULTADO: validación FALLÓ con error: {type(e).__name__}: {e}")
        print("=" * 70)
        print("\nTraceback completo:")
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()