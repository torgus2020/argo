"""
Parser de mensajes de market data crudo de Primary/BIND → objeto TickCrudo.

=== FUNCIÓN PURA (sin red, sin base, sin hilos) ===
Entra el dict crudo que pyRofex entrega en el callback; sale un TickCrudo
listo para que el escritor lo persista por tandas. NO consulta la base: el
mapping_id ya viene resuelto por el collector (cache symbol -> mapping_id,
re-resuelto contra la tabla viva). Esa separación —resolver el símbolo es
del collector, parsear es de acá— es lo que hace a esta función testeable
sin mercado: se le dan dicts de ejemplo y se verifica la salida.

=== ESTADO DE CONFIRMACIÓN DE LA ESTRUCTURA ===
Confirmado contra producción BIND en rueda activa (2026-06-08, 4450 mensajes,
0 symbols sin matchear). El envoltorio Y la forma POBLADA de OF/BI/LA quedaron
verificados con dato real (ya no son supuestos). Ejemplo verbatim de un
mensaje poblado observado:
    {'type': 'Md',
     'timestamp': 1780931591162,                      # epoch ms del push
     'instrumentId': {'marketId': 'ROFX',
                      'symbol': 'MERV - XMEV - AL30 - CI'},
     'marketData': {
         'OF': [{'price': 94060, 'size': 50095}],     # lista de niveles
         'BI': [{'price': 94040, 'size': 2000}],      # lista de niveles
         'LA': {'price': 94060, 'size': 22809,        # dict
                'date': 1780931590000}}}              # epoch ms (resol. segundo)
Estructura confirmada:
    - BI (bids) y OF (offers): LISTA de niveles [{'price':.., 'size':..}, ...];
      en L1 tomamos solo el mejor (primer nivel). Si algún día llega L2, el
      libro completo queda igual en raw_json.
    - LA (last): dict {'price':.., 'size':.., 'date': epoch_ms}.
    - LA.date se observó con resolución de SEGUNDO (termina en ...000), a
      diferencia del 'timestamp' de nivel superior que sí trae ms reales. No
      afecta el parseo (divmod convierte igual); se documenta como hecho del
      feed, no como problema.
El raw_json guarda el mensaje verbatim igual: ante cualquier variante futura
de la forma, el dato crudo no se pierde y se re-deriva.

=== Los tres tiempos ===
- ts_mensaje:      del 'timestamp' de nivel superior (push del servidor).
- ts_recepcion:    cuándo lo recibió Argo. Se estampa ACÁ (en el callback),
                   no se deja al default del modelo, que se dispararía recién
                   en el flush de la tanda y mediría la hora equivocada.
- ts_ultimo_trade: del 'LA.date' (última operación efectiva). Nullable: un
                   instrumento con puntas pero sin operar trae LA=None.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.utils.models import TickCrudo


class ErrorParseoTick(Exception):
    """
    El mensaje no se puede convertir en un TickCrudo persistible. Se lanza
    solo cuando falta algo SIN lo cual la fila no puede existir (típicamente
    el 'timestamp' de nivel superior, que alimenta ts_mensaje NOT NULL). El
    collector la captura, la loguea y saltea ese mensaje, sin tirar el proceso.
    """


def _epoch_ms_a_utc(ms) -> datetime:
    """
    Convierte epoch en milisegundos a datetime UTC-aware.

    Usa divmod en vez de (ms / 1000) a propósito: la división flotante
    arrastra error de redondeo (1700000000796 ms podría quedar en .795999
    de microsegundo). Separando segundos enteros y sumando los milisegundos
    como timedelta, la conversión es exacta. El resultado es SIEMPRE tz-aware
    (UTC), como manda la convención del proyecto (nada naive cruza a la base).
    """
    segundos, milisegundos = divmod(int(ms), 1000)
    return datetime.fromtimestamp(segundos, tz=timezone.utc) + timedelta(
        milliseconds=milisegundos
    )


def _extraer_mejor_punta(niveles) -> tuple[Optional[float], Optional[int]]:
    """
    De una lista de niveles de punta (bids u offers) devuelve (price, size)
    del MEJOR nivel (el primero = L1). Defensivo ante todo lo que no sea una
    lista poblada de dicts: None, lista vacía, o forma inesperada → (None, None).
    Si algún día se captura L2, el libro entero igual queda en raw_json.

    CONFIRMADO 2026-06-08 (producción, rueda activa): BI/OF poblados vienen
    efectivamente como lista de dicts con claves 'price'/'size'.
    Ej: OF=[{'price': 94060, 'size': 50095}].
    """
    if not niveles or not isinstance(niveles, list):
        return None, None
    mejor = niveles[0]
    if not isinstance(mejor, dict):
        return None, None
    return mejor.get("price"), mejor.get("size")


def _extraer_last(
    la,
) -> tuple[Optional[float], Optional[int], Optional[datetime]]:
    """
    De la última operación (LA) devuelve (last_price, last_size, ts_ultimo_trade).
    LA nullable: si no hay (None o forma inesperada) → (None, None, None).

    CONFIRMADO 2026-06-08 (producción, rueda activa): LA poblado viene como
    dict {'price','size','date'}, con 'date' en epoch ms. Detalle observado:
    'date' trae resolución de SEGUNDO (termina en ...000), no ms reales como
    el timestamp de nivel superior. El parseo de 'date' se mantiene defensivo:
    si alguna vez llegara otro formato, ts_ultimo_trade queda en None (es
    nullable) y el valor verdadero se conserva en raw_json para re-derivar.
    No tiramos el tick entero por un campo secundario.
    """
    if not la or not isinstance(la, dict):
        return None, None, None

    last_price = la.get("price")
    last_size = la.get("size")

    date_ms = la.get("date")
    ts_ultimo_trade: Optional[datetime] = None
    if date_ms is not None:
        try:
            ts_ultimo_trade = _epoch_ms_a_utc(date_ms)
        except (ValueError, TypeError):
            # Formato inesperado de LA.date: no rompe, queda None y el crudo
            # preserva el dato original.
            ts_ultimo_trade = None

    return last_price, last_size, ts_ultimo_trade


def parsear_tick(
    mensaje: dict,
    mapping_id: int,
    ts_recepcion: Optional[datetime] = None,
) -> TickCrudo:
    """
    Convierte un mensaje crudo de market data en un TickCrudo (sin persistir).

    Parámetros
    ----------
    mensaje : dict
        El dict tal cual lo entrega el callback de pyRofex.
    mapping_id : int
        El id de instrumento_broker_mapping, ya resuelto por el collector
        contra el cache (no se resuelve acá: el parser es puro, no toca la base).
    ts_recepcion : datetime, opcional
        Momento de recepción (UTC-aware). Si no se pasa, se estampa ahora.
        Se parametriza para que los tests sean deterministas.

    Devuelve
    --------
    TickCrudo (objeto en memoria, sin commit). El escritor de v1 lo agrega
    a la sesión y lo persiste por tandas.

    Lanza
    -----
    ErrorParseoTick si falta el 'timestamp' de nivel superior (sin él no hay
    ts_mensaje, que es NOT NULL: la fila no podría existir).
    """
    if not isinstance(mensaje, dict):
        raise ErrorParseoTick(
            f"El mensaje no es un dict (es {type(mensaje).__name__})."
        )

    ts_ms = mensaje.get("timestamp")
    if ts_ms is None:
        raise ErrorParseoTick(
            "Mensaje sin 'timestamp' de nivel superior: no se puede fijar "
            "ts_mensaje (NOT NULL). Mensaje crudo: " + repr(mensaje)
        )
    ts_mensaje = _epoch_ms_a_utc(ts_ms)

    if ts_recepcion is None:
        ts_recepcion = datetime.now(timezone.utc)

    # marketData puede faltar o venir None (forma vista sin rueda): tratamos
    # con dict vacío para que la extracción devuelva todo None sin romper.
    market_data = mensaje.get("marketData") or {}

    bid_price, bid_size = _extraer_mejor_punta(market_data.get("BI"))
    offer_price, offer_size = _extraer_mejor_punta(market_data.get("OF"))
    last_price, last_size, ts_ultimo_trade = _extraer_last(market_data.get("LA"))

    # raw_json: el mensaje verbatim, fuente de verdad. ensure_ascii=False por
    # convención del proyecto; los valores ya son JSON-serializables (vienen
    # de un JSON que pyRofex parseó). Preserva el orden de claves (dict py3.7+).
    raw_json = json.dumps(mensaje, ensure_ascii=False)

    return TickCrudo(
        mapping_id=mapping_id,
        ts_mensaje=ts_mensaje,
        ts_recepcion=ts_recepcion,
        ts_ultimo_trade=ts_ultimo_trade,
        bid_price=bid_price,
        bid_size=bid_size,
        offer_price=offer_price,
        offer_size=offer_size,
        last_price=last_price,
        last_size=last_size,
        raw_json=raw_json,
    )