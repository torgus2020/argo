"""
Generador de mapeo Argo <-> Primary/Matriz — H1.2.5.5-bis

QUÉ HACE
--------
Cruza los instrumentos del universo de Argo (tabla `instrumentos` de la base)
contra un snapshot detallado de reMarkets, y produce un MAPEO PROPUESTO de
filas listas para la tabla `instrumento_broker_mapping`.

QUÉ NO HACE (a propósito)
-------------------------
NO escribe nada a la base. El flujo del proyecto es: descubrir (este script) ->
revisar humano (H1.2.5.6) -> poblar (H1.2.5.7). Este script solo descubre.
Su salida es un JSON propuesto + un reporte legible.

LÓGICA EN 7 PASOS
-----------------
1. Cargar universo: tickers base mapeables desde la tabla `instrumentos`.
2. Filtrar el snapshot en 3 capas: segmento MERV -> CFICode válido -> ticker
   parseable.
3. Parsear cada símbolo Primary de forma robusta (regex tolerante a espacios).
4. Desambiguar ticker base vs variante D/C, cruzando contra el universo.
   El ticker COMPLETO se chequea primero — así YPFD se resuelve como base
   y no como YPF+D.
5. Validación cruzada con `currency` de reMarkets: el ticker manda, currency
   audita. Discrepancia -> sospechoso, al reporte (no se descarta).
6. Detección de huérfanos bidireccional (snapshot sin universo / universo
   sin snapshot).
7. Salida: JSON propuesto en data/processed/ + reporte por consola.

CONVENCIÓN DE EJECUCIÓN
-----------------------
Correr desde la raíz del proyecto:
    python -m scripts.generar_mapeo_primary
o directamente:
    python scripts/generar_mapeo_primary.py
Ambas funcionan: el script inyecta la raíz al sys.path (ver heartbeat.py).
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Agregar la raíz del proyecto al sys.path para poder importar módulos de src/
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

from src.utils.db import get_session
from src.utils.logger import obtener_logger
from src.utils.models import Instrumento

_log = obtener_logger(__name__)


# ----------------------------------------------------------------------------
# CONFIGURACIÓN
# ----------------------------------------------------------------------------

# Carpeta donde viven los snapshots crudos de reMarkets.
DIR_SNAPSHOTS = RAIZ_PROYECTO / "data" / "raw"

# Carpeta donde se deja el mapeo propuesto (salida del script).
DIR_SALIDA = RAIZ_PROYECTO / "data" / "processed"

# Broker al que corresponde este mapeo. Hoy solo Primary; el modelo soporta
# múltiples brokers, por eso queda explícito y no hardcodeado en cada fila.
BROKER = "primary"

# Segmento de mercado que Argo opera. Filtramos TODO lo que no sea esto.
# reMarkets/Primary expone también TIVA (MAE mayorista), DUAL (futuros),
# DDA, etc. — fuera del alcance del proyecto.
SEGMENTO_OBJETIVO = "MERV"

# Prefijos de CFICode que nos interesan. El primer carácter del CFICode
# clasifica el instrumento:
#   E... = acción (incluye CEDEARs, que figuran como ESXXXX)
#   D... = deuda (bonos, ON)
# Descartamos explícitamente O... = opciones, F... = futuros, etc.
CFICODES_VALIDOS_PREFIJOS = ("E", "D")

# Tipos de instrumento de la tabla `instrumentos` que son mapeables contra
# Primary (es decir, que cotizan en BYMA). Las macro (BCRA/INDEC) y los
# instrumentos calculados NO tienen símbolo de mercado.
TIPOS_MAPEABLES = {
    "bono_usd_ar",
    "bono_pesos_ar",
    "bopreal",
    "lecap",
    "accion_ar",
    "cedear",
}

# Traducción de variante -> valor del campo `moneda_liquidacion` del modelo.
# El modelo InstrumentoBrokerMapping define estos strings exactos.
VARIANTE_A_MONEDA_LIQ = {
    "base": "ARS",      # sin sufijo -> pesos
    "D": "USD_MEP",     # sufijo D   -> dólar MEP
    "C": "USD_CCL",     # sufijo C   -> dólar CCL
}

# Validación cruzada: qué valor de `currency` (campo de reMarkets) se espera
# para cada variante. Si reMarkets dice otra cosa, la fila se marca sospechosa.
#   ARS = pesos
#   USD = dólar local (MEP)
#   EXT = dólar exterior (CCL)
VARIANTE_A_CURRENCY_ESPERADA = {
    "base": {"ARS"},
    "D": {"USD"},
    "C": {"EXT"},
}

# Plazos válidos de Primary. CI = Contado Inmediato (T+0), 24hs = T+1.
PLAZOS_VALIDOS = {"CI", "24hs", "48hs"}

# Regex tolerante para parsear el símbolo Primary.
# Formato nominal: "MERV - XMEV - {TICKER} - {PLAZO}"
# El \s*-\s* acepta cero o más espacios alrededor del guion: así toleramos
# símbolos sucios como "MERV - XMEV - SUPV- 24hs" (sin espacio antes del guion).
PATRON_SIMBOLO = re.compile(
    r"^\s*([A-Z]+)\s*-\s*([A-Z]+)\s*-\s*([A-Z0-9]+)\s*-\s*([A-Za-z0-9]+)\s*$"
)


# ----------------------------------------------------------------------------
# PASO 1 — Cargar el universo de tickers base
# ----------------------------------------------------------------------------

def cargar_universo() -> dict:
    """
    Lee la tabla `instrumentos` y devuelve un dict {ticker: datos} con SOLO
    los instrumentos mapeables contra Primary (tipos en TIPOS_MAPEABLES).

    El ticker es la clave de cruce contra los símbolos del snapshot.
    """
    universo = {}
    with get_session() as session:
        filas = session.query(Instrumento).all()
        for inst in filas:
            if inst.tipo not in TIPOS_MAPEABLES:
                continue
            if not inst.activo:
                # Instrumento marcado inactivo: lo saltamos, no se mapea.
                continue
            universo[inst.ticker] = {
                "instrumento_id": inst.id,
                "ticker": inst.ticker,
                "tipo": inst.tipo,
                "nombre": inst.nombre,
                "moneda": inst.moneda,
            }

    _log.info(f"Universo cargado: {len(universo)} tickers base mapeables.")
    return universo


# ----------------------------------------------------------------------------
# PASO 2 (parcial) — Cargar y filtrar el snapshot
# ----------------------------------------------------------------------------

def encontrar_snapshot_mas_reciente() -> Path:
    """
    Busca en data/raw/ el snapshot DETALLADO más reciente.
    Los detallados se llaman 'remarkets_snapshot_detallado_YYYY-MM-DD.json'.

    Lanza FileNotFoundError si no hay ninguno (es un error duro: sin snapshot
    no hay nada que mapear).
    """
    candidatos = sorted(DIR_SNAPSHOTS.glob("remarkets_snapshot_detallado_*.json"))
    if not candidatos:
        raise FileNotFoundError(
            f"No se encontró ningún snapshot detallado en {DIR_SNAPSHOTS}. "
            f"Correr primero snapshot_remarkets.py."
        )
    elegido = candidatos[-1]  # orden alfabético == orden cronológico por la fecha ISO
    _log.info(f"Snapshot elegido: {elegido.name}")
    return elegido


def cargar_snapshot(ruta: Path) -> tuple[dict, list]:
    """
    Lee un snapshot detallado. Devuelve (metadata, lista_instrumentos).

    El snapshot tiene wrapper: dict con metadata + clave 'instrumentos'.
    """
    with open(ruta, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or "instrumentos" not in data:
        raise ValueError(
            f"El snapshot {ruta.name} no tiene la estructura esperada "
            f"(dict con clave 'instrumentos')."
        )

    metadata = {k: v for k, v in data.items() if k != "instrumentos"}
    instrumentos = data["instrumentos"]
    _log.info(
        f"Snapshot cargado: {len(instrumentos)} instrumentos totales "
        f"(fecha snapshot: {metadata.get('fecha_snapshot_utc', '?')})."
    )
    return metadata, instrumentos


def filtrar_merv(instrumentos: list) -> tuple[list, dict]:
    """
    CAPA 1 y 2 del filtro de 3 capas:
      Capa 1: segmento == MERV
      Capa 2: CFICode con prefijo válido (descarta opciones/futuros)

    Devuelve (instrumentos_filtrados, conteos) donde conteos es un dict con
    el desglose de cuántos se descartaron y por qué — para el reporte.
    """
    conteos = {
        "total": len(instrumentos),
        "no_merv": 0,
        "merv_total": 0,
        "descartados_cficode": 0,
        "sin_cficode": 0,
        "pasaron": 0,
    }
    resultado = []

    for inst in instrumentos:
        segmento = (inst.get("segment") or {}).get("marketSegmentId")
        if segmento != SEGMENTO_OBJETIVO:
            conteos["no_merv"] += 1
            continue
        conteos["merv_total"] += 1

        cficode = inst.get("cficode")
        if not cficode:
            # Sin CFICode no podemos clasificar el instrumento -> lo apartamos.
            conteos["sin_cficode"] += 1
            continue
        if not cficode.startswith(CFICODES_VALIDOS_PREFIJOS):
            # Típicamente opciones (O...). Se descarta silenciosamente.
            conteos["descartados_cficode"] += 1
            continue

        conteos["pasaron"] += 1
        resultado.append(inst)

    return resultado, conteos


# ----------------------------------------------------------------------------
# PASO 3 — Parsear el símbolo Primary
# ----------------------------------------------------------------------------

def parsear_simbolo(simbolo: str) -> dict | None:
    """
    Parsea un símbolo Primary "MERV - XMEV - {TICKER} - {PLAZO}".

    Devuelve dict {mercado, prefijo, ticker, plazo} si matchea, o None si el
    símbolo no tiene el formato esperado (huérfano de parseo).
    """
    if not simbolo:
        return None
    m = PATRON_SIMBOLO.match(simbolo)
    if not m:
        return None
    mercado, prefijo, ticker, plazo = m.groups()
    return {
        "mercado": mercado,
        "prefijo": prefijo,
        "ticker": ticker,
        "plazo": plazo,
    }


# ----------------------------------------------------------------------------
# PASO 4 — Desambiguar ticker base vs variante D/C
# ----------------------------------------------------------------------------

def resolver_variante(ticker_simbolo: str, universo: dict) -> dict | None:
    """
    Dado el ticker extraído de un símbolo Primary, determina si es:
      - un ticker BASE del universo            -> variante 'base'
      - un ticker base + sufijo 'D' (MEP)      -> variante 'D'
      - un ticker base + sufijo 'C' (CCL)      -> variante 'C'
      - ninguno de los anteriores              -> None (huérfano)

    ORDEN CRÍTICO: se chequea el ticker COMPLETO contra el universo PRIMERO.
    Esto es lo que hace que 'YPFD' (CEDEAR de YPF, ticker base que termina
    en D) se resuelva como base y NO como 'YPF' + sufijo D. La heurística
    de sufijo solo se aplica si el ticker completo no está en el universo.
    """
    # 1. ¿Es un ticker base tal cual?
    if ticker_simbolo in universo:
        return {
            "ticker_base": ticker_simbolo,
            "variante": "base",
            "instrumento_id": universo[ticker_simbolo]["instrumento_id"],
        }

    # 2. ¿Termina en D y el resto es un ticker base?
    if ticker_simbolo.endswith("D"):
        candidato = ticker_simbolo[:-1]
        if candidato in universo:
            return {
                "ticker_base": candidato,
                "variante": "D",
                "instrumento_id": universo[candidato]["instrumento_id"],
            }

    # 3. ¿Termina en C y el resto es un ticker base?
    if ticker_simbolo.endswith("C"):
        candidato = ticker_simbolo[:-1]
        if candidato in universo:
            return {
                "ticker_base": candidato,
                "variante": "C",
                "instrumento_id": universo[candidato]["instrumento_id"],
            }

    # 4. No matchea nada -> huérfano.
    return None


# ----------------------------------------------------------------------------
# PASO 5 — Validación cruzada con currency
# ----------------------------------------------------------------------------

def validar_currency(variante: str, currency_remarkets: str) -> bool:
    """
    Chequea que el `currency` que reporta reMarkets sea coherente con la
    variante que dedujimos por el ticker.

    Devuelve True si es coherente, False si es sospechoso.
    El ticker manda; esto solo audita.
    """
    esperadas = VARIANTE_A_CURRENCY_ESPERADA.get(variante, set())
    return currency_remarkets in esperadas


# ----------------------------------------------------------------------------
# PASOS 4-6 — Procesar todos los instrumentos filtrados
# ----------------------------------------------------------------------------

def construir_mapeo(instrumentos_merv: list, universo: dict) -> dict:
    """
    Recorre los instrumentos MERV ya filtrados y construye:
      - filas_propuestas: lista de dicts listos para instrumento_broker_mapping
      - huerfanos_snapshot: símbolos MERV que no matchean ningún ticker
      - sospechosos: filas mapeadas pero con currency incoherente
      - sin_parsear: símbolos que no matchean el patrón regex
      - colisiones: violaciones del UniqueConstraint (broker, symbol, plazo)
    """
    filas_propuestas = []
    huerfanos_snapshot = []
    sospechosos = []
    sin_parsear = []

    # Para detectar colisiones del UniqueConstraint (broker, symbol_externo, plazo).
    claves_unicas_vistas = set()
    colisiones = []

    for inst in instrumentos_merv:
        simbolo = (inst.get("instrumentId") or {}).get("symbol")

        # PASO 3: parsear
        parsed = parsear_simbolo(simbolo)
        if parsed is None:
            sin_parsear.append(simbolo)
            continue

        ticker_simbolo = parsed["ticker"]
        plazo = parsed["plazo"]

        # Chequeo de plazo: si el plazo no es uno de los conocidos, lo
        # registramos como huérfano (puede ser un plazo nuevo o un símbolo raro).
        if plazo not in PLAZOS_VALIDOS:
            huerfanos_snapshot.append(
                {"symbol": simbolo, "motivo": f"plazo desconocido: {plazo}"}
            )
            continue

        # PASO 4: desambiguar variante
        resuelto = resolver_variante(ticker_simbolo, universo)
        if resuelto is None:
            # Ticker no está en el universo -> ruido de reMarkets
            # (ej. PESOS, ROCIO, o instrumento fuera del universo de Argo).
            huerfanos_snapshot.append(
                {"symbol": simbolo, "motivo": f"ticker '{ticker_simbolo}' no está en el universo"}
            )
            continue

        variante = resuelto["variante"]
        currency_rm = inst.get("currency")

        # PASO 5: validación cruzada con currency
        currency_ok = validar_currency(variante, currency_rm)

        # Construir la fila propuesta para instrumento_broker_mapping.
        # Guardamos en metadata_json el detalle de reMarkets que no tiene
        # columna propia — útil para auditoría posterior.
        metadata = {
            "cficode": inst.get("cficode"),
            "currency_remarkets": currency_rm,
            "price_convertion_factor": inst.get("priceConvertionFactor"),
            "tick_size": inst.get("tickSize"),
            "maturity_date": inst.get("maturityDate"),
            "underlying": inst.get("underlying"),
        }

        fila = {
            "instrumento_id": resuelto["instrumento_id"],
            "ticker_base": resuelto["ticker_base"],
            "broker": BROKER,
            "symbol_externo": simbolo,
            "segmento": SEGMENTO_OBJETIVO,
            "moneda_liquidacion": VARIANTE_A_MONEDA_LIQ[variante],
            "plazo": plazo,
            # es_default: True solo para la variante base en plazo CI.
            "es_default": (variante == "base" and plazo == "CI"),
            "activo": True,
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "fecha_validacion": None,  # se llena en la revisión humana (H1.2.5.6)
            "_variante": variante,           # campo auxiliar para el reporte
            "_currency_ok": currency_ok,     # campo auxiliar para el reporte
        }

        # Chequeo del UniqueConstraint (broker, symbol_externo, plazo).
        clave_unica = (BROKER, simbolo, plazo)
        if clave_unica in claves_unicas_vistas:
            colisiones.append(clave_unica)
        else:
            claves_unicas_vistas.add(clave_unica)

        filas_propuestas.append(fila)

        if not currency_ok:
            sospechosos.append(
                {
                    "symbol": simbolo,
                    "variante": variante,
                    "currency_remarkets": currency_rm,
                    "currency_esperada": sorted(VARIANTE_A_CURRENCY_ESPERADA[variante]),
                }
            )

    return {
        "filas_propuestas": filas_propuestas,
        "huerfanos_snapshot": huerfanos_snapshot,
        "sospechosos": sospechosos,
        "sin_parsear": sin_parsear,
        "colisiones": colisiones,
    }


def detectar_huerfanos_universo(filas_propuestas: list, universo: dict) -> list:
    """
    PASO 6 (segunda dirección): tickers del universo que NO aparecieron en
    el snapshot. Esperable que haya varios: reMarkets sandbox tiene un
    catálogo reducido (~61 MERV) frente a los ~76 del universo. El poblado
    completo depende de producción BIND.
    """
    tickers_mapeados = {f["ticker_base"] for f in filas_propuestas}
    huerfanos = []
    for ticker, datos in universo.items():
        if ticker not in tickers_mapeados:
            huerfanos.append({"ticker": ticker, "tipo": datos["tipo"], "nombre": datos["nombre"]})
    return huerfanos


# ----------------------------------------------------------------------------
# PASO 7 — Salida: JSON propuesto + reporte
# ----------------------------------------------------------------------------

def escribir_salida(resultado: dict, huerfanos_universo: list,
                    metadata_snapshot: dict, conteos_filtro: dict) -> Path:
    """
    Escribe el mapeo propuesto a data/processed/ como JSON.
    Devuelve la ruta del archivo escrito.
    """
    DIR_SALIDA.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ruta = DIR_SALIDA / f"mapeo_primary_propuesto_{fecha}.json"

    # Limpiamos los campos auxiliares (prefijo _) de las filas: el JSON
    # propuesto debe tener solo lo que va a la tabla.
    filas_limpias = []
    for f in resultado["filas_propuestas"]:
        filas_limpias.append({k: v for k, v in f.items() if not k.startswith("_")})

    salida = {
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        "broker": BROKER,
        "segmento": SEGMENTO_OBJETIVO,
        "snapshot_origen": metadata_snapshot,
        "resumen": {
            "filas_propuestas": len(filas_limpias),
            "huerfanos_snapshot": len(resultado["huerfanos_snapshot"]),
            "huerfanos_universo": len(huerfanos_universo),
            "sospechosos_currency": len(resultado["sospechosos"]),
            "sin_parsear": len(resultado["sin_parsear"]),
            "colisiones_constraint": len(resultado["colisiones"]),
        },
        "filas_propuestas": filas_limpias,
        "huerfanos_snapshot": resultado["huerfanos_snapshot"],
        "huerfanos_universo": huerfanos_universo,
        "sospechosos_currency": resultado["sospechosos"],
        "sin_parsear": resultado["sin_parsear"],
        "colisiones_constraint": [list(c) for c in resultado["colisiones"]],
    }

    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(salida, f, indent=2, ensure_ascii=False)

    return ruta


def imprimir_reporte(resultado: dict, huerfanos_universo: list,
                     conteos_filtro: dict, ruta_salida: Path) -> None:
    """Imprime un reporte legible por consola."""
    filas = resultado["filas_propuestas"]

    # Desglose por variante.
    por_variante = {"base": 0, "D": 0, "C": 0}
    for f in filas:
        por_variante[f["_variante"]] += 1

    print()
    print("=" * 70)
    print("  MAPEO PRIMARY PROPUESTO — H1.2.5.5-bis")
    print("=" * 70)
    print()
    print("FILTRO DEL SNAPSHOT (3 capas):")
    print(f"  Total instrumentos snapshot : {conteos_filtro['total']}")
    print(f"  Descartados (no MERV)       : {conteos_filtro['no_merv']}")
    print(f"  MERV total                  : {conteos_filtro['merv_total']}")
    print(f"  Descartados (CFICode O/etc) : {conteos_filtro['descartados_cficode']}")
    print(f"  Sin CFICode                 : {conteos_filtro['sin_cficode']}")
    print(f"  Pasaron a parseo            : {conteos_filtro['pasaron']}")
    print()
    print("MAPEO RESULTANTE:")
    print(f"  Filas propuestas            : {len(filas)}")
    print(f"    - variante base (ARS)     : {por_variante['base']}")
    print(f"    - variante D (USD_MEP)    : {por_variante['D']}")
    print(f"    - variante C (USD_CCL)    : {por_variante['C']}")
    print()
    print("REVISIÓN REQUERIDA:")
    print(f"  Huérfanos snapshot          : {len(resultado['huerfanos_snapshot'])}  (símbolos MERV sin ticker en universo)")
    print(f"  Huérfanos universo          : {len(huerfanos_universo)}  (tickers del universo sin símbolo en reMarkets)")
    print(f"  Sospechosos por currency    : {len(resultado['sospechosos'])}")
    print(f"  Símbolos sin parsear        : {len(resultado['sin_parsear'])}")
    print(f"  Colisiones de constraint    : {len(resultado['colisiones'])}")
    print()

    if resultado["sin_parsear"]:
        print("  -- SÍMBOLOS SIN PARSEAR --")
        for s in resultado["sin_parsear"]:
            print(f"     {s!r}")
        print()

    if resultado["colisiones"]:
        print("  -- COLISIONES DE UNIQUE CONSTRAINT (broker, symbol, plazo) --")
        for c in resultado["colisiones"]:
            print(f"     {c}")
        print()

    if resultado["sospechosos"]:
        print("  -- SOSPECHOSOS POR CURRENCY (ticker manda, revisar) --")
        for s in resultado["sospechosos"]:
            print(f"     {s['symbol']}: variante {s['variante']}, "
                  f"currency reMarkets='{s['currency_remarkets']}', "
                  f"esperaba {s['currency_esperada']}")
        print()

    if resultado["huerfanos_snapshot"]:
        print("  -- HUÉRFANOS DEL SNAPSHOT (ruido de reMarkets) --")
        for h in resultado["huerfanos_snapshot"]:
            print(f"     {h['symbol']}  ({h['motivo']})")
        print()

    if huerfanos_universo:
        print("  -- HUÉRFANOS DEL UNIVERSO (faltan en reMarkets sandbox) --")
        for h in huerfanos_universo:
            print(f"     {h['ticker']:10s} {h['tipo']:14s} {h['nombre']}")
        print()

    print(f"JSON propuesto escrito en:")
    print(f"  {ruta_salida}")
    print()
    print("PRÓXIMO PASO: revisión humana del JSON (H1.2.5.6).")
    print("Este script NO escribió nada a la base.")
    print("=" * 70)


# ----------------------------------------------------------------------------
# ORQUESTACIÓN
# ----------------------------------------------------------------------------

def main() -> None:
    _log.info("Iniciando generación de mapeo Primary (H1.2.5.5-bis).")

    # Paso 1
    universo = cargar_universo()

    # Paso 2
    ruta_snapshot = encontrar_snapshot_mas_reciente()
    metadata_snapshot, instrumentos = cargar_snapshot(ruta_snapshot)
    instrumentos_merv, conteos_filtro = filtrar_merv(instrumentos)

    # Pasos 3-6
    resultado = construir_mapeo(instrumentos_merv, universo)
    huerfanos_universo = detectar_huerfanos_universo(
        resultado["filas_propuestas"], universo
    )

    # Paso 7
    ruta_salida = escribir_salida(
        resultado, huerfanos_universo, metadata_snapshot, conteos_filtro
    )
    imprimir_reporte(resultado, huerfanos_universo, conteos_filtro, ruta_salida)

    _log.info(
        f"Mapeo generado: {len(resultado['filas_propuestas'])} filas propuestas, "
        f"{len(resultado['sospechosos'])} sospechosos, "
        f"{len(huerfanos_universo)} huérfanos de universo."
    )


if __name__ == "__main__":
    main()