"""
Engine del roadmap.

Orquesta la lectura del roadmap.json, su validación contra schema,
el análisis vía analyzers.py, y la emisión de alertas vía Telegram.

Filosofía:
- Silencio si todo está OK: si no hay nada accionable, no se manda nada a
  Telegram. Sí se loguea la corrida exitosa.
- Validación defensiva: si roadmap.json no parsea o no valida schema, se
  emite CRITICAL inmediatamente.
- Manejo defensivo de errores: cualquier excepción no esperada se loguea
  y se intenta reportar a Telegram. El engine nunca debe crashear silencioso.

Punto de entrada típico: scripts/run_roadmap_engine.py lo invoca via systemd
timer una vez al día a las 09:00 hora Buenos Aires.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import jsonschema

from src.utils.logger import obtener_logger
from src.utils.telegram_notifier import enviar_alerta
from src.roadmap.analyzers import (
    detectar_hitos_atrasados,
    detectar_hitos_proximos_a_target,
    detectar_hitos_bloqueados,
    detectar_dependencias_rotas,
    hay_algo_accionable,
)


_log = obtener_logger(__name__)

# Paths relativos a raíz del proyecto
_RAIZ_PROYECTO = Path(__file__).resolve().parent.parent.parent
_RUTA_ROADMAP = _RAIZ_PROYECTO / "roadmap.json"
_RUTA_SCHEMA = _RAIZ_PROYECTO / "roadmap.schema.json"


def _cargar_y_validar_roadmap() -> Optional[dict]:
    """
    Lee roadmap.json y lo valida contra roadmap.schema.json.

    Si algo falla (archivo faltante, JSON inválido, schema no cumplido),
    emite alerta CRITICAL y devuelve None.

    Si todo OK, devuelve el dict parseado.
    """
    # Verificar existencia de archivos
    if not _RUTA_ROADMAP.exists():
        mensaje = f"roadmap.json no encontrado en {_RUTA_ROADMAP}"
        _log.critical(mensaje)
        enviar_alerta(mensaje, "CRITICAL")
        return None

    if not _RUTA_SCHEMA.exists():
        mensaje = f"roadmap.schema.json no encontrado en {_RUTA_SCHEMA}"
        _log.critical(mensaje)
        enviar_alerta(mensaje, "CRITICAL")
        return None

    # Parsear JSONs
    try:
        with open(_RUTA_ROADMAP, encoding="utf-8") as f:
            roadmap = json.load(f)
        with open(_RUTA_SCHEMA, encoding="utf-8") as f:
            schema = json.load(f)
    except json.JSONDecodeError as e:
        mensaje = f"Error parseando JSON: {e}"
        _log.critical(mensaje)
        enviar_alerta(mensaje, "CRITICAL")
        return None

    # Validar contra schema
    try:
        jsonschema.validate(instance=roadmap, schema=schema)
    except jsonschema.ValidationError as e:
        mensaje = (
            f"roadmap.json no valida contra schema.\n"
            f"Error: `{e.message}`\n"
            f"Path: `{'.'.join(str(p) for p in e.path)}`"
        )
        _log.critical(mensaje)
        enviar_alerta(mensaje, "CRITICAL")
        return None

    return roadmap


def _formatear_mensaje(
    fecha: date,
    atrasados: list,
    proximos: list,
    bloqueados: list,
    rotas: list,
) -> str:
    """
    Construye el mensaje resumido para Telegram.

    Solo incluye las secciones que tienen contenido. Si no hay nada accionable,
    igual genera un mensaje (aunque en ese caso el caller decide no enviarlo).
    """
    lineas = [f"*Argo Roadmap — {fecha.isoformat()}*", ""]

    if atrasados:
        lineas.append("⚠️ *Hitos atrasados:*")
        for h in atrasados:
            lineas.append(
                f"  • `{h['id']}` ({h['nombre']}) — "
                f"target {h['fecha_target']}, {h['dias_atraso']}d atraso"
            )
        lineas.append("")

    if proximos:
        lineas.append("ℹ️ *Hitos próximos a target:*")
        for h in proximos:
            sufijo = "hoy" if h["dias_restantes"] == 0 else f"en {h['dias_restantes']}d"
            lineas.append(
                f"  • `{h['id']}` ({h['nombre']}) — vence {sufijo}"
            )
        lineas.append("")

    if bloqueados:
        lineas.append("⚠️ *Hitos bloqueados:*")
        for h in bloqueados:
            lineas.append(f"  • `{h['id']}` ({h['nombre']})")
        lineas.append("")

    if rotas:
        lineas.append("❌ *Inconsistencias (dependencias rotas):*")
        for h in rotas:
            deps = ", ".join(h["dependencias_no_terminadas"])
            lineas.append(
                f"  • `{h['id']}` ({h['nombre']}) en_curso pero falta: {deps}"
            )
        lineas.append("")

    return "\n".join(lineas).rstrip()


def _determinar_nivel(atrasados: list, bloqueados: list, rotas: list) -> str:
    """
    Determina el nivel de severidad del mensaje según el peor caso encontrado.

    Reglas:
    - Si hay dependencias rotas: ERROR (es señal de inconsistencia)
    - Si hay atrasados o bloqueados: WARNING
    - Si solo hay próximos a target: INFO

    Si no hay nada accionable, esta función no se llama (el engine no manda
    nada en ese caso).
    """
    if rotas:
        return "ERROR"
    if atrasados or bloqueados:
        return "WARNING"
    return "INFO"


def ejecutar(
    hoy: Optional[date] = None,
    dias_anticipacion: int = 3,
) -> bool:
    """
    Ejecuta una corrida del roadmap engine.

    Parámetros
    ----------
    hoy : date, opcional
        Fecha de referencia para los análisis. Default: fecha actual.
        Útil para testing: permite simular corridas futuras.
    dias_anticipacion : int
        Cuántos días antes del target empezar a alertar como "próximo".
        Default: 3.

    Devuelve
    --------
    bool
        True si la corrida se completó exitosamente (incluso si no hubo nada
        para alertar). False si hubo error de carga/validación/envío.
    """
    if hoy is None:
        hoy = date.today()

    _log.info(f"Roadmap engine corriendo para fecha de referencia: {hoy.isoformat()}")

    # Cargar y validar
    roadmap = _cargar_y_validar_roadmap()
    if roadmap is None:
        # _cargar_y_validar_roadmap ya emitió CRITICAL si hubo problema
        return False

    # Analizar
    atrasados = detectar_hitos_atrasados(roadmap, hoy)
    proximos = detectar_hitos_proximos_a_target(roadmap, hoy, dias_anticipacion)
    bloqueados = detectar_hitos_bloqueados(roadmap)
    rotas = detectar_dependencias_rotas(roadmap)

    _log.info(
        f"Análisis completado: "
        f"{len(atrasados)} atrasados, "
        f"{len(proximos)} próximos, "
        f"{len(bloqueados)} bloqueados, "
        f"{len(rotas)} dependencias rotas"
    )

    # Decidir si hay algo que reportar
    if not hay_algo_accionable(atrasados, proximos, bloqueados, rotas):
        _log.info("Nada accionable. Silencio (filosofía: no notificación es buena noticia).")
        return True

    # Formatear y enviar
    mensaje = _formatear_mensaje(hoy, atrasados, proximos, bloqueados, rotas)
    nivel = _determinar_nivel(atrasados, bloqueados, rotas)

    exito = enviar_alerta(mensaje, nivel)

    if exito:
        _log.info("Resumen del roadmap enviado a Telegram correctamente.")
    else:
        _log.error("Fallo enviando resumen del roadmap a Telegram. Ver logs del notifier.")

    return exito


if __name__ == "__main__":
    # Permitir ejecutar el módulo directamente para test:
    #     python -m src.roadmap.engine
    print("Ejecutando roadmap engine para hoy...")
    exito = ejecutar()
    if exito:
        print("OK: engine ejecutado. Revisá Telegram si había algo accionable.")
    else:
        print("ERROR: engine falló. Ver logs en logs/argo.log")