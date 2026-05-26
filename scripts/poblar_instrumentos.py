"""
Script de población inicial de la tabla 'instrumentos' desde config/universe.json.

Lee el universo definido y carga cada instrumento en la base de datos.
Es idempotente: si un instrumento ya existe (mismo ticker), lo actualiza;
si no existe, lo inserta. Nunca borra instrumentos (para preservar referencias
históricas en cotizaciones).

Uso:
    python scripts/poblar_instrumentos.py

Categorías procesadas:
- bonos_usd_ar
- bonos_pesos_ar
- acciones_panel_lider
- cedears
- subyacentes_us
- calculados
- macro

Las cauciones no son procesadas porque NO son instrumentos tradeables (son
configuración, no entidades). Quedan como bloque informativo en universe.json.

Devuelve código de salida:
- 0: ejecución exitosa
- 1: error en lectura/parseo del universe.json o fallo en escritura DB
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Agregar la raíz del proyecto al sys.path para poder importar módulos de src/
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

from src.utils.db import get_session
from src.utils.models import Instrumento
from src.utils.logger import obtener_logger

_log = obtener_logger(__name__)

_RUTA_UNIVERSE = RAIZ_PROYECTO / "config" / "universe.json"


# Categorías del universo que vamos a procesar.
# Las cauciones se excluyen porque son configuración, no instrumentos.
CATEGORIAS_A_PROCESAR = [
    "bonos_usd_ar",
    "bonos_pesos_ar",
    "acciones_panel_lider",
    "cedears",
    "subyacentes_us",
    "calculados",
    "macro",
]


def _cargar_universe() -> dict:
    """
    Lee y parsea config/universe.json. Devuelve el dict completo.
    Levanta excepción si hay problemas — el caller decide qué hacer.
    """
    if not _RUTA_UNIVERSE.exists():
        raise FileNotFoundError(f"No se encontró {_RUTA_UNIVERSE}")
    with open(_RUTA_UNIVERSE, encoding="utf-8") as f:
        return json.load(f)


def _upsert_instrumento(session, datos_instrumento: dict, categoria: str) -> str:
    """
    Inserta o actualiza un instrumento según su ticker.

    Para las categorías 'macro' y 'calculados', los campos pueden venir
    incompletos (no tienen mercado/moneda en el sentido tradicional). Se
    aplican defaults razonables.

    Devuelve 'insertado' o 'actualizado' según corresponda.
    """
    ticker = datos_instrumento["ticker"]

    
    # Preparar campos con defaults para categorías con info incompleta
    tipo = datos_instrumento.get("tipo", categoria)
    nombre = datos_instrumento.get("nombre", ticker)
    mercado = datos_instrumento.get("mercado", "N/A")
    # Buscar si ya existe, usando la clave compuesta (ticker, mercado).
    # Es el aprendizaje de H1.1: AAPL como CEDEAR y AAPL como subyacente
    # son instrumentos distintos. Buscar solo por ticker los colapsaria.
    existente = session.query(Instrumento).filter_by(ticker=ticker, mercado=mercado).first()

    moneda = datos_instrumento.get("moneda", "N/A")
    fuente = datos_instrumento.get("fuente", "calculado")
    activo = datos_instrumento.get("activo", True)
    metadata = datos_instrumento.get("metadata", {})
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

    if existente:
        # Actualizar campos existentes
        existente.tipo = tipo
        existente.nombre = nombre
        existente.mercado = mercado
        existente.moneda = moneda
        existente.fuente = fuente
        existente.activo = activo
        existente.metadata_json = metadata_json
        existente.updated_at = datetime.utcnow()
        return "actualizado"
    else:
        # Insertar nuevo
        nuevo = Instrumento(
            ticker=ticker,
            tipo=tipo,
            nombre=nombre,
            mercado=mercado,
            moneda=moneda,
            fuente=fuente,
            activo=activo,
            metadata_json=metadata_json,
        )
        session.add(nuevo)
        return "insertado"


def poblar() -> dict:
    """
    Ejecuta la población completa. Devuelve un dict con estadísticas:
    - total_procesados
    - insertados
    - actualizados
    - por_categoria

    Si hay error, levanta excepción.
    """
    _log.info("Iniciando población de instrumentos desde universe.json")

    universe = _cargar_universe()

    stats = {
        "total_procesados": 0,
        "insertados": 0,
        "actualizados": 0,
        "por_categoria": {},
    }

    with get_session() as session:
        for categoria in CATEGORIAS_A_PROCESAR:
            if categoria not in universe:
                _log.warning(f"Categoría '{categoria}' no encontrada en universe.json")
                continue

            instrumentos_categoria = universe[categoria]
            stats["por_categoria"][categoria] = {
                "total": len(instrumentos_categoria),
                "insertados": 0,
                "actualizados": 0,
            }

            for instrumento_data in instrumentos_categoria:
                accion = _upsert_instrumento(session, instrumento_data, categoria)
                stats["total_procesados"] += 1
                if accion == "insertado":
                    stats["insertados"] += 1
                    stats["por_categoria"][categoria]["insertados"] += 1
                else:
                    stats["actualizados"] += 1
                    stats["por_categoria"][categoria]["actualizados"] += 1

        # Commit explícito al final, una sola transacción para todo
        session.commit()

    _log.info(
        f"Población completada: {stats['total_procesados']} procesados, "
        f"{stats['insertados']} insertados, {stats['actualizados']} actualizados"
    )
    return stats


def main() -> int:
    """
    Función principal. Devuelve código de salida.
    """
    try:
        stats = poblar()

        print("=" * 60)
        print("POBLACIÓN DE INSTRUMENTOS - RESUMEN")
        print("=" * 60)
        print(f"Total procesados: {stats['total_procesados']}")
        print(f"  Insertados:    {stats['insertados']}")
        print(f"  Actualizados:  {stats['actualizados']}")
        print()
        print("Por categoría:")
        for cat, datos in stats["por_categoria"].items():
            print(
                f"  {cat:<25} | total: {datos['total']:>3} | "
                f"insertados: {datos['insertados']:>3} | "
                f"actualizados: {datos['actualizados']:>3}"
            )
        print("=" * 60)
        return 0

    except FileNotFoundError as e:
        _log.error(f"Archivo no encontrado: {e}")
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        _log.error(f"Error parseando universe.json: {e}")
        print(f"ERROR: universe.json no parsea como JSON: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        _log.error(f"Error inesperado: {e}", exc_info=True)
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())