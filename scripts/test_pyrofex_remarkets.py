"""
Prueba de conexión a Primary API vía pyRofex contra entorno reMarkets.

Este script es prueba de concepto (proof of concept) — NO es parte del
collector definitivo. Valida que:

1. pyRofex está correctamente instalado.
2. Las credenciales de reMarkets en config/secrets.json son válidas.
3. Podemos autenticarnos y obtener token.
4. Podemos listar segmentos disponibles.
5. Podemos listar instrumentos disponibles.
6. Podemos obtener market data REST de un instrumento.

NO toca la base de datos ni hace operaciones en mercado.
Solo lee datos.

Uso:
    python scripts/test_pyrofex_remarkets.py

Devuelve código de salida:
- 0: todas las pruebas pasaron
- 1: alguna prueba falló (ver logs)
"""

import json
import sys
from pathlib import Path

# Agregar raíz del proyecto al sys.path
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

import pyRofex
from src.utils.logger import obtener_logger

_log = obtener_logger(__name__)

_RUTA_SECRETS = RAIZ_PROYECTO / "config" / "secrets.json"


def _cargar_credenciales_remarkets() -> dict:
    """
    Lee config/secrets.json y devuelve el bloque 'primary_remarkets'.
    Levanta excepción si no existe o está incompleto.
    """
    if not _RUTA_SECRETS.exists():
        raise FileNotFoundError(f"No se encontró {_RUTA_SECRETS}")

    with open(_RUTA_SECRETS, encoding="utf-8") as f:
        secrets = json.load(f)

    if "primary_remarkets" not in secrets:
        raise KeyError("Bloque 'primary_remarkets' faltante en secrets.json")

    cred = secrets["primary_remarkets"]
    for campo in ("user", "password", "account"):
        if not cred.get(campo):
            raise ValueError(f"Campo '{campo}' faltante o vacío en primary_remarkets")

    return cred


def prueba_1_autenticacion(cred: dict) -> bool:
    """
    Inicializa pyRofex con credenciales reMarkets. Valida que la
    autenticación funciona — si las credenciales fueran inválidas,
    esta llamada levantaría ApiException.
    """
    print("\n[1/5] Probando autenticación contra reMarkets...")
    try:
        pyRofex.initialize(
            user=cred["user"],
            password=cred["password"],
            account=cred["account"],
            environment=pyRofex.Environment.REMARKET,
        )
        print("    OK: autenticación exitosa.")
        return True
    except Exception as e:
        print(f"    ERROR: autenticación falló: {e}")
        _log.error(f"Autenticación falló: {e}")
        return False


def prueba_2_segmentos() -> bool:
    """
    Lista los segmentos de mercado disponibles. Estos son los 'ambientes'
    de negociación de Matba Rofex (DDF, DDA, DUAL, MERV, etc.).
    """
    print("\n[2/5] Listando segmentos disponibles...")
    try:
        segments = pyRofex.get_segments()
        if segments.get("status") == "OK":
            print(f"    OK: {len(segments['segments'])} segmentos encontrados:")
            for seg in segments["segments"]:
                print(f"      - {seg.get('marketSegmentId')} ({seg.get('marketId')})")
            return True
        else:
            print(f"    ERROR: respuesta no OK: {segments}")
            return False
    except Exception as e:
        print(f"    ERROR: {e}")
        _log.error(f"get_segments falló: {e}")
        return False


def prueba_3_instrumentos_lista() -> bool:
    """
    Lista los instrumentos disponibles (formato resumido).
    Imprime cuántos hay y muestra los primeros 5 como sample.
    """
    print("\n[3/5] Listando instrumentos disponibles...")
    try:
        instr = pyRofex.get_all_instruments()
        if instr.get("status") == "OK":
            total = len(instr.get("instruments", []))
            print(f"    OK: {total} instrumentos disponibles en reMarkets.")
            print(f"    Primeros 5:")
            for i in instr["instruments"][:5]:
                symbol = i.get("instrumentId", {}).get("symbol", "?")
                cfi = i.get("cficode", "?")
                print(f"      - {symbol} (CFI: {cfi})")
            return True
        else:
            print(f"    ERROR: respuesta no OK: {instr}")
            return False
    except Exception as e:
        print(f"    ERROR: {e}")
        _log.error(f"get_all_instruments falló: {e}")
        return False


def prueba_4_instrumento_dolar() -> str | None:
    """
    Busca un futuro de dólar en la lista de instrumentos. Devuelve
    el primer symbol que matchee DLR/ — para usar en la prueba 5.
    """
    print("\n[4/5] Buscando un futuro de dólar disponible...")
    try:
        instr = pyRofex.get_all_instruments()
        for i in instr.get("instruments", []):
            symbol = i.get("instrumentId", {}).get("symbol", "")
            if symbol.startswith("DLR/"):
                print(f"    OK: encontrado '{symbol}'")
                return symbol
        print("    ADVERTENCIA: no se encontró ningún futuro de dólar (DLR/...)")
        return None
    except Exception as e:
        print(f"    ERROR: {e}")
        _log.error(f"búsqueda DLR/ falló: {e}")
        return None


def prueba_5_market_data(ticker: str) -> bool:
    """
    Pide market data REST del instrumento dado. Trae bid, offer, last,
    open, close, etc. Es lectura puntual, no streaming.
    """
    print(f"\n[5/5] Pidiendo market data REST de '{ticker}'...")
    try:
        md = pyRofex.get_market_data(
            ticker=ticker,
            entries=[
                pyRofex.MarketDataEntry.BIDS,
                pyRofex.MarketDataEntry.OFFERS,
                pyRofex.MarketDataEntry.LAST,
                pyRofex.MarketDataEntry.OPENING_PRICE,
                pyRofex.MarketDataEntry.CLOSING_PRICE,
            ],
        )
        if md.get("status") == "OK":
            print(f"    OK: respuesta recibida.")
            market_data = md.get("marketData", {})
            for key, val in market_data.items():
                print(f"      - {key}: {val}")
            return True
        else:
            print(f"    ERROR: respuesta no OK: {md}")
            return False
    except Exception as e:
        print(f"    ERROR: {e}")
        _log.error(f"get_market_data para {ticker} falló: {e}")
        return False


def main() -> int:
    print("=" * 60)
    print("PRUEBA DE CONEXIÓN A PRIMARY API (reMarkets)")
    print("=" * 60)

    try:
        cred = _cargar_credenciales_remarkets()
    except Exception as e:
        print(f"\nERROR cargando credenciales: {e}")
        return 1

    resultados = []

    # Prueba 1: autenticación
    resultados.append(("Autenticación", prueba_1_autenticacion(cred)))
    if not resultados[-1][1]:
        print("\nFalla crítica en autenticación. Abortando pruebas siguientes.")
        return 1

    # Prueba 2: segmentos
    resultados.append(("Segmentos", prueba_2_segmentos()))

    # Prueba 3: instrumentos lista
    resultados.append(("Instrumentos lista", prueba_3_instrumentos_lista()))

    # Prueba 4: buscar ticker
    ticker = prueba_4_instrumento_dolar()
    resultados.append(("Buscar DLR/...", ticker is not None))

    # Prueba 5: market data (solo si encontramos ticker)
    if ticker:
        resultados.append(("Market data REST", prueba_5_market_data(ticker)))
    else:
        print("\n[5/5] SKIP: no hay ticker para probar market data.")
        resultados.append(("Market data REST", False))

    # Resumen final
    print("\n" + "=" * 60)
    print("RESUMEN")
    print("=" * 60)
    total = len(resultados)
    exitosas = sum(1 for _, ok in resultados if ok)
    for nombre, ok in resultados:
        estado = "✓" if ok else "✗"
        print(f"  [{estado}] {nombre}")
    print(f"\n{exitosas}/{total} pruebas exitosas.")
    print("=" * 60)

    return 0 if exitosas == total else 1


if __name__ == "__main__":
    sys.exit(main())