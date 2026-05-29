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

Nota sobre la rotación (importante, ver H1.6 / fix WinError 32):
La rotación NO se hace renombrando el archivo del día (el patrón clásico
de TimedRotatingFileHandler: escribir a 'argo.log' y renombrarlo a
'argo.log.20260525' al cambiar el día). Ese patrón falla en Windows
porque el sistema operativo no deja renombrar un archivo que está
abierto por algún proceso (PermissionError WinError 32), y en Argo
varios módulos abren el mismo archivo de log dentro de una misma corrida.
En cambio, escribimos directo a un archivo con la fecha en el nombre
('argo_20260529.log') y cuando cambia el día abrimos uno nuevo. Nunca
se renombra nada, así que funciona idéntico en Windows y en Linux, tanto
en scripts cortos como en servicios de larga duración del VPS.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


# Cache de loggers ya configurados para no duplicar handlers
_loggers_configurados: dict[str, logging.Logger] = {}

# Path a config.json (relativo a raíz del proyecto)
_RUTA_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "config.json"


class ManejadorRotativoDiario(logging.FileHandler):
    """
    Handler de archivo que rota por fecha SIN renombrar archivos.

    Escribe directamente a 'logs/{prefijo}_YYYYMMDD.log'. Cuando cambia
    el día, cierra el archivo del día anterior y abre el del día nuevo.
    No renombra nada nunca, así que evita el WinError 32 de Windows
    (no se puede renombrar un archivo abierto por otro proceso/handler).

    La retención se maneja borrando los archivos de este mismo prefijo
    que sean más viejos que la cantidad de días configurada. Es el
    equivalente a backupCount de TimedRotatingFileHandler, pero contado
    en días reales sobre la fecha del nombre del archivo.
    """

    def __init__(
        self,
        carpeta_logs: Path,
        prefijo: str,
        retencion_dias: int,
        encoding: str = "utf-8",
    ):
        self._carpeta_logs = Path(carpeta_logs)
        self._prefijo = prefijo
        self._retencion_dias = retencion_dias
        self._fecha_actual = self._hoy()
        # FileHandler abre, en modo append, el archivo del día actual.
        super().__init__(
            self._ruta_para_fecha(self._fecha_actual),
            mode="a",
            encoding=encoding,
            delay=False,
        )
        # Al arrancar, limpio lo que ya está vencido.
        self._limpiar_antiguos()

    @staticmethod
    def _hoy() -> str:
        # Fecha LOCAL (Buenos Aires). El sistema corre con TZ local
        # tanto en Windows como en el VPS (NTP sincronizado a -03).
        return datetime.now().strftime("%Y%m%d")

    def _ruta_para_fecha(self, fecha: str) -> str:
        return str(self._carpeta_logs / f"{self._prefijo}_{fecha}.log")

    def emit(self, record: logging.LogRecord) -> None:
        # Antes de escribir cada registro, chequeo si cambió el día.
        # Si cambió, roto: cierro el archivo viejo y abro el del día
        # nuevo. El chequeo es una comparación de strings, despreciable.
        fecha_ahora = self._hoy()
        if fecha_ahora != self._fecha_actual:
            self._rotar(fecha_ahora)
        super().emit(record)

    def _rotar(self, fecha_nueva: str) -> None:
        # Cierra el stream del día anterior y abre el del día nuevo.
        # Tomo el lock del handler para que no haya escritura concurrente
        # mientras cambio el archivo destino.
        self.acquire()
        try:
            if self.stream:
                self.stream.close()
                self.stream = None
            self._fecha_actual = fecha_nueva
            self.baseFilename = os.path.abspath(self._ruta_para_fecha(fecha_nueva))
            self.stream = self._open()
            self._limpiar_antiguos()
        finally:
            self.release()

    def _limpiar_antiguos(self) -> None:
        # Borra los logs de ESTE prefijo más viejos que la retención.
        # Si la retención es 0 o negativa, no borra nada (retención infinita).
        if self._retencion_dias <= 0:
            return
        limite = datetime.now() - timedelta(days=self._retencion_dias)
        # El glob por prefijo evita pisar logs de otros componentes:
        # 'argo_*.log' no matchea 'collector_rava_*.log', etc.
        patron = f"{self._prefijo}_*.log"
        for archivo in self._carpeta_logs.glob(patron):
            # Extraigo la fecha del nombre: '{prefijo}_YYYYMMDD'
            sufijo_fecha = archivo.stem[len(self._prefijo) + 1:]
            try:
                fecha_archivo = datetime.strptime(sufijo_fecha, "%Y%m%d")
            except ValueError:
                # Nombre que no termina en una fecha válida: lo ignoro
                # (no es un log rotado por nosotros, no lo toco).
                continue
            if fecha_archivo < limite:
                try:
                    archivo.unlink()
                except OSError:
                    # Si el archivo está bloqueado justo ahora, no es
                    # crítico: lo reintentará la próxima rotación.
                    pass


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
    logging.Logger configurado con handler de archivo rotativo por fecha
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

    # Prefijo del archivo de log (sin fecha; la fecha la agrega el handler)
    prefijo = archivo if archivo else "argo"

    # Crear el logger
    logger = logging.getLogger(nombre)
    logger.setLevel(nivel)
    logger.propagate = False  # Evita duplicación si root logger está configurado

    # Limpio handlers previos por si el logger ya existe en el root
    logger.handlers.clear()

    # Handler de archivo con rotación diaria por fecha en el nombre.
    # backupCount equivalente = retencion_dias (lo maneja el handler).
    handler_archivo = ManejadorRotativoDiario(
        carpeta_logs=carpeta_logs,
        prefijo=prefijo,
        retencion_dias=log_cfg["retencion_dias"],
        encoding="utf-8",
    )
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