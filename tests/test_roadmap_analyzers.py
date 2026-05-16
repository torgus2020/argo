"""
Tests unitarios de los analizadores del roadmap.

Cubren los cuatro detectores principales:
- detectar_hitos_atrasados
- detectar_hitos_proximos_a_target
- detectar_hitos_bloqueados
- detectar_dependencias_rotas

Más la función helper:
- hay_algo_accionable

Filosofía:
- Cada test es chico y testea una única cosa.
- Los roadmaps de prueba son simulados en memoria (dicts), no leen del disco.
- No dependen de Telegram, red, o filesystem.
- Se ejecutan con: pytest tests/test_roadmap_analyzers.py
"""

from datetime import date

import pytest

from src.roadmap.analyzers import (
    detectar_hitos_atrasados,
    detectar_hitos_proximos_a_target,
    detectar_hitos_bloqueados,
    detectar_dependencias_rotas,
    hay_algo_accionable,
)


# =============================================================================
# Helpers para construir roadmaps de prueba
# =============================================================================

def _hito(
    id: str,
    nombre: str = "Test Hito",
    estado: str = "pendiente",
    fecha_cierre_target: str | None = None,
    depende_de: list[str] | None = None,
) -> dict:
    """Constructor mínimo de hito para tests. Solo campos relevantes."""
    return {
        "id": id,
        "nombre": nombre,
        "descripcion": "test",
        "estado": estado,
        "prioridad": "media",
        "fecha_inicio": None,
        "fecha_cierre_target": fecha_cierre_target,
        "fecha_cierre_real": None,
        "depende_de": depende_de or [],
        "entregables": [],
        "criterios_cuantitativos": [],
    }


def _roadmap_con_hitos(*hitos) -> dict:
    """Construye un roadmap minimal con una sola fase y los hitos dados."""
    return {
        "meta": {"schema_version": "1.0.0", "fecha_ultima_actualizacion": "2026-01-01"},
        "fases": [
            {
                "id": "fase_0",
                "nombre": "Test Fase",
                "objetivo": "test",
                "estado": "en_curso",
                "fecha_inicio": None,
                "fecha_cierre_real": None,
                "criterios_cierre": [],
                "hitos": list(hitos),
            }
        ],
        "alertas_config": {
            "dias_anticipacion_target": 3,
            "hora_corrida": "09:00",
            "canales": ["log"],
        },
    }


# =============================================================================
# Tests: detectar_hitos_atrasados
# =============================================================================

def test_atrasados_sin_target_no_aparece():
    """Un hito sin fecha_cierre_target no puede estar atrasado."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", fecha_cierre_target=None),
    )
    resultado = detectar_hitos_atrasados(roadmap, hoy=date(2026, 5, 16))
    assert resultado == []


def test_atrasados_target_pasado_y_pendiente_aparece():
    """Target pasado + estado pendiente = atrasado."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", fecha_cierre_target="2026-05-10", estado="pendiente"),
    )
    resultado = detectar_hitos_atrasados(roadmap, hoy=date(2026, 5, 16))
    assert len(resultado) == 1
    assert resultado[0]["id"] == "H1.1"
    assert resultado[0]["dias_atraso"] == 6


def test_atrasados_target_pasado_pero_cerrado_no_aparece():
    """Target pasado pero ya cerrado = no se reporta."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", fecha_cierre_target="2026-05-10", estado="cerrado"),
    )
    resultado = detectar_hitos_atrasados(roadmap, hoy=date(2026, 5, 16))
    assert resultado == []


def test_atrasados_target_pasado_pero_jubilado_no_aparece():
    """Target pasado pero jubilado = no se reporta (decisión consciente)."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", fecha_cierre_target="2026-05-10", estado="jubilado"),
    )
    resultado = detectar_hitos_atrasados(roadmap, hoy=date(2026, 5, 16))
    assert resultado == []


def test_atrasados_target_futuro_no_aparece():
    """Target en el futuro = no atrasado."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", fecha_cierre_target="2026-12-31", estado="pendiente"),
    )
    resultado = detectar_hitos_atrasados(roadmap, hoy=date(2026, 5, 16))
    assert resultado == []


def test_atrasados_target_hoy_no_aparece():
    """Target = hoy todavía no es atrasado (es el último día válido)."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", fecha_cierre_target="2026-05-16", estado="pendiente"),
    )
    resultado = detectar_hitos_atrasados(roadmap, hoy=date(2026, 5, 16))
    assert resultado == []


# =============================================================================
# Tests: detectar_hitos_proximos_a_target
# =============================================================================

def test_proximos_target_dentro_de_ventana_aparece():
    """Target a 2 días con ventana de 3 = aparece como próximo."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", fecha_cierre_target="2026-05-18", estado="pendiente"),
    )
    resultado = detectar_hitos_proximos_a_target(
        roadmap, hoy=date(2026, 5, 16), dias_anticipacion=3
    )
    assert len(resultado) == 1
    assert resultado[0]["dias_restantes"] == 2


def test_proximos_target_fuera_de_ventana_no_aparece():
    """Target a 10 días con ventana de 3 = no aparece."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", fecha_cierre_target="2026-05-26", estado="pendiente"),
    )
    resultado = detectar_hitos_proximos_a_target(
        roadmap, hoy=date(2026, 5, 16), dias_anticipacion=3
    )
    assert resultado == []


def test_proximos_target_hoy_aparece():
    """Target = hoy aparece como próximo (0 días restantes)."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", fecha_cierre_target="2026-05-16", estado="pendiente"),
    )
    resultado = detectar_hitos_proximos_a_target(
        roadmap, hoy=date(2026, 5, 16), dias_anticipacion=3
    )
    assert len(resultado) == 1
    assert resultado[0]["dias_restantes"] == 0


def test_proximos_target_pasado_no_aparece():
    """Si el target ya pasó, no es 'próximo' — es 'atrasado'."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", fecha_cierre_target="2026-05-10", estado="pendiente"),
    )
    resultado = detectar_hitos_proximos_a_target(
        roadmap, hoy=date(2026, 5, 16), dias_anticipacion=3
    )
    assert resultado == []


# =============================================================================
# Tests: detectar_hitos_bloqueados
# =============================================================================

def test_bloqueados_estado_bloqueado_aparece():
    """Hito con estado=bloqueado se reporta."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", estado="bloqueado"),
    )
    resultado = detectar_hitos_bloqueados(roadmap)
    assert len(resultado) == 1
    assert resultado[0]["id"] == "H1.1"


def test_bloqueados_otros_estados_no_aparecen():
    """Estados distintos a bloqueado no se reportan."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", estado="pendiente"),
        _hito("H1.2", estado="en_curso"),
        _hito("H1.3", estado="cerrado"),
        _hito("H1.4", estado="jubilado"),
    )
    resultado = detectar_hitos_bloqueados(roadmap)
    assert resultado == []


# =============================================================================
# Tests: detectar_dependencias_rotas
# =============================================================================

def test_dependencias_en_curso_con_dependencia_cerrada_ok():
    """En curso + dependencia cerrada = sin problemas."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", estado="cerrado"),
        _hito("H1.2", estado="en_curso", depende_de=["H1.1"]),
    )
    resultado = detectar_dependencias_rotas(roadmap)
    assert resultado == []


def test_dependencias_en_curso_con_dependencia_pendiente_aparece():
    """En curso + dependencia pendiente = inconsistencia."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", estado="pendiente"),
        _hito("H1.2", estado="en_curso", depende_de=["H1.1"]),
    )
    resultado = detectar_dependencias_rotas(roadmap)
    assert len(resultado) == 1
    assert resultado[0]["id"] == "H1.2"
    assert "H1.1 (pendiente)" in resultado[0]["dependencias_no_terminadas"]


def test_dependencias_pendiente_sin_depender_no_aparece():
    """Pendiente sin dependencias = no es inconsistencia."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", estado="pendiente"),
        _hito("H1.2", estado="pendiente", depende_de=["H1.1"]),
    )
    resultado = detectar_dependencias_rotas(roadmap)
    assert resultado == []


def test_dependencias_jubilada_cuenta_como_terminada():
    """Una dependencia jubilada es válida (decisión consciente)."""
    roadmap = _roadmap_con_hitos(
        _hito("H1.1", estado="jubilado"),
        _hito("H1.2", estado="en_curso", depende_de=["H1.1"]),
    )
    resultado = detectar_dependencias_rotas(roadmap)
    assert resultado == []


# =============================================================================
# Tests: hay_algo_accionable
# =============================================================================

def test_accionable_listas_vacias_devuelve_false():
    assert hay_algo_accionable([], [], [], []) is False


def test_accionable_con_atrasados_devuelve_true():
    assert hay_algo_accionable([{"id": "H1.1"}], [], [], []) is True


def test_accionable_con_proximos_devuelve_true():
    assert hay_algo_accionable([], [{"id": "H1.1"}], [], []) is True


def test_accionable_con_bloqueados_devuelve_true():
    assert hay_algo_accionable([], [], [{"id": "H1.1"}], []) is True


def test_accionable_con_rotas_devuelve_true():
    assert hay_algo_accionable([], [], [], [{"id": "H1.1"}]) is True