"""
Exploración del detalle de instrumentos de reMarkets (H1.2.5.4-bis).

Script EXPLORATORIO y DESCARTABLE. No es parte del pipeline definitivo.
Su único objetivo es entender la estructura de datos que devuelve
get_detailed_instruments() y get_segments(), para poder distinguir los
dos formatos de símbolo detectados en el catálogo:
    - formato largo : "MERV - XMEV - GD30 - CI"
    - formato corto : "GD30/CI"

Una vez que entendamos qué campo distingue ambos formatos, modificaremos
el snapshot_remarkets.py definitivo para capturar el detalle completo.

No escribe en la base de datos. No persiste archivos (solo imprime a
consola). Read-only total.

Uso:
    python scripts/explorar_detalle_remarkets.py
"""

import json
import sys
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

import pyRofex


_RUTA_SECRETS = RAIZ_PROYECTO / "config" / "secrets.json"

# Tickers de muestra: instrumentos que sabemos aparecen en distintos formatos.
# GD30 y GGAL aparecen en formato largo Y corto; GD35 solo en corto.
# Esto nos permite comparar los campos de cada formato lado a lado.
_TICKERS_MUESTRA = ["GD30", "GD35", "GGAL"]


def cargar_credenciales() -> dict:
    """Carga credenciales de reMarkets desde secrets.json."""
    with open(_RUTA_SECRETS, encoding="utf-8") as f:
        return json.load(f)["primary_remarkets"]


def conectar() -> None:
    """Inicializa la conexión a reMarkets."""
    cred = cargar_credenciales()
    pyRofex.initialize(
        user=cred["user"],
        password=cred["password"],
        account=cred["account"],
        environment=pyRofex.Environment.REMARKET,
    )
    print(f"Conexión a reMarkets OK (account={cred['account']})\n")


def explorar_segmentos() -> None:
    """Imprime la lista de segmentos válidos del mercado."""
    print("=" * 72)
    print("SEGMENTOS (get_segments())")
    print("=" * 72)
    respuesta = pyRofex.get_segments()
    print(json.dumps(respuesta, indent=2, ensure_ascii=False))
    print()


def explorar_detalle() -> None:
    """
    Trae el catálogo detallado y muestra:
      1. La estructura completa (todos los campos) del primer instrumento.
      2. El detalle completo de cada símbolo que mencione un ticker de muestra.
    """
    print("=" * 72)
    print("DETALLE DE INSTRUMENTOS (get_detailed_instruments())")
    print("=" * 72)

    respuesta = pyRofex.get_detailed_instruments()
    if respuesta.get("status") != "OK":
        raise RuntimeError(f"status no-OK: {respuesta}")

    instrumentos = respuesta.get("instruments", [])
    print(f"Total de instrumentos detallados: {len(instrumentos)}\n")

    # 1. Estructura del primer instrumento: ver TODOS los campos disponibles
    print("-" * 72)
    print("ESTRUCTURA COMPLETA DEL PRIMER INSTRUMENTO:")
    print("-" * 72)
    if instrumentos:
        print(json.dumps(instrumentos[0], indent=2, ensure_ascii=False))
    print()

    # 2. Listar los campos top-level disponibles (resumen rápido)
    print("-" * 72)
    print("CAMPOS TOP-LEVEL DISPONIBLES:")
    print("-" * 72)
    if instrumentos:
        for campo in sorted(instrumentos[0].keys()):
            print(f"  - {campo}")
    print()

    # 3. Detalle completo de cada instrumento de muestra (ambos formatos)
    for ticker in _TICKERS_MUESTRA:
        print("-" * 72)
        print(f"INSTRUMENTOS QUE MENCIONAN '{ticker}':")
        print("-" * 72)
        encontrados = [
            inst
            for inst in instrumentos
            if ticker in inst.get("instrumentId", {}).get("symbol", "")
        ]
        if not encontrados:
            print(f"  (ninguno)\n")
            continue
        for inst in encontrados:
            symbol = inst.get("instrumentId", {}).get("symbol", "?")
            print(f"\n  >>> {symbol}")
            print(json.dumps(inst, indent=4, ensure_ascii=False))
        print()


def main() -> int:
    """Punto de entrada. Devuelve 0 si todo OK, 1 si hubo error."""
    try:
        conectar()
        explorar_segmentos()
        explorar_detalle()
        print("=" * 72)
        print("EXPLORACIÓN COMPLETADA")
        print("=" * 72)
        return 0
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())