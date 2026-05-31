"""
Tests de integración del script scripts/poblar_mapeo_primary.py.

Verifican el comportamiento de la función poblar_mapeo() de punta a punta:
siembra instrumentos en una base SQLite en memoria, lee un JSON de mapeo de
fixture, puebla la tabla instrumento_broker_mapping, y comprueba el resultado.
Nunca tocan la base ni el JSON reales.

Filosofía (espejo de test_poblar_instrumentos.py):
- Cada test verifica una única propiedad.
- El JSON de mapeo de prueba es un archivo temporal (tmp_path de pytest),
  borrado automáticamente al terminar. Se usa un archivo real, no un dict,
  para ejercitar también _cargar_mapeo().
- La base es SQLite en memoria (fixture engine_memoria de conftest.py), con
  PRAGMA foreign_keys=ON, así que la integridad referencial se valida de verdad.
- poblar_mapeo() recibe por inyección de dependencias la session_factory y el
  mapeo_path.
- Se ejecutan con: pytest tests/test_poblar_mapeo_primary.py

El test central es test_fk_excluye_subyacente_us: el análogo al homónimo AAPL,
pero del lado del mapeo. Un ticker presente como CEDEAR y como subyacente US
debe mapear SIEMPRE al CEDEAR argentino, nunca al subyacente — porque un símbolo
de Primary (MERV - XMEV - ...) cotiza en el mercado argentino.
"""

import json
from contextlib import contextmanager

import pytest
from sqlalchemy.orm import sessionmaker

from src.utils.models import Instrumento, InstrumentoBrokerMapping

# Importar el script bajo prueba. poblar_mapeo_primary.py inyecta la raíz
# del proyecto al sys.path al importarse, así que el import de 'scripts'
# resuelve sin configuración extra.
from scripts.poblar_mapeo_primary import poblar_mapeo


# ----------------------------------------------------------------------
# Helpers para construir filas de fixture con la forma real del JSON.
# ----------------------------------------------------------------------

def _fila(ticker_base, symbol_externo, moneda, plazo, es_default,
          requiere_revision=False, fecha_validacion=None, instrumento_id=999):
    """
    Construye una fila de mapeo con la forma exacta del JSON propuesto.
    instrumento_id default = 999 (basura intencional: el poblador debe
    re-resolverlo, nunca confiar en este valor).
    """
    return {
        "instrumento_id": instrumento_id,
        "ticker_base": ticker_base,
        "broker": "primary",
        "symbol_externo": symbol_externo,
        "segmento": "MERV",
        "moneda_liquidacion": moneda,
        "plazo": plazo,
        "es_default": es_default,
        "activo": True,
        "requiere_revision_manual": requiere_revision,
        "metadata_json": "{}",
        "fecha_validacion": fecha_validacion,
    }


def _instrumento(ticker, tipo, mercado, moneda="ARS"):
    """Construye un Instrumento para sembrar en la base de test."""
    return Instrumento(
        ticker=ticker,
        tipo=tipo,
        nombre=f"{ticker} ({tipo})",
        mercado=mercado,
        moneda=moneda,
        fuente="primary",
        activo=True,
        metadata_json=None,
    )


# ----------------------------------------------------------------------
# Fixtures locales
# ----------------------------------------------------------------------

@pytest.fixture
def factory_memoria(engine_memoria):
    """
    Construye una session_factory para inyectar en poblar_mapeo().
    Mismo contrato que en test_poblar_instrumentos.py: un callable que,
    invocado sin argumentos, devuelve un context manager de sesión.
    """
    Session = sessionmaker(bind=engine_memoria)

    @contextmanager
    def _factory():
        sesion = Session()
        try:
            yield sesion
        finally:
            sesion.close()

    return _factory


@pytest.fixture
def sembrar_instrumentos(engine_memoria):
    """
    Siembra en la base de test los instrumentos que los mapeos van a
    referenciar. Devuelve un dict {(ticker, tipo): id} para que los tests
    puedan verificar contra qué id resolvió cada FK.

    Incluye el caso ambiguo: AAPL como cedear (BYMA) y como subyacente_us
    (NASDAQ), que es el corazón del test central.
    """
    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        s.add_all([
            _instrumento("AL30", "bono", "BYMA"),
            _instrumento("AAPL", "cedear", "BYMA"),
            _instrumento("AAPL", "subyacente_us", "NASDAQ", moneda="USD"),
        ])
        s.commit()
        ids = {
            ("AL30", "bono"): s.query(Instrumento).filter_by(ticker="AL30").first().id,
            ("AAPL", "cedear"): s.query(Instrumento).filter_by(
                ticker="AAPL", tipo="cedear").first().id,
            ("AAPL", "subyacente_us"): s.query(Instrumento).filter_by(
                ticker="AAPL", tipo="subyacente_us").first().id,
        }
    return ids


@pytest.fixture
def mapeo_fixture(tmp_path):
    """
    Factory de fixture: devuelve una función que escribe una lista de filas
    a un JSON temporal con la estructura del mapeo propuesto, y devuelve la ruta.
    """
    def _escribir(filas):
        ruta = tmp_path / "mapeo_test.json"
        contenido = {
            "generado_utc": "2026-05-29T00:00:00+00:00",
            "broker": "primary",
            "segmento": "MERV",
            "snapshot_origen": {"status": "OK"},
            "resumen": {},
            "filas_propuestas": filas,
            "revision_manual": [],
            "huerfanos_snapshot": [],
            "huerfanos_universo": [],
            "rechazados_por_currency": [],
            "sin_parsear": [],
            "colisiones_constraint": [],
        }
        ruta.write_text(json.dumps(contenido, ensure_ascii=False), encoding="utf-8")
        return ruta
    return _escribir


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

def test_poblar_inserta_todas_las_filas(sembrar_instrumentos, factory_memoria,
                                        mapeo_fixture, engine_memoria):
    """
    Una corrida sobre tabla vacía inserta todas las filas válidas del mapeo
    y ninguna queda como 'actualizada'.
    """
    filas = [
        _fila("AL30", "MERV - XMEV - AL30 - CI", "ARS", "CI", True),
        _fila("AL30", "MERV - XMEV - AL30D - CI", "USD_MEP", "CI", False),
    ]
    ruta = mapeo_fixture(filas)

    stats = poblar_mapeo(session_factory=factory_memoria, mapeo_path=ruta)

    assert stats["insertados"] == 2
    assert stats["actualizados"] == 0
    assert stats["salteadas_revision_manual"] == 0
    assert stats["huerfanos_instrumento"] == []

    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        assert s.query(InstrumentoBrokerMapping).count() == 2


def test_poblar_es_idempotente(sembrar_instrumentos, factory_memoria,
                               mapeo_fixture, engine_memoria):
    """
    Poblar dos veces con el mismo mapeo no duplica filas: la segunda corrida
    actualiza, no inserta. Es lo que hace seguro re-correr el script.
    """
    filas = [_fila("AL30", "MERV - XMEV - AL30 - CI", "ARS", "CI", True)]
    ruta = mapeo_fixture(filas)

    poblar_mapeo(session_factory=factory_memoria, mapeo_path=ruta)
    stats2 = poblar_mapeo(session_factory=factory_memoria, mapeo_path=ruta)

    assert stats2["insertados"] == 0
    assert stats2["actualizados"] == 1

    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        assert s.query(InstrumentoBrokerMapping).count() == 1


def test_fk_excluye_subyacente_us(sembrar_instrumentos, factory_memoria,
                                  mapeo_fixture, engine_memoria):
    """
    Test central — el análogo al homónimo AAPL, del lado del mapeo.

    AAPL existe como CEDEAR (BYMA) y como subyacente_us (NASDAQ). Una fila de
    mapeo Primary para AAPL debe resolver su FK al CEDEAR argentino, NUNCA al
    subyacente US: un símbolo MERV - XMEV - ... cotiza en el mercado argentino.
    """
    ids = sembrar_instrumentos
    id_cedear = ids[("AAPL", "cedear")]
    id_subyacente = ids[("AAPL", "subyacente_us")]

    filas = [_fila("AAPL", "MERV - XMEV - AAPL - CI", "ARS", "CI", True)]
    ruta = mapeo_fixture(filas)

    poblar_mapeo(session_factory=factory_memoria, mapeo_path=ruta)

    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        fila = s.query(InstrumentoBrokerMapping).filter_by(
            symbol_externo="MERV - XMEV - AAPL - CI").one()
        assert fila.instrumento_id == id_cedear
        assert fila.instrumento_id != id_subyacente


def test_fk_del_json_se_ignora(sembrar_instrumentos, factory_memoria,
                               mapeo_fixture, engine_memoria):
    """
    El instrumento_id que trae el JSON es basura (puede estar desfasado).
    El poblador debe re-resolverlo por ticker, ignorando el valor del JSON.
    """
    ids = sembrar_instrumentos
    id_real = ids[("AL30", "bono")]

    # La fila trae instrumento_id=999 (inexistente). El FK persistido debe
    # ser el re-resuelto (id_real), no 999.
    filas = [_fila("AL30", "MERV - XMEV - AL30 - CI", "ARS", "CI", True,
                   instrumento_id=999)]
    ruta = mapeo_fixture(filas)

    poblar_mapeo(session_factory=factory_memoria, mapeo_path=ruta)

    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        fila = s.query(InstrumentoBrokerMapping).one()
        assert fila.instrumento_id == id_real
        assert fila.instrumento_id != 999


def test_marca_revision_se_saltea(sembrar_instrumentos, factory_memoria,
                                  mapeo_fixture, engine_memoria):
    """
    Una fila con requiere_revision_manual=True NO se puebla: se saltea y se
    cuenta aparte. Es el contrato elegido en H1.6 (opción saltear-marcadas).
    """
    filas = [
        _fila("AL30", "MERV - XMEV - AL30 - CI", "ARS", "CI", True),
        _fila("AL30", "MERV - XMEV - AL30D - CI", "USD_MEP", "CI", False,
              requiere_revision=True),
    ]
    ruta = mapeo_fixture(filas)

    stats = poblar_mapeo(session_factory=factory_memoria, mapeo_path=ruta)

    assert stats["insertados"] == 1
    assert stats["salteadas_revision_manual"] == 1

    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        # La marcada no entró: solo está la AL30 base.
        assert s.query(InstrumentoBrokerMapping).count() == 1
        marcada = s.query(InstrumentoBrokerMapping).filter_by(
            symbol_externo="MERV - XMEV - AL30D - CI").first()
        assert marcada is None


def test_huerfano_se_reporta_sin_romper(sembrar_instrumentos, factory_memoria,
                                        mapeo_fixture, engine_memoria):
    """
    Una fila cuyo ticker_base no tiene instrumento en la base es un huérfano:
    se reporta en el bucket, NO se puebla (evitando un FK roto), y las demás
    filas se pueblan igual. Con PRAGMA foreign_keys=ON, si el poblador
    intentara insertar el huérfano, la base lo rechazaría — así que este test
    también prueba que el poblador NO intenta insertarlo.
    """
    filas = [
        _fila("AL30", "MERV - XMEV - AL30 - CI", "ARS", "CI", True),
        _fila("GHOST", "MERV - XMEV - GHOST - CI", "ARS", "CI", True),
    ]
    ruta = mapeo_fixture(filas)

    stats = poblar_mapeo(session_factory=factory_memoria, mapeo_path=ruta)

    assert stats["insertados"] == 1
    assert "GHOST" in stats["huerfanos_instrumento"]

    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        assert s.query(InstrumentoBrokerMapping).count() == 1
        ghost = s.query(InstrumentoBrokerMapping).filter_by(
            symbol_externo="MERV - XMEV - GHOST - CI").first()
        assert ghost is None


def test_fecha_validacion_se_parsea(sembrar_instrumentos, factory_memoria,
                                    mapeo_fixture, engine_memoria):
    """
    Una fila con fecha_validacion ISO se persiste como datetime; una con null
    queda en null. No se inventan fechas.
    """
    from datetime import datetime

    filas = [
        _fila("AL30", "MERV - XMEV - AL30 - CI", "ARS", "CI", True,
              fecha_validacion="2026-05-29T00:00:00"),
        _fila("AL30", "MERV - XMEV - AL30D - CI", "USD_MEP", "CI", False,
              fecha_validacion=None),
    ]
    ruta = mapeo_fixture(filas)

    poblar_mapeo(session_factory=factory_memoria, mapeo_path=ruta)

    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        con_fecha = s.query(InstrumentoBrokerMapping).filter_by(
            symbol_externo="MERV - XMEV - AL30 - CI").one()
        sin_fecha = s.query(InstrumentoBrokerMapping).filter_by(
            symbol_externo="MERV - XMEV - AL30D - CI").one()
        assert con_fecha.fecha_validacion == datetime(2026, 5, 29, 0, 0, 0)
        assert sin_fecha.fecha_validacion is None