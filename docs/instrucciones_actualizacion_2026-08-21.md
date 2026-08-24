# Actualización de Instrucciones del Project — ARGO
### Sesión 2026-08-21 · 5 bloques de texto, 4 ubicaciones

> Cómo usar este archivo: cada bloque dice **DÓNDE** va y **QUÉ** hacer
> (reemplazar una sección existente o insertar una nueva). El texto a pegar
> está en el bloque de código. No hace falta tocar nada más.

---

## BLOQUE A — Gobernanza de cartera y cuentas

**DÓNDE:** reemplazá **por completo** la sección actual
`## Gobernanza de la cartera discrecional`.

**POR QUÉ:** la sección vieja decía "3 cuentas comitentes" sin números ni
mandatos, porque en junio no estaban cerrados. Ahora sí lo están, y el modelo
de dos logins es el hecho que hace que el control tenga que vivir en software.
Se le suma acá el principio de defaults (candidato 7 del punteo) porque es la
misma materia: no merece sección propia, merece ser la última línea de esta.

```markdown
## Gobernanza de la cartera y modelo de cuentas

Argo monitorea 3 cuentas comitentes en BIND, en vista consolidada read-only por
factor, con tracking separado por comitente:

| Comitente | Titular | Login | Mandato | Rol |
|---|---|---|---|---|
| **17169** | Gus | A | **ejecutable** | **Cuenta piloto de Argo** |
| 11647 | Gus | A | solo lectura | Discrecional de Gus |
| 11776 | Ana | B | solo lectura | Discrecional de Ana |

**El hecho central de seguridad: la cuenta ejecutable (17169) comparte login con
una de solo lectura (11647).** Mismo usuario, misma contraseña, mismo endpoint;
lo único que cambia entre operar la piloto y operar la cartera de Gus es el valor
del campo `account`. No hay confirmación del broker, no hay credencial distinta,
no hay fricción. Por eso:

> **La frontera de ejecución NO la dan las credenciales. La da el software.**
> **Allowlist de ejecución = `{17169}`. Nada más.**

El pre-order check (`mandato == ejecutable` **Y** comitente ∈ allowlist, validado
antes de toda orden) no es una buena práctica opcional: es el único control que
existe. Se construye en Fase 3/4, antes de la primera línea de código de ejecución.

Argo NUNCA ejecuta sobre 11647 ni 11776. La cartera discrecional la decide y opera
el titular; Argo observa, compara contra el IPS (`config/cartera_discrecional.json`)
y reporta.

**Modelado en el schema (H2.0): dos tablas, no una.**
- `logins` — nombre del bloque de `secrets.json`, **nunca** el secreto.
- `comitentes` — número, titular, `mandato`, FK a `logins`. Tres filas.

**Principio de defaults: el default apunta siempre a la cuenta segura de operar,
nunca a la intocable.** Todo módulo compartido de conexión hereda su `account` por
default; si ese default es una cuenta de solo lectura, cualquier código futuro que
reuse el módulo tal cual apunta a la cartera que no hay que tocar. El default de
`primary_produccion.account` es **17169**; los comitentes de solo lectura viven en
bloques separados y explícitos.
```

---

## BLOQUE B — Objetivo numérico

**DÓNDE:** insertá como **sección nueva**, inmediatamente **después** de
`## Métricas estándar del proyecto`.

**POR QUÉ:** las métricas dicen *con qué se mide*; esto dice *cuánto es el
objetivo*. Van pegadas. Y sin esto escrito, la conversación del objetivo se
vuelve a abrir sola cada tres meses.

```markdown
## Objetivo numérico — la escalera

El objetivo de Argo NO se expresa en plata por día. Una cuota diaria destruye
sistemas con esperanza positiva: fuerza trades sin edge en días tranquilos,
invita a "recuperar" lo perdido, y paga costos completos cada vez.

**El objetivo por etapa:**

| Nivel | Etapa | Objetivo |
|---|---|---|
| **0** | Hoy → cierre Fase 1 | **Datos, no plata.** Días limpios consecutivos, huecos = 0, reconciliación recibidos = encolados = persistidos. |
| **1** | Paper 90 días (Fases 2-3) | Sharpe ≥ 1,0 backtest / ≥ 0,8 paper · DD ≤ 15 % · profit factor ≥ 1,3 · slippage real/simulado ≤ 1,5x. **Plata objetivo: cero.** |
| **2** | Real, USD 4.000 (Fase 4) | **USD 40–60 por mes** (12–18 % anual) con **DD ≤ 20 %**. ≈ USD 2,50 por rueda. |
| **3** | Escalamiento (Fase 5) | El objetivo en dólares crece porque crece el capital, no porque crezca el % exigido. |

**Regla de encuadre: PnL visible todos los días, objetivo evaluado por mes.**

**Cota dura de plausibilidad, para no re-litigar esto:** con Sharpe 1,0 y
volatilidad anual del 15 %, el exceso esperado es 15 % → sobre USD 4.000 son
USD 600/año ≈ **USD 2,45 por rueda**. Exigir USD 20/día (≈ 240 % anual compuesto)
con Sharpe 1,0 requeriría volatilidad anual de ~122 %, y a −1σ eso es −122 %:
ruina, no drawdown del 20 %. **Pedir retornos de tres dígitos y respetar
`drawdown ≤ 20 %` es matemáticamente incompatible.** Ante cualquier objetivo
propuesto, hacer primero esta cuenta.
```

---

## BLOQUE C — Aportes periódicos de capital

**DÓNDE:** insertá como **sección nueva**, inmediatamente **después** del Bloque B.

**POR QUÉ:** es lo que hace que el Bloque B sea medible. Tiene una consecuencia
técnica dura (una tabla y dos métricas) que si no está escrita, se descubre tarde
y con métricas ya contaminadas.

```markdown
## Aportes periódicos de capital

Gus inyecta capital de forma periódica (mensual/anual) a medida que Argo demuestra
que funciona. Esto tiene dos consecuencias que no son negociables:

**1. Consecuencia técnica — sin esto, toda métrica de performance es basura desde
el primer aporte.** Con aportes, "saldo hoy vs saldo ayer" deja de medir
performance: un aporte **parece ganancia**, y en la dirección peligrosa (infla el
resultado justo mientras se está decidiendo si aportar más). Requerido en Capa 6 /
H2.0:
   - tabla **`movimientos_capital`** (fecha, comitente, monto, moneda, tipo
     aporte/retiro);
   - **TWR** (time-weighted return) para juzgar **la estrategia**;
   - **MWR / IRR** (money-weighted) para juzgar **cómo le fue a Gus**.

**2. Consecuencia de disciplina — resolución del choque con el principio 7
(walk-forward de capital):** **el aporte entra siempre** (el hábito de aportar no
se condiciona a nada, porque es lo que más pesa en el largo plazo), pero **su
despliegue a estrategias está condicionado** a los criterios cuantitativos. Si no
se cumplen, el aporte queda en tesorería (`cash_management.py`). *El capital nunca
deja de crecer; lo que se gana o se pierde es el derecho a desplegarlo.*
```

---

## BLOQUE D — Salud operativa: se mide en datos, no en estado de proceso

**DÓNDE:** insertá como **sección nueva**, inmediatamente **antes** de
`## Reglas duras del proyecto`.

**POR QUÉ:** es la lección más cara del proyecto hasta hoy — 58 días de captura
perdida con el service en `active (running)`. Es un principio de diseño general,
no una convención de Primary, por eso va como sección propia y no como viñeta.

```markdown
## Señales de salud operativa

**La salud de un proceso de datos se mide en filas escritas, no en estado de
systemd.** Ni `active (running)` ni `NRestarts` son señales de salud: describen al
proceso, no al flujo. Un collector puede estar vivo, sano, logueando, reconectando
prolijamente — y no persistir un solo dato. Aprendizaje del incidente 2026-06-24 →
2026-08-21: 58 días de captura caída con el service en verde.

**Señales que sí valen, en orden:**
1. **mtime de `data/argo.sqlite`** — si no cambió hoy y la rueda estuvo abierta,
   está roto, sin importar qué diga systemd.
2. **Tamaño de los logs diarios** — dos ruedas distintas no pueden producir logs
   byte-idénticos si hay datos. Logs de tamaño igual = mismo error en loop.
3. **Conteo de filas nuevas por rueda**, reconciliado recibidos = encolados =
   persistidos.

**Corolario obligatorio: todo proceso de captura debe tener una alerta de ausencia
de datos.** "Rueda abierta hace N minutos y 0 ticks persistidos" → Telegram. Una
alerta de que *algo falló* no alcanza; hace falta una alerta de que *nada pasó*.
Los monitores que solo reaccionan a errores no ven los silencios, y el silencio es
el modo de falla más caro.
```

---

## BLOQUE E — Suscripción por lotes + el universo envejece

**DÓNDE:** agregá estas **dos viñetas al final** de la sección existente
`## Convenciones de Primary/Matriz` (después de la viñeta que arranca con
"Fuente autoritativa del universo de suscripción a Primary").

**POR QUÉ:** son convenciones específicas de Primary, así que van donde viven
las otras. Y van juntas: son la causa y el mecanismo del mismo incidente —
el universo envejeció (causa), y el batch todo-o-nada convirtió ese
envejecimiento en una rueda perdida (mecanismo).

```markdown
* **Suscripción a market data: SIEMPRE por lotes, nunca en un solo batch.**
  Primary/BIND valida el mensaje de suscripción como una unidad: si **un** símbolo
  del batch no existe, **rechaza el mensaje completo** y devuelve el payload íntegro
  con un solo `description` señalando el primero que falló. Con los 366 símbolos en
  un mensaje único, un instrumento deslistado tumba la captura de la rueda entera.
  Suscribir en lotes (p. ej. de 20) hace que un símbolo malo mate su lote y no el
  día. **Fail-soft, no fail-all.** Corolario de lectura de logs: el mensaje de error
  nombra **un** símbolo, pero eso NO significa que sea el único inválido — es el
  primero que el servidor encontró. Validar el universo entero, no solo el símbolo
  que aparece en el error.

* **El universo envejece y hay que revalidarlo.** El catálogo de BYMA/BIND deriva:
  instrumentos se deslistan, cambian de variante, dejan de existir. Un
  `instrumento_broker_mapping` que era correcto al generarse (2026-05-29, con
  metadata que solo pudo salir del catálogo real) queda inválido meses después sin
  que nada avise. La columna `fecha_validacion` existe exactamente para esto y no
  se puede dejar en NULL. **Regla: revalidación periódica (semanal) del universo
  activo contra el catálogo vivo del broker**, vía
  `scripts/validar_universo_vs_catalogo.py` — read-only por default; con `--aplicar`
  marca `activo=0` a los faltantes (**nunca DELETE**) y sella `fecha_validacion` en
  los válidos.
```

---

## Resumen de la operación

| Bloque | Acción | Ubicación |
|---|---|---|
| **A** | Reemplazar | `## Gobernanza de la cartera discrecional` (renombrada) |
| **B** | Insertar | después de `## Métricas estándar del proyecto` |
| **C** | Insertar | después del Bloque B |
| **D** | Insertar | antes de `## Reglas duras del proyecto` |
| **E** | Agregar 2 viñetas | al final de `## Convenciones de Primary/Matriz` |

**Del punteo original de 12 candidatos, esto cubre los puntos 1 a 7.**

**Lo que queda AFUERA a propósito, y por qué:**

- **8, 9, 10** (semántica de valuación BIND, `priceConversionFactor`, sleeve
  externo): son hallazgos de sondeo que pertenecen al **documento de diseño de
  H2.0**, no a las Instrucciones. Entran a Instrucciones cuando la DDL se cierre y
  se conviertan en convención del schema, no antes.
- **11** (patrón "Claude escribe script → Gus corre → Claude lee `logs\`"): es modo
  de trabajo de sesión, no del proyecto. Vive bien en el resumen de cierre. Se
  promueve a Instrucciones si sobrevive unas sesiones más.
- **12** (Joaquín): todavía no hay nada operativo — ni repo compartido, ni duelo
  corriendo. Cuando el duelo arranque y tenga estructura de datos, entra.
```
