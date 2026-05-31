"""
Módulo de persistencia: configuración de conexión a SQLite y gestión de sesiones.

Provee:
- engine: motor SQLAlchemy conectado a data/argo.sqlite
- SessionLocal: factory de sesiones para usar en collectors, estrategias, tests
- get_session(): context manager para gestión limpia de sesiones
- init_db(): inicializa la DB (crea archivo si no existe; no aplica migraciones)

Para aplicar/crear schema usar Alembic:
    alembic upgrade head        # aplicar migraciones pendientes
    alembic downgrade -1        # revertir última migración

Uso típico:

    from src.utils.db import get_session
    from src.utils.models import Instrumento

    with get_session() as session:
        instrumento = session.query(Instrumento).filter_by(ticker="AL30").first()
        print(instrumento)
"""

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.utils.logger import obtener_logger


_log = obtener_logger(__name__)

# Path a la base de datos. Por convención vive en data/argo.sqlite.
_RAIZ_PROYECTO = Path(__file__).resolve().parent.parent.parent
_RUTA_DB = _RAIZ_PROYECTO / "data" / "argo.sqlite"

# URL de conexión en formato SQLAlchemy.
# Para SQLite local: 'sqlite:///<path absoluto>'
DATABASE_URL = f"sqlite:///{_RUTA_DB}"

# Engine: maneja el pool de conexiones a la DB.
# echo=False evita que SQLAlchemy logueé cada query (muy verboso).
# connect_args es específico de SQLite para permitir uso desde múltiples threads.
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _activar_foreign_keys(conexion_dbapi, registro_conexion):
    """
    Activa la validación de foreign keys en cada conexión SQLite.

    SQLite NO valida foreign keys por defecto: hay que pedirlo explícitamente
    con PRAGMA foreign_keys=ON, y se setea POR CONEXIÓN (no es global ni
    persistente entre conexiones). Este listener corre cada vez que el pool
    abre una conexión física nueva, garantizando que TODA conexión productiva
    valide integridad referencial.

    Sin esto, la base aceptaría —por ejemplo— una fila en cotizaciones_1min o
    en instrumento_broker_mapping apuntando a un instrumento_id inexistente,
    sin chistar. Es el mismo PRAGMA que tests/conftest.py activa para los
    tests; acá lo activamos para la base real.

    Específico de SQLite. El proyecto usa SQLite exclusivamente.
    """
    cursor = conexion_dbapi.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# Factory de sesiones. autoflush=False evita escrituras implícitas que pueden
# confundir; el commit explícito es siempre preferible.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


@contextmanager
def get_session():
    """
    Context manager para obtener una sesión, hacer trabajo, y cerrarla limpio.

    Garantiza que la sesión se cierra incluso si hay excepción. Si hay
    excepción, hace rollback automático.

    Uso:
        with get_session() as session:
            session.add(objeto)
            session.commit()
        # acá la sesión ya está cerrada
    """
    session = SessionLocal()
    try:
        yield session
    except Exception as e:
        _log.error(f"Error en sesión de DB, ejecutando rollback: {e}")
        session.rollback()
        raise
    finally:
        session.close()


def verificar_db_existe() -> bool:
    """
    Verifica si el archivo de la DB existe en disco.

    Devuelve True si existe, False si todavía no se creó.
    No conecta; solo chequea filesystem.
    """
    return _RUTA_DB.exists()


def obtener_ruta_db() -> Path:
    """
    Devuelve el path absoluto de la DB. Útil para tests y diagnóstico.
    """
    return _RUTA_DB