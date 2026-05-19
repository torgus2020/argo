# Pendientes técnicos de Argo

Cosas identificadas a lo largo del desarrollo que no califican como hitos del roadmap, pero hay que tener registradas para no olvidarlas. Se priorizan según necesidad.

## Mejoras de bajo impacto pero pendientes

### Modelo de estados del roadmap engine

**Identificado:** 2026-05-19 durante H1.1.

**Problema:** El detector `detectar_dependencias_rotas` considera un hito en "validación pasiva" (técnicamente implementado, esperando criterio cuantitativo temporal) como NO terminado, lo que genera falsas alarmas cuando hitos dependientes arrancan en paralelo.

**Solución propuesta:** Agregar estado `implementado` al schema (`roadmap.schema.json`) entre `en_curso` y `cerrado`. Actualizar `analyzers.py` para tratar `implementado` como estado terminado a efectos de dependencias. Migrar hitos actuales en validación pasiva (H0.3, H0.4, H1.1) al nuevo estado.

**Archivos a tocar:**
- `roadmap.schema.json` (agregar valor al enum de estados)
- `src/roadmap/analyzers.py` (modificar `ESTADOS_TERMINADOS`)
- `tests/test_roadmap_analyzers.py` (agregar tests para el nuevo estado)
- `roadmap.json` (migrar hitos en validación pasiva)

**Tiempo estimado:** 1-2 horas en una sesión dedicada.

**Prioridad:** media. El parche actual (reformular dependencias caso por caso) funciona pero no escala bien — vamos a tener que hacerlo cada vez que arranquemos un hito en paralelo a otro en validación.

---

### Mensaje "listo para avanzar a H0.2" en verificar_instalacion.py

**Identificado:** Sesión anterior.

**Problema:** El mensaje final del script de verificación dice "El proyecto está listo para avanzar a H0.2." independientemente de qué hito esté pendiente realmente.

**Solución propuesta:** Hacer que el script lea el roadmap.json y muestre dinámicamente el próximo hito pendiente.

**Prioridad:** baja. Es cosmético, no afecta funcionalidad.

---

### Warning de pytest-asyncio

**Identificado:** Sesión anterior.

**Problema:** Aparece warning `PytestDeprecationWarning: asyncio_default_fixture_loop_scope is unset` al correr tests.

**Solución propuesta:** Agregar configuración en `pytest.ini` o `pyproject.toml`:
```ini
[tool:pytest]
asyncio_default_fixture_loop_scope = "function"
```

**Prioridad:** baja. No afecta tests. Arreglar cuando lleguemos a tests asíncronos en Fase 1-2.

---

### Tests unitarios para modelos SQLAlchemy y poblar_instrumentos.py (H1.1.7)

**Identificado:** 2026-05-19 cierre de H1.1.

**Problema:** H1.1 está funcionalmente completo pero faltan tests unitarios formales. Decisión deliberada de cerrar la sesión sin ellos por tiempo.

**Solución propuesta:** En próxima sesión, crear `tests/test_models.py` y `tests/test_poblar_instrumentos.py` con cobertura básica: instanciación de modelos, idempotencia del script, validación de constraint compuesta.

**Prioridad:** alta. Antes de avanzar a H1.2 (collector Rava) debería estar cerrado.

---

### Documentación del schema de DB (H1.1.8)

**Identificado:** 2026-05-19 cierre de H1.1.

**Problema:** Falta `docs/db_schema.md` con diagrama entidad-relación y justificaciones de diseño.

**Solución propuesta:** Documento markdown con: diagrama de las 5 tablas, columnas con tipos y constraints, decisiones de diseño documentadas (UTC, volume_dolarizado redundante, UniqueConstraint compuesta).

**Prioridad:** media. No bloquea avance pero conviene tenerlo antes de Fase 2.

---

### Política de actualizaciones del VPS (Opción C confirmada)

**Identificado:** Sesión H0.2.

**Problema:** El VPS tiene "12 updates can be applied immediately" desde hace varios días. No urgente pero hay que tener una política.

**Solución propuesta (Opción C confirmada):** Configurar `unattended-upgrades` solo para parches de seguridad. Resto manual con `sudo apt update && sudo apt upgrade` periódico.

**Prioridad:** media. Aplicar cuando se inicie Fase 1 plena.