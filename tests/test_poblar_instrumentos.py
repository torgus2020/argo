"""
Tests de integración del script scripts/poblar_instrumentos.py.

Verifican el comportamiento de la función poblar() de punta a punta:
lee un universe.json de fixture, puebla una base SQLite en memoria, y
comprueba el resultado. Nunca tocan la base ni el universe.json reales.

Filosofía:
- Cada test verifica una única propiedad.
- El universe de prueba es un archivo JSON temporal (fixture tmp_path de
  pytest), borrado automáticamente al terminar. Se usa un archivo real, no
  un dict, para ejercitar también _cargar_universe().
- La base es SQLite en memoria (fixture engine_memoria de conftest.py).
- poblar() recibe por inyección de dependencias la session_factory y el
  universe_path — la maquinaria agregada en H1.1 justamente para testear
  el script sin tocar el entorno real.
- Se ejecutan con: pytest tests/test_poblar_instrumentos.py

El test central es el caso homónimo cedear/subyacente: un mismo ticker
(AAPL) presente en dos categorías con distinto mercado debe producir DOS
filas, no una. Es el aprendizaje de H1.1 verificado sobre el script real.
"""

import json
from contextlib import contextmanager

import pytest
from sqlalchemy.orm import sessionmaker

from src.utils.models import Instrumento

# Importar el script bajo prueba. poblar_instrumentos.py inyecta la raíz
# del proyecto al sys.path al importarse, así que el import de 'scripts'
# resuelve sin configuración extra.
from scripts.poblar_instrumentos import poblar


# ----------------------------------------------------------------------
# Universe de fixture: forma idéntica a la del universe.json real, pero
# reducido y con 'fuente' corregida a 'primary' (el universe real todavía
# arrastra 'rava' en cedears — deuda registrada en PENDIENTES.md, ajena a
# este test). Incluye el par homónimo AAPL cedear / AAPL subyacente.
# ----------------------------------------------------------------------

_UNIVERSE_FIXTURE = {
    "cedears": [
        {
            "ticker": "AAPL",
            "tipo": "cedear",
            "nombre": "Apple Inc.",
            "mercado": "BYMA",
            "moneda": "ARS",
            "fuente": "primary",
            "activo": True,
            "metadata": {"subyacente": "AAPL", "mercado_subyacente": "NASDAQ"},
        },
    ],
    "subyacentes_us": [
        {
            "ticker": "AAPL",
            "tipo": "subyacente_us",
            "nombre": "Apple Inc.",
            "mercado": "NASDAQ",
            "moneda": "USD",
            "fuente": "polygon",
            "activo": True,
            "metadata": {},
        },
    ],
    "calculados": [
        {
            "ticker": "DOLAR_MEP",
            "tipo": "calculado",
            "nombre": "Dólar MEP (calculado AL30/AL30D)",
            "mercado": "DERIVADO",
            "moneda": "ARS",
            "fuente": "calculado",
            "activo": True,
            "metadata": {
                "formula": "precio_AL30_ARS / precio_AL30D_USD",
                "depende_de": ["AL30", "AL30D"],
            },
        },
    ],
    # 'macro' refleja la forma real: SIN campo 'mercado' ni 'moneda'.
    # El script debe aplicarles el default "N/A".
    "macro": [
        {
            "ticker": "BCRA_RESERVAS_BRUTAS",
            "tipo": "macro",
            "nombre": "Reservas brutas BCRA",
            "fuente": "bcra",
            "activo": True,
            "metadata": {"endpoint": "reservas/brutas", "frecuencia": "diaria"},
        },
    ],
}

# Total de instrumentos en el fixture: 1 + 1 + 1 + 1 = 4.
_TOTAL_FIXTURE = 4


# ----------------------------------------------------------------------
# Fixtures locales
# ----------------------------------------------------------------------

@pytest.fixture
def universe_fixture(tmp_path):
    """
    Escribe el universe de prueba a un archivo JSON temporal y devuelve su
    ruta. tmp_path es un directorio temporal de pytest, borrado al terminar.
    """
    ruta = tmp_path / "universe_test.json"
    ruta.write_text(
        json.dumps(_UNIVERSE_FIXTURE, ensure_ascii=False), encoding="utf-8"
    )
    return ruta


@pytest.fixture
def factory_memoria(engine_memoria):
    """
    Construye una session_factory para inyectar en poblar().

    poblar() espera un callable que, invocado sin argumentos, devuelva un
    context manager de sesión. SQLAlchemy Session ya es un context manager,
    pero lo envolvemos en un @contextmanager explícito para dejar el
    contrato a la vista y garantizar el cierre de la sesión.
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


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

def test_poblar_inserta_todos_los_instrumentos(universe_fixture, factory_memoria,
                                                engine_memoria):
    """
    Una corrida sobre base vacía inserta todos los instrumentos del universe
    y ninguno queda como 'actualizado'.
    """
    stats = poblar(session_factory=factory_memoria, universe_path=universe_fixture)

    assert stats["total_procesados"] == _TOTAL_FIXTURE
    assert stats["insertados"] == _TOTAL_FIXTURE
    assert stats["actualizados"] == 0

    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        assert s.query(Instrumento).count() == _TOTAL_FIXTURE


def test_poblar_es_idempotente(universe_fixture, factory_memoria, engine_memoria):
    """
    Poblar dos veces seguidas con el mismo universe no duplica filas:
    la segunda corrida actualiza, no inserta. Es la propiedad que hace
    seguro re-correr el script.
    """
    poblar(session_factory=factory_memoria, universe_path=universe_fixture)
    stats2 = poblar(session_factory=factory_memoria, universe_path=universe_fixture)

    # La segunda corrida: todo actualizado, nada insertado.
    assert stats2["insertados"] == 0
    assert stats2["actualizados"] == _TOTAL_FIXTURE

    # Y la base sigue teniendo exactamente la misma cantidad de filas.
    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        assert s.query(Instrumento).count() == _TOTAL_FIXTURE


def test_poblar_homonimo_cedear_subyacente_genera_dos_filas(universe_fixture,
                                                            factory_memoria,
                                                            engine_memoria):
    """
    Test central — el aprendizaje de H1.1 sobre el script real.

    AAPL existe en el universe dos veces: como CEDEAR (mercado BYMA) y como
    subyacente US (mercado NASDAQ). Son instrumentos distintos. Tras poblar,
    deben existir DOS filas con ticker AAPL, una por cada mercado.

    El bug original buscaba/insertaba sólo por ticker: la segunda entrada
    AAPL pisaba a la primera y quedaba una sola fila. Este test lo detecta.
    """
    poblar(session_factory=factory_memoria, universe_path=universe_fixture)

    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        filas_aapl = s.query(Instrumento).filter_by(ticker="AAPL").all()
        assert len(filas_aapl) == 2

        mercados = {f.mercado for f in filas_aapl}
        assert mercados == {"BYMA", "NASDAQ"}

        # Y son, efectivamente, un cedear y un subyacente distintos.
        tipos = {f.tipo for f in filas_aapl}
        assert tipos == {"cedear", "subyacente_us"}


def test_poblar_macro_aplica_defaults(universe_fixture, factory_memoria,
                                      engine_memoria):
    """
    Las entradas de 'macro' no traen 'mercado' ni 'moneda' en el universe.
    El script debe poblarlas igual, aplicando el default "N/A" en esos campos.
    """
    poblar(session_factory=factory_memoria, universe_path=universe_fixture)

    Session = sessionmaker(bind=engine_memoria)
    with Session() as s:
        macro = s.query(Instrumento).filter_by(ticker="BCRA_RESERVAS_BRUTAS").one()
        assert macro.mercado == "N/A"
        assert macro.moneda == "N/A"
        assert macro.tipo == "macro"