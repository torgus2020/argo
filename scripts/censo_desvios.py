"""
Censo de desvíos — ¿cuántas veces por rueda hay una oportunidad real?

=== LA PREGUNTA ===
El censo de spreads midió cuánto CUESTA operar. Esto mide cuánto PAGA.
Sin este número, `pairs_cedear` y sus primos son una idea sin evidencia.

=== QUÉ SE MIDE, EXACTAMENTE ===
Cada instrumento que cotiza en pesos y en dólares (AL30 / AL30D) define un
tipo de cambio implícito. Si dos instrumentos distintos implican tipos de
cambio distintos AL MISMO TIEMPO, hay una diferencia de precio observable
—no una predicción— y se puede cerrar el circuito:

    pesos --compro AL30--> AL30 --vendo AL30D--> USD
    USD --compro GD30D--> GD30 --vendo GD30--> pesos

CUATRO operaciones, cuatro comisiones. El circuito vuelve a pesos: por eso
el número que sale es ganancia de verdad y no una conversión de moneda.

=== POR QUÉ SE USAN PRECIOS EJECUTABLES Y NO EL PUNTO MEDIO ===
Para comprar se paga la PUNTA VENDEDORA; para vender se cobra la PUNTA
COMPRADORA. Midiendo con puntas ejecutables, el spread ya está DENTRO del
número y no hay que sumarlo aparte (sumarlo sería contarlo dos veces).
Usar el punto medio infla la oportunidad exactamente en un spread — es el
error clásico que hace que un backtest de arbitraje parezca genial.

    fx_compra_usd = offer_ARS / bid_USD    (pesos que pago por cada dólar)
    fx_venta_usd  = bid_ARS  / offer_USD   (pesos que cobro por cada dólar)

=== LAS DOS TRAMPAS QUE ESTE SCRIPT EVITA ===

1. ASINCRONÍA. Comparar AL30 de las 11:00:03 con GD30 de las 11:04:12
   inventa desvíos que nunca existieron. Por eso se muestrea sobre una
   grilla temporal y se descarta toda punta con más de --frescura segundos.

2. EL SESGO DEL EXTREMO — la más peligrosa de las dos. En cada momento el
   script busca el dólar implícito MÁS BARATO y el MÁS CARO entre ~120
   instrumentos. Cuando tomás el extremo de 120 series con ruido, el
   resultado es positivo AUNQUE NO HAYA NINGÚN EDGE: estás midiendo el
   ruido del peor instrumento, no una diferencia aprovechable. Los extremos
   los ocupan siempre los ilíquidos.
   Antídoto: --min-ticks exige que las DOS patas del instrumento tengan un
   mínimo de actividad en la ventana para participar. Y --barrido corre el
   análisis a varios umbrales de una, para VER si el desvío se derrumba
   cuando se sacan los instrumentos finitos. Si se derrumba, no había edge.

=== ESCALA DE PRECIOS ===
No hace falta convertir el ×100 de los bonos: las dos patas del mismo
instrumento vienen en la misma escala, así que el cociente ya es el tipo de
cambio verdadero. Vale igual para bonos y para CEDEARs.

=== CÓMO SE CORRE ===
    source /home/argo/argo/.venv/bin/activate
    cd /home/argo/argo
    python scripts/censo_desvios.py --dias 2 --barrido
"""

import argparse
import csv
import json
import sqlite3
import statistics
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent

COMISION_BPS = {"renta_fija": 10.0, "default": 25.0}
TIPOS_RENTA_FIJA = {"bono_usd_ar", "bopreal", "boncer", "lecap", "boncap", "on"}
OFFSET_LOCAL_HORAS = -3

# Un tipo de cambio implícito fuera de esta banda respecto de la mediana del
# momento es dato malo (punta fantasma, instrumento mal mapeado), no una
# oportunidad. La banda vieja era ±20 % y NUNCA se activó: la basura real de
# este mercado aparece como desvíos del 3-4 %. Corregida tras el censo del
# 2026-08-24, donde tres acciones en CI mostraron "ganancias" del 3 % que el
# filtro dejó pasar enteras.
BANDA_CORDURA = 0.02  # ±2 %

# Umbrales del barrido de liquidez (mínimo de ticks por pata en la ventana).
UMBRALES_BARRIDO = [0, 100, 500, 2000, 5000]


def comision_bps(tipo: str, comision_cero: bool) -> float:
    """Comisión por operación. Con --comision-cero (escenario Cocos) es 0."""
    if comision_cero:
        return 0.0
    if (tipo or "").lower() in TIPOS_RENTA_FIJA:
        return COMISION_BPS["renta_fija"]
    return COMISION_BPS["default"]


def costo_circuito(tipo_a, tipo_b, legs, derechos, comision_cero) -> float:
    """
    Costo total de dar la vuelta, en bps.

    El circuito son `legs` operaciones repartidas entre los dos instrumentos:
    la mitad en el que compro dólares, la mitad en el que los vendo. Los
    derechos de mercado se pagan en TODAS las operaciones, tenga o no
    comisión el broker.
    """
    com = legs * (comision_bps(tipo_a, comision_cero)
                  + comision_bps(tipo_b, comision_cero)) / 2.0
    return com + legs * derechos


def cargar_ruta_base(ruta_override=None) -> Path:
    if ruta_override:
        return Path(ruta_override)
    config = json.loads(
        (RAIZ_PROYECTO / "config" / "config.json").read_text(encoding="utf-8")
    )
    return RAIZ_PROYECTO / config["paths"]["database"]


def a_dt(texto: str):
    try:
        return datetime.strptime(texto[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


class Informe:
    def __init__(self, ruta: Path):
        self.ruta = ruta
        self.fh = open(ruta, "w", encoding="utf-8")

    def w(self, linea: str = ""):
        print(linea)
        self.fh.write(linea + "\n")
        self.fh.flush()

    def cerrar(self):
        self.fh.close()


def cargar_series(args, inf: Informe):
    """Trae los ticks de instrumentos que tienen pata ARS y pata dólar."""
    ruta_base = cargar_ruta_base(args.db)
    inf.w(f"Base            : {ruta_base}")
    if not ruta_base.exists():
        raise FileNotFoundError(f"No existe la base en {ruta_base}")

    desde = (datetime.now(timezone.utc) - timedelta(days=args.dias)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    inf.w(f"Ventana         : últimos {args.dias} día(s) → desde {desde} UTC")
    inf.w(f"Moneda dólar    : {args.moneda}")
    inf.w(f"Frescura máxima : {args.frescura} s")
    inf.w(f"Grilla          : cada {args.grilla} s")
    inf.w(f"Banda de cordura: ±{BANDA_CORDURA*100:.0f} %")
    inf.w(f"Circuito        : {args.legs} operaciones · derechos "
          f"{args.derechos} bps c/u")
    inf.w("")

    con = sqlite3.connect(f"file:{ruta_base}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    sql_pares = """
        SELECT i.ticker, i.tipo, m.plazo,
               MAX(CASE WHEN m.moneda_liquidacion='ARS' THEN m.id END) AS id_ars,
               MAX(CASE WHEN m.moneda_liquidacion=? THEN m.id END) AS id_usd
        FROM instrumento_broker_mapping m
        JOIN instrumentos i ON i.id = m.instrumento_id
        WHERE m.activo = 1
        GROUP BY i.ticker, i.tipo, m.plazo
        HAVING id_ars IS NOT NULL AND id_usd IS NOT NULL
    """
    pares = [dict(r) for r in con.execute(sql_pares, (args.moneda,))]
    inf.w(f"Tickers con las dos patas ({args.moneda}): {len(pares)}")

    mapping_a_par = {}
    for p in pares:
        mapping_a_par[p["id_ars"]] = (p["ticker"], p["plazo"], p["tipo"], "ARS")
        mapping_a_par[p["id_usd"]] = (p["ticker"], p["plazo"], p["tipo"], "USD")
    if not mapping_a_par:
        raise RuntimeError("Ningún ticker tiene las dos patas. Nada que medir.")

    ids = ",".join(str(i) for i in mapping_a_par)
    sql = f"""
        SELECT mapping_id, ts_mensaje, bid_price, bid_size, offer_price, offer_size
        FROM ticks_crudos
        WHERE ts_mensaje >= ? AND mapping_id IN ({ids})
          AND bid_price > 0 AND offer_price > 0 AND offer_price >= bid_price
        ORDER BY ts_mensaje
    """
    eventos = []
    conteos = {}
    for r in con.execute(sql, (desde,)):
        ts = a_dt(r["ts_mensaje"])
        if ts is None:
            continue
        clave = mapping_a_par[r["mapping_id"]]
        eventos.append(
            (ts, clave, r["bid_price"], r["bid_size"], r["offer_price"],
             r["offer_size"])
        )
        k = (clave[0], clave[1], clave[3])
        conteos[k] = conteos.get(k, 0) + 1
    con.close()

    inf.w(f"Ticks utilizables en la ventana: {len(eventos):,}")
    if not eventos:
        raise RuntimeError("Sin ticks con las dos puntas en la ventana.")
    inf.w(f"Rango           : {eventos[0][0]} → {eventos[-1][0]} UTC")

    # Actividad por instrumento = la pata MÁS FLOJA. De nada sirve que el
    # lado en pesos tenga 20.000 ticks si el lado en dólares tiene 12.
    actividad = {}
    for (ticker, plazo, lado), n in conteos.items():
        k = (ticker, plazo)
        actividad[k] = min(actividad.get(k, n), n)
    inf.w(f"Instrumentos con ambas patas presentes: {len(actividad)}")
    inf.w("")
    return eventos, actividad


def recorrer(args, eventos, permitidos):
    """Camina el tiempo y busca el mejor circuito de cada momento."""
    estado = {}
    muestras = 0
    con_dos = 0
    brutos = []
    oportunidades = []
    descartes_cordura = 0

    frescura = timedelta(seconds=args.frescura)
    paso = timedelta(seconds=args.grilla)
    proximo_corte = eventos[0][0] + paso
    i, n = 0, len(eventos)

    while i < n:
        while i < n and eventos[i][0] <= proximo_corte:
            ts, (ticker, plazo, tipo, lado), bid, bsz, offer, osz = eventos[i]
            i += 1
            if (ticker, plazo) not in permitidos:
                continue
            d = estado.setdefault((ticker, plazo), {"tipo": tipo})
            d[lado] = (ts, bid, bsz, offer, osz)

        muestras += 1
        ahora = proximo_corte
        fx = []
        for (ticker, plazo), d in estado.items():
            a, u = d.get("ARS"), d.get("USD")
            if not a or not u:
                continue
            if ahora - a[0] > frescura or ahora - u[0] > frescura:
                continue
            fx.append(
                {
                    "ticker": ticker, "plazo": plazo, "tipo": d["tipo"],
                    "fx_compra": a[3] / u[1],
                    "fx_venta": a[1] / u[3],
                    "size_compra": min(a[4] or 0, u[2] or 0),
                    "size_venta": min(a[2] or 0, u[4] or 0),
                }
            )

        if len(fx) >= 2:
            con_dos += 1
            mediana_fx = statistics.median([f["fx_compra"] for f in fx])
            sanos = [f for f in fx
                     if abs(f["fx_compra"] / mediana_fx - 1) <= BANDA_CORDURA]
            descartes_cordura += len(fx) - len(sanos)

            if len(sanos) >= 2:
                barato = min(sanos, key=lambda f: f["fx_compra"])
                caro = max(sanos, key=lambda f: f["fx_venta"])
                if barato["ticker"] != caro["ticker"]:
                    bruto = (caro["fx_venta"] / barato["fx_compra"] - 1) * 10000
                    size = min(barato["size_compra"], caro["size_venta"])
                    brutos.append(bruto)
                    if size >= args.min_size:
                        costo = costo_circuito(
                            barato["tipo"], caro["tipo"], args.legs,
                            args.derechos, args.comision_cero
                        )
                        if bruto - costo > 0:
                            oportunidades.append(
                                {"ts": ahora, "compra": barato["ticker"],
                                 "vende": caro["ticker"], "plazo": barato["plazo"],
                                 "bruto": bruto, "costo": costo,
                                 "neto": bruto - costo, "size": size}
                            )
        proximo_corte += paso

    return {"muestras": muestras, "con_dos": con_dos, "brutos": brutos,
            "oportunidades": oportunidades, "descartes": descartes_cordura}


def pct(valores_ordenados, p):
    if not valores_ordenados:
        return 0.0
    return valores_ordenados[int(round((len(valores_ordenados) - 1) * p))]


def barrido(args, inf: Informe, eventos, actividad):
    """
    Corre el análisis a varios umbrales de liquidez y muestra el efecto.

    Esta tabla es el antídoto contra el sesgo del extremo: si el desvío
    disponible se derrumba al sacar los instrumentos finitos, entonces lo
    que estábamos midiendo era ruido de ilíquidos y no una oportunidad.
    """
    inf.w("=" * 78)
    inf.w("BARRIDO DE LIQUIDEZ — ¿el desvío sobrevive al sacar los ilíquidos?")
    inf.w("=" * 78)
    inf.w("min ticks = actividad mínima exigida a la pata MÁS FLOJA del instrumento")
    inf.w("")
    inf.w(f"{'min ticks':>10}{'instrum.':>10}{'momentos':>10}"
          f"{'mediana':>10}{'p90':>9}{'p99':>9}{'máximo':>10}{'oport.':>9}")
    inf.w("-" * 78)
    for umbral in UMBRALES_BARRIDO:
        permitidos = {k for k, v in actividad.items() if v >= umbral}
        if len(permitidos) < 2:
            inf.w(f"{umbral:>10}{len(permitidos):>10}   (menos de 2 instrumentos: sin comparación)")
            continue
        r = recorrer(args, eventos, permitidos)
        b = sorted(r["brutos"])
        if not b:
            inf.w(f"{umbral:>10}{len(permitidos):>10}{0:>10}   (sin momentos comparables)")
            continue
        inf.w(f"{umbral:>10}{len(permitidos):>10}{len(b):>10}"
              f"{statistics.median(b):>10.1f}{pct(b,0.90):>9.1f}"
              f"{pct(b,0.99):>9.1f}{b[-1]:>10.1f}{len(r['oportunidades']):>9}")
    inf.w("")
    inf.w("Todas las cifras de desvío están en puntos básicos, brutas (el spread")
    inf.w("ya está adentro; falta restar el costo del circuito).")
    inf.w("")


def reportar(args, inf: Informe, res, permitidos):
    brutos = sorted(res["brutos"])
    ops = res["oportunidades"]

    inf.w("=" * 78)
    inf.w(f"DETALLE CON min-ticks = {args.min_ticks}"
          f"{'  (COMISIÓN CERO — escenario Cocos)' if args.comision_cero else ''}")
    inf.w("=" * 78)
    inf.w(f"Instrumentos que participan   : {len(permitidos)}")
    inf.w(f"Momentos evaluados            : {res['muestras']:,}")
    inf.w(f"Momentos con 2+ frescos       : {res['con_dos']:,}")
    inf.w(f"Puntas fuera de banda (±{BANDA_CORDURA*100:.0f} %) : {res['descartes']:,}")
    inf.w(f"Tamaño mínimo exigido         : {args.min_size}")
    inf.w("")

    if not brutos:
        inf.w("Sin momentos comparables con este filtro.")
        return []

    inf.w("Desvío bruto disponible (bps):")
    for etiqueta, p in [("mínimo", 0.0), ("p25", 0.25), ("mediana", 0.5),
                        ("p75", 0.75), ("p90", 0.90), ("p99", 0.99),
                        ("máximo", 1.0)]:
        inf.w(f"  {etiqueta:<10}: {pct(brutos, p):>10.1f}")
    inf.w("")

    costo_bonos = costo_circuito("bono_usd_ar", "bono_usd_ar", args.legs,
                                 args.derechos, args.comision_cero)
    inf.w(f"Costo de referencia (dos bonos): {costo_bonos:.1f} bps")
    inf.w(f"Momentos con ganancia neta > 0 : {len(ops):,} de {len(brutos):,}"
          f" ({100.0*len(ops)/len(brutos):.2f} %)")
    if ops:
        netos = sorted(o["neto"] for o in ops)
        inf.w(f"Neto mediano                   : {statistics.median(netos):.1f} bps")
        inf.w(f"Neto máximo                    : {netos[-1]:.1f} bps")
        inf.w("")
        inf.w("Momentos, NO operaciones: un desvío que dura 5 minutos aparece en")
        inf.w("muchos momentos consecutivos y es UNA sola oportunidad.")
        inf.w("")
        por_par = {}
        for o in ops:
            por_par.setdefault((o["compra"], o["vende"], o["plazo"]), []).append(o["neto"])
        inf.w(f"{'Compro USD en':<14}{'Vendo USD en':<14}{'Plz':<6}"
              f"{'Momentos':>10}{'Neto med':>11}")
        inf.w("-" * 78)
        for (c, v, pl), netos_par in sorted(por_par.items(), key=lambda x: -len(x[1]))[: args.top]:
            inf.w(f"{c:<14}{v:<14}{pl:<6}{len(netos_par):>10,}"
                  f"{statistics.median(netos_par):>11.1f}")
        inf.w("")
        inf.w("Los 10 momentos más rentables:")
        inf.w(f"{'Timestamp UTC':<21}{'Compro':<9}{'Vendo':<9}{'Bruto':>8}"
              f"{'Costo':>8}{'Neto':>8}{'Size':>10}")
        inf.w("-" * 78)
        for o in sorted(ops, key=lambda x: -x["neto"])[:10]:
            inf.w(f"{str(o['ts']):<21}{o['compra']:<9}{o['vende']:<9}"
                  f"{o['bruto']:>8.1f}{o['costo']:>8.0f}{o['neto']:>8.1f}"
                  f"{o['size']:>10,}")
        inf.w("")
        inf.w("Size = tamaño de la punta más chica del circuito. Una oportunidad")
        inf.w("de 40 bps sobre 12 nominales no es un negocio.")
    else:
        inf.w("")
        inf.w("Ninguna. Con estos costos y este filtro, el circuito no cierra.")
    inf.w("")
    return ops


def main():
    ap = argparse.ArgumentParser(description="Censo de desvíos entre dólares implícitos.")
    ap.add_argument("--dias", type=int, default=2)
    ap.add_argument("--moneda", default="USD_MEP", choices=["USD_MEP", "USD_CCL"])
    ap.add_argument("--frescura", type=int, default=60)
    ap.add_argument("--grilla", type=int, default=10)
    ap.add_argument("--legs", type=int, default=4,
                    help="operaciones del circuito completo (default 4)")
    ap.add_argument("--derechos", type=float, default=1.0,
                    help="derechos de mercado en bps por operación (default 1.0 = 0,010%%)")
    ap.add_argument("--comision-cero", action="store_true",
                    help="escenario broker sin comisión (Cocos): solo derechos")
    ap.add_argument("--min-ticks", type=int, default=500,
                    help="actividad mínima por pata para participar (default 500)")
    ap.add_argument("--min-size", type=int, default=0,
                    help="tamaño mínimo de punta para contar una oportunidad")
    ap.add_argument("--barrido", action="store_true",
                    help="corre varios umbrales de liquidez y compara")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    sello = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dir_logs = RAIZ_PROYECTO / "logs"
    dir_logs.mkdir(exist_ok=True)
    ruta_informe = dir_logs / f"censo_desvios_{sello}.txt"
    ruta_csv = dir_logs / f"censo_desvios_{sello}.csv"

    inf = Informe(ruta_informe)
    try:
        inf.w("=" * 78)
        inf.w("CENSO DE DESVÍOS — Argo")
        inf.w("=" * 78)
        inf.w(f"Corrida         : {datetime.now(timezone.utc).isoformat()} UTC")
        eventos, actividad = cargar_series(args, inf)

        if args.barrido:
            barrido(args, inf, eventos, actividad)

        permitidos = {k for k, v in actividad.items() if v >= args.min_ticks}
        if len(permitidos) < 2:
            inf.w(f"Con min-ticks={args.min_ticks} quedan {len(permitidos)} "
                  f"instrumentos: no alcanza para comparar. Bajá el umbral.")
            inf.w("FIN OK")
            inf.cerrar()
            return
        res = recorrer(args, eventos, permitidos)
        ops = reportar(args, inf, res, permitidos)

        with open(ruta_csv, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ts_utc", "compro_usd_en", "vendo_usd_en", "plazo",
                        "bruto_bps", "costo_bps", "neto_bps", "size_limitante"])
            for o in ops:
                w.writerow([o["ts"], o["compra"], o["vende"], o["plazo"],
                            round(o["bruto"], 2), round(o["costo"], 1),
                            round(o["neto"], 2), o["size"]])
        inf.w(f"CSV: {ruta_csv}")
        inf.w("FIN OK")
    except Exception:
        inf.w("")
        inf.w("!! EL CENSO FALLÓ — traceback completo abajo")
        inf.w(traceback.format_exc())
        inf.cerrar()
        print(f"\nInforme parcial guardado en: {ruta_informe}", file=sys.stderr)
        sys.exit(1)
    inf.cerrar()
    print(f"\nInforme: {ruta_informe}")


if __name__ == "__main__":
    main()
