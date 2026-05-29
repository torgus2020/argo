"""
Generador de mapeo Argo <-> Primary/Matriz — H1.6

QUÉ HACE
--------
Cruza los instrumentos del universo de Argo (tabla `instrumentos` de la base)
contra el snapshot detallado del catálogo PRODUCTIVO de Primary/Matriz (BIND),
y produce un MAPEO PROPUESTO de filas listas para la tabla
`instrumento_broker_mapping`.

NOTA SOBRE SANDBOX vs PRODUCTIVO
--------------------------------
Este script nació apuntando a reMarkets (sandbox) y ahora apunta al catálogo
productivo de BIND, que es la fuente de verdad. La maquinaria (parseo,
desambiguación D/C, detección de huérfanos) es permanente y transfirió intacta:
la API de pyRofex devuelve la misma estructura por instrumento en ambos
entornos. Lo único que cambió es el envoltorio de afuera (clave 'instruments'
en inglés en el productivo) y el nombre del archivo de snapshot.

SEMÁNTICA DE CURRENCY EN PRODUCCIÓN (aprendizaje clave de H1.6)
--------------------------------------------------------------
El catálogo productivo NO distingue MEP de CCL por el campo `currency`: las
dos variantes dólar (sufijo D = MEP, sufijo C = CCL) reportan ambas 'USD'.
Solo la variante base (pesos) reporta 'ARS'. Por eso `currency` sirve para
distinguir PESO vs DÓLAR, pero NO MEP vs CCL — eso lo da exclusivamente el
sufijo del ticker. Esto cambió respecto a reMarkets, donde CCL reportaba 'EXT'.

CURRENCY COMO COMPUERTA DE RECHAZO
----------------------------------
En vez de mapear igual y avisar (como hacíamos en sandbox con 'sospechosos'),
ahora currency RECHAZA: si la variante deducida por el ticker contradice la
moneda real (ej: dedujo CCL pero cotiza en ARS), la fila NO se propone y va a
`rechazados_por_currency`. Es más conservador: preferimos proponer de menos y
que el operador sume a mano, antes que colar una fila mal al poblado.

QUÉ NO HACE (a propósito)
-------------------------
NO escribe nada a la base. El flujo del proyecto es: descubrir (este script) ->
revisar humano -> poblar. Este script solo descubre. Su salida es un JSON
propuesto + un reporte legible.

LÓGICA EN 7 PASOS
-----------------
1. Cargar universo: tickers base mapeables desde la tabla `instrumentos`.
2. Filtrar el snapshot en 3 capas: segmento MERV -> CFICode válido -> ticker
   parseable.
3. Parsear cada símbolo Primary de forma robusta (regex tolerante a espacios
   y al punto en el ticker).
4. Desambiguar ticker base vs variante D/C, cruzando contra el universo.
   El ticker COMPLETO se chequea primero — así YPFD se resuelve como base
   y no como YPF+D.
5. Compuerta de currency: el ticker propone, la moneda real valida o rechaza.
   Lo incoherente no se propone.
6. Detección de huérfanos bidireccional (snapshot sin universo / universo
   sin snapshot).
7. Salida: JSON propuesto en data/processed/ + reporte por consola.

CONVENCIÓN DE EJECUCIÓN
-----------------------
Correr desde la raíz del proyecto:
    python -m scripts.generar_mapeo_primary
o directamente:
    python scripts/generar_mapeo_primary.py

Por defecto agarra el snapshot productivo más reciente de data/raw/. Para
forzar un snapshot específico:
    python -m scripts.generar_mapeo_primary data/raw/otro_snapshot.json
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

# Carpeta donde viven los snapshots crudos del catálogo de Primary/Matriz.
DIR_SNAPSHOTS = RAIZ_PROYECTO / "data" / "raw"

# Patrón del snapshot PRODUCTIVO (lo genera validar_handshake_primary_produccion.py).
PATRON_SNAPSHOT_PRODUCTIVO = "primary_produccion_catalogo_*.json"

# Carpeta donde se deja el mapeo propuesto (salida del script).
DIR_SALIDA = RAIZ_PROYECTO / "data" / "processed"

# Tope de elementos a listar por consola para listas potencialmente enormes.
# El JSON las guarda completas; la consola muestra una muestra para ser legible.
MAX_LISTAR_CONSOLA = 25

# Broker al que corresponde este mapeo. Hoy solo Primary; el modelo soporta
# múltiples brokers, por eso queda explícito y no hardcodeado en cada fila.
BROKER = "primary"

# Segmento de mercado que Argo opera. Filtramos TODO lo que no sea esto.
SEGMENTO_OBJETIVO = "MERV"

# Prefijos de CFICode que nos interesan. El primer carácter del CFICode
# clasifica el instrumento:
#   E... = acción (incluye CEDEARs, que figuran como EMXXXX)
#   D... = deuda (bonos, ON)
# Descartamos explícitamente O... = opciones, F... = futuros, R... = repos.
# OJO: las ONs (DBXXFR) PASAN este filtro (prefijo D). Como no las trackeamos
# en el universo, terminan en huérfanos del snapshot — esperable y correcto.
CFICODES_VALIDOS_PREFIJOS = ("E", "D")

# Tipos de instrumento de la tabla `instrumentos` que son mapeables contra
# Primary (es decir, que cotizan en BYMA). Las macro (BCRA/INDEC), los
# subyacentes US (Polygon) y los calculados NO tienen símbolo de mercado AR.
# NOTA: boncap y boncer se agregaron en Step B (H1.6). Antes faltaban y dejaban
# seis bonos en pesos vivos invisibles al mapeo (TO26, TO27, TX26, TX28,
# TZX27, TZX28).
TIPOS_MAPEABLES = {
    "bono_usd_ar",
    "bono_pesos_ar",
    "bopreal",
    "lecap",
    "boncap",
    "boncer",
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

# Compuerta de currency: qué valor de `currency` (campo de Primary) se espera
# para cada variante, en SEMÁNTICA PRODUCTIVA.
#   ARS = pesos
#   USD = dólar (tanto MEP como CCL; producción no los distingue por currency)
# Si la moneda real contradice esto, la fila se RECHAZA (no se propone).
VARIANTE_A_CURRENCY_ESPERADA = {
    "base": {"ARS"},
    "D": {"USD"},
    "C": {"USD"},
}

# Plazos válidos de Primary. CI = Contado Inmediato (T+0), 24hs = T+1.
PLAZOS_VALIDOS = {"CI", "24hs", "48hs"}

# Regex tolerante para parsear el símbolo Primary.
# Formato nominal: "MERV - XMEV - {TICKER} - {PLAZO}"
# El \s*-\s* acepta cero o más espacios alrededor del guion.
# El ticker admite letras, dígitos Y PUNTO: en producción hay tickers base
# con punto (BA.C = Bank of America, AKO.B = Embotelladora Andina clase B,
# BMA.X = cupón Banco Macro). El punto se permite pero NO al inicio/fin del
# ticker, para no tragar separadores raros.
PATRON_SIMBOLO = re.compile(
    r"^\s*([A-Z]+)\s*-\s*([A-Z]+)\s*-\s*([A-Z0-9](?:[A-Z0-9.]*[A-Z0-9])?)\s*-\s*([A-Za-z0-9]+)\s*$"
)


# ----------------------------------------------------------------------------
# PASO 1 — Cargar el universo de tickers base
# ----------------------------------------------------------------------------

def cargar_universo() -> dict:
    """
    Lee la tabla `instrumentos` y devuelve un dict {ticker: datos} con SOLO
    los instrumentos mapeables contra Primary (tipos en TIPOS_MAPEABLES).

    El ticker es la clave de cruce contra los símbolos del snapshot.
    Solo se incluyen instrumentos activos: los jubilados (activo=False) no se
    mapean (ej: los zombies BAC/DIS de BYMA, o las LECAP vencidas).
    """
    universo = {}
    with get_session() as session:
        filas = session.query(Instrumento).all()
        for inst in filas:
            if inst.tipo not in TIPOS_MAPEABLES:
                continue
            if not inst.activo:
                continue
            universo[inst.ticker] = {
                "instrumento_id": inst.id,
                "ticker": inst.ticker,
                "tipo": inst.tipo,
                "nombre": inst.nombre,
                "moneda": inst.moneda,
            }

    if not universo:
        _log.warning(
            "Universo VACÍO: la tabla `instrumentos` no devolvió tickers "
            "mapeables. Sin universo no hay nada que mapear — revisar que el "
            "universo esté cargado en la base."
        )
    _log.info(f"Universo cargado: {len(universo)} tickers base mapeables.")
    return universo


# ----------------------------------------------------------------------------
# PASO 2 (parcial) — Cargar y filtrar el snapshot
# ----------------------------------------------------------------------------

def encontrar_snapshot_mas_reciente() -> Path:
    """
    Busca en data/raw/ el snapshot PRODUCTIVO más reciente de Primary/Matriz.
    Los productivos se llaman 'primary_produccion_catalogo_YYYYMMDD.json'.

    Lanza FileNotFoundError si no hay ninguno.
    """
    candidatos = sorted(DIR_SNAPSHOTS.glob(PATRON_SNAPSHOT_PRODUCTIVO))
    if not candidatos:
        raise FileNotFoundError(
            f"No se encontró ningún snapshot productivo "
            f"({PATRON_SNAPSHOT_PRODUCTIVO}) en {DIR_SNAPSHOTS}. "
            f"Correr primero validar_handshake_primary_produccion.py, "
            f"o pasar una ruta explícita como argumento."
        )
    elegido = candidatos[-1]  # YYYYMMDD: orden alfabético == cronológico
    _log.info(f"Snapshot elegido: {elegido.name}")
    return elegido


def cargar_snapshot(ruta: Path) -> tuple[dict, list]:
    """
    Lee un snapshot detallado. Devuelve (metadata, lista_instrumentos).

    Acepta dos nombres de clave para la lista:
      - 'instruments'  (inglés)  -> snapshot productivo de BIND (pyRofex crudo)
      - 'instrumentos' (español) -> viejo snapshot de reMarkets
    """
    with open(ruta, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"El snapshot {ruta.name} no tiene la estructura esperada "
            f"(se esperaba un dict con la lista de instrumentos)."
        )

    if "instruments" in data:
        clave_lista = "instruments"
    elif "instrumentos" in data:
        clave_lista = "instrumentos"
    else:
        raise ValueError(
            f"El snapshot {ruta.name} no tiene clave de lista reconocible "
            f"('instruments' o 'instrumentos'). Claves presentes: "
            f"{sorted(data.keys())}."
        )

    metadata = {k: v for k, v in data.items() if k != clave_lista}
    instrumentos = data[clave_lista]
    _log.info(
        f"Snapshot cargado: {len(instrumentos)} instrumentos totales "
        f"(clave '{clave_lista}', metadata: {metadata})."
    )
    return metadata, instrumentos


def filtrar_merv(instrumentos: list) -> tuple[list, dict]:
    """
    CAPA 1 y 2 del filtro de 3 capas:
      Capa 1: segmento == MERV
      Capa 2: CFICode con prefijo válido (descarta opciones/futuros/repos)
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
            conteos["sin_cficode"] += 1
            continue
        if not cficode.startswith(CFICODES_VALIDOS_PREFIJOS):
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
    Devuelve dict {mercado, prefijo, ticker, plazo} o None si no matchea.
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
    Determina si el ticker es base, variante D (MEP), variante C (CCL), o None.

    ORDEN CRÍTICO: se chequea el ticker COMPLETO contra el universo PRIMERO.
    Así 'YPFD' (CEDEAR de YPF) se resuelve como base, no como YPF+D. Y 'BA.C'
    (Bank of America, con punto en la base) se resuelve como base, no como
    'BA.' + sufijo C. La heurística de sufijo solo se aplica si el ticker
    completo no está en el universo.

    LÍMITE CONOCIDO: muchas ONs tienen tickers que terminan en D/C/O como parte
    de su propio nombre. La salvaguarda es que solo clasificamos como variante
    si la base SIN la letra está en el universo. El riesgo residual (un ticker
    'XYZD' cuya base 'XYZ' coincida con un ticker real) lo ataja la compuerta
    de currency en el PASO 5.
    """
    # 1. ¿Es un ticker base tal cual? (incluye tickers con punto: BA.C, AKO.B)
    if ticker_simbolo in universo:
        return {
            "ticker_base": ticker_simbolo,
            "variante": "base",
            "instrumento_id": universo[ticker_simbolo]["instrumento_id"],
        }

    # 2. ¿Termina en D y el resto es un ticker base? (BA.CD -> BA.C + D)
    if ticker_simbolo.endswith("D"):
        candidato = ticker_simbolo[:-1]
        if candidato in universo:
            return {
                "ticker_base": candidato,
                "variante": "D",
                "instrumento_id": universo[candidato]["instrumento_id"],
            }

    # 3. ¿Termina en C y el resto es un ticker base? (BA.CC -> BA.C + C)
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
# PASO 5 — Compuerta de currency
# ----------------------------------------------------------------------------

def currency_coherente(variante: str, currency_primary: str) -> bool:
    """
    Chequea que el `currency` que reporta Primary sea coherente con la variante
    deducida por el ticker. True = coherente (se propone), False = se rechaza.

    Recordatorio de semántica productiva: base->ARS, D->USD, C->USD.
    """
    esperadas = VARIANTE_A_CURRENCY_ESPERADA.get(variante, set())
    return currency_primary in esperadas


# ----------------------------------------------------------------------------
# PASOS 4-6 — Procesar todos los instrumentos filtrados
# ----------------------------------------------------------------------------

def construir_mapeo(instrumentos_merv: list, universo: dict) -> dict:
    """
    Recorre los instrumentos MERV filtrados y construye:
      - filas_propuestas: dicts listos para instrumento_broker_mapping
      - huerfanos_snapshot: símbolos MERV que no matchean ningún ticker
      - rechazados_por_currency: deducción del ticker contradicha por la moneda
        real -> NO se proponen (compuerta de rechazo)
      - sin_parsear: símbolos que no matchean el patrón regex
      - colisiones: violaciones del UniqueConstraint (broker, symbol, plazo)
      - revision_manual: filas propuestas pero con ticker con punto en la base
        (estructuralmente ambiguas) -> se mapean pero se marcan para que el
        operador les pase el ojo antes de poblar
    """
    filas_propuestas = []
    huerfanos_snapshot = []
    rechazados_por_currency = []
    sin_parsear = []
    revision_manual = []

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

        if plazo not in PLAZOS_VALIDOS:
            huerfanos_snapshot.append(
                {"symbol": simbolo, "motivo": f"plazo desconocido: {plazo}"}
            )
            continue

        # PASO 4: desambiguar variante
        resuelto = resolver_variante(ticker_simbolo, universo)
        if resuelto is None:
            huerfanos_snapshot.append(
                {"symbol": simbolo, "motivo": f"ticker '{ticker_simbolo}' no está en el universo"}
            )
            continue

        variante = resuelto["variante"]
        currency_primary = inst.get("currency")

        # PASO 5: COMPUERTA de currency. Si la moneda contradice la variante
        # deducida, NO se propone — va a rechazados.
        if not currency_coherente(variante, currency_primary):
            rechazados_por_currency.append(
                {
                    "symbol": simbolo,
                    "ticker_base_deducido": resuelto["ticker_base"],
                    "variante_deducida": variante,
                    "currency_primary": currency_primary,
                    "currency_esperada": sorted(VARIANTE_A_CURRENCY_ESPERADA[variante]),
                    "motivo": "la moneda real contradice la variante deducida por el ticker",
                }
            )
            continue

        # ¿La base tiene punto? -> estructuralmente ambigua, marcar para revisión.
        tiene_punto = "." in resuelto["ticker_base"]

        metadata = {
            "cficode": inst.get("cficode"),
            "currency_primary": currency_primary,
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
            "es_default": (variante == "base" and plazo == "CI"),
            "activo": True,
            "requiere_revision_manual": tiene_punto,
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "fecha_validacion": None,
            "_variante": variante,  # auxiliar para el reporte
        }

        clave_unica = (BROKER, simbolo, plazo)
        if clave_unica in claves_unicas_vistas:
            colisiones.append(clave_unica)
        else:
            claves_unicas_vistas.add(clave_unica)

        filas_propuestas.append(fila)

        if tiene_punto:
            revision_manual.append(
                {
                    "symbol": simbolo,
                    "ticker_base": resuelto["ticker_base"],
                    "variante": variante,
                    "underlying": inst.get("underlying"),
                    "currency_primary": currency_primary,
                }
            )

    return {
        "filas_propuestas": filas_propuestas,
        "huerfanos_snapshot": huerfanos_snapshot,
        "rechazados_por_currency": rechazados_por_currency,
        "sin_parsear": sin_parsear,
        "colisiones": colisiones,
        "revision_manual": revision_manual,
    }


def detectar_huerfanos_universo(filas_propuestas: list, universo: dict) -> list:
    """
    PASO 6 (segunda dirección): tickers del universo que NO aparecieron en el
    snapshot. Contra el catálogo productivo debería quedar cerca de cero; cada
    huérfano acá es señal real (typo, ticker deslistado, no cotiza por Primary).
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
    """Escribe el mapeo propuesto a data/processed/ como JSON."""
    DIR_SALIDA.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ruta = DIR_SALIDA / f"mapeo_primary_propuesto_{fecha}.json"

    # Limpiamos los campos auxiliares (prefijo _) de las filas.
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
            "requiere_revision_manual": len(resultado["revision_manual"]),
            "huerfanos_snapshot": len(resultado["huerfanos_snapshot"]),
            "huerfanos_universo": len(huerfanos_universo),
            "rechazados_por_currency": len(resultado["rechazados_por_currency"]),
            "sin_parsear": len(resultado["sin_parsear"]),
            "colisiones_constraint": len(resultado["colisiones"]),
        },
        "filas_propuestas": filas_limpias,
        "revision_manual": resultado["revision_manual"],
        "huerfanos_snapshot": resultado["huerfanos_snapshot"],
        "huerfanos_universo": huerfanos_universo,
        "rechazados_por_currency": resultado["rechazados_por_currency"],
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

    por_variante = {"base": 0, "D": 0, "C": 0}
    for f in filas:
        por_variante[f["_variante"]] += 1

    print()
    print("=" * 70)
    print("  MAPEO PRIMARY PROPUESTO — H1.6 (catálogo productivo BIND)")
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
    print(f"    de las cuales revisión    : {len(resultado['revision_manual'])}  (ticker con punto en la base)")
    print()
    print("REVISIÓN REQUERIDA:")
    print(f"  Huérfanos snapshot          : {len(resultado['huerfanos_snapshot'])}  (símbolos MERV sin ticker en universo — esperable: miles)")
    print(f"  Huérfanos universo          : {len(huerfanos_universo)}  (tickers del universo sin símbolo en Primary — debería ser ~0)")
    print(f"  Rechazados por currency     : {len(resultado['rechazados_por_currency'])}  (deducción del ticker contradicha por la moneda)")
    print(f"  Símbolos sin parsear        : {len(resultado['sin_parsear'])}")
    print(f"  Colisiones de constraint    : {len(resultado['colisiones'])}")
    print()

    if resultado["revision_manual"]:
        print("  -- REVISIÓN MANUAL (ticker con punto, estructuralmente ambiguo) --")
        for r in resultado["revision_manual"]:
            print(f"     {r['symbol']}  base={r['ticker_base']} variante={r['variante']} "
                  f"currency={r['currency_primary']} underlying={r['underlying']!r}")
        print()

    if resultado["rechazados_por_currency"]:
        print(f"  -- RECHAZADOS POR CURRENCY (primeros {MAX_LISTAR_CONSOLA}) --")
        for r in resultado["rechazados_por_currency"][:MAX_LISTAR_CONSOLA]:
            print(f"     {r['symbol']}: dedujo {r['variante_deducida']} (base {r['ticker_base_deducido']}), "
                  f"currency='{r['currency_primary']}', esperaba {r['currency_esperada']}")
        if len(resultado["rechazados_por_currency"]) > MAX_LISTAR_CONSOLA:
            print(f"     ... y {len(resultado['rechazados_por_currency']) - MAX_LISTAR_CONSOLA} más (ver JSON).")
        print()

    sin_parsear = resultado["sin_parsear"]
    if sin_parsear:
        print(f"  -- SÍMBOLOS SIN PARSEAR (primeros {MAX_LISTAR_CONSOLA}) --")
        for s in sin_parsear[:MAX_LISTAR_CONSOLA]:
            print(f"     {s!r}")
        if len(sin_parsear) > MAX_LISTAR_CONSOLA:
            print(f"     ... y {len(sin_parsear) - MAX_LISTAR_CONSOLA} más (ver JSON).")
        print()

    if resultado["colisiones"]:
        print("  -- COLISIONES DE UNIQUE CONSTRAINT (broker, symbol, plazo) --")
        for c in resultado["colisiones"]:
            print(f"     {c}")
        print()

    huerfanos_snap = resultado["huerfanos_snapshot"]
    if huerfanos_snap:
        print(f"  -- HUÉRFANOS DEL SNAPSHOT (primeros {MAX_LISTAR_CONSOLA}) --")
        for h in huerfanos_snap[:MAX_LISTAR_CONSOLA]:
            print(f"     {h['symbol']}  ({h['motivo']})")
        if len(huerfanos_snap) > MAX_LISTAR_CONSOLA:
            print(f"     ... y {len(huerfanos_snap) - MAX_LISTAR_CONSOLA} más (ver JSON).")
        print()

    if huerfanos_universo:
        print("  -- HUÉRFANOS DEL UNIVERSO (faltan en Primary — REVISAR) --")
        for h in huerfanos_universo:
            print(f"     {h['ticker']:10s} {h['tipo']:14s} {h['nombre']}")
        print()

    print(f"JSON propuesto escrito en:")
    print(f"  {ruta_salida}")
    print()
    print("PRÓXIMO PASO: revisión humana del JSON.")
    print("Este script NO escribió nada a la base.")
    print("=" * 70)


# ----------------------------------------------------------------------------
# ORQUESTACIÓN
# ----------------------------------------------------------------------------

def main(ruta_snapshot_explicita: str | None = None) -> None:
    _log.info("Iniciando generación de mapeo Primary (H1.6, catálogo productivo).")

    universo = cargar_universo()

    if ruta_snapshot_explicita:
        ruta_snapshot = Path(ruta_snapshot_explicita)
        if not ruta_snapshot.exists():
            raise FileNotFoundError(f"No existe el snapshot indicado: {ruta_snapshot}")
        _log.info(f"Snapshot forzado por argumento: {ruta_snapshot.name}")
    else:
        ruta_snapshot = encontrar_snapshot_mas_reciente()

    metadata_snapshot, instrumentos = cargar_snapshot(ruta_snapshot)
    instrumentos_merv, conteos_filtro = filtrar_merv(instrumentos)

    resultado = construir_mapeo(instrumentos_merv, universo)
    huerfanos_universo = detectar_huerfanos_universo(
        resultado["filas_propuestas"], universo
    )

    ruta_salida = escribir_salida(
        resultado, huerfanos_universo, metadata_snapshot, conteos_filtro
    )
    imprimir_reporte(resultado, huerfanos_universo, conteos_filtro, ruta_salida)

    _log.info(
        f"Mapeo generado: {len(resultado['filas_propuestas'])} filas propuestas, "
        f"{len(resultado['rechazados_por_currency'])} rechazados por currency, "
        f"{len(resultado['revision_manual'])} para revisión manual, "
        f"{len(huerfanos_universo)} huérfanos de universo."
    )


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(arg)