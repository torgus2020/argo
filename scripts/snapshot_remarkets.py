"""
Snapshot DETALLADO del catálogo de instrumentos de reMarkets (Primary/Matriz).

Genera un volcado crudo y completo de los instrumentos disponibles en
reMarkets usando get_detailed_instruments(), que devuelve ~27 campos por
instrumento (segmento, moneda, tick size, vencimiento, límites de precio,
tipos de orden, etc.), persistido a:
    data/raw/remarkets_snapshot_detallado_YYYY-MM-DD.json

Es la fuente de verdad contra la que se reconcilia el universo Argo en
H1.2.5.5 (generación del mapeo Primary), y la base de datos de referencia
para el collector productivo (H1.2.7).

Por qué detallado y no simple (Decisión A, sesión 2026-05-23):
  get_detailed_instruments() contiene toda la información de
  get_all_instruments() y mucho más. El campo 'segment.marketSegmentId'
  es crítico: distingue MERV (BYMA, donde opera Argo) de TIVA (MAE,
  mayorista) y DUAL (futuros). Sin ese campo no se puede mapear bien.

Filosofía:
  - Guardamos crudo completo. Mejor archivo pesado con info de más, que
    archivo liviano con info perdida.
  - Cada corrida queda registrada en log_collectors.
  - Solo se corre en local manualmente. VPS no lo necesita en esta fase.

Uso:
    python scripts/snapshot_remarkets.py
"""

import json
import sys
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

# Resolver path raíz del proyecto antes de importar módulos internos
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

import pyRofex

from src.utils.db import get_session
from src.utils.logger import obtener_logger_collector
from src.utils.models import LogCollector


# Paths de configuración
_RUTA_SECRETS = RAIZ_PROYECTO / "config" / "secrets.json"
_DIR_RAW = RAIZ_PROYECTO / "data" / "raw"

# Identificador del collector para log_collectors y para el archivo de log
_COLLECTOR_NAME = "snapshot_remarkets"

# Mapeo de prefijos de CFI code (ISO 10962) a etiquetas legibles.
_CFICODE_ETIQUETAS = {
    "ESXXXX": "Acción",
    "DBXXXX": "Bono",
    "DBXXFR": "Obligación Negociable",
    "EMXXXX": "CEDEAR",
    "RPXXXX": "Caución / Repo",
    "FXXXSX": "Futuro",
    "FXXXXX": "Futuro",
    "OCEFXS": "Opción Call s/futuro",
    "OPEFXS": "Opción Put s/futuro",
    "OCAFXS": "Opción Call s/acción",
    "OPAFXS": "Opción Put s/acción",
    "OCASPS": "Opción Call s/acción",
    "OPASPS": "Opción Put s/acción",
    "MRIXXX": "Índice de referencia",
    "DXXXXX": "Deuda (otro)",
    "DYXTXR": "Deuda (otro)",
    "DTXXXX": "Deuda (otro)",
    "MCXXXX": "Misceláneo",
    "MXXXXX": "Misceláneo",
}

# Logger dedicado: escribe a logs/collector_snapshot_remarkets.log
log = obtener_logger_collector(_COLLECTOR_NAME)


def traducir_cficode(cficode: str) -> str:
    """
    Traduce un CFI code (ISO 10962) a una etiqueta legible.

    Si el código exacto no está en el mapeo conocido, devuelve una etiqueta
    genérica que conserva el código original para no perder información.
    """
    if not cficode:
        return "SIN_CFICODE"
    return _CFICODE_ETIQUETAS.get(cficode, f"Otro ({cficode})")


def extraer_segmento(instrumento: dict) -> str:
    """
    Extrae el marketSegmentId de un instrumento detallado.

    El campo 'segment' es un dict anidado: {"marketSegmentId": "MERV", ...}.
    Maneja defensivamente el caso de que falte o no sea un dict.
    """
    segmento_field = instrumento.get("segment")
    if isinstance(segmento_field, dict):
        return segmento_field.get("marketSegmentId") or "SIN_SEGMENTO"
    return "SIN_SEGMENTO"


def cargar_credenciales() -> dict:
    """Carga credenciales de reMarkets desde secrets.json."""
    with open(_RUTA_SECRETS, encoding="utf-8") as f:
        return json.load(f)["primary_remarkets"]


def abrir_log_corrida(session) -> LogCollector:
    """
    Inserta una fila inicial en log_collectors con estado 'en_curso'.
    Devuelve la instancia para que el caller la actualice al finalizar.
    """
    registro = LogCollector(
        collector=_COLLECTOR_NAME,
        timestamp_inicio=datetime.utcnow(),
        estado="en_curso",
    )
    session.add(registro)
    session.commit()
    session.refresh(registro)
    log.info(f"Log de corrida abierto (id={registro.id})")
    return registro


def cerrar_log_corrida(
    session,
    registro: LogCollector,
    estado: str,
    instrumentos_procesados: int = 0,
    metadata: dict | None = None,
    error: str | None = None,
) -> None:
    """Actualiza la fila de log con estado final y metadata."""
    registro.timestamp_fin = datetime.utcnow()
    registro.estado = estado
    registro.instrumentos_procesados = instrumentos_procesados
    registro.instrumentos_exitosos = (
        instrumentos_procesados if estado == "completado" else 0
    )
    registro.instrumentos_fallidos = (
        0 if estado == "completado" else instrumentos_procesados
    )
    registro.filas_insertadas = 0  # No toca tablas de cotizaciones
    if metadata is not None:
        registro.metadata_json = json.dumps(metadata, ensure_ascii=False)
    if error is not None:
        registro.errores_json = json.dumps({"error": error}, ensure_ascii=False)
    session.commit()
    log.info(f"Log de corrida cerrado (id={registro.id}, estado={estado})")


def conectar_remarkets(cred: dict) -> None:
    """Inicializa la conexión a reMarkets vía pyRofex."""
    pyRofex.initialize(
        user=cred["user"],
        password=cred["password"],
        account=cred["account"],
        environment=pyRofex.Environment.REMARKET,
    )
    log.info(f"Conexión a reMarkets inicializada (account={cred['account']})")


def descargar_catalogo_detallado() -> list[dict]:
    """
    Descarga el catálogo detallado de instrumentos de reMarkets.

    pyRofex.get_detailed_instruments() devuelve un dict con shape:
        {"status": "OK", "instruments": [...]}
    donde cada instrumento trae ~27 campos (segment, currency, tickSize,
    maturityDate, etc.).

    Si el status no es OK, levantamos excepción para que el script falle
    y quede registro en log_collectors con estado='error'.
    """
    respuesta = pyRofex.get_detailed_instruments()
    if respuesta.get("status") != "OK":
        raise RuntimeError(
            f"Primary devolvió status no-OK: {respuesta.get('status')} "
            f"(detail: {respuesta.get('detail', 'sin detalle')})"
        )
    instrumentos = respuesta.get("instruments", [])
    log.info(f"Catálogo detallado descargado: {len(instrumentos)} instrumentos")
    return instrumentos


def guardar_snapshot(instrumentos: list[dict]) -> Path:
    """
    Persiste el catálogo detallado crudo a
    data/raw/remarkets_snapshot_detallado_YYYY-MM-DD.json.

    Si el archivo ya existe (corrida del mismo día), lo sobrescribe.

    Devuelve el path del archivo generado.
    """
    _DIR_RAW.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now().strftime("%Y-%m-%d")
    ruta = _DIR_RAW / f"remarkets_snapshot_detallado_{fecha}.json"

    payload = {
        "fuente": "remarkets",
        "tipo_snapshot": "detallado",
        "metodo_pyrofex": "get_detailed_instruments",
        "fecha_snapshot_utc": datetime.utcnow().isoformat(),
        "cantidad_instrumentos": len(instrumentos),
        "instrumentos": instrumentos,
    }
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log.info(f"Snapshot detallado guardado en {ruta}")
    return ruta


def calcular_breakdown_por_cficode(instrumentos: list[dict]) -> dict[str, dict]:
    """
    Calcula cuántos instrumentos hay por CFI code.

    Devuelve un dict ordenado de mayor a menor cantidad, con shape:
        {"DBXXXX": {"etiqueta": "Bono", "cantidad": 498}, ...}
    """
    contador = Counter(
        inst.get("cficode") or "SIN_CFICODE" for inst in instrumentos
    )
    breakdown = {}
    for cficode, cantidad in contador.most_common():
        breakdown[cficode] = {
            "etiqueta": traducir_cficode(cficode),
            "cantidad": cantidad,
        }
    return breakdown


def calcular_breakdown_por_segmento(instrumentos: list[dict]) -> dict[str, int]:
    """
    Calcula cuántos instrumentos hay por segmento de mercado.

    El segmento es el dato clave para el mapeo: MERV = BYMA (Argo opera
    acá), TIVA = MAE (mayorista), DUAL = futuros, etc.

    Devuelve un dict ordenado de mayor a menor cantidad.
    """
    contador = Counter(extraer_segmento(inst) for inst in instrumentos)
    return dict(contador.most_common())


def imprimir_resumen(
    instrumentos: list[dict],
    ruta_archivo: Path,
    breakdown_cfi: dict[str, dict],
    breakdown_seg: dict[str, int],
) -> None:
    """Imprime resumen a consola."""
    print("\n" + "=" * 70)
    print("SNAPSHOT DETALLADO REMARKETS COMPLETADO")
    print("=" * 70)
    print(f"Instrumentos descargados : {len(instrumentos)}")
    print(f"Archivo                  : {ruta_archivo}")
    print(f"Tamaño archivo           : {ruta_archivo.stat().st_size / 1024:.1f} KB")

    print("\nBreakdown por SEGMENTO:")
    for segmento, cantidad in breakdown_seg.items():
        print(f"  {segmento:<16} {cantidad}")

    print("\nBreakdown por CFI code:")
    for cficode, info in breakdown_cfi.items():
        etiqueta = info["etiqueta"]
        cantidad = info["cantidad"]
        print(f"  {cficode:<10} {etiqueta:<28} {cantidad}")
    print("=" * 70 + "\n")


def main() -> int:
    """Punto de entrada. Devuelve 0 si todo OK, 1 si hubo error."""
    log.info("Inicio: snapshot detallado reMarkets")

    with get_session() as session:
        registro = abrir_log_corrida(session)
        try:
            cred = cargar_credenciales()
            conectar_remarkets(cred)
            instrumentos = descargar_catalogo_detallado()
            ruta = guardar_snapshot(instrumentos)
            breakdown_cfi = calcular_breakdown_por_cficode(instrumentos)
            breakdown_seg = calcular_breakdown_por_segmento(instrumentos)

            cerrar_log_corrida(
                session,
                registro,
                estado="completado",
                instrumentos_procesados=len(instrumentos),
                metadata={
                    "archivo": str(ruta.relative_to(RAIZ_PROYECTO)),
                    "tipo_snapshot": "detallado",
                    "tamaño_bytes": ruta.stat().st_size,
                    "breakdown_segmentos": breakdown_seg,
                    "breakdown_cficode": breakdown_cfi,
                },
            )
            imprimir_resumen(instrumentos, ruta, breakdown_cfi, breakdown_seg)
            return 0

        except Exception as e:
            tb = traceback.format_exc()
            log.error(f"Snapshot detallado falló: {e}\n{tb}")
            cerrar_log_corrida(
                session,
                registro,
                estado="error",
                error=f"{type(e).__name__}: {e}\n{tb}",
            )
            print(f"\nERROR: {e}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())