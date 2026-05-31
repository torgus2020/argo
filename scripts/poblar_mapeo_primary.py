"""
Script de población de la tabla 'instrumento_broker_mapping' desde el JSON de
mapeo Primary propuesto (H1.6).

Lee el mapeo propuesto (data/processed/mapeo_primary_propuesto_*.json) y carga
cada fila en la tabla instrumento_broker_mapping. Es idempotente: si una fila
ya existe (misma clave del UniqueConstraint: broker + symbol_externo + plazo),
la actualiza; si no existe, la inserta. Nunca borra mapeos.

Tres reglas propias de esta tabla:

1. RE-RESOLUCIÓN DEL FK. No confía en el instrumento_id que trae el JSON
   (calculado en otra corrida, puede estar desfasado). Re-resuelve el FK por
   ticker_base contra la tabla 'instrumentos' actual. Cuando un ticker está
   duplicado (CEDEAR argentino vs subyacente US), excluye el tipo
   'subyacente_us': un símbolo de Primary (MERV - XMEV - ...) cotiza en el
   mercado argentino, nunca en el subyacente extranjero.

2. CONTRATO DE REVISIÓN MANUAL. Una fila con requiere_revision_manual == True
   NO se puebla: se saltea y se reporta. Para poblar una fila marcada hay que
   destrabarla explícitamente antes (bajar la marca tras revisión humana).
   Este es el contrato elegido en H1.6 (opción "saltear marcadas").

3. COLUMNAS PERSISTIDAS. El campo requiere_revision_manual vive solo en el JSON
   (es metadata del proceso de mapeo), NO es columna de la tabla. El poblador
   lo lee para decidir, pero no lo persiste.

Uso:
    python scripts/poblar_mapeo_primary.py

La función poblar_mapeo() acepta inyección de dependencias (session_factory,
mapeo_path) para testearse contra una base en memoria y un JSON de fixture.
Con los defaults se comporta como la ejecución productiva.

Devuelve código de salida:
- 0: ejecución exitosa
- 1: error en lectura/parseo del JSON o fallo en escritura DB
"""

import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

# Agregar la raíz del proyecto al sys.path para poder importar módulos de src/
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

from src.utils.db import get_session
from src.utils.models import Instrumento, InstrumentoBrokerMapping
from src.utils.logger import obtener_logger

_log = obtener_logger(__name__)

_RUTA_MAPEO = (
    RAIZ_PROYECTO / "data" / "processed" / "mapeo_primary_propuesto_2026-05-29.json"
)

# Tipo de instrumento que NUNCA es destino de un símbolo de Primary.
# Es la única razón por la que un ticker está duplicado en 'instrumentos'
# (se puebla aparte para la estrategia pairs_cedear, vía Polygon). Excluirlo
# desambigua el FK: un MERV - XMEV - ... siempre apunta al lado argentino.
TIPO_EXCLUIDO_FK = "subyacente_us"


def _cargar_mapeo(mapeo_path: Path) -> dict:
    """
    Lee y parsea el JSON de mapeo propuesto. Devuelve el dict completo.
    Levanta excepción si hay problemas — el caller decide qué hacer.

    El path se recibe por parámetro (en vez de leer la constante de módulo)
    para permitir que los tests apunten a un fixture.
    """
    if not mapeo_path.exists():
        raise FileNotFoundError(f"No se encontró {mapeo_path}")
    with open(mapeo_path, encoding="utf-8") as f:
        return json.load(f)


def _resolver_fk(session, ticker_base: str) -> tuple[int | None, str]:
    """
    Resuelve el instrumento_id (FK) para un ticker_base, re-resolviendo contra
    la tabla 'instrumentos' actual (NO se confía en el ID del JSON).

    Regla de desambiguación: si el ticker está duplicado, se excluye el tipo
    'subyacente_us'. Tras filtrar debe quedar exactamente 1 instrumento.

    Devuelve (instrumento_id, motivo):
    - (id, "ok")                     -> resuelto sin ambigüedad
    - (None, "huerfano")             -> ningún instrumento con ese ticker
    - (None, "ambiguedad_no_resuelta") -> sigue habiendo >1 tras excluir subyacente
    """
    matches = session.query(Instrumento).filter_by(ticker=ticker_base).all()

    if len(matches) == 0:
        return None, "huerfano"

    if len(matches) == 1:
        return matches[0].id, "ok"

    # Hay varios: excluyo el subyacente US (lo que Primary nunca puede ser).
    candidatos = [m for m in matches if m.tipo != TIPO_EXCLUIDO_FK]

    if len(candidatos) == 1:
        return candidatos[0].id, "ok"

    # 0 candidatos (todos eran subyacente_us, raro) o >1 (ambigüedad real
    # entre instrumentos argentinos, no anticipada): no desambiguo solo.
    return None, "ambiguedad_no_resuelta"


def _parsear_fecha_validacion(valor) -> datetime | None:
    """
    Convierte el campo fecha_validacion del JSON a datetime (la columna es
    DateTime). Si viene null/vacío, devuelve None (no se inventa fecha).
    """
    if not valor:
        return None
    # Formato ISO escrito por el destrabe: "2026-05-29T00:00:00"
    return datetime.fromisoformat(valor)


def _upsert_mapeo(session, fila: dict, instrumento_id: int) -> str:
    """
    Inserta o actualiza una fila de mapeo según la clave del UniqueConstraint
    real de la tabla: (broker, symbol_externo, plazo) -> uq_broker_symbol_plazo.

    Persiste solo las columnas de la tabla. NO persiste requiere_revision_manual
    (vive solo en el JSON, es metadata del proceso de mapeo).

    Devuelve 'insertado' o 'actualizado'.
    """
    broker = fila["broker"]
    symbol_externo = fila["symbol_externo"]
    plazo = fila["plazo"]

    fecha_validacion = _parsear_fecha_validacion(fila.get("fecha_validacion"))

    existente = (
        session.query(InstrumentoBrokerMapping)
        .filter_by(broker=broker, symbol_externo=symbol_externo, plazo=plazo)
        .first()
    )

    if existente:
        existente.instrumento_id = instrumento_id
        existente.segmento = fila["segmento"]
        existente.moneda_liquidacion = fila["moneda_liquidacion"]
        existente.es_default = fila["es_default"]
        existente.activo = fila["activo"]
        existente.metadata_json = fila.get("metadata_json")
        existente.fecha_validacion = fecha_validacion
        existente.updated_at = datetime.now(timezone.utc)
        return "actualizado"
    else:
        nuevo = InstrumentoBrokerMapping(
            instrumento_id=instrumento_id,
            broker=broker,
            symbol_externo=symbol_externo,
            segmento=fila["segmento"],
            moneda_liquidacion=fila["moneda_liquidacion"],
            plazo=plazo,
            es_default=fila["es_default"],
            activo=fila["activo"],
            metadata_json=fila.get("metadata_json"),
            fecha_validacion=fecha_validacion,
        )
        session.add(nuevo)
        return "insertado"


def poblar_mapeo(
    session_factory: Callable | None = None,
    mapeo_path: Path | None = None,
) -> dict:
    """
    Ejecuta la población completa del mapeo. Devuelve un dict con estadísticas:
    - total_filas
    - insertados
    - actualizados
    - salteadas_revision_manual
    - huerfanos_instrumento   (lista de ticker_base)
    - ambiguedad_no_resuelta  (lista de ticker_base)

    Parámetros (inyección de dependencias, opcionales):
    - session_factory: callable que devuelve un context manager de sesión.
      Default: get_session (base real). Los tests inyectan una factory ligada
      a un engine SQLite en memoria.
    - mapeo_path: ruta al JSON de mapeo. Default: _RUTA_MAPEO. Los tests
      inyectan un fixture.

    Si hay error, levanta excepción.
    """
    if session_factory is None:
        session_factory = get_session
    if mapeo_path is None:
        mapeo_path = _RUTA_MAPEO

    _log.info("Iniciando población de mapeo Primary desde JSON propuesto")

    mapeo = _cargar_mapeo(mapeo_path)
    filas = mapeo["filas_propuestas"]

    stats = {
        "total_filas": len(filas),
        "insertados": 0,
        "actualizados": 0,
        "salteadas_revision_manual": 0,
        "huerfanos_instrumento": [],
        "ambiguedad_no_resuelta": [],
    }

    with session_factory() as session:
        for fila in filas:
            # Contrato de revisión manual: las marcadas no se pueblan.
            if fila.get("requiere_revision_manual") is True:
                stats["salteadas_revision_manual"] += 1
                _log.warning(
                    f"Salteada (revisión manual pendiente): {fila['symbol_externo']}"
                )
                continue

            # Re-resolución del FK por ticker_base contra la base actual.
            instrumento_id, motivo = _resolver_fk(session, fila["ticker_base"])

            if motivo == "huerfano":
                if fila["ticker_base"] not in stats["huerfanos_instrumento"]:
                    stats["huerfanos_instrumento"].append(fila["ticker_base"])
                _log.error(
                    f"Huérfano de instrumento: ticker_base='{fila['ticker_base']}' "
                    f"({fila['symbol_externo']}) no tiene instrumento en la base."
                )
                continue

            if motivo == "ambiguedad_no_resuelta":
                if fila["ticker_base"] not in stats["ambiguedad_no_resuelta"]:
                    stats["ambiguedad_no_resuelta"].append(fila["ticker_base"])
                _log.error(
                    f"Ambigüedad no resuelta: ticker_base='{fila['ticker_base']}' "
                    f"({fila['symbol_externo']}) resuelve a varios tras excluir subyacente."
                )
                continue

            accion = _upsert_mapeo(session, fila, instrumento_id)
            if accion == "insertado":
                stats["insertados"] += 1
            else:
                stats["actualizados"] += 1

        # Commit explícito al final, una sola transacción para todo.
        session.commit()

    _log.info(
        f"Población de mapeo completada: {stats['insertados']} insertados, "
        f"{stats['actualizados']} actualizados, "
        f"{stats['salteadas_revision_manual']} salteadas por revisión manual, "
        f"{len(stats['huerfanos_instrumento'])} huérfanos, "
        f"{len(stats['ambiguedad_no_resuelta'])} ambigüedades no resueltas"
    )
    return stats


def main() -> int:
    """
    Función principal. Devuelve código de salida.
    Llama a poblar_mapeo() sin argumentos: usa los defaults (base real + JSON
    propuesto productivo).
    """
    try:
        stats = poblar_mapeo()

        print("=" * 60)
        print("POBLACIÓN DE MAPEO PRIMARY - RESUMEN")
        print("=" * 60)
        print(f"Total filas en JSON:          {stats['total_filas']}")
        print(f"  Insertados:                 {stats['insertados']}")
        print(f"  Actualizados:               {stats['actualizados']}")
        print(f"  Salteadas (revisión manual):{stats['salteadas_revision_manual']:>4}")
        print(f"  Huérfanos de instrumento:   {len(stats['huerfanos_instrumento']):>4}")
        print(f"  Ambigüedad no resuelta:     {len(stats['ambiguedad_no_resuelta']):>4}")

        if stats["huerfanos_instrumento"]:
            print("\n  Tickers huérfanos (sin instrumento en la base):")
            for t in stats["huerfanos_instrumento"]:
                print(f"    - {t}")
        if stats["ambiguedad_no_resuelta"]:
            print("\n  Tickers con ambigüedad no resuelta:")
            for t in stats["ambiguedad_no_resuelta"]:
                print(f"    - {t}")

        print("=" * 60)
        # Código de salida 0 aunque haya salteadas/huérfanos: no son errores
        # de ejecución, son resultados informados. El operador decide qué hacer.
        return 0

    except FileNotFoundError as e:
        _log.error(f"Archivo no encontrado: {e}")
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        _log.error(f"Error parseando el JSON de mapeo: {e}")
        print(f"ERROR: el JSON de mapeo no parsea: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        _log.error(f"Error inesperado: {e}", exc_info=True)
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())