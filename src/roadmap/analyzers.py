"""
Analizadores del roadmap.

Funciones puras de detección: reciben el roadmap parseado (dict) y una fecha
de referencia, devuelven listas de hitos en cada categoría problemática.

Sin side effects: no leen archivos, no hacen llamadas de red, no escriben logs.
Esto las vuelve trivialmente testeables con roadmaps simulados en memoria.

La lógica de orquestación (cargar archivo, validar schema, formatear mensaje,
enviar Telegram) vive en engine.py.

Categorías detectadas:

1. Hitos atrasados: fecha_cierre_target ya pasada y estado != cerrado/jubilado.
2. Hitos próximos a target: fecha_cierre_target dentro de los próximos N días.
3. Hitos bloqueados: estado == 'bloqueado'.
4. Hitos con dependencias rotas: estado == 'en_curso' pero alguna de sus
   dependencias no está cerrada/jubilada.
"""

from datetime import date
from typing import Optional


# Estados que cuentan como "hito ya terminado" (cerrado o descartado conscientemente)
ESTADOS_TERMINADOS = {"cerrado", "jubilado"}


def _parsear_fecha(fecha_str: Optional[str]) -> Optional[date]:
    """
    Convierte un string YYYY-MM-DD a date. Devuelve None si la entrada es None
    o si no parsea (defensivo: nunca crashea, deja que el caller decida).
    """
    if fecha_str is None or fecha_str == "":
        return None
    try:
        return date.fromisoformat(fecha_str)
    except (ValueError, TypeError):
        return None


def _iterar_hitos(roadmap: dict):
    """
    Generador que itera sobre todos los hitos de todas las fases.
    Yields tuplas (fase, hito) para que el caller tenga contexto de la fase.
    """
    for fase in roadmap.get("fases", []):
        for hito in fase.get("hitos", []):
            yield fase, hito


def detectar_hitos_atrasados(roadmap: dict, hoy: date) -> list[dict]:
    """
    Devuelve hitos cuya fecha_cierre_target ya pasó y siguen sin estar
    cerrados/jubilados.

    Cada elemento devuelto es un dict con:
    - id: identificador del hito (ej "H0.4")
    - nombre: nombre humano
    - fase_id: id de la fase contenedora
    - fecha_target: fecha_cierre_target original
    - dias_atraso: cuántos días pasaron desde el target
    """
    atrasados = []
    for fase, hito in _iterar_hitos(roadmap):
        if hito.get("estado") in ESTADOS_TERMINADOS:
            continue

        target = _parsear_fecha(hito.get("fecha_cierre_target"))
        if target is None:
            continue  # Sin target definido, no podemos decir si está atrasado

        if target < hoy:
            atrasados.append({
                "id": hito["id"],
                "nombre": hito["nombre"],
                "fase_id": fase["id"],
                "fecha_target": hito["fecha_cierre_target"],
                "dias_atraso": (hoy - target).days,
            })
    return atrasados


def detectar_hitos_proximos_a_target(
    roadmap: dict,
    hoy: date,
    dias_anticipacion: int = 3,
) -> list[dict]:
    """
    Devuelve hitos cuya fecha_cierre_target está dentro de los próximos
    'dias_anticipacion' días (inclusive), y que aún no están cerrados/jubilados.

    Excluye los ya atrasados (esos los reporta detectar_hitos_atrasados).
    """
    proximos = []
    for fase, hito in _iterar_hitos(roadmap):
        if hito.get("estado") in ESTADOS_TERMINADOS:
            continue

        target = _parsear_fecha(hito.get("fecha_cierre_target"))
        if target is None:
            continue

        dias_restantes = (target - hoy).days
        if 0 <= dias_restantes <= dias_anticipacion:
            proximos.append({
                "id": hito["id"],
                "nombre": hito["nombre"],
                "fase_id": fase["id"],
                "fecha_target": hito["fecha_cierre_target"],
                "dias_restantes": dias_restantes,
            })
    return proximos


def detectar_hitos_bloqueados(roadmap: dict) -> list[dict]:
    """
    Devuelve hitos con estado == 'bloqueado'.

    Esto es un estado explícito que alguien (vos o el sistema) marca manualmente
    cuando un hito no puede progresar por alguna razón. El engine lo reporta
    para que no se olvide.
    """
    bloqueados = []
    for fase, hito in _iterar_hitos(roadmap):
        if hito.get("estado") == "bloqueado":
            bloqueados.append({
                "id": hito["id"],
                "nombre": hito["nombre"],
                "fase_id": fase["id"],
            })
    return bloqueados


def detectar_dependencias_rotas(roadmap: dict) -> list[dict]:
    """
    Devuelve hitos en estado 'en_curso' cuyas dependencias no están todas
    cerradas/jubiladas.

    Si un hito está en_curso pero depende de otros que aún no terminaron,
    hay una inconsistencia: o el hito en_curso no debería haberse arrancado
    todavía, o los pre-requisitos están cerrados de hecho pero no marcados.
    """
    # Mapa rápido de id_hito -> estado para resolver dependencias
    mapa_estados = {}
    for _, hito in _iterar_hitos(roadmap):
        mapa_estados[hito["id"]] = hito.get("estado")

    rotas = []
    for fase, hito in _iterar_hitos(roadmap):
        if hito.get("estado") != "en_curso":
            continue

        deps_no_terminadas = []
        for dep_id in hito.get("depende_de", []):
            estado_dep = mapa_estados.get(dep_id)
            if estado_dep is None:
                # Dependencia referenciada que no existe en el roadmap.
                # Esto debería estar bloqueado por el schema validator, pero
                # por seguridad lo manejamos defensivamente.
                deps_no_terminadas.append(f"{dep_id} (no existe)")
            elif estado_dep not in ESTADOS_TERMINADOS:
                deps_no_terminadas.append(f"{dep_id} ({estado_dep})")

        if deps_no_terminadas:
            rotas.append({
                "id": hito["id"],
                "nombre": hito["nombre"],
                "fase_id": fase["id"],
                "dependencias_no_terminadas": deps_no_terminadas,
            })
    return rotas


def hay_algo_accionable(
    atrasados: list,
    proximos: list,
    bloqueados: list,
    rotas: list,
) -> bool:
    """
    Determina si hay al menos una situación que amerite enviar alerta.

    Filosofía 'silencio si todo está OK': si esta función devuelve False,
    el engine no manda nada a Telegram. Lo único que sí se loguea es la
    corrida del engine (que ejecutó sin encontrar nada nuevo).
    """
    return bool(atrasados or proximos or bloqueados or rotas)