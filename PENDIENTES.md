# Pendientes técnicos de Argo

Cosas identificadas a lo largo del desarrollo que no califican como hitos del roadmap, pero hay que tener registradas para no olvidarlas. Se priorizan según necesidad.

## Prioridad alta

### priceConvertionFactor de reMarkets es inconsistente

**Identificado:** 2026-05-25 durante H1.2.5.6.

**Problema:** El campo `priceConvertionFactor` del snapshot de reMarkets sandbox varía entre plazos del mismo instrumento (ej. `GD30D - CI` reporta 0.01 y `GD30D - 24hs` reporta 1.0). El factor de conversión de un instrumento debería ser único e independiente del plazo. El dato de sandbox no es confiable.

**Por qué importa:** El `priceConvertionFactor` es el multiplicador para obtener el precio real a partir del precio crudo. Si está mal, todo precio calculado a partir de él queda mal — y eso contamina collector, backtest y ejecución. Hoy no afecta nada (el parser lo guarda en `metadata_json` pero no lo usa), pero es una bomba de tiempo para cuando el collector empiece a traer precios.

**Solución propuesta:** NO usar `priceConvertionFactor` como factor de conversión sin antes validarlo contra el catálogo productivo de Matriz/BIND. Cuando se diseñe el collector de market data, verificar el comportamiento real del campo en producción.

**Prioridad:** alta. No afecta hoy, pero es riesgo silencioso para la capa de precios.

---

### Tests unitarios para modelos SQLAlchemy y poblar_instrumentos.py (deuda de H1.1.7)

**Identificado:** 2026-05-19 cierre de H1.1. **Verificado pendiente:** 2026-05-25 (no existen `tests/test_models.py` ni `tests/test_poblar_instrumentos.py`, sin historia en git).

**Problema:** H1.1 se cerró funcionalmente completo pero sin tests unitarios formales, como decisión deliberada por tiempo. La nota original decía "antes de avanzar a H1.2 debería estar cerrado" — no se cumplió: el proyecto avanzó hasta H1.2.5 sin saldar esta deuda.

**Por qué importa:** `models.py` y `poblar_instrumentos.py` son la base de toda la capa de datos. H1.2.5 agregó schema sobre esa base (tabla `instrumento_broker_mapping`, columna `segmento`). Cuanto más se avanza sin red de tests, más caro es ponerla.

**Solución propuesta:** Crear `tests/test_models.py` y `tests/test_poblar_instrumentos.py` con cobertura básica: instanciación de modelos, idempotencia del script de poblado, validación de las constraints compuestas (incluida `uq_broker_symbol_plazo` de `instrumento_broker_mapping`).

**Estado:** RESUELTO 2026-05-26 (commit 3433d31). Se crearon `tests/test_models.py` (10 tests: constraints, relaciones, cascades, integridad referencial) y `tests/test_poblar_instrumentos.py` (4 tests: idempotencia y caso homónimo cedear/subyacente). Se agregó infraestructura de testing: `pytest.ini` y `tests/conftest.py` con fixture de base SQLite en memoria y foreign keys activadas. Suite completa: 35 tests en verde.

---

## Prioridad media

### Modelo de estados del roadmap engine

**Identificado:** 2026-05-19 durante H1.1.

**Problema:** El detector `detectar_dependencias_rotas` considera un hito en "validación pasiva" (técnicamente implementado, esperando criterio cuantitativo temporal) como NO terminado, lo que genera falsas alarmas cuando hitos dependientes arrancan en paralelo.

**Solución propuesta:** Agregar estado `implementado` al schema (`roadmap.schema.json`) entre `en_curso` y `cerrado`. Actualizar `analyzers.py` para tratar `implementado` como estado terminado a efectos de dependencias.

**Archivos a tocar:**
- `roadmap.schema.json` (agregar valor al enum de estados)
- `src/roadmap/analyzers.py` (modificar `ESTADOS_TERMINADOS`)
- `tests/test_roadmap_analyzers.py` (agregar tests para el nuevo estado)
- `roadmap.json` (migrar hitos en validación pasiva, si los hubiera)

**Tiempo estimado:** 1-2 horas en una sesión dedicada.

**Prioridad:** media. El parche actual (reformular dependencias caso por caso) funciona pero no escala bien.

---

### secrets.json del VPS sin bloque primary_remarkets

**Prioridad:** media
**Identificado:** 2026-05-27 durante limpieza de Rava
**Descripción:** El secrets.json del VPS nunca tuvo el bloque
`primary_remarkets`. El de local sí lo tiene. Hoy ningún proceso del
VPS lo necesita (heartbeat y roadmap engine no tocan Primary), por eso
no rompió nada. Pero hay que sincronizarlo desde local ANTES de levantar
collectors de datos AR en el VPS (hito H1.x). Si un collector arranca en
el VPS sin ese bloque, va a fallar la autenticación contra Primary.
**Estado:** pendiente

---

### universe.json: actualización con catálogo productivo de Matriz

**Identificado:** 2026-05-25 durante H1.2.5.6.

**Problema:** La revisión del mapeo Primary destapó faltantes y desalineaciones en `universe.json` y en los tipos mapeables del parser. Las decisiones ya están tomadas, pero se ejecutan SOLO cuando haya acceso al catálogo productivo de Matriz/BIND — editar config adivinando tickers contra sandbox errático sería trabajo descartable (regla #10).

**Cambios decididos a ejecutar:**
- Agregar `boncap` y `boncer` a `TIPOS_MAPEABLES` de `generar_mapeo_primary.py` y tratarlos como instrumentos mapeables (son deuda AR de BYMA, operables).
- Agregar `DIA` (CEDEAR del SPDR Dow Jones ETF) al universo de cedears.
- NO agregar `BBD` (Banco Bradesco) — sin estrategia que lo requiera.
- Resolver desalineación de ticker de bopreal: el universo usa `BPOA7/BPOB7/BPOC7/BPOD7`, reMarkets expone `BPY26`. Verificar la nomenclatura correcta contra producción.
- Investigar `XD30` y `ROCIO` (símbolos MERV de reMarkets sin correspondencia clara en el universo).
- Revisar y corregir el campo `fuente` dentro de `universe.json`: arrastra `"rava"` hardcodeado en la categoría `cedears` y en el bloque `cauciones`, pese a que el proyecto ya migró la fuente de datos AR de Rava a Primary/Matriz. Verificar todos los `fuente` del archivo y corregir los obsoletos.

**Prioridad:** media. Depende de acceso productivo BIND.

---

### currency de reMarkets no es fuente confiable de moneda de liquidación

**Identificado:** 2026-05-25 durante H1.2.5.6.

**Problema:** El campo `currency` del snapshot de reMarkets sandbox no codifica la moneda de liquidación de forma consistente (ej. `AL30D` reporta `USD` pero `AL29D`/`AL35D`/`AL41D` reportan `ARS`, siendo todos bonos ley AR variante MEP). El mapeo de variantes D/C se hace por sufijo del ticker, no por `currency`.

**Solución propuesta:** Mantener el criterio "ticker manda, currency audita". Re-verificar el comportamiento de `currency` contra el catálogo productivo de Matriz: si en producción el campo es consistente, puede recuperarse como validación cruzada.

**Prioridad:** media. Informativo / a re-verificar. El parser ya maneja esto correctamente.

---

### Documentación del schema de DB (H1.1.8)

**Identificado:** 2026-05-19 cierre de H1.1.

**Problema:** Falta `docs/db_schema.md` con diagrama entidad-relación y justificaciones de diseño.

**Solución propuesta:** Documento markdown con: diagrama de las tablas, columnas con tipos y constraints, decisiones de diseño documentadas (UTC, volume_dolarizado redundante, UniqueConstraint compuestas). Incluir la tabla `instrumento_broker_mapping` agregada en H1.2.5.

**Prioridad:** media. No bloquea avance pero conviene tenerlo antes de Fase 2.

---

### Política de actualizaciones del VPS (Opción C confirmada)

**Identificado:** Sesión H0.2.

**Problema:** El VPS acumula updates pendientes. No urgente pero hay que tener una política.

**Solución propuesta (Opción C confirmada):** Configurar `unattended-upgrades` solo para parches de seguridad. Resto manual con `sudo apt update && sudo apt upgrade` periódico.

**Prioridad:** media. Aplicar cuando se inicie Fase 1 plena.

---

### Verificar PRAGMA foreign_keys en la conexión productiva (db.py)

**Identificado:** 2026-05-26 durante H1.1 (escritura de tests).

**Problema:** SQLite no valida foreign keys salvo que se active explícitamente `PRAGMA foreign_keys=ON` en cada conexión. Los tests lo activan (`tests/conftest.py`), pero no está verificado que `src/utils/db.py` lo haga para la base real. Si no lo hace, la base de producción NO está validando integridad referencial: aceptaría, por ejemplo, una fila en `instrumento_broker_mapping` apuntando a un `instrumento_id` inexistente.

**Por qué importa:** Es una deuda silenciosa. Hoy no rompe nada visible, pero significa que una de las garantías del schema (las foreign keys) podría no estar activa donde más importa. Una corrupción de datos por FK no validada es difícil de detectar después.

**Solución propuesta:** Revisar `src/utils/db.py`. Si no activa el PRAGMA, agregar un listener de conexión que lo haga (mismo patrón que `tests/conftest.py`). Verificar después con un chequeo puntual contra la base real.

**Prioridad:** media. No afecta hoy, pero es una garantía del schema que conviene confirmar activa.

---

## Prioridad baja

### Validación cruzada de plazo en el parser de mapeo

**Identificado:** 2026-05-25 durante H1.2.5.6.

**Problema:** `generar_mapeo_primary.py` deriva el plazo (`CI`/`24hs`) parseándolo del símbolo. El snapshot trae además el campo `settlType`, que en sandbox es perfectamente consistente: `1` para todos los `CI`, `2` para todos los `24hs`.

**Solución propuesta:** Agregar al parser una validación cruzada plazo-del-símbolo vs `settlType`, análoga a la que ya hace con `currency`. A diferencia de `currency`, `settlType` es confiable y sirve como auditoría real del plazo.

**Prioridad:** baja. Mejora; el parser ya funciona correctamente.

---

### Mensaje "listo para avanzar a H0.2" en verificar_instalacion.py

**Identificado:** Sesión H0.2.

**Problema:** El mensaje final del script de verificación dice "El proyecto está listo para avanzar a H0.2." independientemente del hito realmente pendiente.

**Solución propuesta:** Hacer que el script lea `roadmap.json` y muestre dinámicamente el próximo hito pendiente. Considerar también que `verificar_instalacion.py` chequee si el venv está activo.

**Prioridad:** baja. Cosmético, no afecta funcionalidad.

---

### Warning de pytest-asyncio

**Identificado:** Sesión anterior.

**Problema:** Aparece `PytestDeprecationWarning: asyncio_default_fixture_loop_scope is unset` al correr tests.

**Solución propuesta:** Agregar configuración en `pytest.ini` o `pyproject.toml`:
```ini
[tool:pytest]
asyncio_default_fixture_loop_scope = "function"
```

**Prioridad:** baja. No afecta tests. Arreglar cuando lleguemos a tests asíncronos en Fase 1-2.

---

### Logger: rotación falla en Windows con múltiples handlers

**Identificado:** Sesión 2026-05-24, recurrente.

**Problema:** `TimedRotatingFileHandler` falla la rotación con `PermissionError [WinError 32]` cuando hay múltiples handlers sobre el mismo `argo.log` en el mismo proceso. Windows no permite renombrar archivos abiertos; Linux sí (el VPS no lo sufre). No afecta lógica de negocio — el script corre y completa su trabajo — pero ensucia la consola en local y obliga a mover el `argo.log` viejo a mano.

**Solución propuesta:** Hacer el logger robusto a fallos de rotación (capturar el `PermissionError` en el handler, o revisar la configuración de handlers para que no haya más de uno sobre el mismo archivo). Toca módulo central `src/utils/logger.py`.

**Prioridad:** media-alta. Molesta seguido en local. Tratar como tarea dedicada con su test, no como parche al pasar.

---

### macro_indicadores: versionado de revisiones de indicadores

**Prioridad:** baja
**Identificado:** 2026-05-27 durante revisión del schema
**Descripción:** macro_indicadores guarda un solo valor por (indicador,
fecha). Cuando una fuente revisa un dato (ej. INDEC corrige el IPC: dato
preliminar vs definitivo), el valor nuevo pisa al viejo. Hoy es correcto:
las estrategias quieren el mejor dato disponible. Solo importaría para
backtests point-in-time estrictos (reconstruir qué sabía el sistema en una
fecha pasada). Si se necesita, es un cambio barato: columna opcional de
revisión o tabla de versiones. No bloquea nada actualmente.
**Estado:** pendiente

---

### Auditoría de arquitectura y diseño al cerrar Fase 1

**Prioridad:** baja
**Identificado:** 2026-05-27
**Descripción:** Al terminar la Fase 1 (infraestructura de datos / hitos
H1.x), hacer una revisión integral de estructura, diseño y arquitectura:
las seis capas del pipeline, el schema, el flujo de datos, las decisiones
de diseño, contra cómo lo haría un fondo cuantitativo serio. Se difiere a
ese momento a propósito: hoy las capas 3-6 son mayormente plan, no código
corriendo; auditar a fondo rinde cuando haya algo concreto que auditar
(schema cerrado, collectors corriendo, datos reales). Es una sesión
dedicada, no un paso dentro de otra.
**Estado:** pendiente

---

### Refactor datetime.utcnow() → datetime.now(timezone.utc)

**Identificado:** Sesión 2026-05-24.

**Problema:** `datetime.utcnow()` está deprecado en Python 3.12. Aparece en `src/utils/models.py` (defaults de `created_at`/`updated_at`) y en varios scripts.

**Solución propuesta:** Refactor a `datetime.now(timezone.utc)` en todos los puntos de uso. El parser `generar_mapeo_primary.py` (creado 2026-05-25) ya usa la forma correcta — no nace deprecado.

**Prioridad:** baja. Deprecation warning, no error. Hacer en una pasada cuando se toque `models.py`.