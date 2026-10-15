# Resumen de cierre acumulativo — Proyecto ARGO

> Última actualización: **2026-08-25**.
> Este documento reemplaza la necesidad de pegar resúmenes anteriores: con este
> alcanza para arrancar la próxima sesión.

---

# ✅ ESTADO AL CIERRE

**Commit en los tres lados: `ed6ef45` + el de `tasa_implicita.py`.** Alembic head
`68eb9caf94d6` en local y VPS. El collector corre con código v3, suscribe 360
símbolos en lotes, persiste ticks y tiene alarma de silencio probada punta a punta.

**La captura funciona.** Dos ruedas completas grabadas (2026-08-24 y 2026-08-25):
~670.000 ticks por rueda, 360 símbolos, cobertura del 100 % de los momentos
evaluados. La base del VPS pesa **1,64 GB**.

**Fase 1 sigue activa.** El objetivo de Nivel 0 (días limpios consecutivos,
huecos = 0) es medible desde el 2026-08-24.

---

# 1. Lo que se construyó esta sesión: la máquina de descartar

El salto de la sesión no fue una estrategia: fue **la capacidad de evaluar
cualquiera de las ocho en una tarde**, con datos propios, en vez de dos meses de
desarrollo y plata puesta.

Cuatro herramientas, todas en `scripts/`, todas solo stdlib (corren en el VPS sin
SQLAlchemy ni CLI de sqlite3), todas escriben el informe **mientras avanzan** con
traceback completo ante excepción:

| Script | Pregunta que contesta |
|---|---|
| `censo_spreads.py` | ¿Cuánto cuesta operar esto? |
| `censo_desvios.py` | ¿Hay algo, en cualquier lado? |
| `analizar_par.py` | ¿Es real o es un artefacto de medición? |
| `tasa_implicita.py` | ¿Le gana a la caución? |

### Los tres diagnósticos que trae `analizar_par.py`

1. **Frescura** — el que decide. Segmenta la señal por antigüedad de las puntas.
   Si la señal crece con la antigüedad, es **rezago entre libros**, no precio.
2. **Duración de episodios** — un desvío que vive 20 segundos no es operable con
   un circuito de varias patas.
3. **Capacidad** — el tamaño de la punta más chica del circuito.

---

# 2. Tres estrategias cerradas con evidencia propia

## 2.1 `pairs_cedear` — CERRADA

**Versión original (CEDEAR vs subyacente en EE.UU.): no es ejecutable**, y eso se
supo sin datos — Argo no tiene forma de operar la pata de allá. Sin las dos patas
no es arbitraje, es una posición direccional disfrazada.

## 2.2 Arbitraje de dólar implícito (`rulo_mep_ccl` local) — CERRADA

Medido sobre una rueda completa: 664.523 ticks, 37 instrumentos líquidos,
**434 pares**. Resultado: **ningún par con desvío mediano positivo, ninguno
rentable.**

El par más limpio de la plaza, `AL30` vs `GD30` (1.537 momentos, sin selección de
ningún tipo): **mediana −6,2 bps, máximo del día +2,5 bps**, ambos **brutos, antes
de descontar un solo peso**. Los dólares implícitos están más juntos que el spread
que habría que cruzar para tocarlos.

**Diagnóstico de frescura por clase de par** (la prueba decisiva):

| Clase | Puntas frescas (<5 s) | Puntas viejas (>30 s) | Rezago |
|---|---|---|---|
| bono–bono | −5,5 | −6,6 | **−1,0** |
| bono–CEDEAR | −22,6 | −20,5 | **+2,1** |
| CEDEAR–CEDEAR | −43,1 | −36,7 | **+6,4** |

En pares de bonos el desvío **no** crece con la antigüedad: medición honesta, y da
negativo. En pares con CEDEARs **sí** crece, y más cuanto más CEDEARs hay — firma
del rezago: el CEDEAR sigue a una acción que se mueve en Nueva York mientras los
dos libros locales repricean a destiempo. **No era una diferencia de precio, era un
retraso.**

**Con comisión cero tampoco cierra.** Aparecían pares "rentables" (hasta 22,6 % de
los momentos), pero **los diez primeros tenían al menos un CEDEAR y ninguno era
bono contra bono**: los únicos que parecían pagar eran los contaminados. Y aun
tomándolos en serio, el neto era de 1 a 4 bps (20 a 76 centavos de dólar por
vuelta) en episodios de 20 a 30 segundos.

## 2.3 CI vs 24hs (tasa implícita) — CERRADA

Propuesta por el operador de BIND. **No es un arbitraje: es prestar plata por un
día.** La diferencia de precio entre plazos es la tasa implícita entre fechas de
liquidación, y está ahí a propósito.

Medido: **99.294 momentos, 65 instrumentos, dos ruedas.**

**La medición se validó a sí misma:**

| | TNA |
|---|---|
| Tasa implícita CI/24hs medida por Argo (mediana) | **20,9 %** |
| Caución a 1 día en pantalla de BIND (2026-08-25) | **22,70 %** |

Dos instrumentos distintos, caminos independientes, misma tasa. Es el mismo mercado
de dinero a un día visto desde dos lados.

**Diagnóstico de frescura: plano** (−185,2 / −186,1 / −185,1 / −185,0 en todos los
tramos). Cero rezago — tiene sentido, a CI y 24hs del mismo bono los mueve lo mismo.
**Esta medición está limpia.**

**Resultado: ni un momento de 99.294 le gana a la caución.** El mejor instrumento
del país, `AL30`: mercado 19,3 % → **neta para vos −67,4 %**. La cuenta cierra
exactamente: `19,3 − 80 (costos anualizados) − 9 (spreads) ≈ −70`.

En dólares es peor: mediana de mercado 0,0 %, neta −267 %.

**Razón estructural: la caución ES el instrumento nativo para prestar a un día.**
Replicarla sintéticamente con un bono paga peaje de entrada y salida. Un sintético
no le gana al nativo cuando el nativo no tiene esa fricción.

---

# 3. EL HALLAZGO TRANSFERIBLE: la ley del horizonte mínimo

Lo más valioso del día, y aplica a **toda** estrategia futura.

> **Los costos son fijos por operación; el retorno se devenga por tiempo.
> Cuanto más corto el horizonte, más brutal el costo.**

Con los costos reales de BIND (22 bps en renta fija) y un mercado que paga ~21 %
anual:

| Días de posición | Costo anualizado | ¿Cierra? |
|---|---|---|
| 1 día | 80,3 % | No, por 60 puntos |
| 3 días (viernes) | 26,8 % | No, por 6 puntos |
| **4 días** (finde largo) | **20,1 %** | **Al filo** |
| 7 días | 11,5 % | Sí |
| 30 días | 2,7 % | Holgado |

**Con los costos de BIND, ninguna estrategia que capture tasa puede tener horizonte
menor a ~4 días.** Esto también explica por qué murió el arbitraje: un rulo
intradiario tiene horizonte de minutos, el caso extremo de esta misma tabla.

---

# 4. Costos de operación — CONFIRMADOS con el operador de BIND (2026-08-25)

| Concepto | Bonos | Acciones / CEDEARs |
|---|---|---|
| Comisión | **0,10 %** | **0,10 %** |
| Derechos de mercado | **0,01 %** | **0,08 %** |

**Entrada y salida se cobran ×2, NO ×4** — y aplica también cuando las dos patas
son del mismo día en plazos distintos.

**Costo de un circuito completo:**
- Renta fija: `2 × (10 + 1)` = **22 bps**
- CEDEARs: `2 × (10 + 8)` = **36 bps**

Dos cosas que cambiaron respecto de lo que se asumía antes:
1. La comisión de CEDEARs bajó de 0,25 % a 0,10 % — ahora es igual a bonos.
2. **En CEDEARs los derechos (8 bps) son el costo dominante**, cuatro veces la
   comisión. La diferencia bono/CEDEAR ya no la hace la comisión sino el arancel.

*Sigue siendo parámetro provisorio: el número que vale es el que salga de las
primeras operaciones reales reconciliadas contra el resumen del broker.*

---

# 5. Lo que SÍ quedó en pie: la curva de cauciones

De la pantalla de BIND del 2026-08-25 (cauciones en pesos):

| Plazo | TNA |
|---|---|
| 1D | 22,70 |
| 2D | 20,60 |
| 3D | 22,00 |
| 6D | 21,40 |
| **7D** | **23,50** |
| 8D | 22,10 |
| **9D** | **20,10** |
| 27D | 22,60 |

**3,4 puntos de diferencia entre el 7D y el 9D.** Dos plazos casi iguales, tasas
muy distintas. No es arbitraje (no se puede vender una caución), pero **es
exactamente la decisión de tesorería**: si la plata va a quedar quieta una semana,
se elige al 23,50 y no al 20,10.

Sin edge, sin predicción, sin 90 días de paper. Y con horizontes de 7 a 28 días,
que es justo donde la ley del horizonte mínimo dice que sí cierra.

**Volatilidad confirmada:** la caución a 1 día cerró el día anterior en 17,70 % y
abrió en 22,70 % — **5 puntos de salto en una rueda**. Cualquier tasa "standard"
supuesta habría estado mal por 5 puntos. **La comparación tiene que ser contra la
tasa real.**

**Las cauciones ESTÁN listadas en Primary/Matriz** (confirmado por Gus con captura
de pantalla). Tickers visibles: `PESOS - 1D`, `PESOS - 2D`, `PESOS - 3D`, `6D`,
`7D`, `8D`, `9D`, `10D`, `13D` … `28D`, más las de dólar aparte.

⚠️ **Ojo con la liquidez al elegir plazo:** el 1D tenía 1.358 millones de volumen
comprador y el 8D tenía 125.000. La tasa linda de un plazo finito no sirve si no
entra el monto.

---

# 6. Estado de Instrucciones del Project

Existe `docs/instrucciones_actualizacion_2026-08-25.md` (**sin commitear**) con tres
bloques listos para pegar:

| Bloque | Contenido | Estado |
|---|---|---|
| **A** | **Método de evaluación de estrategias** — orden de las 3 preguntas, las 2 trampas estructurales, diagnóstico de frescura, 8 reglas de higiene, criterios de operabilidad | ✅ Válido tal como está |
| **B** | Conteo de comisiones | ⚠️ **Rehacer**: se escribió con el supuesto de ×4; los costos confirmados son ×2 |
| **C** | Callejones cerrados con evidencia | ⚠️ **Actualizar**: agregar CI/24hs y los costos definitivos |

**Agregar además la ley del horizonte mínimo** (sección 3 de este resumen) — es el
hallazgo más transferible y hoy no está en ningún lado.

**Nada de esto se aplicó todavía al Project.** Se frenó a propósito: los Bloques B
y C dependían de los costos, que se confirmaron recién al final de la sesión.

---

# 7. Investigación de Cocos — cerrada por ahora

Motivo: el 0 % de comisión parecía dar vuelta la ecuación del arbitraje.

- **No existe API oficial.** Lo que hay es `pyCocos`, librería de ingeniería inversa
  sobre endpoints internos de la app. No oficial, puede romperse sin aviso, y
  probablemente choca con los términos del servicio.
- **No tiene cotizaciones en tiempo real** (Cocos usa SocketIO y la librería no lo
  cubre; solo REST). Para una estrategia que necesita ver el desvío *ahora*, leer
  por REST es llegar tarde por diseño.
- **Quedó irrelevante de todos modos:** el desvío bruto de los pares limpios es
  negativo, y ninguna estructura de costos salva un bruto negativo.

En Argo, Cocos está **previsto pero no investigado**: bloque `cocos` en
`secrets.json.template` con `habilitado: false`, el schema soporta
`broker = primary|iol|cocos|polygon`, y el roadmap lo ubica en Fase 5.

---

# 8. Pendientes vivos al 2026-08-25

**Prioridad 1 — lo que habilita tesorería:**
1. **Meter las cauciones al universo de captura.** Es la pieza que le falta a
   `cash_management.py` para funcionar sin que nadie mire una pantalla. Requiere:
   descubrir los símbolos exactos en el catálogo de Primary, sumarlos a
   `universe.json` + `instrumento_broker_mapping`, y reiniciar el collector.
2. **Revalidación semanal automática** del universo (systemd timer para
   `validar_universo_vs_catalogo.py`). Hoy es manual.
3. **`generar_mapeo_primary.py`: validar (símbolo, plazo)**, no solo símbolo. Es la
   causa raíz del incidente; la revalidación solo tapa el síntoma.

**Prioridad 2 — deuda técnica:**
4. **Tanda de escritura fallida se descarta entera con un warning.** Observado:
   `encolados 130.670 != persistidos 130.391` = **279 ticks perdidos** (~0,2 %).
   Debe reintentarse antes de descartar; el descuadre debe ser métrica, no log.
5. Cambiar el default `primary_produccion.account` de **11647 a 17169** (sigue en
   11647). Y renombrar `primary_produccion_ana` → `primary_lectura_ana`.
6. `scp` del bloque de Ana al `secrets.json` del VPS (local 10 bloques, VPS 8).
7. Habilitar `ssh-agent` en Windows + `ssh-add` (trípode desatendido).
8. **H2.0:** documento de diseño de tablas — `logins`, `comitentes`, `posiciones`,
   `operaciones`, **`movimientos_capital`**, `eventos_renta_fija`, `dividendos`.
9. Regenerar el token de Telegram (texto plano; el notifier loguea la URL con el
   token). `chmod 600 secrets.json` en el VPS.
10. **Identificar los 6 símbolos `BA.C`.** Están en el catálogo vivo y se capturan,
    pero **sus datos se excluyen de todo análisis** hasta saber qué son.
11. **Capa de agregación post-collector** (`ticks_crudos` → `cotizaciones_1min`) y
    política de retención del crudo. La base ya pesa 1,64 GB. *(Se conecta con el
    foco 3 de la próxima sesión.)*
12. **Diagnosticar BBAR / TXAR / CRES en plazo CI.** Mostraron "ganancias" del 3-4 %
    con tamaños de 12 a 211 nominales, siempre como pata barata, siempre en CI.
    Casi seguro puntas no ejecutables, pero no está confirmado.
13. Warning cosmético "Error cerrando WebSocket"; ruido de tracebacks en reconexión.
14. Offset de relojes local vs BIND (~2,2 s). Confirmar feriados BYMA.
15. Aislar la credencial de producción de las sesiones interactivas de Claude.
16. `DeprecationWarning` de sqlite3 con datetime en Python 3.12.

**Acciones de archivo derivadas de esta sesión:**
17. Marcar `pairs_cedear` y `rulo_mep_ccl` como **cerradas** en
    `config/strategies_catalog.json`, con fecha y referencia a este análisis.
18. **Re-scope de la Fase 2 del `roadmap.json`**: tenía a `pairs_cedear` como primera
    estrategia y ese supuesto se cayó. El candidato natural ahora es
    `cash_management`.
19. Commitear `docs/instrucciones_actualizacion_2026-08-25.md` (con B y C corregidos).

---

# 9. Los tres focos de la PRÓXIMA sesión (pedido explícito de Gus)

### 1. Tesorería (`cash_management`)
Con la curva de cauciones como centro. La pregunta operativa: dado un monto y un
horizonte, ¿qué plazo conviene? Primer paso técnico: capturar las cauciones.

### 2. Repasar las estrategias
Quedan 5 sin tocar del catálogo de 8. Hay que re-rankearlas a la luz de dos cosas
nuevas: la ley del horizonte mínimo y los costos confirmados. `arb_bonos_ny_local`
(AL vs GD) **sigue intacta** — no es arbitraje instantáneo sino una apuesta a
percepción de riesgo legal, y se evalúa con otro método.

### 3. ¿Estamos capturando datos que nunca vamos a usar?
**Percepción de Gus, y hay evidencia a favor:** la base pesa 1,64 GB, se graban 360
símbolos tick a tick, y el análisis más ambicioso de esta sesión usó **37**. Vale
mirarlo con números: cuánto aporta cada símbolo, qué se usa, qué se podría agregar
a 1 minuto y qué se podría dejar de capturar. Se conecta directo con el pendiente 11.

---

# 10. Ritual de re-arranque

1. Correr `.\scripts\tripode.ps1` y pedirle a Claude que lea el `logs\tripode_*.txt`
   más reciente.
2. **Verificar que el sistema esté CAPTURANDO** — el trípode no lo mira. La señal
   robusta es conteo de filas y `MAX(ts_recepcion)` en `ticks_crudos`, nunca `mtime`
   ni el estado de systemd.
3. El trípode mira el **schema**, no los **datos**. Salida vacía de `alembic current`
   es indistinguible de base sin estampar: re-correr ante la duda.

---

# 11. Modo de trabajo (sin cambios, confirmado en la práctica)

- **Claude corre comandos directamente sobre `C:\Argo`** (leer logs, inspeccionar la
  base, editar archivos).
- **Claude NO alcanza el VPS.** Puerto 22 saliente bloqueado. Todo tramo VPS lo
  ejecuta Gus; los logs vuelven por `scp` a `C:\Argo\logs\vps\`.
- ⚠️ **Git siempre desde PowerShell, nunca desde el puente Linux** (CRLF).
- El usuario del VPS es **`argo`**, no `root`: `ssh argo@167.99.57.224`.

**Aprendizaje operativo de esta sesión (se repitió tres veces):** el ciclo
local → GitHub → VPS se cortó en distintos eslabones cada vez — una vez faltó el
`git add`, otra el `git commit`, otra el `scp` usaba el usuario equivocado. **El
síntoma siempre fue el mismo: "Already up to date" en el VPS.** Eso no es un error,
es git diciendo que no había nada nuevo. Verificar con `git log --oneline -1`
después del push, y con `ls -la` en el VPS antes de correr nada.

---

# 12. Joaquín (16 años) — sin cambios

**Encuadre:** quiere estudiar Finanzas; le seduce el trading por redes. Objetivo:
que entienda que las redes son humo, **sin pincharle el globo**.

**El mecanismo central — "el duelo":** dos carteras **paper** en paralelo, mismo
capital nominal, mismos costos. **A** la maneja Argo; **B** la maneja Joaco con lo
que ve en redes. Reporte semanal comparando. Seis meses, cero plata en riesgo.

**Primer entregable de Joaco:** elegir UNA estrategia de redes y escribirla como
reglas explícitas. El 80 % de la lección está ahí: **la mayoría no se puede
escribir**, porque no son reglas, son narrativas.

**Dato duro** (Chague, De-Losso & Giovannetti, *Day Trading for a Living?*, Brasil
2013-2015): de 1.551 day traders que persistieron +300 días, **el 97 % perdió
plata**; 0,5 % superó el sueldo de un cajero.
https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101

**📄 Pieza publicada — "El Vellocino Lento"**
https://claude.ai/code/artifact/2f268df2-d0ce-4725-9a82-3b9d2306d1ca
*(Privada hasta que Gus la comparta. Para actualizarla desde otro chat hay que pasar
esa URL como `url` al publicar.)*

**Material nuevo disponible:** toda esta sesión es la mejor clase posible sobre por
qué el trading de redes es humo — el mercado está calibrado al costo del
arbitrajista marginal, y nosotros no somos ese. Se midió, no se opinó.

---

# 13. Insumos de H2.0 (del sondeo BIND de junio, sin cambios)

1. **DOS valuaciones de cartera, NO son la misma.** `account_report.portfolio` =
   garantía post-aforo. `detailed_position.totalMarketValue` = mercado bruto. Para
   "monto / P&L / drawdown en plata" la fuente es **`totalMarketValue`**.
2. **`priceConversionFactor`:** los `*InitialPrice` vienen **crudos** (×100 en bonos);
   `marketPrice`/`marketValue` ya en magnitud pantalla. El cost-basis se persiste ya
   convertido, o con el pCF al lado.
3. **MEP vs CCL viene segmentado nativo en caja:** buckets `USD C` y `USD D`.
4. **Para el join:** reconciliar por `symbolReference` (ticker pelado), **no** por el
   string de símbolo (las posiciones figuran en 24hs, `tradingSymbol` es CI).
5. **pyRofex NO ve el sleeve externo** (ADRs/ETF del exterior, ≈ USD 31k). La capa de
   Portfolio Management no puede valuar la cartera completa solo con pyRofex.

---

# 14. La frase que resume la sesión

> **Si el análisis dice que hay plata gratis, la pregunta correcta no es
> "¿cómo la agarro?" sino "¿qué está roto en la medición?".**

Pasó dos veces en dos días. La primera, "ganancias" del 3 % en tres acciones en CI
con tamaños de 12 nominales. La segunda, un 99,33 % de momentos rentables en el
escenario de comisión cero. **Las dos veces el error estaba del lado de la
medición, no en el mercado.**

Y el saldo neto es bueno: se descartaron tres estrategias en dos días, con datos
propios, sin arriesgar un peso — y quedó construida la máquina que va a descartar
(o confirmar) las cinco que faltan.
