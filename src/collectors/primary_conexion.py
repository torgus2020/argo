"""
Conexión a Primary/Matriz en PRODUCCIÓN (BIND) vía pyRofex 0.5.0.

Este módulo encapsula el patrón de conexión a producción para que ningún
otro módulo (collector, ejecución, etc.) tenga que reimplementar el detalle
de bajo nivel. La lógica vive en un solo lugar.

Por qué hace falta un override de host (contexto del proyecto):
pyRofex 0.5.0 trae el environment LIVE apuntando de fábrica al host de
Primary (api.primary.com.ar). Gus opera por BIND, cuyo host es
api.bindinversiones.matrizoms.com.ar. Hay que sobreescribir DOS parámetros
del environment LIVE ANTES de initialize:
    - "url" → endpoint REST  (https://{host}/)
    - "ws"  → endpoint WebSocket (wss://{host}/)
La firma de pyRofex.initialize NO acepta host custom; el override por
_set_environment_parameter es el único mecanismo. Sin él, pyRofex autentica
contra el host equivocado. Validado en handshake 2026-06-02 (status OK).

Dos responsabilidades, dos funciones separadas (modularidad estricta):
    - conectar_primary_produccion(): entra (override de host + autenticación).
    - verificar_salud_conexion(): comprueba que llega dato real (pide un
      instrumento testigo y valida la respuesta).
Se separan porque "conectar" y "recibir datos" pueden fallar por motivos
distintos; mezclarlas ocultaría cuál de las dos se rompió.
"""

import json
from pathlib import Path
from typing import Optional

import pyRofex
from pyRofex.components.enums import Environment

from src.utils.logger import obtener_logger_collector


log = obtener_logger_collector("primary")

# Raíz del proyecto y ruta a secrets.json (credenciales NO versionadas).
_RAIZ_PROYECTO = Path(__file__).resolve().parent.parent.parent
_RUTA_SECRETS = _RAIZ_PROYECTO / "config" / "secrets.json"

# Bloque de secrets que contiene las credenciales de producción.
_BLOQUE_PRODUCCION = "primary_produccion"

# Claves que el bloque de producción debe tener sí o sí.
_CLAVES_REQUERIDAS = {"user", "password", "account", "endpoint"}

# Instrumento testigo para verificar salud: AL30 contado inmediato.
# Bono hiperlíquido del panel, casi siempre tiene libro en horario de mercado.
# Formato de símbolo Primary (convención del proyecto).
_SIMBOLO_TESTIGO = "MERV - XMEV - AL30 - CI"


class ErrorConexionPrimary(Exception):
    """Error de conexión/configuración contra Primary. Se lanza ruidosamente
    para que el llamador (collector) decida si abortar o reintentar."""


def _cargar_credenciales() -> dict:
    """
    Lee el bloque primary_produccion de secrets.json y valida que estén
    todas las claves requeridas. Falla ruidosamente si falta algo: una
    conexión a medias es peor que no conectar.
    """
    try:
        with open(_RUTA_SECRETS, encoding="utf-8") as f:
            secrets = json.load(f)
    except FileNotFoundError:
        raise ErrorConexionPrimary(
            f"No se encontró secrets.json en {_RUTA_SECRETS}"
        )
    except json.JSONDecodeError as e:
        raise ErrorConexionPrimary(f"secrets.json no es JSON válido: {e}")

    # El bloque vive en la raíz; toleramos también que esté bajo 'brokers'.
    bloque = secrets.get(_BLOQUE_PRODUCCION) or secrets.get("brokers", {}).get(
        _BLOQUE_PRODUCCION
    )
    if bloque is None:
        raise ErrorConexionPrimary(
            f"No existe el bloque '{_BLOQUE_PRODUCCION}' en secrets.json"
        )

    faltantes = _CLAVES_REQUERIDAS - set(bloque.keys())
    if faltantes:
        raise ErrorConexionPrimary(
            f"Al bloque '{_BLOQUE_PRODUCCION}' le faltan claves: {sorted(faltantes)}"
        )

    # Defensa contra valores vacíos (presente pero en blanco).
    vacias = [k for k in _CLAVES_REQUERIDAS if not str(bloque[k]).strip()]
    if vacias:
        raise ErrorConexionPrimary(
            f"El bloque '{_BLOQUE_PRODUCCION}' tiene claves vacías: {sorted(vacias)}"
        )

    return bloque


def _construir_urls_desde_host(host_pelado: str) -> tuple[str, str]:
    """
    Construye (url_rest, url_ws) completas a partir del host pelado guardado
    en secrets (ej. 'api.bindinversiones.matrizoms.com.ar', sin esquema ni
    barra). pyRofex espera 'https://host/' y 'wss://host/'.

    Es defensivo: si por error el host viniera con esquema, lo limpia para
    no duplicarlo.
    """
    host = host_pelado.strip().rstrip("/")
    for prefijo in ("https://", "http://", "wss://", "ws://"):
        if host.startswith(prefijo):
            host = host[len(prefijo):]
    url_rest = f"https://{host}/"
    url_ws = f"wss://{host}/"
    return url_rest, url_ws


def conectar_primary_produccion() -> dict:
    """
    Conecta y autentica contra Primary PRODUCCIÓN (BIND).

    Pasos:
      1. Carga y valida credenciales de secrets.json.
      2. Sobreescribe el host del environment LIVE para apuntar a BIND
         (url para REST, ws para WebSocket), ANTES de initialize.
      3. Inicializa pyRofex y autentica.

    NO verifica que lleguen datos: eso es responsabilidad de
    verificar_salud_conexion(). Esta función solo "entra".

    Devuelve un dict con info de la conexión (host, cuenta) para logging/
    diagnóstico del llamador. Lanza ErrorConexionPrimary si algo falla.
    """
    log.info("Conectando a Primary PRODUCCIÓN (BIND)...")

    creds = _cargar_credenciales()
    url_rest, url_ws = _construir_urls_desde_host(creds["endpoint"])

    # Override de host sobre LIVE, ANTES de initialize. Es el único mecanismo
    # para apuntar a BIND (ver docstring del módulo).
    pyRofex._set_environment_parameter("url", url_rest, Environment.LIVE)
    pyRofex._set_environment_parameter("ws", url_ws, Environment.LIVE)
    log.info(f"Host LIVE redirigido a BIND -> REST={url_rest} WS={url_ws}")

    try:
        pyRofex.initialize(
            user=creds["user"],
            password=creds["password"],
            account=creds["account"],
            environment=Environment.LIVE,
        )
    except Exception as e:
        log.error(f"Falló initialize/autenticación contra BIND: {e}", exc_info=True)
        raise ErrorConexionPrimary(f"No se pudo autenticar contra BIND: {e}")

    log.info(f"Conexión a producción BIND establecida (cuenta {creds['account']}).")
    return {
        "host_rest": url_rest,
        "host_ws": url_ws,
        "cuenta": creds["account"],
        "environment": "LIVE",
    }


def verificar_salud_conexion(simbolo_testigo: str = _SIMBOLO_TESTIGO) -> dict:
    """
    Comprueba que la conexión devuelve DATO REAL, no solo que autenticó.

    Pide market data del instrumento testigo (AL30 CI por defecto) y evalúa
    la respuesta. Debe llamarse DESPUÉS de conectar_primary_produccion().

    Distingue tres situaciones, sin mentir sobre ninguna:
      - "sano_con_precio": hay último precio o libro -> producción viva, dato real.
      - "sano_sin_precio": status OK pero libro vacío -> conexión bien, pero
         sin precio (típicamente mercado cerrado/pre-apertura). NO es fallo.
      - lanza ErrorConexionPrimary si la respuesta no tiene status OK o
        revienta: ahí sí hay un problema real.

    Importante sobre magnitudes: el precio viene en magnitud de pantalla
    (no se aplica priceConvertionFactor acá; el factor entra al valuar
    posición, no a mostrar la cotización). NO validamos rangos de precio
    en este chequeo: rangos "de cordura" hardcodeados son frágiles (ya nos
    pasó: AL30 cotiza en decenas de miles de pesos, no en decenas). El
    chequeo de salud confirma "hay dato", no "el dato vale X".

    Devuelve un dict con el diagnóstico para que el llamador decida.
    """
    log.info(f"Verificando salud de conexión con testigo {simbolo_testigo}...")

    try:
        md = pyRofex.get_market_data(
            ticker=simbolo_testigo,
            entries=[
                pyRofex.MarketDataEntry.LAST,
                pyRofex.MarketDataEntry.BIDS,
                pyRofex.MarketDataEntry.OFFERS,
            ],
        )
    except Exception as e:
        log.error(f"get_market_data lanzó excepción: {e}", exc_info=True)
        raise ErrorConexionPrimary(f"Falló get_market_data del testigo: {e}")

    log.info(f"Respuesta cruda de market data: {md}")

    if not isinstance(md, dict) or md.get("status") != "OK":
        log.error(f"Market data sin status OK: {md}")
        raise ErrorConexionPrimary(f"Respuesta sin status OK: {md}")

    market_data = md.get("marketData", {})
    last = market_data.get("LA")
    bids = market_data.get("BI")
    offers = market_data.get("OF")

    precio_last = last.get("price") if isinstance(last, dict) else None
    hay_libro = bool(bids) or bool(offers)

    if precio_last is not None or hay_libro:
        log.info(
            f"Salud OK: testigo con dato real "
            f"(last={precio_last}, bids={bids}, offers={offers})."
        )
        return {
            "estado": "sano_con_precio",
            "simbolo": simbolo_testigo,
            "last": precio_last,
            "bids": bids,
            "offers": offers,
        }

    # status OK pero todo vacío: conexión bien, sin precio (mercado cerrado?).
    log.info(
        "Salud OK pero sin precio/libro: conexión correcta, probable "
        "mercado cerrado o pre-apertura. No es fallo de conexión."
    )
    return {
        "estado": "sano_sin_precio",
        "simbolo": simbolo_testigo,
        "last": None,
        "bids": None,
        "offers": None,
    }