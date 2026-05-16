"""
Heartbeat diario de Argo.

Script que ejecuta systemd timer una vez al día a las 09:00 hora Buenos Aires.
Su única función es enviar una alerta INFO a Telegram confirmando que el VPS
sigue operativo.

La lógica es deliberadamente simple: si no recibís el heartbeat en tu horario
esperado, sabés que algo está mal (VPS caído, problema de red, falla en
Telegram, etc.). La ausencia de heartbeat es la alerta.

Información incluida:
- Timestamp local (Buenos Aires).
- Uptime del servidor (cuánto tiempo lleva encendido).
- Uso de memoria.
- Uso de disco.

Estos datos sirven para detectar tempranamente problemas de capacidad
(memoria llenándose, disco creciendo) antes de que se vuelvan críticos.
"""

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
import sys

# Agregar la raíz del proyecto al sys.path para poder importar módulos de src/
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

from src.utils.telegram_notifier import enviar_alerta
from src.utils.logger import obtener_logger

_log = obtener_logger(__name__)


def obtener_uptime() -> str:
    """
    Devuelve el uptime del sistema en formato legible.
    Lee /proc/uptime que está disponible en cualquier Linux.
    """
    try:
        with open("/proc/uptime", "r") as f:
            segundos = float(f.read().split()[0])
        dias = int(segundos // 86400)
        horas = int((segundos % 86400) // 3600)
        minutos = int((segundos % 3600) // 60)
        return f"{dias}d {horas}h {minutos}m"
    except Exception as e:
        _log.warning(f"No se pudo leer uptime: {e}")
        return "desconocido"


def obtener_uso_memoria() -> str:
    """
    Devuelve el porcentaje de memoria usada.
    Usa el comando 'free' que viene en cualquier distribución Linux.
    """
    try:
        resultado = subprocess.run(
            ["free", "-m"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # La línea relevante empieza con "Mem:" y tiene columnas:
        # total used free shared buff/cache available
        for linea in resultado.stdout.split("\n"):
            if linea.startswith("Mem:"):
                campos = linea.split()
                total = int(campos[1])
                usado = int(campos[2])
                pct = (usado / total) * 100
                return f"{pct:.0f}% ({usado}/{total} MB)"
        return "no parseable"
    except Exception as e:
        _log.warning(f"No se pudo leer memoria: {e}")
        return "desconocido"


def obtener_uso_disco() -> str:
    """
    Devuelve el porcentaje de disco usado en la partición raíz (/).
    Usa shutil.disk_usage que es portable y no requiere comandos externos.
    """
    try:
        total, usado, libre = shutil.disk_usage("/")
        pct = (usado / total) * 100
        usado_gb = usado / (1024 ** 3)
        total_gb = total / (1024 ** 3)
        return f"{pct:.0f}% ({usado_gb:.1f}/{total_gb:.1f} GB)"
    except Exception as e:
        _log.warning(f"No se pudo leer disco: {e}")
        return "desconocido"


def main() -> int:
    """
    Función principal. Envía la alerta de heartbeat.

    Devuelve 0 si la alerta se envió correctamente, 1 si falló.
    El código de salida es importante porque systemd lo registra y
    puede usarse para detectar fallos en el timer.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uptime = obtener_uptime()
    memoria = obtener_uso_memoria()
    disco = obtener_uso_disco()

    mensaje = (
        f"*Heartbeat Argo VPS*\n\n"
        f"Hora local: `{timestamp}`\n"
        f"Uptime: `{uptime}`\n"
        f"Memoria: `{memoria}`\n"
        f"Disco: `{disco}`\n\n"
        f"_VPS operativo. Todos los sistemas OK._"
    )

    _log.info("Ejecutando heartbeat diario")

    exito = enviar_alerta(mensaje, "INFO")

    if exito:
        _log.info("Heartbeat enviado correctamente")
        return 0
    else:
        _log.error("Heartbeat NO se pudo enviar")
        return 1


if __name__ == "__main__":
    sys.exit(main())