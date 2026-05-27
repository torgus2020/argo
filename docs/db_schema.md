# Schema de la base de datos — Argo

**Archivo de base:** `data/argo.sqlite`
**Motor:** SQLite, accedido vía SQLAlchemy 2.0
**Migraciones:** Alembic (`migrations/`)
**Fuente de verdad del schema:** `src/utils/models.py`
**Creado:** 2026-05-27

Este documento describe la estructura de la base de datos de Argo: las seis
tablas, sus columnas, los constraints e índices, y —sobre todo— el *porqué*
de las decisiones de diseño. La definición ejecutable vive en `models.py`;
este documento existe para explicar el razonamiento, que el código no siempre
puede expresar.

---

## Visión general

La base tiene seis tablas, agrupables en tres bloques funcionales:

1. **Catálogo de instrumentos** — `instrumentos` e `instrumento_broker_mapping`.
   Definen *qué* se monitorea y *cómo se llama* ese instrumento en cada broker.
2. **Datos de mercado** — `cotizaciones_1min` y `cotizaciones_diarias`.
   Las series de precios, en dos granularidades.
3. **Datos macro y auditoría** — `macro_indicadores` y `log_collectors`.
   Contexto macroeconómico y registro de cada corrida de los collectors.

El catálogo es el centro: las tablas de cotizaciones cuelgan de `instrumentos`
vía foreign key, y `instrumento_broker_mapping` traduce cada instrumento a los
símbolos concretos que usa cada broker.

---

## Decisiones de diseño transversales

Cuatro decisiones atraviesan el schema y conviene entenderlas antes de leer
tabla por tabla.

### 1. Timestamps en UTC

Todas las columnas de fecha-hora (`timestamp`, `created_at`, `updated_at`)
guardan UTC, no hora de Buenos Aires. La conversión a hora local es
responsabilidad de la capa de presentación (dashboards, reportes), nunca de la
capa de datos.

El motivo: la hora local argentina es ambigua como clave de almacenamiento
—cambios de huso, comparación con datos de mercados externos como Polygon—
mientras que UTC es un punto de referencia único y sin ambigüedad. Guardar en
UTC y convertir al mostrar es la práctica estándar y evita una clase entera de
bugs de desfasaje horario.

### 2. `volume_dolarizado` es redundante a propósito

Las tablas de cotizaciones (`cotizaciones_1min` y `cotizaciones_diarias`)
tienen una columna `volume_dolarizado` que podría calcularse al vuelo en cada
consulta. Se almacena precalculada, al momento de insertar la fila.

Es una desnormalización deliberada: un trade-off de espacio en disco a cambio
de velocidad de consulta. Las estrategias y el backtester van a filtrar y
ordenar por volumen dolarizado constantemente; pagar ese cálculo una vez al
insertar, en lugar de en cada query sobre millones de filas, vale la pena. La
columna es redundante respecto de `volume` y el precio, pero la redundancia
está elegida, no es un descuido.

### 3. UniqueConstraints compuestos

Dos tablas usan claves únicas de varias columnas, y en ambos casos la elección
de *qué columnas* entran al constraint es una decisión de diseño, no un detalle.

**`instrumentos`: unicidad sobre `(ticker, mercado)`, no sobre `ticker` solo.**
Un mismo ticker puede existir legítimamente en dos mercados distintos. El caso
testigo es un CEDEAR y su subyacente: `AAPL` como CEDEAR en BYMA y `AAPL` como
acción subyacente en NASDAQ son dos instrumentos distintos que comparten
ticker. Si la unicidad fuera solo `ticker`, el segundo no podría insertarse.
La clave correcta es el par `(ticker, mercado)`, y todo el código de upsert
(ver `scripts/poblar_instrumentos.py`) busca por esa clave compuesta completa.

**`instrumento_broker_mapping`: unicidad sobre `(broker, symbol_externo, plazo)`,
y deliberadamente *sin* `instrumento_id`.** El razonamiento: un símbolo externo
debe ser único globalmente dentro de un broker para un plazo dado. Un mismo
`symbol_externo` no puede mapear a dos plazos distintos en el mismo broker, y
tampoco puede pertenecer a dos instrumentos Argo distintos. Incluir
`instrumento_id` en el constraint debilitaría la garantía: permitiría que el
mismo símbolo de broker se cargara dos veces apuntando a instrumentos
distintos. Dejándolo afuera, la base misma impide esa inconsistencia.

### 4. La capa de mapeo instrumento ↔ broker

`instrumento_broker_mapping` existe como tabla separada —en lugar de columnas
de símbolo dentro de `instrumentos`— porque la relación es uno-a-muchos en dos
dimensiones a la vez:

- **Multi-broker:** un instrumento Argo (ej. `AL30`) tiene símbolos distintos
  en Primary, IOL, Cocos, Polygon.
- **Multi-variante dentro de un broker:** dentro de Primary, `AL30` se negocia
  en pesos (`AL30`), en USD MEP (`AL30D`) y en USD CCL (`AL30C`), y cada
  variante en distintos plazos (CI, 24hs).

Meter eso en columnas de `instrumentos` sería imposible sin multiplicar filas o
columnas sin control. Una tabla de mapeo dedicada lo resuelve limpio: una fila
por cada combinación concreta de (broker, símbolo, moneda, plazo).

---

## Tabla 1 — `instrumentos`

Catálogo maestro. Una fila por cada instrumento del `universe.json`.

El `id` interno autoincremental es la referencia estable de todo el sistema; el
`ticker` no lo es, porque puede cambiar por splits o renombramientos. Todas las
foreign keys del schema apuntan a `instrumentos.id`, nunca al ticker.

| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| `id` | Integer | No | PK, autoincremental |
| `ticker` | String(20) | No | Indexado. No es único por sí solo |
| `tipo` | String(30) | No | Acción, bono, CEDEAR, ON, etc. |
| `nombre` | String(200) | No | Nombre descriptivo |
| `mercado` | String(30) | No | BYMA, NASDAQ, etc. |
| `moneda` | String(20) | No | Moneda de denominación |
| `fuente` | String(30) | No | Origen del dato (primary, polygon, etc.) |
| `activo` | Boolean | No | Default `True`. `False` = fuera del universo activo |
| `metadata_json` | Text | Sí | JSON con datos específicos del tipo |
| `created_at` | DateTime | No | UTC, default al insertar |
| `updated_at` | DateTime | No | UTC, se actualiza en cada modificación |

**Constraint único:** `uq_instrumento_ticker_mercado` sobre `(ticker, mercado)`.
**Índice:** sobre `ticker`.

El campo `metadata_json` guarda información que depende del tipo de instrumento
—vencimiento de un bono, ratio de conversión de un CEDEAR, sector de una
acción— como JSON serializado. Esto da flexibilidad sin necesidad de un schema
rígido distinto por cada tipo de instrumento.

---

## Tabla 2 — `instrumento_broker_mapping`

Traduce cada instrumento del universo Argo a los símbolos concretos que usa
cada broker. Ver "La capa de mapeo" más arriba para el porqué de la tabla.

| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| `id` | Integer | No | PK, autoincremental |
| `instrumento_id` | Integer | No | FK a `instrumentos.id`. Indexado |
| `broker` | String(20) | No | primary, iol, cocos, polygon. Indexado |
| `symbol_externo` | String(80) | No | Símbolo tal como lo usa el broker |
| `segmento` | String(20) | No | Segmento de mercado del símbolo |
| `moneda_liquidacion` | String(15) | No | ARS, USD_MEP, USD_CCL |
| `plazo` | String(10) | No | CI (T+0), 24hs (T+1), 48hs |
| `es_default` | Boolean | No | Default `False`. Símbolo preferido del instrumento |
| `activo` | Boolean | No | Default `True`. `False` = mapeo obsoleto, no borrado |
| `metadata_json` | Text | Sí | JSON con datos adicionales del mapeo |
| `fecha_validacion` | DateTime | Sí | UTC. Última validación del símbolo contra el broker |
| `created_at` | DateTime | No | UTC, default al insertar |
| `updated_at` | DateTime | No | UTC, se actualiza en cada modificación |

**Constraint único:** `uq_broker_symbol_plazo` sobre `(broker, symbol_externo, plazo)`.
**Índices:** sobre `instrumento_id`; sobre `broker`; e índice compuesto
`ix_inst_broker_default` sobre `(instrumento_id, broker, es_default)`.

Ejemplo de las filas para `AL30` vía Primary:

| symbol_externo | moneda_liquidacion | plazo |
|---|---|---|
| `MERV - XMEV - AL30 - CI` | ARS | CI |
| `MERV - XMEV - AL30 - 24hs` | ARS | 24hs |
| `MERV - XMEV - AL30D - CI` | USD_MEP | CI |
| `MERV - XMEV - AL30C - CI` | USD_CCL | CI |

Notas sobre tres campos con lógica propia:

- **`segmento`** registra el segmento de mercado del símbolo (MERV = BYMA,
  donde opera Argo; TIVA = MAE mayorista; DUAL = futuros). Es dato estructural
  de ejecución: una estrategia necesita saber en qué segmento se negocia el
  instrumento. Argo opera exclusivamente en MERV. Esta columna se agregó en la
  migración `120cc791cc97`, posterior a la creación de la tabla.

- **`es_default`** define qué símbolo responde cuando una estrategia pide "el
  precio de AL30" sin especificar moneda ni plazo. El default inicial es plazo
  CI, moneda ARS. Se ajusta con datos reales una vez que haya 2-3 días de
  market data (registrado en `PENDIENTES.md`).

- **`activo = False`** marca mapeos obsoletos sin borrarlos. Un símbolo que ya
  no está en producción debe seguir siendo resoluble, porque los backtests
  históricos necesitan poder traducir símbolos que existían en el pasado.

---

## Tabla 3 — `cotizaciones_1min`

Datos intradía con granularidad de un minuto. Es la tabla más grande del
sistema: del orden de 10 millones de filas por año con el universo actual.

| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| `id` | Integer | No | PK, autoincremental |
| `instrumento_id` | Integer | No | FK a `instrumentos.id` |
| `timestamp` | DateTime | No | UTC |
| `open` | Float | No | Precio de apertura del minuto |
| `high` | Float | No | Máximo del minuto |
| `low` | Float | No | Mínimo del minuto |
| `close` | Float | No | Precio de cierre del minuto |
| `volume` | Integer | No | Default 0. Volumen en unidades |
| `volume_dolarizado` | Float | Sí | Precalculado. Ver decisión de diseño 2 |
| `cantidad_operaciones` | Integer | Sí | Número de operaciones en el minuto |
| `fuente` | String(30) | No | Origen del dato |
| `created_at` | DateTime | No | UTC, default al insertar |

**Constraint único:** `uq_cot1min_instr_ts` sobre `(instrumento_id, timestamp)`.
**Índices:** `ix_cot1min_instr_ts_desc` sobre `(instrumento_id, timestamp)`;
`ix_cot1min_timestamp` sobre `timestamp`.

El constraint único sobre `(instrumento_id, timestamp)` garantiza que no haya
dos filas para el mismo instrumento en el mismo minuto. Es una defensa explícita
contra bugs de re-procesamiento: si un collector corre dos veces sobre el mismo
período, la base rechaza los duplicados en lugar de aceptarlos en silencio.

---

## Tabla 4 — `cotizaciones_diarias`

Datos OHLCV diarios. Bastante más chica que `cotizaciones_1min`: del orden de
25 mil filas por año.

| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| `id` | Integer | No | PK, autoincremental |
| `instrumento_id` | Integer | No | FK a `instrumentos.id` |
| `fecha` | Date | No | Fecha del dato (sin componente horario) |
| `open` | Float | No | Precio de apertura |
| `high` | Float | No | Máximo del día |
| `low` | Float | No | Mínimo del día |
| `close` | Float | No | Precio de cierre |
| `volume` | Integer | No | Default 0 |
| `volume_dolarizado` | Float | Sí | Precalculado. Ver decisión de diseño 2 |
| `cantidad_operaciones` | Integer | Sí | Número de operaciones del día |
| `fuente` | String(30) | No | Origen del dato |
| `created_at` | DateTime | No | UTC, default al insertar |

**Constraint único:** `uq_cotdiaria_instr_fecha` sobre `(instrumento_id, fecha)`.
**Índices:** `ix_cotdiaria_instr_fecha_desc` sobre `(instrumento_id, fecha)`;
`ix_cotdiaria_fecha` sobre `fecha`.

A diferencia de `cotizaciones_1min`, la clave temporal es `fecha` (tipo `Date`,
sin hora). Los datos diarios pueden venir directo del histórico de Primary
—que tiene granularidad diaria con años de profundidad— o agregarse desde
`cotizaciones_1min`. El hito H1.5 contempla un job que verifica la consistencia
entre ambas granularidades.

---

## Tabla 5 — `macro_indicadores`

Indicadores macroeconómicos: reservas del BCRA, IPC del INDEC, tasa de política
monetaria, tipos de cambio, etc. No están atados a ningún instrumento, por eso
no tienen foreign key a `instrumentos`.

| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| `id` | Integer | No | PK, autoincremental |
| `indicador` | String(50) | No | Nombre del indicador. Indexado |
| `fecha` | Date | No | Fecha del dato |
| `valor` | Float | No | Valor del indicador |
| `unidad` | String(30) | No | Unidad de medida |
| `fuente` | String(30) | No | BCRA, INDEC, etc. |
| `metadata_json` | Text | Sí | JSON con detalles del indicador |
| `created_at` | DateTime | No | UTC, default al insertar |

**Constraint único:** `uq_macro_indicador_fecha` sobre `(indicador, fecha)`.
**Índices:** indexado sobre `indicador`; `ix_macro_indicador_fecha_desc` sobre
`(indicador, fecha)`.

Una fila por par `(indicador, fecha)`. La granularidad varía según el indicador:
diaria para reservas y tipos de cambio, mensual para IPC o EMAE. El campo
`metadata_json` permite especificar detalles —sub-componente, serie exacta de
la fuente— sin necesidad de columnas dedicadas.

---

## Tabla 6 — `log_collectors`

Auditoría de las corridas de los collectors. Central a la filosofía del proyecto
de "loggear TODO, especialmente lo que sale mal".

| Columna | Tipo | Nulo | Notas |
|---|---|---|---|
| `id` | Integer | No | PK, autoincremental |
| `collector` | String(30) | No | Nombre del collector. Indexado |
| `timestamp_inicio` | DateTime | No | UTC. Inicio de la corrida |
| `timestamp_fin` | DateTime | Sí | UTC. Fin de la corrida (nulo mientras corre) |
| `instrumentos_procesados` | Integer | No | Default 0 |
| `instrumentos_exitosos` | Integer | No | Default 0 |
| `instrumentos_fallidos` | Integer | No | Default 0 |
| `filas_insertadas` | Integer | No | Default 0 |
| `estado` | String(20) | No | `en_curso`, y estado final al terminar |
| `errores_json` | Text | Sí | JSON con el detalle de los errores |
| `metadata_json` | Text | Sí | JSON con datos adicionales de la corrida |

**Índices:** `ix_logcol_collector_inicio_desc` sobre `(collector, timestamp_inicio)`;
`ix_logcol_estado` sobre `estado`.

Esta tabla no tiene constraint único: cada corrida es un evento distinto y se
registra como fila nueva. Permite responder preguntas operativas como "¿tuvimos
datos completos en este período?" o "¿cuántas veces falló Primary en la última
semana?".

El estado `en_curso` es transitorio: cuando una corrida arranca, se inserta la
fila con `estado = en_curso` y `timestamp_fin` nulo; al terminar, se actualiza
la misma fila con el estado final y la hora de cierre. Una fila que quede en
`en_curso` con `timestamp_inicio` viejo es señal de un collector que murió sin
cerrar su registro.

---

## Historial de migraciones

| Revisión | Descripción |
|---|---|
| `9c62f61fa821` | Schema inicial: 5 tablas, con UniqueConstraint compuesto `(ticker, mercado)` en `instrumentos` |
| `056f2b857204` | H1.2.5: tabla `instrumento_broker_mapping` para el mapeo Argo ↔ brokers |
| `120cc791cc97` | H1.2.5: columna `segmento` agregada a `instrumento_broker_mapping` |

Revisión actual de la base (`head`): `120cc791cc97`.

La tabla `instrumento_broker_mapping` no formó parte del schema inicial: se
incorporó en la segunda migración, y la columna `segmento` en la tercera. Por
eso el schema inicial creó cinco tablas y el total actual es seis.