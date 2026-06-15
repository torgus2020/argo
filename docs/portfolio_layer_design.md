# Diseño de la Capa de Portfolio (H2.0) — Modelo de datos en papel

**Proyecto:** Argo · **Hito:** H2.0 (Portfolio Management Layer)
**Estado:** diseño en papel · **Fecha:** 2026-06-15
**Entregable:** #1 de H2.0 ("diseño entidad-relación documentado") · cumple el criterio "schema documentado y revisado antes de tocar código"

> **Qué es y qué no es este documento.** Es el diseño conceptual de la capa,
> NO la DDL. La migración Alembic se escribe *después* de validar este diseño
> contra lo que realmente devuelve BIND (ver §6, "Pendiente de BIND"). La nota
> del operador en H2.0 es explícita: diseñar el portfolio layer *después* de
> tener experiencia con datos reales de brokers. Este documento separa
> deliberadamente lo que ya tiene fundamento (cuentas, factor, subyacente) de
> lo que todavía depende de un `get_account_report` real (posiciones,
> operaciones, eventos de renta fija).

---

## 1. Por qué esta capa está separada de market data (H1.x)

Market data (`ticks_crudos`, `cotizaciones_*`) y portfolio son dos mundos con
fuente de verdad, frecuencia y auditoría distintas:

- **Market data:** fuente = el feed de Primary/BIND; alta frecuencia; el dato es
  inmutable una vez capturado; su integridad se mide por cobertura (sin huecos).
- **Portfolio:** fuente = el extracto/estado de cuenta del broker; baja
  frecuencia (diaria); el dato se *reconcilia* contra el broker y debe cuadrar al
  centavo; su integridad se mide por reconciliación cero-diferencia.

Mezclarlos sería un error de diseño. Esta capa vive en sus propias tablas y se
relaciona con `instrumentos` por FK, igual que market data, pero no comparte
ninguna otra tabla.

---

## 2. El concepto "cuenta" son cuatro ejes, no uno

La estructura real de las tres cuentas comitentes revela que "cuenta" colapsa
cuatro dimensiones que conviene mantener separadas en el modelo. Si se colapsan,
el modelo no puede expresar el caso real (dos comitentes bajo un login) ni
enforzar la frontera de ejecución.

| Eje | Qué es | Por qué importa |
|---|---|---|
| **Credencial de acceso (login)** | El set user/password/account con el que pyRofex autentica. | Dos comitentes pueden compartir uno (caso C1+C2). No es la unidad de segregación. |
| **Cuenta comitente** | El número de comitente en el broker. Unidad real de custodia y segregación de capital. | Es el grano de la regla dura 7 (tracking separado, no mezclar capital). |
| **Titular** | Persona humana dueña (Gus / su mujer). | Relevante para lo legal e impositivo (coordinación con el contador). |
| **Mandato** | Discrecional (Argo *observa*, read-only) vs. sistemático (Argo *ejecuta*). | Codifica la frontera dura del IPS §8 como dato estructural, no como convención. |

### Foto real de las tres cuentas

| Cuenta | Login | Comitente | Titular | Mandato |
|---|---|---|---|---|
| Discrecional-1 | L1 | C1 | Gus | discrecional (read-only) |
| Piloto Argo | L1 (compartido con D1) | C2 | Gus | sistemático (ejecutable) |
| Discrecional-2 | L2 | C3 | Mujer | discrecional (read-only) |

**Las tres son comitentes 100% segregados:** no comparten cuenta corriente ni
compensan saldos. El login compartido entre C1 y C2 es solo autenticación.

### El riesgo que el login compartido deja expuesto (importante)

La segregación de **capital** existe a nivel comitente y protege la regla dura 7.
Pero la frontera de **ejecución** (IPS §8: "Argo nunca opera las discrecionales")
NO está garantizada por las credenciales: el login L1 que Argo usa para operar la
piloto (C2) también abre la discrecional C1. El aislamiento depende de que Argo
*siempre* dirija la orden al comitente correcto.

Implicancia de diseño: la columna `mandato` (o `ejecutable`) es la semilla de una
**validación pre-orden** (a construir en Fase 3/4): antes de emitir cualquier
orden, el sistema verifica que el comitente destino tenga `mandato = ejecutable`,
con una allowlist que deja a C1 y C3 estructuralmente afuera. Hoy no se construye
esa validación, pero el modelo deja el dato listo para que se apoye en una columna
y no en un `if` suelto.

### Tabla propuesta: `cuentas`

Una fila por cuenta comitente. Diseño firme (no depende de BIND para su forma;
ver §6 para el único campo a confirmar).

| Columna | Tipo | Notas |
|---|---|---|
| `id` | Integer PK | autoincremental |
| `nombre_interno` | String | etiqueta legible ("Discrecional Gus", "Piloto Argo", "Discrecional Mujer") |
| `broker` | String | "bind" por ahora; deja lugar a multi-broker (Fase 5) |
| `login_ref` | String | identificador lógico del login (NO la credencial). Agrupa C1+C2 bajo "L1". La credencial real vive en `secrets.json`, nunca en la base. |
| `nro_comitente` | String | número de comitente en el broker. Unidad de segregación. |
| `titular` | String | "gus" / "mujer" |
| `mandato` | String | enum: "discrecional" \| "sistematico" |
| `ejecutable` | Boolean | derivable de `mandato`, pero explícito para la validación pre-orden. `True` solo para la piloto. |
| `moneda_base` | String | "USD" para las tres cuentas (decidido 2026-06-15). El P&L se almacena en USD; la conversión a pesos u otra moneda se hace solo en la capa de presentación. |
| `activo` | Boolean | retiro lógico, nunca borrado (mismo criterio que el resto del schema) |
| `metadata_json` | Text nullable | escape de flexibilidad |
| `created_at` / `updated_at` | DateTimeUTC | convención del proyecto |

> **Nota de seguridad:** `login_ref` es un *apodo lógico* ("L1"), no la credencial.
> Las credenciales siguen viviendo solo en `secrets.json` (gitignored). La base
> nunca guarda user/password/account reales.

---

## 3. La dimensión factor (clasificación económica curada)

El IPS define la asignación **por factor de riesgo económico**, no por la moneda
de liquidación ni por el tipo de instrumento. Dos sutilezas que el modelo debe
respetar:

1. **El factor NO es derivable del tipo ni del mercado del instrumento.** VIST es
   un CEDEAR (su tipo/mercado dirían "global") pero su factor económico real es
   Acciones AR/LatAm. El IPS lo dice explícito ("~13% nominal, ~10% real"). Por lo
   tanto el factor es un atributo **curado por el titular**, no calculado.

2. **El factor es una lente de gobernanza, no del catálogo de market data.** Por
   eso NO va sobre la tabla `instrumentos` (que es catálogo de mercado y debe
   quedar agnóstica a la cartera). Va en una tabla propia de clasificación, que se
   puede versionar y re-curar sin tocar el catálogo.

### Factores del IPS (taxonomía cerrada v1)

- `bonos_ar` — crédito soberano AR + BCRA (AL30, AE38, AL35, GD35, Bopreal…)
- `acciones_ar` — equity AR/LatAm, incluida la que cotiza como CEDEAR/cable pero
  es apuesta argentina (YPF/YPFD, VIST, PAMP, GGAL…)
- `global_ex_ar` — equity global real (Mag7 vía CEDEAR, índices amplios)
- `ballast` — lo que NO cae cuando cae Argentina (USD corto, Treasuries, oro)
- `efectivo` — colchón / pólvora seca

---

## 4. Subyacente económico (no contar doble)

Regla del IPS §8: la exposición económica real se suma **por subyacente**, no por
instrumento. VIST cable y VIST CEDEAR son **una** apuesta a Vista, no dos. AL30 y
AL30D son **un** crédito (el soberano AL30), liquidado en pesos o en dólar MEP.

Esto introduce una jerarquía de tres niveles sobre cada instrumento:

```
instrumento  →  subyacente económico  →  factor
   AL30                AL30                bonos_ar
   AL30D               AL30                bonos_ar
   VIST (cable)        VISTA               acciones_ar
   VIST (CEDEAR)       VISTA               acciones_ar
   AAPL (CEDEAR)       APPLE               global_ex_ar
```

- **Factor** = clase de riesgo (para la asignación estratégica y las bandas).
- **Subyacente** = la entidad económica concreta (para no contar doble una misma
  apuesta repartida en variantes).

Un subyacente pertenece a exactamente un factor. Son dos agrupaciones encadenadas,
no ortogonales.

### Tabla propuesta: `clasificacion_economica`

Una fila por instrumento clasificado. Curada por el titular. Diseño firme.

| Columna | Tipo | Notas |
|---|---|---|
| `id` | Integer PK | |
| `instrumento_id` | Integer FK → instrumentos.id | único (una clasificación vigente por instrumento) |
| `subyacente_economico` | String | "AL30", "VISTA", "APPLE"… agrupa variantes de la misma apuesta |
| `factor` | String | enum de §3 |
| `curado_por` | String | "gus" — deja registro de que es decisión humana |
| `nota` | Text nullable | justificación (ej.: "VIST = apuesta AR aunque liquide cable") |
| `created_at` / `updated_at` | DateTimeUTC | |

> Alternativa considerada y descartada: meter `factor`/`subyacente` en
> `instrumentos.metadata_json`. Descartada porque son dimensiones por las que se
> *agrupa y filtra* (la vista consolidada agrupa por factor); meterlas en un blob
> JSON obliga a parsear en cada consulta. Columnas reales en tabla propia es lo
> correcto.

---

## 5. La vista consolidada por factor (el objetivo del IPS §8)

Con `cuentas` + `clasificacion_economica` + (la futura tabla `posiciones`), la
vista consolidada sale de una sola agregación:

- **Por factor** (para el IPS / glide path): sumar valor de mercado de las
  posiciones agrupando por `factor`, sobre las tres cuentas, comparar contra el
  ancla objetivo y las bandas. Esto responde "¿estoy en ~55% AR o me corrí?".
- **Por subyacente** (para no contar doble): sumar agrupando por
  `subyacente_economico` antes de mirar concentración. Esto responde "¿cuánta
  Vista tengo en total, sumando cable + CEDEAR?".
- **Con tracking separado** (regla dura 7): la misma data, sin agrupar las
  cuentas, da el P&L y el drawdown por comitente. Vista unificada para los ojos
  del titular; segregación intacta en los datos.

Argo lee las tres cuentas **read-only**; ejecuta solo sobre la piloto. La vista es
de monitoreo: misma lente para las tres, ejecución sobre una.

---

## 6. Pendiente de validar contra BIND (la mitad que NO se diseña a ciegas)

Estas piezas se diseñan en detalle *después* de un `get_account_report` (o
equivalente pyRofex) contra las tres cuentas. Diseñarlas hoy a ciegas repetiría
el error que la nota de H2.0 advierte. Lo que hay que observar primero:

1. **Cómo BIND identifica un comitente bajo login compartido.** ¿pyRofex toma el
   `account` en `initialize` y eso fija el comitente? ¿Se puede consultar C1 y C2
   con el mismo login pasando distinto `account`? Esto confirma el campo
   `nro_comitente` y cómo el collector de portfolio selecciona la cuenta.
2. **Qué estructura tiene una posición en el extracto.** Campos disponibles:
   ¿nominales, precio promedio de costo, valor de mercado, moneda? Esto define la
   tabla `posiciones`.
3. **Qué da el broker para operaciones / movimientos.** ¿Hay histórico de trades
   por cuenta? ¿Cómo aparecen cupones y amortizaciones de bonos (eventos de renta
   fija) y dividendos? Esto define `operaciones`, `eventos_renta_fija`,
   `dividendos`.
4. **Cómo se ven las transferencias entre comitentes** (si las hay) y la
   valuación diaria que reporta el broker (para reconciliar).

### Tablas preliminares (formas a confirmar, NO definitivas)

- `posiciones` — (cuenta, instrumento, nominales, costo, valuación, fecha). Grano:
  snapshot diario por (cuenta, instrumento).
- `operaciones` — trades ejecutados, atribuidos por cuenta.
- `eventos_renta_fija` — cupones y amortizaciones cobrados.
- `dividendos` — dividendos cobrados.
- `transferencias` — movimientos de fondos entre cuentas (si aplica).
- `valuacion_diaria` — foto de valor por cuenta para P&L y drawdown en plata.

Todas con FK a `cuentas` y a `instrumentos`. El detalle fino espera al sondeo.

---

## 7. Orden de construcción propuesto (cuando se baje a DDL)

1. Sondeo a BIND: `get_account_report` contra las 3 cuentas (no necesita rueda
   abierta; se puede hacer en sesión corta). Observar §6.
2. Migración Alembic #1: `cuentas` + `clasificacion_economica` (las dos firmes).
   Poblar las 3 cuentas y la clasificación curada del universo discrecional.
3. Migración Alembic #2: `posiciones` + `valuacion_diaria`, ya con forma validada
   contra el extracto real. Carga inicial de posiciones.
4. Migración Alembic #3: `operaciones` + `eventos_renta_fija` + `dividendos` +
   `transferencias`.
5. Reconciliación cero-diferencia contra extracto, 7 días (criterio de cierre de
   H2.0).

---

## 8. Decisiones del titular

**Cerradas (2026-06-15):**

- **Moneda base de P&L = USD** para las tres cuentas. Se almacena en USD; la
  conversión a pesos u otra moneda se hace solo en la capa de presentación
  (mismo patrón que la convención UTC: una base de almacenamiento, conversión en
  el borde).
- **Granularidad de clasificación = por instrumento** (cada variante se clasifica
  explícitamente). Más robusto ante variantes nuevas que clasificar el subyacente
  y heredar. El `subyacente_economico` agrupa igual para no contar doble.

**Pendiente:**

- **Alcance de la carga inicial.** ¿Cargamos la foto de las tres cuentas de una, o
  arrancamos por la piloto (que es la que va a operar) y sumamos las discrecionales
  después? (A decidir cuando se baje a DDL, post-sondeo BIND).
