# Actualización de Instrucciones del Project — ARGO
### Sesión 2026-08-25 · 3 bloques de texto, 3 ubicaciones

> Cómo usar este archivo: cada bloque dice **DÓNDE** va y **QUÉ** hacer
> (reemplazar una sección existente, agregar texto, o insertar una nueva).
> El texto a pegar está en el bloque de código.

---

## BLOQUE A — Método de evaluación de estrategias

**DÓNDE:** insertá como **sección nueva**, inmediatamente **después** de
`## Cuánta historia hace falta depende del TIPO de estrategia`.

**POR QUÉ:** es lo más valioso que produjo la sesión. Argo pasó de "tengo un
catálogo de ocho ideas" a "tengo una máquina que las descarta en una tarde".
Esa máquina tiene un orden, tiene trampas conocidas, y las trampas se
repiten en toda estrategia futura — no son específicas del arbitraje. Sin
esto escrito, la próxima evaluación vuelve a caer en los mismos dos errores
que casi nos hacen construir algo que no existía.

```markdown
## Método de evaluación de estrategias — la máquina de descartar

Toda estrategia candidata se evalúa con datos ANTES de escribir una línea de
la estrategia. El orden no es negociable, porque cada paso puede matar la
idea y ahorrarse el siguiente:

| # | Pregunta | Herramienta |
|---|---|---|
| 1 | **¿Cuánto cuesta operar esto?** | `scripts/censo_spreads.py` |
| 2 | **¿Hay algo, en cualquier lado?** | `scripts/censo_desvios.py` |
| 3 | **¿Es real o es un artefacto de medición?** | `scripts/analizar_par.py` |

El paso 3 no es opcional y es el que más veces cambia la respuesta. Las dos
veces que un análisis mostró ganancias extraordinarias (2026-08-24/25), el
error estaba del lado de la medición, no en el mercado.

> **Si el análisis dice que hay plata gratis, la pregunta correcta no es
> "¿cómo la agarro?" sino "¿qué está roto en la medición?".**
> Cuando un resultado implica que todos los demás participantes son tontos,
> el que está equivocado es el que mide.

### Las dos trampas estructurales

**1. El sesgo del extremo.** Cuando una medición elige *el mejor de N*, el
resultado es positivo aunque no exista ningún edge: el extremo de N series
con ruido crece con N. Los extremos los ocupan siempre los instrumentos
ilíquidos, que son justo los que no se pueden operar.
*Antídoto:* medir **pares fijos, sin elegir**. Y desconfiar de todo barrido
que reduzca simultáneamente el ruido y la cantidad de candidatos — no separa
las dos causas.

**2. El rezago entre libros.** Dos instrumentos que siguen al mismo
subyacente repricean a destiempo. Ambos pueden estar "frescos" (recibieron
un tick hace segundos) y aun así reflejar información de momentos distintos.
Eso produce una diferencia de precio **que no se puede ejecutar**: para
cuando llegan las órdenes, el libro rezagado ya se acomodó.
*Antídoto:* el **diagnóstico de frescura** (abajo).

### El diagnóstico de frescura — la prueba que corta el nudo

Se mide la señal segmentada por antigüedad de las puntas (0-5 s, 5-15 s,
15-30 s, 30-60 s):

- Si la señal **no depende** de la antigüedad → es una diferencia de precio real.
- Si la señal **crece** con la antigüedad → es rezago, no edge. No hay negocio.

Validado 2026-08-24: en pares bono–bono el desvío no crecía con la
antigüedad (−1,0 bps); en pares con CEDEARs crecía (+2,1 bps con uno, +6,4
con dos). La contaminación escala con la cantidad de instrumentos que siguen
a un subyacente externo. **Este diagnóstico se aplica a cualquier señal, no
solo a arbitraje.**

### Higiene de medición — reglas duras

1. **Precios ejecutables, nunca el punto medio.** Para comprar se paga la
   punta vendedora; para vender se cobra la compradora. Midiendo así, el
   spread ya está DENTRO del número. Usar el punto medio infla el resultado
   exactamente en un spread — es el error que hace que cualquier backtest de
   arbitraje parezca brillante.
2. **Solo precios simultáneos.** Toda punta con más antigüedad que la
   frescura declarada se descarta. Comparar precios de momentos distintos
   inventa señales que nunca existieron.
3. **No cruzar plazos.** El dólar implícito de CI y el de 24hs difieren por
   la tasa entre fechas de liquidación. Compararlos mide **financiamiento**,
   no un desvío arbitrable.
4. **Excluir `subyacente_us` de todo análisis de instrumentos argentinos.**
   Tickers como MU, INTC o NVDA existen dos veces en `instrumentos` (CEDEAR
   argentino y subyacente de Polygon); sin excluirlos, un escaneo compara un
   instrumento contra sí mismo. Misma regla que usa el poblador de mapeos.
5. **Recortar los bordes de la rueda** (15 min por default). Las subastas de
   apertura y cierre tienen precios anchos que no representan al mercado y
   concentran los falsos positivos.
6. **No usar datos de tickers sin identificar** (hoy: los 6 símbolos `BA.C`).
7. **Reportar los descartes, nunca esconderlos.** Un filtro que no dice
   cuánto tiró es un filtro en el que no se puede confiar.
8. **Los filtros de cordura se calibran a la escala real de la basura.** Una
   banda de ±20 % no detecta nada en un mercado donde el dato malo aparece
   como desvíos del 3-4 %. Un filtro que nunca se activa es peor que no
   tenerlo: da falsa tranquilidad.

### Criterios de operabilidad — además del signo

Un desvío positivo no alcanza. Antes de considerar viable una señal:

- **Duración del episodio.** Una oportunidad que vive 20 segundos no es
  operable con un circuito de varias patas. Se agrupan los momentos
  consecutivos en episodios y se reporta cuánto duran.
- **Capacidad.** El tamaño de la punta más chica del circuito. Un desvío de
  40 bps sobre 12 nominales es una curiosidad, no un negocio.
- **Riesgo de pata.** Las operaciones de un circuito NO se ejecutan juntas.
  Si se completan 3 de 4, queda una posición abierta. Con ganancias
  esperadas de pocos puntos básicos, **una sola pata fallada se come muchas
  vueltas buenas**. El margen esperado tiene que cubrir ese riesgo, no solo
  los costos.
```

---

## BLOQUE B — Corrección al conteo de comisiones

**DÓNDE:** dentro de la sección `## Costos de operación reales`, agregá esto
al final del bloque de **Reglas de uso** (antes de la última viñeta sobre
derechos de mercado, o después — el orden no importa).

**POR QUÉ:** el beneficio de "se paga una sola vez" es más chico de lo que
parece, y confundirlo llevó a subestimar el costo de un circuito a la mitad.
Es un error de negocio, no de implementación, y por eso va en Instrucciones.

```markdown
- **El descuento de "una sola vez en el día" aplica a UN instrumento, no a una
  operación de varias patas.** Comprar AL30 y vender AL30D son dos
  instrumentos distintos: se paga comisión en cada uno. Un circuito que
  cierra el rulo completo (pesos → dólares → pesos, usando dos instrumentos)
  son **cuatro operaciones y cuatro comisiones**, no dos. Antes de usar
  cualquier umbral de rentabilidad, contar las operaciones del circuito
  completo, no las "vueltas".
```

---

## BLOQUE C — Callejones cerrados con evidencia

**DÓNDE:** insertá como **sección nueva**, inmediatamente **antes** de
`## Reglas duras del proyecto`.

**POR QUÉ:** sin esto escrito, la conversación se reabre sola cada tres meses
—igual que pasaba con el objetivo numérico— y se vuelve a gastar tiempo en
una idea ya medida. Documentar un "no" con su evidencia es tan valioso como
documentar un "sí".

```markdown
## Callejones cerrados con evidencia

Ideas ya evaluadas con datos propios. **No se reabren sin evidencia nueva**
(datos nuevos o un cambio estructural de costos), y reabrirlas exige rehacer
la medición, no repetir el argumento.

### Arbitraje de dólar implícito — CERRADO 2026-08-25

Afecta a `pairs_cedear` (en su versión local) y a `rulo_mep_ccl` (versión de
un solo broker). Medido sobre una rueda completa: 664.523 ticks, 37
instrumentos líquidos, 434 pares.

**Resultado: ningún par con desvío mediano positivo. Ninguno rentable.**
El par más limpio de la plaza (AL30 vs GD30, 1.537 momentos) da mediana
**−6,2 bps antes de descontar un solo peso de costo**: los dólares implícitos
están más juntos que el spread que habría que cruzar para tocarlos.

Tres conclusiones que valen más allá de esta estrategia:

1. **El mercado está calibrado al costo del arbitrajista marginal, que no
   somos nosotros.** Los desvíos mayores que el costo de cerrarlos ya fueron
   cerrados por alguien que llega antes y paga menos.
2. **Comisión cero no rescata una estrategia sin edge.** Con costos de un
   broker sin comisión aparecían pares "rentables", pero **los diez primeros
   tenían al menos un CEDEAR y ninguno era bono contra bono**: los únicos que
   parecían pagar eran los contaminados por rezago. Y aun tomándolos en
   serio, el neto era de 1 a 4 bps —centavos de dólar por vuelta— en
   episodios de 20 a 30 segundos.
3. **`pairs_cedear` en su formulación original (CEDEAR vs subyacente en
   Estados Unidos) no es ejecutable**, y eso se sabe sin datos: Argo no tiene
   forma de operar la pata de allá. Sin las dos patas no es arbitraje, es una
   posición direccional disfrazada.

**Lo que esto NO cierra:** `arb_bonos_ny_local` (AL vs GD) sigue intacta — no
es arbitraje instantáneo sino una apuesta a percepción de riesgo legal, y se
evalúa con otro método. Las estrategias de prima tampoco fueron tocadas.
```

---

## Resumen de la operación

| Bloque | Acción | Ubicación |
|---|---|---|
| **A** | Insertar sección nueva | después de `## Cuánta historia hace falta...` |
| **B** | Agregar viñeta | dentro de `## Costos de operación reales` |
| **C** | Insertar sección nueva | antes de `## Reglas duras del proyecto` |

**Deliberadamente AFUERA de Instrucciones, y por qué:**

- **Estado de la investigación de Cocos** (no hay API oficial; existe
  `pyCocos`, no oficial y sin cotizaciones en tiempo real): es un hecho del
  mundo que puede cambiar. Va al resumen de cierre y a `PENDIENTES.md`.
- **Los números concretos de spreads por instrumento** (AL30 a 1,2 bps, etc.):
  son mediciones de una rueda, no convenciones. Viven en `logs/` y en el CSV.
- **El detalle de los tres scripts** (parámetros, flags): vive en el docstring
  de cada uno, que es donde alguien lo va a buscar.

**Acciones de archivo derivadas (no son Instrucciones):**

- Marcar `pairs_cedear` y `rulo_mep_ccl` como cerradas en
  `config/strategies_catalog.json`, con fecha y referencia a este análisis.
- Revisar la Fase 2 del `roadmap.json`: tenía a `pairs_cedear` como primera
  estrategia y ese supuesto ya no se sostiene.
- Agregar a `PENDIENTES.md`: investigación de Cocos (API), y el diagnóstico
  pendiente de BBAR/TXAR/CRES en plazo CI.
