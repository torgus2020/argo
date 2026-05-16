"""
Notificador de alertas vía Telegram.

Módulo central de notificaciones de Argo. Toda la plataforma lo usa
para emitir alertas: collectors, estrategias, risk engine, roadmap engine,
kill switches.

Filosofía:
- Interfaz simple: enviar_alerta(mensaje, nivel) y nada más.
- Cuatro niveles semánticamente distintos: INFO, WARNING, ERROR, CRITICAL.
- Loggeo paralelo: cada alerta también se escribe en el log local.
- Manejo defensivo: si Telegram falla, devuelve False y loguea el error,
  pero NO levanta excepción no manejada. No queremos crashear el sistema
  porque una notificación no se entregó.
- Markdown soportado para mensajes formateados.

Uso típico:

    from src.utils.telegram_notifier import enviar_alerta

    enviar_alerta("VPS Argo OK", "INFO")
    enviar_alerta("Slippage real 2.3x simulado", "WARNING")
    enviar_alerta("Collector Rava sin datos hace 1hs", "ERROR")
    enviar_alerta("Kill switch disparado: drawdown 20%", "CRITICAL")
"""

import json
from pathlib import Path
from typing import Optional

import requests

from src.utils.logger import obtener_logger


# Logger del módulo
_log = obtener_logger(__name__)

# Niveles válidos de alerta y sus emojis representativos
NIVELES_VALIDOS = {
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "CRITICAL": "🚨",
}

# Timeout en segundos para llamadas a la API de Telegram
TIMEOUT_TELEGRAM = 5

# Path a secrets.json (relativo a raíz del proyecto)
_RUTA_SECRETS = Path(__file__).resolve().parent.parent.parent / "config" / "secrets.json"


def _cargar_credenciales() -> Optional[dict]:
    """
    Lee config/secrets.json y devuelve el bloque 'telegram'.

    Devuelve None si:
    - El archivo no existe.
    - El archivo no es JSON válido.
    - El bloque 'telegram' no está presente.
    - El bot_token o chat_id son placeholders sin reemplazar.

    En todos los casos de None, se loguea el motivo con detalle.
    """
    if not _RUTA_SECRETS.exists():
        _log.error("No se encontró config/secrets.json. Notificaciones deshabilitadas.")
        return None

    try:
        with open(_RUTA_SECRETS, encoding="utf-8") as f:
            secrets = json.load(f)
    except json.JSONDecodeError as e:
        _log.error(f"config/secrets.json no es JSON válido: {e}")
        return None

    if "telegram" not in secrets:
        _log.error("Bloque 'telegram' faltante en secrets.json.")
        return None

    cred = secrets["telegram"]

    # Validación defensiva: detectar placeholders sin reemplazar
    if not cred.get("bot_token") or "REEMPLAZAR" in str(cred.get("bot_token", "")):
        _log.error("bot_token de Telegram no configurado en secrets.json.")
        return None

    if not cred.get("chat_id") or "REEMPLAZAR" in str(cred.get("chat_id", "")):
        _log.error("chat_id de Telegram no configurado en secrets.json.")
        return None

    return cred


def enviar_alerta(mensaje: str, nivel: str = "INFO") -> bool:
    """
    Envía una alerta a Telegram al chat configurado en secrets.json.

    Parámetros
    ----------
    mensaje : str
        Texto de la alerta. Soporta Markdown de Telegram (negrita con *texto*,
        cursiva con _texto_, código con `texto`, etc).
    nivel : str
        Uno de: 'INFO', 'WARNING', 'ERROR', 'CRITICAL'. Default 'INFO'.

    Devuelve
    --------
    bool
        True si la alerta se envió correctamente a Telegram.
        False si hubo algún problema (credenciales faltantes, error de red,
        timeout, error de API de Telegram). El motivo siempre queda en log.

    El mensaje también se loguea localmente al nivel correspondiente,
    independientemente de si llegó a Telegram o no.
    """
    nivel = nivel.upper()
    if nivel not in NIVELES_VALIDOS:
        _log.warning(f"Nivel '{nivel}' no válido. Usando INFO. Mensaje original: {mensaje}")
        nivel = "INFO"

    # Loguear localmente primero (independiente de si Telegram funciona)
    nivel_log = {
        "INFO": _log.info,
        "WARNING": _log.warning,
        "ERROR": _log.error,
        "CRITICAL": _log.critical,
    }
    nivel_log[nivel](f"ALERTA: {mensaje}")

    # Cargar credenciales (puede devolver None si hay problemas)
    cred = _cargar_credenciales()
    if cred is None:
        _log.warning("Alerta no enviada a Telegram por falta de credenciales válidas.")
        return False

    # Formatear el mensaje con el emoji del nivel
    emoji = NIVELES_VALIDOS[nivel]
    mensaje_formateado = f"{emoji} *{nivel}*\n\n{mensaje}"

    # Llamar a la API de Telegram
    url = f"https://api.telegram.org/bot{cred['bot_token']}/sendMessage"
    payload = {
        "chat_id": cred["chat_id"],
        "text": mensaje_formateado,
        "parse_mode": "Markdown",
    }

    try:
        respuesta = requests.post(url, json=payload, timeout=TIMEOUT_TELEGRAM)
        if respuesta.status_code == 200:
            _log.info("Alerta enviada a Telegram exitosamente.")
            return True
        else:
            _log.error(
                f"Telegram devolvió HTTP {respuesta.status_code}: {respuesta.text}"
            )
            return False
    except requests.exceptions.Timeout:
        _log.error(f"Timeout ({TIMEOUT_TELEGRAM}s) al enviar alerta a Telegram.")
        return False
    except requests.exceptions.RequestException as e:
        _log.error(f"Error de red al enviar alerta a Telegram: {e}")
        return False


def enviar_alerta_test() -> bool:
    """
    Envía una alerta de prueba con un mensaje predefinido y nivel INFO.

    Útil para validar que la configuración de Telegram funciona correctamente
    desde el entorno actual (Windows local o VPS).
    """
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mensaje = (
        f"*Test de notificaciones Argo*\n\n"
        f"Hora: `{timestamp}`\n"
        f"Si recibís este mensaje, el módulo `telegram_notifier` "
        f"está funcionando correctamente."
    )
    return enviar_alerta(mensaje, "INFO")


if __name__ == "__main__":
    # Permitir ejecutar el módulo directamente para test:
    #     python -m src.utils.telegram_notifier
    print("Enviando alerta de prueba a Telegram...")
    if enviar_alerta_test():
        print("OK: alerta enviada. Revisá tu Telegram.")
    else:
        print("ERROR: alerta no enviada. Revisar logs en logs/argo.log para detalle.")