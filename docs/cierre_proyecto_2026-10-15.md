# Acta de cierre — Proyecto ARGO

> **Fecha:** 2026-10-15 · **Estado:** CERRADO (stand-by indefinido)
> **Decisión de:** Gus. Reemplaza a todos los resúmenes de cierre anteriores.
> El último resumen operativo (2026-08-25) queda en `C:\Argo\docs\resumen_cierre.md` y en git.

---

## 1. La decisión y por qué

Argo se cierra porque **la probabilidad de encontrar una estrategia ganadora, con la
evidencia necesaria y en un plazo razonable, es muy baja para el tiempo que demanda.**
No es una sensación: es lo que dieron los números del propio proyecto.

**El corto plazo está cerrado.** Con los costos reales de BIND (22 bps por circuito en
renta fija, 36 bps en CEDEARs), mantener una posición 1 día equivale a pagar ~80 % anual
de costo. **Nada con horizonte menor a ~4 días cierra** (ley del horizonte mínimo).
El mercado está calibrado al arbitrajista más barato, y no somos ese.

**El largo plazo no se puede probar a tiempo.** Lo que queda (factor, tendencia, bonos
macro, AL vs GD) son estrategias de prima. Para distinguir Sharpe 1,0 de la suerte:

> t = Sharpe × √años ≥ 2 → hacen falta **4 años**.
> Con los 25 meses de historia diaria disponibles: t = 1,0 × √2,1 ≈ **1,45**. No alcanza.

**La recompensa esperada es chica.** El objetivo realista de la etapa real era
USD 40–60 por mes sobre USD 4.000, con un camino mínimo de ~6 meses (backtester +
estrategia + 90 días de paper) a 1–2 h por día, y llegando con evidencia débil.

## 2. Lo que el proyecto sí probó (con datos propios, sin arriesgar un peso)

| Estrategia | Resultado |
|---|---|
| `pairs_cedear` (CEDEAR vs subyacente US) | **Cerrada.** No hay forma de operar la pata de EE.UU. |
| `rulo_mep_ccl` local | **Cerrada.** 434 pares medidos, ninguno con desvío positivo ni antes de costos. Lo que parecía ganancia era rezago entre libros. |
| CI vs 24hs (tasa implícita) | **Cerrada.** 99.294 momentos, ninguno le gana a la caución. La medición se validó sola: 20,9 % medido vs 22,7 % de caución en pantalla. |
| Las otras 5 del catálogo | Sin evaluar. Todas de horizonte largo → chocan con el punto 1. |

**Costos confirmados con BIND (2026-08-25):** comisión 0,10 % en bonos y CEDEARs;
derechos 0,01 % bonos / 0,08 % CEDEARs; entrada y salida se cobran ×2.

**Lo único que quedó en pie: la curva de cauciones.** Es una decisión de tesorería,
no un edge: si la plata va a estar quieta, elegir el plazo con mejor tasa (el 25/08
había 3,4 puntos entre el 7D y el 9D). Se puede hacer a mano mirando la pantalla de BIND.

## 3. Lo que se conserva: el motor para entrar y operar en BIND

Todo vive en **GitHub (`torgus2020/argo`, privado)** y en **`C:\Argo`**.

| Pieza | Para qué sirve al retomar |
|---|---|
| `src/collectors/primary_conexion.py` | Conexión a producción BIND vía pyRofex (incluye el redireccionamiento del host, sin el cual pyRofex se autentica contra el servidor equivocado). |
| `scripts/validar_handshake_primary_produccion.py` | Prueba en un comando que las credenciales entran. Funciona con mercado cerrado. |
| `scripts/validar_universo_vs_catalogo.py` | Revalida el universo contra el catálogo vivo de BIND (el universo envejece). |
| `scripts/smoke_test_suscripcion_primary.py` | Prueba que el servidor acepta la suscripción a precios. |
| `scripts/generar_mapeo_primary.py` + `poblar_*.py` | Arman el universo de instrumentos y sus símbolos de BIND. |
| `src/collectors/market_data_collector.py` (v3) | Captura en vivo, suscripción por lotes tolerante a símbolos deslistados. |
| `censo_spreads.py`, `censo_desvios.py`, `analizar_par.py`, `tasa_implicita.py` | La "máquina de descartar": evalúa una idea en una tarde. |
| `data/argo.sqlite` local (7,6 MB) | Universo (instrumentos) y mapeo a BIND ya poblados. |
| `config/secrets.json` local | Credenciales. **Nunca a git.** |

**Seguridad que hay que respetar si se vuelve a operar:** las cuentas 17169 (piloto)
y 11647 (cartera personal) comparten login. La frontera la pone el software:
**allowlist de ejecución = {17169}**, validada antes de toda orden. El default de
`primary_produccion.account` todavía dice 11647 y hay que cambiarlo a 17169 antes
de escribir una sola línea de ejecución.

## 4. Lo que se borra

- **VPS:** base `argo.sqlite` de 4,4 GB (ticks de 8 ruedas, 24/08 → 02/09) y la
  carpeta `/home/argo/argo` completa. Nada es único: el código está en GitHub y en
  `C:\Argo`, y los ticks quedarían viejos para cualquier análisis futuro.
- **VPS:** servicios `argo-collector`, `argo-heartbeat` y `argo-roadmap-engine`
  apagados y deshabilitados (dejan de llegar los mensajes diarios de Telegram).

## 5. Estado técnico al cierre (para no llevarse sorpresas al retomar)

- **La credencial de BIND en `secrets.json` está vencida.** Gus cambió la clave
  ~2026-09-02; desde el 03/09 BIND rechaza la conexión. La captura estuvo caída del
  03/09 al cierre, otra vez sin aviso.
- **Por qué no avisó nada:** la alarma de silencio vive dentro del collector; si el
  collector no arranca, la alarma tampoco. Además systemd se rinde tras 5 intentos
  fallidos en 5 minutos (`StartLimitBurst=5`) y no vuelve a probar.

## 6. Cómo retomar, si algún día hace falta

1. Actualizar la clave de BIND en `C:\Argo\config\secrets.json` (bloque `primary_produccion`).
2. `python scripts\validar_handshake_primary_produccion.py` → tiene que dar OK.
3. `python scripts\validar_universo_vs_catalogo.py` → el universo habrá envejecido.
4. Cambiar el default de cuenta a **17169** antes de cualquier código de ejecución.
5. **Antes de volver a confiar en una captura desatendida**, arreglar las tres fallas
   estructurales: vigilante externo al collector (chequeo diario de filas grabadas),
   alerta explícita de credencial rechazada, y que systemd nunca deje de reintentar.
6. Leer este acta y la sección "Reglas duras" de las Instrucciones del Project.

## 7. Lecciones que sirven para cualquier otro proyecto

1. **La salud de un proceso se mide por lo que produce, no por si "está corriendo".**
   Dos veces, casi dos meses cada una, el sistema estuvo caído sin que nadie lo supiera.
2. **El vigilante no puede vivir adentro del vigilado.** Una alarma que depende del
   proceso que vigila se apaga justo cuando hace falta.
3. **Una alarma no está operativa hasta que el mensaje llega al celular.**
4. **Los costos son por operación; el retorno es por tiempo.** Cuanto más corto el
   horizonte, más pesa el costo.
5. **Si el análisis dice que hay plata gratis, la pregunta es qué está roto en la
   medición.** Pasó dos veces y las dos el error era nuestro.
6. **Cuando un comando depende del anterior, encadenar con `&&`.** Un paso 2 que
   "funciona" después de un paso 1 fallido parece éxito y no lo es.

## 8. Material para Joaquín

La pieza **"El Vellocino Lento"** sigue publicada (privada hasta que Gus la comparta):
https://claude.ai/code/artifact/2f268df2-d0ce-4725-9a82-3b9d2306d1ca

El cierre de Argo es en sí mismo una buena lección: se midió con datos propios que el
trading de corto plazo no deja plata a un operador minorista con costos de mercado, y
se decidió parar en vez de forzarlo. Dato de respaldo: Chague, De-Losso & Giovannetti,
*Day Trading for a Living?* (Brasil 2013-2015) — el 97 % de los day traders que
persistieron más de 300 días perdió plata.
https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101
