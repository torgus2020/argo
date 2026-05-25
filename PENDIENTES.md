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

**Prioridad:** alta. Deuda técnica arrastrada. Saldar pronto, antes de que la capa de datos crezca más.

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

### universe.json: actualización con catálogo productivo de Matriz

**Identificado:** 2026-05-25 durante H1.2.5.6.

**Problema:** La revisión del mapeo Primary destapó faltantes y desalineaciones en `universe.json` y en los tipos mapeables del parser. Las decisiones ya están tomadas, pero se ejecutan SOLO cuando haya acceso al catálogo productivo de Matriz/BIND — editar config adivinando tickers contra sandbox errático sería trabajo descartable (regla #10).

**Cambios decididos a ejecutar:**
- Agrega