"""
Logger centralizado de Argo.

Toda la plataforma importa de acá. La filosofía es:
- Loguear TODO, especialmente lo que sale mal.
- Rotación diaria automática.
- Retención configurable (default 90 días).
- Mismo formato en todo el sistema para que el dashboard
  y los reportes parseen consistentemente.

Uso típico desde otro módulo:

    from src.utils.logger import obtener_logger
    log = obtener_logger(__name__)
    log.info("Mensaje informativo")
    log.error("Algo salió mal", exc_info=True)
"""

import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional


# Cache de loggers ya configurados para no duplicar handlers
_loggers_configurados: dict[str, logging.Logger] = {}

# Path a config.json (relativo a raíz del proyecto)
_RUTA_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "config.json"


def _cargar_config() -> dict:
    """
    Lee config/config.json. Si no existe o está roto,
    devuelve defaults conservadores para que el logger
    funcione igual aunque el sistema esté a medio configurar.
    """
    defaults = {
        "logging": {
            "nivel": "INFO",
            "rotacion": "diaria",
            "retencion_dias": 90,
            "formato": "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        },
        "paths": {"logs": "logs"},
    }
    try:
        with open(_RUTA_CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        # Merge defensivo: si falta alguna clave, uso default
        cfg.setdefault("logging", defaults["logging"])
        cfg.setdefault("paths", defaults["paths"])
        for clave, valor in defaults["logging"].items():
            cfg["logging"].setdefault(clave, valor)
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        # El logger funciona aunque no haya config.
        # Esto es importante: si algo falla al cargar config,
        # queremos poder loguear ese mismo error.
        return defaults


def obtener_logger(nombre: str, archivo: Optional[str] = None) -> logging.Logger:
    """
    Devuelve un logger configurado.

    Parámetros
    ----------
    nombre : str
        Nombre del logger, típicamente __name__ del módulo llamador.
    archivo : str, opcional
        Si se especifica, el log se escribe a logs/{archivo}_YYYYMMDD.log
        Si no, se escribe al log general logs/argo_YYYYMMDD.log

    Devuelve
    --------
    logging.Logger configurado con handler de archivo rotativo
    y handler de consola.
    """
    if nombre in _loggers_configurados:
        return _loggers_configurados[nombre]

    cfg = _cargar_config()
    log_cfg = cfg["logging"]
    nivel = getattr(logging, log_cfg["nivel"].upper(), logging.INFO)
    formato = logging.Formatter(log_cfg["formato"])

    # Resolver carpeta de logs (relativa a raíz del proyecto)
    raiz_proyecto = Path(__file__).resolve().parent.parent.parent
    carpeta_logs = raiz_proyecto / cfg["paths"]["logs"]
    carpeta_logs.mkdir(parents=True, exist_ok=True)

    # Nombre del archivo de log
    nombre_archivo = archivo if archivo else "argo"
    ruta_log = carpeta_logs / f"{nombre_archivo}.log"

    # Crear el logger
    logger = logging.getLogger(nombre)
    logger.setLevel(nivel)
    logger.propagate = False  # Evita duplicación si root logger está configurado

    # Limpio handlers previos por si el logger ya existe en el root
    logger.handlers.clear()

    # Handler de archivo con rotación diaria
    # backupCount = días de retención configurados
    handler_archivo = TimedRotatingFileHandler(
        filename=ruta_log,
        when="midnight",
        interval=1,
        backupCount=log_cfg["retencion_dias"],
        encoding="utf-8",
        utc=False,  # Usamos hora local (Buenos Aires)
    )
    handler_archivo.suffix = "%Y%m%d"  # logs/argo.log.20251215
    handler_archivo.setFormatter(formato)
    handler_archivo.setLevel(nivel)
    logger.addHandler(handler_archivo)

    # Handler de consola (útil en desarrollo y para ver corridas manuales)
    handler_consola = logging.StreamHandler()
    handler_consola.setFormatter(formato)
    handler_consola.setLevel(nivel)
    logger.addHandler(handler_consola)

    _loggers_configurados[nombre] = logger
    return logger


def obtener_logger_collector(nombre_collector: str) -> logging.Logger:
    """
    Atajo para collectors. Cada collector tiene su archivo de log
    separado para facilitar debug sin tener que filtrar el log general.

    Ejemplo: obtener_logger_collector("rava") escribe a
    logs/collector_rava_YYYYMMDD.log
    """
    return obtener_logger(
        nombre=f"argo.collectors.{nombre_collector}",
        archivo=f"collector_{nombre_collector}",
    )


def obtener_logger_estrategia(nombre_estrategia: str) -> logging.Logger:
    """
    Atajo para estrategias. Cada estrategia tiene su archivo
    de log separado.

    Ejemplo: obtener_logger_estrategia("pairs_cedear") escribe a
    logs/estrategia_pairs_cedear_YYYYMMDD.log
    """
    return obtener_logger(
        nombre=f"argo.estrategias.{nombre_estrategia}",
        archivo=f"estrategia_{nombre_estrategia}",
    )