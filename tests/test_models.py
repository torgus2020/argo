"""
Tests unitarios de los modelos de SQLAlchemy de Argo (src/utils/models.py).

Verifican el comportamiento del schema a nivel de base de datos: constraints
únicos, relaciones, cascades e integridad referencial.

Filosofía:
- Cada test es chico y verifica una única cosa.
- Corren contra una base SQLite en memoria (fixture 'sesion_memoria' de
  conftest.py), creada de cero para cada test. Nunca tocan data/argo.sqlite.
- No dependen de red, filesystem ni de la base real.
- Se ejecutan con: pytest tests/test_models.py

El foco principal es el UniqueConstraint compuesto (ticker, mercado) de la
tabla instrumentos: es el aprendizaje central de H1.1. Un mismo ticker puede
existir en dos mercados distintos (ej. la acción AAPL en EE.UU. y el CEDEAR
de AAPL en Argentina son instrumentos distintos), pero no dos veces en el
mismo mercado.
"""

from datetime import datetime, date

import pytest
from sqlalchemy.exc import IntegrityError

from src.utils.models import (
    Instrumento,
    InstrumentoBrokerMapping,
    Cotizacion1Min,
    CotizacionDiaria,
    MacroIndicador,
)


# ----------------------------------------------------------------------
# Helpers: constructores de instancias con valores válidos por defecto.
# Permiten que cada test sólo especifique los campos que le importan.
# ----------------------------------------------------------------------

def _instrumento(ticker="AL30", mercado="MERV", **kwargs):
    """Crea un Instrumento válido. Cualquier campo se puede sobrescribir."""
    datos = dict(
        ticker=ticker,
        tipo="bono",
        nombre=f"Instrumento {ticker}",
        mercado=mercado,
        moneda="ARS",
        fuente="universe",
        activo=True,
    )
    datos.update(kwargs)
    return Instrumento(**datos)


def _mapping(instrumento_id, broker="primary", symbol_externo="MERV - XMEV - AL30 - CI",
             plazo="CI", **kwargs):
    """Crea un InstrumentoBrokerMapping válido."""
    datos = dict(
        instrumento_id=instrumento_id,
        broker=broker,
        symbol_externo=symbol_externo,
        segmento="MERV",
        moneda_liquidacion="ARS",
        plazo=plazo,
        es_default=True,
        activo=True,
    )
    datos.update(kwargs)
    return InstrumentoBrokerMapping(**datos)


def _cotizacion_1min(instrumento_id, timestamp, **kwargs):
    """Crea una Cotizacion1Min válida."""
    datos = dict(
        instrumento_id=instrumento_id,
        timestamp=timestamp,
        open=100.0, high=101.0, low=99.0, close=100.5,
        volume=1000,
        fuente="primary",
    )
    datos.update(kwargs)
    return Cotizacion1Min(**datos)


def _cotizacion_diaria(instrumento_id, fecha, **kwargs):
    """Crea una CotizacionDiaria válida."""
    datos = dict(
        instrumento_id=instrumento_id,
        fecha=fecha,
        open=100.0, high=101.0, low=99.0, close=100.5,
        volume=1000,
        fuente="primary",
    )
    datos.update(kwargs)
    return CotizacionDiaria(**datos)


def _macro(indicador="reservas_bcra", fecha=date(2026, 5, 26), **kwargs):
    """Crea un MacroIndicador válido."""
    datos = dict(
        indicador=indicador,
        fecha=fecha,
        valor=28000.0,
        unidad="millones_usd",
        fuente="bcra",
    )
    datos.update(kwargs)
    return MacroIndicador(**datos)


# ----------------------------------------------------------------------
# Bloque 1 — UniqueConstraint compuesto (ticker, mercado) en instrumentos.
# Es el aprendizaje central de H1.1. Dos tests, cara y contracara.
# ----------------------------------------------------------------------

def test_instrumento_mismo_ticker_mismo_mercado_es_rechazado(sesion_memoria):
    """
    Dos instrumentos con el MISMO ticker y el MISMO mercado violan el
    UniqueConstraint compuesto. La base debe rechazar el segundo.

    Es la protección contra duplicados reales: no puede haber dos 'AL30'
    en 'MERV'.
    """
    sesion_memoria.add(_instrumento(ticker="AL30", mercado="MERV"))
    sesion_memoria.commit()

    sesion_memoria.add(_instrumento(ticker="AL30", mercado="MERV"))
    with pytest.raises(IntegrityError):
        sesion_memoria.commit()


def test_instrumento_mismo_ticker_distinto_mercado_es_aceptado(sesion_memoria):
    """
    Dos instrumentos con el mismo ticker pero DISTINTO mercado son dos
    entidades legítimas y deben coexistir.

    Es el caso que el bug de H1.1 rompía: la acción AAPL en el mercado de
    EE.UU. y el CEDEAR de AAPL en el mercado argentino comparten ticker
    pero NO son el mismo instrumento. Buscar/insertar sólo por ticker los
    colapsaba en una sola fila.
    """
    sesion_memoria.add(_instrumento(ticker="AAPL", mercado="NASDAQ"))
    sesion_memoria.add(_instrumento(ticker="AAPL", mercado="MERV"))
    sesion_memoria.commit()

    filas = sesion_memoria.query(Instrumento).filter_by(ticker="AAPL").all()
    assert len(filas) == 2
    mercados = {f.mercado for f in filas}
    assert mercados == {"NASDAQ", "MERV"}


# ----------------------------------------------------------------------
# Bloque 2 — Otros UniqueConstraints, uno por tabla.
# ----------------------------------------------------------------------

def test_mapping_broker_symbol_plazo_duplicado_es_rechazado(sesion_memoria):
    """
    El UniqueConstraint (broker, symbol_externo, plazo) impide que el mismo
    símbolo externo se cargue dos veces para el mismo broker y plazo.
    """
    instr = _instrumento(ticker="AL30", mercado="MERV")
    sesion_memoria.add(instr)
    sesion_memoria.commit()

    sesion_memoria.add(_mapping(instr.id, broker="primary",
                                symbol_externo="MERV - XMEV - AL30 - CI", plazo="CI"))
    sesion_memoria.commit()

    sesion_memoria.add(_mapping(instr.id, broker="primary",
                                symbol_externo="MERV - XMEV - AL30 - CI", plazo="CI"))
    with pytest.raises(IntegrityError):
        sesion_memoria.commit()


def test_mapping_mismo_symbol_distinto_plazo_es_aceptado(sesion_memoria):
    """
    El mismo símbolo externo con distinto plazo SÍ debe poder coexistir:
    son dos puntas de negociación distintas del mismo instrumento.
    """
    instr = _instrumento(ticker="AL30", mercado="MERV")
    sesion_memoria.add(instr)
    sesion_memoria.commit()

    sesion_memoria.add(_mapping(instr.id, symbol_externo="MERV - XMEV - AL30 - CI", plazo="CI"))
    sesion_memoria.add(_mapping(instr.id, symbol_externo="MERV - XMEV - AL30 - CI", plazo="24hs"))
    sesion_memoria.commit()

    assert sesion_memoria.query(InstrumentoBrokerMapping).count() == 2


def test_cotizacion_1min_instrumento_timestamp_duplicado_es_rechazado(sesion_memoria):
    """
    El UniqueConstraint (instrumento_id, timestamp) impide cargar dos veces
    la misma vela de 1 minuto para el mismo instrumento. Defensa contra
    bugs de re-procesamiento.
    """
    instr = _instrumento()
    sesion_memoria.add(instr)
    sesion_memoria.commit()

    ts = datetime(2026, 5, 26, 14, 30, 0)
    sesion_memoria.add(_cotizacion_1min(instr.id, ts))
    sesion_memoria.commit()

    sesion_memoria.add(_cotizacion_1min(instr.id, ts))
    with pytest.raises(IntegrityError):
        sesion_memoria.commit()


def test_cotizacion_diaria_instrumento_fecha_duplicado_es_rechazado(sesion_memoria):
    """
    El UniqueConstraint (instrumento_id, fecha) impide cargar dos veces el
    OHLCV diario del mismo instrumento para la misma fecha.
    """
    instr = _instrumento()
    sesion_memoria.add(instr)
    sesion_memoria.commit()

    f = date(2026, 5, 26)
    sesion_memoria.add(_cotizacion_diaria(instr.id, f))
    sesion_memoria.commit()

    sesion_memoria.add(_cotizacion_diaria(instr.id, f))
    with pytest.raises(IntegrityError):
        sesion_memoria.commit()


def test_macro_indicador_fecha_duplicado_es_rechazado(sesion_memoria):
    """
    El UniqueConstraint (indicador, fecha) impide cargar dos veces el mismo
    indicador macro para la misma fecha.
    """
    sesion_memoria.add(_macro(indicador="reservas_bcra", fecha=date(2026, 5, 26)))
    sesion_memoria.commit()

    sesion_memoria.add(_macro(indicador="reservas_bcra", fecha=date(2026, 5, 26)))
    with pytest.raises(IntegrityError):
        sesion_memoria.commit()


# ----------------------------------------------------------------------
# Bloque 3 — Relaciones y cascade.
# ----------------------------------------------------------------------

def test_instrumento_navega_a_sus_hijos(sesion_memoria):
    """
    Desde un Instrumento se puede navegar a sus cotizaciones y mappings
    vía las relationships definidas en el modelo.
    """
    instr = _instrumento()
    sesion_memoria.add(instr)
    sesion_memoria.commit()

    sesion_memoria.add(_mapping(instr.id))
    sesion_memoria.add(_cotizacion_1min(instr.id, datetime(2026, 5, 26, 14, 30, 0)))
    sesion_memoria.add(_cotizacion_diaria(instr.id, date(2026, 5, 26)))
    sesion_memoria.commit()

    sesion_memoria.refresh(instr)
    assert len(instr.broker_mappings) == 1
    assert len(instr.cotizaciones_1min) == 1
    assert len(instr.cotizaciones_diarias) == 1


def test_borrar_instrumento_arrastra_a_sus_hijos(sesion_memoria):
    """
    El cascade 'all, delete-orphan' hace que borrar un Instrumento borre
    también sus cotizaciones y mappings. No deben quedar filas huérfanas.
    """
    instr = _instrumento()
    sesion_memoria.add(instr)
    sesion_memoria.commit()

    sesion_memoria.add(_mapping(instr.id))
    sesion_memoria.add(_cotizacion_1min(instr.id, datetime(2026, 5, 26, 14, 30, 0)))
    sesion_memoria.add(_cotizacion_diaria(instr.id, date(2026, 5, 26)))
    sesion_memoria.commit()

    sesion_memoria.delete(instr)
    sesion_memoria.commit()

    assert sesion_memoria.query(Instrumento).count() == 0
    assert sesion_memoria.query(InstrumentoBrokerMapping).count() == 0
    assert sesion_memoria.query(Cotizacion1Min).count() == 0
    assert sesion_memoria.query(CotizacionDiaria).count() == 0


# ----------------------------------------------------------------------
# Bloque 4 — Integridad referencial (foreign keys).
# Este test depende del PRAGMA foreign_keys=ON activado en conftest.py.
# ----------------------------------------------------------------------

def test_mapping_con_instrumento_inexistente_es_rechazado(sesion_memoria):
    """
    Un InstrumentoBrokerMapping que apunta a un instrumento_id que no existe
    viola la foreign key y debe ser rechazado.

    Sin el PRAGMA foreign_keys=ON (activado en el fixture de conftest.py),
    SQLite ignoraría la foreign key y este test daría un falso OK.
    """
    sesion_memoria.add(_mapping(instrumento_id=99999))
    with pytest.raises(IntegrityError):
        sesion_memoria.commit()