"""
Configuración compartida de pytest para la suite de tests de Argo.

pytest descubre y carga este archivo automáticamente — no hay que importarlo.
Acá viven los fixtures que usan varios archivos de test.

El fixture 'sesion_memoria' entrega una sesión de SQLAlchemy contra una base
SQLite en memoria, creada desde cero para cada test y descartada al terminar.
Nunca toca la base real (data/argo.sqlite): los tests son 100% aislados.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.utils.models import Base


@pytest.fixture
def engine_memoria():
    """
    Crea un engine SQLite en memoria con el schema completo de Argo.

    Activa el PRAGMA foreign_keys=ON en cada conexión: SQLite, por defecto,
    NO valida foreign keys. Sin este PRAGMA, un test de integridad referencial
    daría un falso OK. Lo activamos para que la base de test se comporte como
    una base que respeta las relaciones.

    El engine vive lo que dura el test y se destruye al final.
    """
    engine = create_engine("sqlite:///:memory:")

    # SQLite no fuerza foreign keys salvo que se pida explícitamente,
    # y hay que pedirlo en cada conexión nueva — de ahí el listener.
    @event.listens_for(engine, "connect")
    def _activar_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Crear todas las tablas del modelo en la base en memoria.
    Base.metadata.create_all(engine)
    yield engine
    # Limpieza: cerrar el engine descarta la base en memoria por completo.
    engine.dispose()


@pytest.fixture
def sesion_memoria(engine_memoria):
    """
    Entrega una sesión de SQLAlchemy ligada a la base en memoria.

    Cada test que pida 'sesion_memoria' recibe una sesión limpia sobre una
    base recién creada. Al terminar el test, la sesión se cierra.
    """
    Session = sessionmaker(bind=engine_memoria)
    sesion = Session()
    yield sesion
    sesion.close()