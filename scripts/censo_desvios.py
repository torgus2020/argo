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

Si el dólar implícito de AL30 es más barato que el de GD30, el circuito
termina con más pesos de los que empezó. Cuatro operaciones, cuatro comisiones.

=== POR QUÉ SE USAN PRECIOS EJECUTABLES Y NO EL PUNTO MEDIO ===
Para comprar se paga la PUNTA VENDEDORA; para vender se cobra la PUNTA
COMPRADORA. Midiendo con puntas ejecutables, el spread ya está DENTRO del
número y no hay que sumarlo aparte (sumarlo sería contarlo dos veces).
Usar el punto medio infla la oportunidad exactamente en un spread — es el
error clásico que hace que un backtest de arbitraje parezca genial.

    fx_compra_usd = offer_ARS / bid_USD    (pesos que pago por cada dólar)
    fx_venta_usd  = bid_ARS  / offer_USD   (pesos que cobro por cada dólar)

Oportunidad = existe un instrumento A donde compro dólares barato y otro B
distinto donde los vendo caro, y la diferencia supera las comisiones.

=== LA TRAMPA QUE ESTE SCRIPT EVITA ===
Los ticks son ASINCRÓNICOS. Comparar AL30 de las 11:00:03 con GD30 de las
11:04:12 inventa desvíos que nunca existieron: es comparar un precio vivo
con uno viejo. Por eso se muestrea sobre una grilla temporal y se descarta
toda punta con más de --frescura segundos de antigüedad. Bajar esa exigencia
"encuentra" más oportunidades; todas falsas.

=== ESCALA DE PRECIOS ===
No hace falta convertir el ×100 de los bonos: las dos patas del mismo
instrumento vienen en la misma escala, así que el cociente ya es el tipo de
cambio verdadero. Vale igual para bonos y para CEDEARs.

=== CÓMO SE CORRE ===
    source /home/argo/argo/.venv/bin/activate
    cd /home/argo/argo
    python scripts/censo_desvios.py --dias 3
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
# oportunidad de 300 %. Se reporta aparte.
BANDA_CORDURA = 0.20  # ±20 %


def comision_bps(tipo: str) -> float:
    if (tipo or "").lower() in TIPOS_RENTA_FIJA:
        return COMISION_BPS["renta_fija"]
    return COMISION_BPS["default"]


def cargar_ruta_base(ruta_override=None) -> Path:
    if ruta_override:
        return Path(ruta_override)
    config = json.loads(
        (RAIZ_PROYECTO / "config" / "config.json").read_text(encoding="utf-8")
    )
    return RAIZ_PROYECTO / config["paths"]["database"]


def a_dt(texto: str):
    """Parsea el timestamp de texto de la base. None si no parsea."""
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
    inf.w(f"Frescura máxima : {args.frescura} s (puntas más viejas se descartan)")
    inf.w(f"Grilla          : cada {args.grilla} s")
    inf.w(f"Comisiones      : {args.legs} operaciones por circuito")
    inf.w("")

    con = sqlite3.connect(f"file:{ruta_base}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # Solo tickers que tengan LAS DOS patas activas en el mismo plazo: sin las
    # dos no hay tipo de cambio implícito que calcular.
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
    for r in con.execute(sql, (desde,)):
        ts = a_dt(r["ts_mensaje"])
        if ts is None:
            continue
        eventos.append(
            (ts, mapping_a_par[r["mapping_id"]], r["bid_price"], r["bid_size"],
             r["offer_price"], r["offer_size"])
        )
    con.close()
    inf.w(f"Ticks utilizables en la ventana: {len(eventos):,}")
    if not eventos:
        raise RuntimeError("Sin ticks con las dos puntas en la ventana.")
    inf.w(f"Rango           : {eventos[0][0]} → {eventos[-1][0]} UTC")
    inf.w("")
    return eventos


def recorrer(args, inf: Informe, eventos):
    """Camina el tiempo, arma la foto de cada momento y busca el circuito."""
    estado = {}  # (ticker, plazo) -> {'tipo', 'ARS': (...), 'USD': (...)}
    muestras = 0
    con_dos_tickers = 0
    oportunidades = []
    brutos = []
    descartes_cordura = 0

    frescura = timedelta(seconds=args.frescura)
    paso = timedelta(seconds=args.grilla)
    proximo_corte = eventos[0][0] + paso
    i = 0
    n = len(eventos)

    while i < n:
        # Consumir todos los eventos hasta el próximo punto de la grilla.
        while i < n and eventos[i][0] <= proximo_corte:
            ts, (ticker, plazo, tipo, lado), bid, bsz, offer, osz = eventos[i]
            d = estado.setdefault((ticker, plazo), {"tipo": tipo})
            d[lado] = (ts, bid, bsz, offer, osz)
            i += 1

        muestras += 1
        ahora = proximo_corte
        fx = []
        for (ticker, plazo), d in estado.items():
            a, u = d.get("ARS"), d.get("USD")
            if not a or not u:
                continue
            if ahora - a[0] > frescura or ahora - u[0] > frescura:
                continue  # punta vieja: no es un precio, es un recuerdo
            # Comprar dólares: pago la punta vendedora del ARS, cobro la
            # compradora del USD. Vender dólares: al revés.
            fx_compra = a[3] / u[1]
            fx_venta = a[1] / u[3]
            fx.append(
                {
                    "ticker": ticker, "plazo": plazo, "tipo": d["tipo"],
                    "fx_compra": fx_compra, "fx_venta": fx_venta,
                    # Tamaño disponible en la pata más chica (capacidad).
                    "size_compra": min(a[4] or 0, u[2] or 0),
                    "size_venta": min(a[2] or 0, u[4] or 0),
                }
            )

        if len(fx) >= 2:
            con_dos_tickers += 1
            mediana_fx = statistics.median([f["fx_compra"] for f in fx])
            sanos = [
                f for f in fx
                if abs(f["fx_compra"] / mediana_fx - 1) <= BANDA_CORDURA
            ]
            descartes_cordura += len(fx) - len(sanos)

            if len(sanos) >= 2:
                barato = min(sanos, key=lambda f: f["fx_compra"])
                caro = max(sanos, key=lambda f: f["fx_venta"])
                if barato["ticker"] != caro["ticker"]:
                    bruto = (caro["fx_venta"] / barato["fx_compra"] - 1) * 10000
                    brutos.append(bruto)
                    costo = args.legs * (
                        comision_bps(barato["tipo"]) + comision_bps(caro["tipo"])
                    ) / 2.0
                    neto = bruto - costo
                    if neto > 0:
                        oportunidades.append(
                            {
                                "ts": ahora, "compra": barato["ticker"],
                                "vende": caro["ticker"], "plazo": barato["plazo"],
                                "bruto": bruto, "costo": costo, "neto": neto,
                                "size": min(barato["size_compra"], caro["size_venta"]),
                            }
                        )
        proximo_corte += paso

    return {
        "muestras": muestras,
        "con_dos_tickers": con_dos_tickers,
        "oportunidades": oportunidades,
        "brutos": brutos,
        "descartes_cordura": descartes_cordura,
    }


def reportar(args, inf: Informe, res):
    muestras = res["muestras"]
    brutos = sorted(res["brutos"])
    ops = res["oportunidades"]

    inf.w("=" * 78)
    inf.w("COBERTURA DE LA MEDICIÓN")
    inf.w("=" * 78)
    inf.w(f"Momentos evaluados (grilla de {args.grilla}s) : {muestras:,}")
    inf.w(f"Momentos con 2+ tickers frescos              : {res['con_dos_tickers']:,}"
          f" ({100.0*res['con_dos_tickers']/muestras if muestras else 0:.1f} %)")
    inf.w(f"Puntas descartadas por fuera de banda        : {res['descartes_cordura']:,}")
    inf.w("")
    inf.w("Si el porcentaje de momentos con 2+ tickers frescos es bajo, el problema")
    inf.w("no es que no haya oportunidades: es que no hay datos simultáneos para")
    inf.w("verlas. Con pocas ruedas capturadas esto es lo esperable.")
    inf.w("")

    if not brutos:
        inf.w("Sin momentos comparables. No se puede concluir nada todavía.")
        return []

    inf.w("=" * 78)
    inf.w("DESVÍO BRUTO DISPONIBLE (bps, con puntas ejecutables)")
    inf.w("=" * 78)
    inf.w("Ya incluye el spread. Falta restarle las comisiones.")
    inf.w("")
    for etiqueta, p in [("mínimo", 0.0), ("p25", 0.25), ("mediana", 0.5),
                        ("p75", 0.75), ("p90", 0.90), ("p99", 0.99), ("máximo", 1.0)]:
        idx = int(round((len(brutos) - 1) * p))
        inf.w(f"  {etiqueta:<10}: {brutos[idx]:>10.1f} bps")
    inf.w("")

    inf.w("=" * 78)
    inf.w("OPORTUNIDADES NETAS (después de comisiones)")
    inf.w("=" * 78)
    pct = 100.0 * len(ops) / len(brutos)
    inf.w(f"Momentos con ganancia neta > 0 : {len(ops):,} de {len(brutos):,} ({pct:.2f} %)")
    if ops:
        netos = sorted(o["neto"] for o in ops)
        inf.w(f"Neto mediano                   : {statistics.median(netos):.1f} bps")
        inf.w(f"Neto máximo                    : {netos[-1]:.1f} bps")
        inf.w("")
        inf.w("Nota: momentos, NO operaciones. Un desvío que dura 5 minutos aparece")
        inf.w("en muchos momentos consecutivos y es UNA sola oportunidad.")
        inf.w("")

        # Agrupar por par de tickers: dónde está el edge, si es que está.
        por_par = {}
        for o in ops:
            k = (o["compra"], o["vende"], o["plazo"])
            por_par.setdefault(k, []).append(o["neto"])
        inf.w(f"{'Compro USD en':<14}{'Vendo USD en':<14}{'Plz':<6}"
              f"{'Momentos':>10}{'Neto med':>11}")
        inf.w("-" * 78)
        for (c, v, pl), netos_par in sorted(
            por_par.items(), key=lambda x: -len(x[1])
        )[: args.top]:
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
        inf.w("La columna Size es el tamaño de la punta más chica del circuito.")
        inf.w("Una oportunidad de 40 bps sobre 100 nominales no es un negocio.")
    else:
        inf.w("")
        inf.w("Ninguna. Con estos costos y estos datos, el circuito no cierra.")
        inf.w("Eso es un resultado, no una falla: dice que hay que bajar costos,")
        inf.w("mirar otros instrumentos, o abandonar la idea.")
    inf.w("")
    return ops


def main():
    ap = argparse.ArgumentParser(description="Censo de desvíos entre dólares implícitos.")
    ap.add_argument("--dias", type=int, default=3)
    ap.add_argument("--moneda", default="USD_MEP", choices=["USD_MEP", "USD_CCL"])
    ap.add_argument("--frescura", type=int, default=60,
                    help="antigüedad máxima de una punta, en segundos (default 60)")
    ap.add_argument("--grilla", type=int, default=10,
                    help="cada cuántos segundos se toma la foto (default 10)")
    ap.add_argument("--legs", type=int, default=4,
                    help="operaciones por circuito (default 4). Si BIND cobra el "
                         "par como una sola operación, correr con --legs 2.")
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
        eventos = cargar_series(args, inf)
        res = recorrer(args, inf, eventos)
        ops = reportar(args, inf, res)
        with open(ruta_csv, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ts_utc", "compro_usd_en", "vendo_usd_en", "plazo",
                        "bruto_bps", "costo_bps", "neto_bps", "size_limitante"])
            for o in ops:
                w.writerow([o["ts"], o["compra"], o["vende"], o["plazo"],
                            round(o["bruto"], 2), o["costo"], round(o["neto"], 2),
                            o["size"]])
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
