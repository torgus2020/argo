"""
Tests del parser de market data crudo.

No tocan la base: instancian TickCrudo en memoria (sin sesión) y verifican
los atributos que el parser fija. Por eso corren hoy, domingo, sin mercado.

Casos cubiertos:
  - mensaje vacío REAL (el que se vio el 2026-06-07: marketData todo None)
  - mensaje poblado (forma estándar pyRofex documentada)
  - LA null con puntas presentes (instrumento con libro pero sin operar)
  - conversión epoch ms → UTC exacta y tz-aware (incluido el milisegundo)
  - L1: con varios niveles de punta se toma solo el mejor (primero)
  - raw_json preserva el mensaje verbatim (round-trip)
  - falta de 'timestamp' → ErrorParseoTick
  - mapping_id pasado se asigna a la fila
"""

import json
from datetime import datetime, timezone

import pytest

from src.collectors.parser_market_data import (
    ErrorParseoTick,
    parsear_tick,
)


# Recepción fija para tests deterministas (no dependemos del reloj real).
_TS_RECEPCION_FIJA = datetime(2026, 6, 8, 15, 30, 0, tzinfo=timezone.utc)

# Mensaje VACÍO real, copiado textual del run del 2026-06-07 (MELID, mapping 6).
_MENSAJE_VACIO_REAL = {
    "type": "Md",
    "timestamp": 1780865968796,
    "instrumentId": {"marketId": "ROFX", "symbol": "MERV - XMEV - MELID - CI"},
    "marketData": {"OF": None, "BI": None, "LA": None},
}


def test_mensaje_vacio_real_no_rompe():
    """El mensaje vacío que SÍ observamos hoy se parsea: ts_mensaje fijado,
    todo el resto del marketData en None, raw_json presente."""
    tick = parsear_tick(_MENSAJE_VACIO_REAL, mapping_id=6,
                        ts_recepcion=_TS_RECEPCION_FIJA)

    assert tick.mapping_id == 6
    assert tick.ts_mensaje is not None
    assert tick.ts_recepcion == _TS_RECEPCION_FIJA
    assert tick.ts_ultimo_trade is None
    assert tick.bid_price is None and tick.bid_size is None
    assert tick.offer_price is None and tick.offer_size is None
    assert tick.last_price is None and tick.last_size is None
    assert tick.raw_json  # no vacío


def test_conversion_timestamp_exacta_y_tz_aware():
    """epoch ms → UTC exacto al milisegundo, y tz-aware (nunca naive).
    1700000000796 ms = 2026... no: = 2023-11-14 22:13:20.796 UTC."""
    mensaje = dict(_MENSAJE_VACIO_REAL, timestamp=1700000000796)
    tick = parsear_tick(mensaje, mapping_id=1, ts_recepcion=_TS_RECEPCION_FIJA)

    esperado = datetime(2023, 11, 14, 22, 13, 20, 796000, tzinfo=timezone.utc)
    assert tick.ts_mensaje == esperado
    # tz-aware: la convención del proyecto prohíbe naive cruzando a la base.
    assert tick.ts_mensaje.tzinfo is not None


def test_mensaje_poblado_forma_estandar():
    """Forma poblada estándar de pyRofex (CONFIRMAR mañana contra dato real):
    BI/OF como lista de niveles, LA como dict con date epoch ms."""
    mensaje = {
        "type": "Md",
        "timestamp": 1700000001000,
        "instrumentId": {"marketId": "ROFX", "symbol": "MERV - XMEV - AL30 - CI"},
        "marketData": {
            "BI": [{"price": 75250.0, "size": 100}],
            "OF": [{"price": 75300.0, "size": 50}],
            "LA": {"price": 75275.0, "size": 10, "date": 1700000000500},
        },
    }
    tick = parsear_tick(mensaje, mapping_id=259, ts_recepcion=_TS_RECEPCION_FIJA)

    assert tick.bid_price == 75250.0 and tick.bid_size == 100
    assert tick.offer_price == 75300.0 and tick.offer_size == 50
    assert tick.last_price == 75275.0 and tick.last_size == 10
    assert tick.ts_ultimo_trade == datetime(
        2023, 11, 14, 22, 13, 20, 500000, tzinfo=timezone.utc
    )


def test_la_null_con_puntas_presentes():
    """Instrumento con libro pero sin operar todavía: BI/OF poblados, LA None.
    ts_ultimo_trade y last_* quedan None; las puntas se extraen igual."""
    mensaje = {
        "type": "Md",
        "timestamp": 1700000002000,
        "instrumentId": {"marketId": "ROFX", "symbol": "MERV - XMEV - TX26C - CI"},
        "marketData": {
            "BI": [{"price": 100.5, "size": 1000}],
            "OF": [{"price": 101.0, "size": 800}],
            "LA": None,
        },
    }
    tick = parsear_tick(mensaje, mapping_id=42, ts_recepcion=_TS_RECEPCION_FIJA)

    assert tick.bid_price == 100.5 and tick.bid_size == 1000
    assert tick.offer_price == 101.0 and tick.offer_size == 800
    assert tick.last_price is None and tick.last_size is None
    assert tick.ts_ultimo_trade is None


def test_l1_toma_solo_el_mejor_nivel():
    """Si llega profundidad (varios niveles), L1 toma solo el primero (mejor).
    El resto del libro igual queda en raw_json sin perderse."""
    mensaje = {
        "type": "Md",
        "timestamp": 1700000003000,
        "instrumentId": {"marketId": "ROFX", "symbol": "MERV - XMEV - GGAL - CI"},
        "marketData": {
            "BI": [
                {"price": 5000.0, "size": 100},   # mejor punta
                {"price": 4999.0, "size": 200},
                {"price": 4998.0, "size": 300},
            ],
            "OF": [{"price": 5010.0, "size": 50}, {"price": 5011.0, "size": 75}],
            "LA": None,
        },
    }
    tick = parsear_tick(mensaje, mapping_id=262, ts_recepcion=_TS_RECEPCION_FIJA)

    assert tick.bid_price == 5000.0 and tick.bid_size == 100
    assert tick.offer_price == 5010.0 and tick.offer_size == 50
    # El libro completo sigue en el crudo:
    libro = json.loads(tick.raw_json)["marketData"]["BI"]
    assert len(libro) == 3


def test_raw_json_preserva_verbatim():
    """El raw_json debe reconstruir exactamente el mensaje original."""
    tick = parsear_tick(_MENSAJE_VACIO_REAL, mapping_id=6,
                        ts_recepcion=_TS_RECEPCION_FIJA)
    assert json.loads(tick.raw_json) == _MENSAJE_VACIO_REAL


def test_falta_timestamp_lanza_error():
    """Sin 'timestamp' de nivel superior no hay ts_mensaje (NOT NULL):
    el parser lanza ErrorParseoTick para que el collector saltee ese mensaje."""
    mensaje = {
        "type": "Md",
        "instrumentId": {"marketId": "ROFX", "symbol": "MERV - XMEV - AL30 - CI"},
        "marketData": {"OF": None, "BI": None, "LA": None},
    }
    with pytest.raises(ErrorParseoTick):
        parsear_tick(mensaje, mapping_id=259, ts_recepcion=_TS_RECEPCION_FIJA)


def test_mapping_id_se_asigna():
    """El mapping_id que pasa el collector llega tal cual a la fila."""
    tick = parsear_tick(_MENSAJE_VACIO_REAL, mapping_id=999,
                        ts_recepcion=_TS_RECEPCION_FIJA)
    assert tick.mapping_id == 999


def test_marketdata_ausente_no_rompe():
    """Mensaje sin clave 'marketData' (borde): no rompe, todo el libro None."""
    mensaje = {
        "type": "Md",
        "timestamp": 1700000004000,
        "instrumentId": {"marketId": "ROFX", "symbol": "MERV - XMEV - AAPL - CI"},
    }
    tick = parsear_tick(mensaje, mapping_id=10, ts_recepcion=_TS_RECEPCION_FIJA)
    assert tick.bid_price is None and tick.offer_price is None
    assert tick.last_price is None and tick.ts_ultimo_trade is None