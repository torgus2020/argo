# Investment Policy Statement (IPS) — Cartera Discrecional

**Proyecto:** Argo · capa de soporte de decisión para capital discrecional
**Titular:** Gus
**Versión:** v1 · **Fecha:** 11/06/2026
**Herramienta de apoyo:** Claude (Argo). El titular es el responsable y la autoridad de decisión; este documento es un marco, no asesoramiento financiero matriculado.

---

## 1. Propósito y alcance

Este documento define la política de inversión de la **cartera discrecional** del titular: capital de mediano/largo plazo invertido a criterio, alojado en **dos** de las tres cuentas comitentes (las dos discrecionales). Convierte una práctica histórica basada en fundamentals "a ojo" en un proceso medible: objetivos, restricciones, asignación estratégica y reglas de ejecución.

**No cubre** el piloto sistemático de Argo (~USD 5K, cuenta comitente exclusiva, estrategias backtesteadas con gates cuantitativos). Esa es otra disciplina (sistemática), con su propio mandato.

**Frontera dura entre ambas:** Argo *observa y reporta* esta cartera (read-only, vista consolidada por factor); **nunca la ejecuta**. El capital y el tracking quedan separados por cuenta comitente (regla dura 7 del proyecto). Las decisiones sobre esta cartera son discrecionales del titular, informadas por datos pero **no sistematizadas**: no son una estrategia Argo.

---

## 2. Objetivos

**Objetivo de retorno.** Crecer con piso de protección, con sesgo de crecimiento *dentro* del techo de riesgo. No se fija un número de retorno duro a propósito: anclar una meta de retorno presiona a forzar riesgo para alcanzarla. El objetivo es real, no nominal (el titular razona en USD).

**Objetivo de riesgo (vinculante).** Drawdown máximo tolerable: **-20%** pico-a-valle sobre esta cartera. Esta es la restricción que manda sobre todo lo demás. Se sostiene con **asignación + gestión activa** (bandas de rebalanceo y gatillos por señales), no con la asignación sola. Honestidad explícita: una asignación con ~55% AR mantiene el -20% en drawdowns normales y stress leve, pero en un stress argentino *severo* depende de que los recortes tácticos se activen a tiempo. El -20% pasivo (sin gestión activa) requeriría bajar AR hacia ~40%.

---

## 3. Restricciones

| Dimensión | Definición |
|---|---|
| **Horizonte** | Largo plazo, con una fecha de riesgo marcada: elección legislativa de octubre 2027. |
| **Liquidez** | Sin retiros forzados. Mecanismo histórico: caución sobre bonos de aforo alto, repagada con cupones/amortizaciones. La caución es apalancamiento (amplifica drawdown; el aforo se ensancha en el stress), por lo que se usa **solo como herramienta táctica/transitoria**, nunca como posición estructural permanente. |
| **Impuestos** | Exento de Impuesto a los Débitos y Créditos. CCL habilitado. Toma de ganancias y cosecha de pérdidas se coordinan con el contador (experiencia en mercado de capitales). |
| **Legal / operativo** | Persona humana, operatoria estándar. Tres cuentas comitentes: dos discrecionales (esta cartera) + una exclusiva Argo. La separación es física, no solo lógica. |
| **Circunstancias particulares** | Concentración de factor único (~90% Argentina al inicio); dentro de AR, sesgo a energía y bancos; dentro de lo global, concentración en Mag7. Tesis AR constructiva pero **condicional** (ver §5): el titular revisa su visión según evolucione la economía. |

---

## 4. Asignación estratégica (objetivo)

La asignación se define **por factor de riesgo económico**, no por la moneda de liquidación del broker (Pesos / MEP / Cable son cañería de settlement, no riesgos distintos).

### Punto de partida (foto 11/06/2026)

| Factor | Participación | Comentario |
|---|---|---|
| Bonos AR | ~50% | AL30 / AE38 / AL35 / Bopreal / GD35 — un solo crédito soberano + BCRA |
| Acciones AR | ~43% | YPF / YPFD / VIST / PAMP / TGSU2 / GGAL / SUPV / TXAR — energía + bancos |
| Acciones global ex-AR | ~13% nominal | Tech US (Mag7) vía CEDEARs; ~10% real (VIST y MELI son AR/LatAm) |
| Colchón (efectivo) | ~0% | Sin pólvora seca |
| Apalancamiento (caución USD) | **-6,2%** | Al 1% TNA; carry barato, pero gearing sobre cartera mono-factor |
| **Exposición total a factor Argentina** | **~90%** | El riesgo dominante; ~2x el techo de drawdown |

### Ancla estratégica objetivo (perfil #2, "crecer con piso")

| Factor | Objetivo | Banda | Notas |
|---|---|---|---|
| Bonos AR | ~30% | ±5 pts | Mantiene carry (~12% USD) y la pata de compresión; deja de ser la mitad de la cartera |
| Acciones AR | ~25% | ±5 pts | Conserva Vaca Muerta/recuperación; **diversificar más allá de energía/bancos** |
| Acciones global ex-AR | ~18% | ±4 pts | **Ampliar más allá de Mag7** (índice amplio/calidad) |
| Ballast (USD corto / Treasuries / algo de oro) | ~27% | ±5 pts | El "piso" que hoy falta: lo que NO cae cuando cae Argentina |
| Apalancamiento | 0% | — | Caución cerrada 100% a ene 2027 |
| **Exposición total a factor Argentina** | **~55%** | — | Seguís siendo mayoría AR: la tesis no se abandona, deja de ser todo |

**Perilla del titular (dial AR).** El ~55% es el ancla *constructiva* (extremo de mayor convicción del perfil #2). El extremo *defensivo* de la banda es AR ~40% / Ballast ~38%: sostiene el -20% de forma más pasiva, a costa de upside. Decisión del titular según convicción vs. tranquilidad.

**Rebalanceo por bandas.** Cuando un factor se sale de su banda por subas, se recorta hasta volver a banda. Es toma de ganancia automática: vende caro lo que voló, sin tener que adivinar el techo. Es la herramienta central de "proteger sin perder subas".

---

## 5. Tilt táctico (satélite) y gestión de la tesis AR

La visión constructiva sobre Argentina vive en el **satélite** (tilt acotado), no mueve el **core** (el techo de -20%). Se sostiene como **tesis condicional y falsable**: qué se cree, qué señales la confirman/refutan, y qué se hace cuando se mueven.

**Señales (signposts), tablero objetivo:**
- Escalera de calificaciones soberanas (hoy B-, tras salir de CCC; objetivo IG a +6 escalones). Cada notch es un checkpoint.
- Reservas BCRA, desinflación, riesgo país, resultado fiscal.
- Catalizador del titular: que la mejora macro **llegue a la población** antes de la elección — volverlo observable vía salario real, empleo y posicionamiento en encuestas.

**Gatillos pre-comprometidos:**
- **Confirmación → recortar.** Cada notch que sube la calificación (B → B+ → BB-) dispara soltar una tanda de AR. Contraintuitivo pero correcto: el upgrade confirma la tesis *y* significa que el precio ya se movió *y* que el upside restante es más fino. Se vende *en* la buena noticia.
- **Freno/reversión → de-riskear.** Si las reservas se estancan, la desinflación se da vuelta, o el riesgo electoral se materializa, se recorta el tilt.
- **Elección oct 2027 = stress-test programado.** No es apuesta binaria: la cartera debe poder atravesarla dentro del techo. Opción táctica a afinar más cerca: dipear por debajo de 55% (hacia ~45%) en el pico de incertidumbre y re-riesgo después de que resuelva, si resuelve a favor.

---

## 6. Cronograma de glide path (jun 2026 → Q1 2027)

Objetivo: aterrizar de ~90% AR a ~55% AR, cerrar la caución y construir el ballast, **antes de que el mercado empiece a pricear la elección**.

**Mecanismo híbrido.** Gatillos por fuerza (notch de rating / nuevos máximos / niveles de riesgo país) capturan buenos precios cuando se puede; un **backstop temporal** garantiza el aterrizaje aunque los precios no colaboren. El cuadro de abajo es el backstop (el "no más tarde que"): si los gatillos permiten avanzar antes, mejor.

| Período | Acción principal | AR % (fin) | Caución | Ballast % |
|---|---|---|---|---|
| **Inicio — jun '26** | Punto de partida | ~90% | -6% | ~0% |
| **Q3 '26** (jul–sep) | Reducir la pata más extendida (acciones AR post +100%); empezar a construir ballast | ~80% | -6% | ~8% |
| **Q4 '26** (oct–dic) | Continuar reducción; reducir caución a la mitad | ~68% | -3% | ~18% |
| **Ene '27** | **Cerrar caución 100%** — no se entra al año electoral con deuda en USD | ~62% | **0%** | ~22% |
| **Q1 '27** (feb–mar) | **Aterrizaje:** ~55% AR, ballast ~27%, apalancamiento 0 | **~55%** | 0% | ~27% |
| **Backstop — abr/may '27** | Completar sin excepción si quedó algo pendiente | ~55% | 0% | ~27% |

**Refinamiento de timing.** El mercado no espera a octubre para pricear la elección: se reposiciona en la previa. Por eso el aterrizaje real es **Q1 2027**, no abril/mayo. Abril/mayo es el backstop, no la meta — querés estar abajo *antes* de que se prenda la ventana, para no vender dentro de un mercado que ya está vendiendo.

**Principio de ejecución.** Vender en las subas, no esperar el techo. El de-risking discrecional "cuando vea un buen momento" se posterga solo; por eso está pre-comprometido en este cronograma.

---

## 7. Riesgo: líneas duras y monitoreo

**Líneas duras:**
- Drawdown global **-20%** = límite no negociable.
- Concentración de factor único AR: objetivo ≤ ~55% post-aterrizaje.
- Apalancamiento: 0% a partir de ene 2027.

**Indicadores a trackear (insumo del dashboard de Argo, §8):**
- Exposición por factor (AR vs. global vs. ballast) y drawdown corriente.
- Bonos: paridad, TIR, duration, current yield; riesgo país; escalera de ratings.
- Acciones: valuación vs. historia y pares; concentración sectorial (energía/bancos) y Mag7.
- Caución (mientras exista): monto abierto vs. flujo de cupones que la repaga.
- Señales "población": salario real, empleo, encuestas.

---

## 8. Gobernanza y separación de Argo

- Argo monitorea **read-only** las tres cuentas comitentes en una vista consolidada **por factor**; nunca ejecuta sobre las dos cuentas discrecionales.
- En el modelo de datos, cada posición lleva dimensión de cuenta/cartera; ledgers y P&L atribuidos por separado. Vista unificada para los ojos del titular; tracking separado en los datos (regla dura 7).
- La exposición económica real se suma por subyacente (ej.: VIST aparece como acción-cable y como CEDEAR; es **una** apuesta a Vista, no dos).

---

## 9. Revisión

- Revisar objetivo/riesgo/asignación **trimestralmente** y ante cualquier cambio material de régimen.
- La tesis AR es **explícitamente condicional**: el titular puede revisar su visión según evolucione la economía. Un cambio de tesis se documenta acá y reajusta el tilt del satélite, nunca el techo de -20% del core.

---

## 10. Nota de gobernanza

Este IPS es un marco propiedad del titular, que asigna el capital y decide. Claude (Argo) es la herramienta de soporte: produce análisis, propone, modela y monitorea. No es asesoramiento financiero matriculado. Si el sistema y el titular discrepan, gana el titular (principio del proyecto).
