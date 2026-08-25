"""
Analizador de pares — la herramienta de descarte de Argo.

=== PARA QUÉ ===
`censo_desvios.py` responde "¿hay algo?" tomando el mejor de ~120 instrumentos.
Ese diseño tiene un sesgo conocido: el extremo de muchas series ruidosas es
positivo aunque no haya edge. Este script hace lo contrario: mide **pares
fijos, sin elegir**, y trae tres diagnósticos que separan un edge real de un
artefacto de medición.

=== LOS TRES DIAGNÓSTICOS ===

1. FRESCURA — el que decide.
   Si el desvío es real, no debería depender de qué tan viejas son las puntas.
   Si es un artefacto de rezago (un libro repreció y el otro todavía no), el
   desvío CRECE con la antigüedad de las puntas y tiende a cero cuando las
   dos acaban de actualizarse.
   La tabla de frescura muestra el desvío mediano por tramo de antigüedad.
   Si baja hacia cero a medida que las puntas son más nuevas, no hay negocio:
   estábamos midiendo el retraso entre dos libros, no una diferencia de precio.

2. DURACIÓN DE EPISODIOS.
   Un desvío que dura 10 segundos no es operable con cuatro patas manuales.
   Se agrupan los momentos consecutivos por encima del costo en episodios y
   se reporta cuánto duran. Un edge de 20 bps que vive 10 segundos vale cero.

3. CAPACIDAD.
   El tamaño de la punta más chica del circuito. Un desvío enorme sobre 12
   nominales no es un negocio, es una curiosidad.

=== MODOS ===
    --par AL30,GD30        un par fijo, análisis completo
    --todos                escanea todos los pares líquidos y los rankea

=== HIGIENE ===
    --recorte N            saca los primeros y últimos N minutos de la rueda
                           (la subasta de apertura y cierre tiene precios
                           anchos que no representan al mercado)
    --excluir BA.C,X       saca tickers no confiables (BA.C sigue sin
                           identificar: no se usan sus datos para análisis)

=== CÓMO SE CORRE ===
    source /home/argo/argo/.venv/bin/activate
    cd /home/argo/argo
    python scripts/analizar_par.py --dias 2 --par AL30,GD30
    python scripts/analizar_par.py --dias 2 --todos --min-ticks 2000
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

# Tramos de antigüedad de puntas para el diagnóstico de frescura, en segundos.
TRAMOS_FRESCURA = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 999)]


def comision_bps(tipo, comision_cero):
    if comision_cero:
        return 0.0
    if (tipo or "").lower() in TIPOS_RENTA_FIJA:
        return COMISION_BPS["renta_fija"]
    return COMISION_BPS["default"]


def costo_circuito(tipo_a, tipo_b, legs, derechos, comision_cero):
    com = legs * (comision_bps(tipo_a, comision_cero)
                  + comision_bps(tipo_b, comision_cero)) / 2.0
    return com + legs * derechos


def cargar_ruta_base(ruta_override=None):
    if ruta_override:
        return Path(ruta_override)
    config = json.loads(
        (RAIZ_PROYECTO / "config" / "config.json").read_text(encoding="utf-8")
    )
    return RAIZ_PROYECTO / config["paths"]["database"]


def a_dt(texto):
    try:
        return datetime.strptime(texto[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def pct(ordenados, p):
    if not ordenados:
        return 0.0
    return ordenados[int(round((len(ordenados) - 1) * p))]


class Informe:
    def __init__(self, ruta):
        self.ruta = ruta
        self.fh = open(ruta, "w", encoding="utf-8")

    def w(self, linea=""):
        print(linea)
        self.fh.write(linea + "\n")
        self.fh.flush()

    def cerrar(self):
        self.fh.close()


def cargar(args, inf):
    """Trae ticks de instrumentos con las dos patas y arma la grilla temporal."""
    ruta_base = cargar_ruta_base(args.db)
    inf.w(f"Base            : {ruta_base}")
    if not ruta_base.exists():
        raise FileNotFoundError(f"No existe la base en {ruta_base}")

    desde = (datetime.now(timezone.utc) - timedelta(days=args.dias)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    excluidos = {t.strip().upper() for t in args.excluir.split(",") if t.strip()}
    inf.w(f"Ventana         : últimos {args.dias} día(s) → desde {desde} UTC")
    inf.w(f"Moneda dólar    : {args.moneda}")
    inf.w(f"Grilla          : cada {args.grilla} s · frescura máx {args.frescura} s")
    inf.w(f"Recorte         : {args.recorte} min al abrir y al cerrar")
    inf.w(f"Excluidos       : {', '.join(sorted(excluidos)) or '(ninguno)'}")
    inf.w("")

    con = sqlite3.connect(f"file:{ruta_base}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    sql_pares = """
        SELECT i.ticker, i.tipo, m.plazo,
               MAX(CASE WHEN m.moneda_liquidacion='ARS' THEN m.id END) AS id_ars,
               MAX(CASE WHEN m.moneda_liquidacion=? THEN m.id END) AS id_usd
        FROM instrumento_broker_mapping m
        JOIN instrumentos i ON i.id = m.instrumento_id
        WHERE m.activo = 1 AND i.tipo != 'subyacente_us'
        GROUP BY i.ticker, i.tipo, m.plazo
        HAVING id_ars IS NOT NULL AND id_usd IS NOT NULL
    """
    # Se excluye subyacente_us por la misma regla que usa el poblador de
    # mapeos: un símbolo de Primary nunca es el subyacente de Estados Unidos.
    # Sin esto, un ticker como MU o INTC entra dos veces (CEDEAR argentino y
    # subyacente) y el escaneo compara un instrumento contra sí mismo.
    mapping_a_par = {}
    for r in con.execute(sql_pares, (args.moneda,)):
        if r["ticker"].upper() in excluidos:
            continue
        mapping_a_par[r["id_ars"]] = (r["ticker"], r["plazo"], r["tipo"], "ARS")
        mapping_a_par[r["id_usd"]] = (r["ticker"], r["plazo"], r["tipo"], "USD")
    if not mapping_a_par:
        raise RuntimeError("Ningún ticker utilizable. Nada que medir.")

    ids = ",".join(str(i) for i in mapping_a_par)
    sql = f"""
        SELECT mapping_id, ts_mensaje, bid_price, bid_size, offer_price, offer_size
        FROM ticks_crudos
        WHERE ts_mensaje >= ? AND mapping_id IN ({ids})
          AND bid_price > 0 AND offer_price > 0 AND offer_price >= bid_price
        ORDER BY ts_mensaje
    """
    eventos, conteos = [], {}
    for r in con.execute(sql, (desde,)):
        ts = a_dt(r["ts_mensaje"])
        if ts is None:
            continue
        clave = mapping_a_par[r["mapping_id"]]
        eventos.append((ts, clave, r["bid_price"], r["bid_size"],
                        r["offer_price"], r["offer_size"]))
        k = (clave[0], clave[1], clave[3])
        conteos[k] = conteos.get(k, 0) + 1
    con.close()

    if not eventos:
        raise RuntimeError("Sin ticks utilizables en la ventana.")
    inf.w(f"Ticks utilizables : {len(eventos):,}")
    inf.w(f"Rango             : {eventos[0][0]} → {eventos[-1][0]} UTC")

    actividad = {}
    for (ticker, plazo, lado), n in conteos.items():
        k = (ticker, plazo)
        actividad[k] = min(actividad.get(k, n), n)

    # Recorte de bordes de rueda, por día.
    por_dia = {}
    for ts, *_ in eventos:
        d = ts.date()
        lo, hi = por_dia.get(d, (ts, ts))
        por_dia[d] = (min(lo, ts), max(hi, ts))
    ventanas = {d: (lo + timedelta(minutes=args.recorte),
                    hi - timedelta(minutes=args.recorte))
                for d, (lo, hi) in por_dia.items()}
    for d, (lo, hi) in sorted(ventanas.items()):
        inf.w(f"Rueda {d}    : se analiza {lo.time()} → {hi.time()} UTC")
    inf.w("")
    return eventos, actividad, ventanas


def construir_grilla(args, eventos, ventanas, permitidos):
    """
    Muestrea el estado del mercado en una grilla temporal regular.

    Devuelve (momentos, series) donde series[(ticker,plazo)] es una lista
    paralela a `momentos` con dict o None. Precalcular esto hace que evaluar
    N pares sea barato: se recorre el tiempo UNA sola vez.
    """
    estado = {}
    momentos, series = [], {k: [] for k in permitidos}
    frescura = timedelta(seconds=args.frescura)
    paso = timedelta(seconds=args.grilla)
    corte = eventos[0][0] + paso
    fin = eventos[-1][0]
    i, n = 0, len(eventos)

    while corte <= fin:
        while i < n and eventos[i][0] <= corte:
            ts, (ticker, plazo, tipo, lado), bid, bsz, offer, osz = eventos[i]
            i += 1
            if (ticker, plazo) in permitidos:
                estado.setdefault((ticker, plazo), {"tipo": tipo})[lado] = (
                    ts, bid, bsz, offer, osz)

        lo, hi = ventanas.get(corte.date(), (None, None))
        dentro = lo is not None and lo <= corte <= hi
        if dentro:
            momentos.append(corte)
            for k in permitidos:
                d = estado.get(k)
                v = None
                if d:
                    a, u = d.get("ARS"), d.get("USD")
                    if a and u and (corte - a[0]) <= frescura and (corte - u[0]) <= frescura:
                        v = {
                            "tipo": d["tipo"],
                            "fx_compra": a[3] / u[1],
                            "fx_venta": a[1] / u[3],
                            "size_compra": min(a[4] or 0, u[2] or 0),
                            "size_venta": min(a[2] or 0, u[4] or 0),
                            "edad": max((corte - a[0]).total_seconds(),
                                        (corte - u[0]).total_seconds()),
                        }
                series[k].append(v)
        corte += paso
    return momentos, series


def evaluar_par(args, momentos, series, ka, kb):
    """
    Mide el desvío entre DOS instrumentos fijos. Sin elegir, sin extremos.

    En cada momento se evalúan las dos direcciones posibles del circuito y se
    toma la mejor. Son dos opciones, no ciento veinte: el sesgo del extremo
    acá es despreciable y además refleja la decisión real (el circuito se
    puede recorrer para cualquiera de los dos lados).
    """
    sa, sb = series[ka], series[kb]
    filas = []
    for idx, ts in enumerate(momentos):
        a, b = sa[idx], sb[idx]
        if not a or not b:
            continue
        d1 = (b["fx_venta"] / a["fx_compra"] - 1) * 10000
        d2 = (a["fx_venta"] / b["fx_compra"] - 1) * 10000
        if d1 >= d2:
            desvio, size = d1, min(a["size_compra"], b["size_venta"])
            compro, vendo = ka[0], kb[0]
        else:
            desvio, size = d2, min(b["size_compra"], a["size_venta"])
            compro, vendo = kb[0], ka[0]
        filas.append({"ts": ts, "idx": idx, "desvio": desvio, "size": size,
                      "edad": max(a["edad"], b["edad"]),
                      "compro": compro, "vendo": vendo,
                      "tipo_a": a["tipo"], "tipo_b": b["tipo"]})
    return filas


def episodios(filas, costo, grilla):
    """Agrupa momentos consecutivos por encima del costo en episodios."""
    eps, actual = [], None
    for f in filas:
        if f["desvio"] - costo > 0:
            if actual and f["idx"] == actual["fin_idx"] + 1:
                actual["fin_idx"] = f["idx"]
                actual["max"] = max(actual["max"], f["desvio"])
                actual["size"] = max(actual["size"], f["size"])
            else:
                if actual:
                    eps.append(actual)
                actual = {"ts": f["ts"], "ini_idx": f["idx"], "fin_idx": f["idx"],
                          "max": f["desvio"], "size": f["size"],
                          "compro": f["compro"], "vendo": f["vendo"]}
        # Un momento por debajo del costo corta el episodio.
    if actual:
        eps.append(actual)
    for e in eps:
        e["segundos"] = (e["fin_idx"] - e["ini_idx"] + 1) * grilla
    return eps


def tabla_frescura(inf, filas, etiqueta):
    """EL diagnóstico: ¿el desvío depende de la antigüedad de las puntas?"""
    inf.w("=" * 78)
    inf.w(f"DIAGNÓSTICO DE FRESCURA — {etiqueta}")
    inf.w("=" * 78)
    inf.w("Si el desvío CRECE con la antigüedad de las puntas, no es una")
    inf.w("diferencia de precio: es el retraso de un libro contra el otro.")
    inf.w("")
    inf.w(f"{'Antigüedad':<16}{'Momentos':>10}{'Mediana':>10}{'p90':>10}{'Máximo':>10}")
    inf.w("-" * 78)
    for lo, hi in TRAMOS_FRESCURA:
        sub = sorted(f["desvio"] for f in filas if lo <= f["edad"] < hi)
        if not sub:
            inf.w(f"{f'{lo}-{hi} s':<16}{0:>10}         —         —         —")
            continue
        inf.w(f"{f'{lo}-{hi} s':<16}{len(sub):>10,}{statistics.median(sub):>10.1f}"
              f"{pct(sub,0.90):>10.1f}{sub[-1]:>10.1f}")
    inf.w("")


def reportar_par(args, inf, filas, ka, kb):
    if not filas:
        inf.w("Sin momentos comparables para este par.")
        return []
    tipo_a, tipo_b = filas[0]["tipo_a"], filas[0]["tipo_b"]
    costo_bind = costo_circuito(tipo_a, tipo_b, args.legs, args.derechos, False)
    costo_cocos = costo_circuito(tipo_a, tipo_b, args.legs, args.derechos, True)
    d = sorted(f["desvio"] for f in filas)

    inf.w("=" * 78)
    inf.w(f"PAR FIJO: {ka[0]} ({ka[1]}) vs {kb[0]} ({kb[1]})")
    inf.w("=" * 78)
    inf.w(f"Momentos comparables : {len(filas):,}")
    inf.w("")
    inf.w("Desvío disponible (bps, puntas ejecutables, sin selección):")
    for etq, p in [("mínimo", 0.0), ("p25", 0.25), ("mediana", 0.5),
                   ("p75", 0.75), ("p90", 0.90), ("p99", 0.99), ("máximo", 1.0)]:
        inf.w(f"  {etq:<10}: {pct(d, p):>10.1f}")
    inf.w("")
    inf.w(f"Costo del circuito   : {costo_bind:.1f} bps (BIND) · "
          f"{costo_cocos:.1f} bps (comisión cero)")
    for etiqueta, costo in [("BIND", costo_bind), ("comisión cero", costo_cocos)]:
        arriba = [f for f in filas if f["desvio"] > costo]
        eps = episodios(filas, costo, args.grilla)
        inf.w("")
        inf.w(f"--- Escenario {etiqueta} (costo {costo:.1f} bps) ---")
        inf.w(f"  Momentos rentables : {len(arriba):,} de {len(filas):,} "
              f"({100.0*len(arriba)/len(filas):.2f} %)")
        if eps:
            dur = sorted(e["segundos"] for e in eps)
            inf.w(f"  Episodios          : {len(eps)}")
            inf.w(f"  Duración mediana   : {statistics.median(dur):.0f} s "
                  f"(máx {dur[-1]:.0f} s)")
            inf.w(f"  Neto mediano       : "
                  f"{statistics.median([f['desvio']-costo for f in arriba]):.1f} bps")
            inf.w(f"  Tamaño mediano     : "
                  f"{statistics.median([e['size'] for e in eps]):.0f}")
    inf.w("")
    tabla_frescura(inf, filas, f"{ka[0]} vs {kb[0]}")
    return filas


def escanear_todos(args, inf, momentos, series, permitidos):
    """Rankea todos los pares posibles. La versión sistemática del análisis."""
    claves = sorted(permitidos)
    inf.w("=" * 78)
    inf.w(f"ESCANEO DE TODOS LOS PARES — {len(claves)} instrumentos, "
          f"{len(claves)*(len(claves)-1)//2:,} pares")
    inf.w("=" * 78)
    resultados = []
    for i in range(len(claves)):
        for j in range(i + 1, len(claves)):
            ka, kb = claves[i], claves[j]
            # Por default NO se cruzan plazos. El dólar implícito de CI y el de
            # 24hs difieren por la tasa entre fechas de liquidación: comparar
            # uno contra otro mide financiamiento, no un desvío arbitrable.
            if ka[1] != kb[1] and not args.cruzar_plazos:
                continue
            filas = evaluar_par(args, momentos, series, ka, kb)
            if len(filas) < args.min_momentos:
                continue
            tipo_a, tipo_b = filas[0]["tipo_a"], filas[0]["tipo_b"]
            costo = costo_circuito(tipo_a, tipo_b, args.legs, args.derechos,
                                   args.comision_cero)
            d = sorted(f["desvio"] for f in filas)
            eps = episodios(filas, costo, args.grilla)
            arriba = [f for f in filas if f["desvio"] > costo]
            # Correlación gruesa con la antigüedad: mediana del desvío con
            # puntas muy frescas vs con puntas viejas. Si la segunda es mucho
            # mayor, el par vive de rezago.
            frescos = sorted(f["desvio"] for f in filas if f["edad"] < 5)
            viejos = sorted(f["desvio"] for f in filas if f["edad"] >= 30)
            plazo_etq = ka[1] if ka[1] == kb[1] else f"{ka[1]}/{kb[1]}"
            resultados.append({
                "par": f"{ka[0]}/{kb[0]}", "plazo": plazo_etq,
                "momentos": len(filas), "mediana": statistics.median(d),
                "p90": pct(d, 0.90), "costo": costo,
                "pct_rentable": 100.0 * len(arriba) / len(filas),
                "episodios": len(eps),
                "dur_mediana": statistics.median([e["segundos"] for e in eps]) if eps else 0,
                "size_mediano": statistics.median([e["size"] for e in eps]) if eps else 0,
                "med_frescos": statistics.median(frescos) if frescos else None,
                "med_viejos": statistics.median(viejos) if viejos else None,
            })
    resultados.sort(key=lambda r: -r["pct_rentable"])
    inf.w(f"Pares con al menos {args.min_momentos} momentos: {len(resultados):,}")
    inf.w("")
    inf.w(f"{'Par':<16}{'Plz':<6}{'Mom.':>7}{'Med':>7}{'p90':>7}{'Costo':>7}"
          f"{'%rent':>7}{'Epis':>6}{'Dur s':>7}{'Frsc':>7}{'Viej':>7}")
    inf.w("-" * 78)
    for r in resultados[: args.top]:
        mf = f"{r['med_frescos']:.1f}" if r["med_frescos"] is not None else "—"
        mv = f"{r['med_viejos']:.1f}" if r["med_viejos"] is not None else "—"
        inf.w(f"{r['par']:<16}{r['plazo']:<6}{r['momentos']:>7,}{r['mediana']:>7.1f}"
              f"{r['p90']:>7.1f}{r['costo']:>7.0f}{r['pct_rentable']:>7.1f}"
              f"{r['episodios']:>6}{r['dur_mediana']:>7.0f}{mf:>7}{mv:>7}")
    inf.w("")
    inf.w("Frsc = desvío mediano con puntas de menos de 5 s. Viej = con puntas")
    inf.w("de más de 30 s. Si Viej >> Frsc, el par vive del rezago entre libros")
    inf.w("y no de una diferencia de precio aprovechable.")
    inf.w("")
    return resultados


def main():
    ap = argparse.ArgumentParser(description="Analizador de pares de Argo.")
    ap.add_argument("--dias", type=int, default=2)
    ap.add_argument("--moneda", default="USD_MEP", choices=["USD_MEP", "USD_CCL"])
    ap.add_argument("--par", default=None,
                    help="dos tickers separados por coma, ej. AL30,GD30")
    ap.add_argument("--plazo", default="24hs", help="plazo del par fijo (default 24hs)")
    ap.add_argument("--todos", action="store_true", help="escanear todos los pares")
    ap.add_argument("--frescura", type=int, default=60)
    ap.add_argument("--grilla", type=int, default=10)
    ap.add_argument("--recorte", type=int, default=15,
                    help="minutos a descartar al abrir y al cerrar (default 15)")
    ap.add_argument("--excluir", default="BA.C",
                    help="tickers a excluir, separados por coma (default BA.C)")
    ap.add_argument("--min-ticks", type=int, default=2000)
    ap.add_argument("--min-momentos", type=int, default=200)
    ap.add_argument("--legs", type=int, default=4)
    ap.add_argument("--derechos", type=float, default=1.0)
    ap.add_argument("--comision-cero", action="store_true")
    ap.add_argument("--cruzar-plazos", action="store_true",
                    help="permitir pares CI vs 24hs (default no: mide tasa, no desvío)")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    sello = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dir_logs = RAIZ_PROYECTO / "logs"
    dir_logs.mkdir(exist_ok=True)
    ruta_informe = dir_logs / f"analizar_par_{sello}.txt"
    ruta_csv = dir_logs / f"analizar_par_{sello}.csv"

    inf = Informe(ruta_informe)
    try:
        inf.w("=" * 78)
        inf.w("ANALIZADOR DE PARES — Argo")
        inf.w("=" * 78)
        inf.w(f"Corrida         : {datetime.now(timezone.utc).isoformat()} UTC")
        eventos, actividad, ventanas = cargar(args, inf)

        permitidos = {k for k, v in actividad.items() if v >= args.min_ticks}
        inf.w(f"Instrumentos con ≥{args.min_ticks} ticks por pata: {len(permitidos)}")
        if args.par:
            a, b = [t.strip().upper() for t in args.par.split(",")]
            permitidos |= {(a, args.plazo), (b, args.plazo)}
        if len(permitidos) < 2:
            raise RuntimeError("Menos de 2 instrumentos: bajá --min-ticks.")
        inf.w("")

        momentos, series = construir_grilla(args, eventos, ventanas, permitidos)
        inf.w(f"Momentos en la grilla (post recorte): {len(momentos):,}")
        inf.w("")

        resultados = []
        if args.par:
            a, b = [t.strip().upper() for t in args.par.split(",")]
            ka, kb = (a, args.plazo), (b, args.plazo)
            for k in (ka, kb):
                if k not in series:
                    raise RuntimeError(
                        f"{k[0]} en plazo {k[1]} no tiene datos con las dos patas.")
            filas = evaluar_par(args, momentos, series, ka, kb)
            reportar_par(args, inf, filas, ka, kb)

        if args.todos:
            resultados = escanear_todos(args, inf, momentos, series, permitidos)
            with open(ruta_csv, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["par", "plazo", "momentos", "mediana_bps", "p90_bps",
                            "costo_bps", "pct_rentable", "episodios",
                            "dur_mediana_s", "size_mediano",
                            "mediana_puntas_frescas", "mediana_puntas_viejas"])
                for r in resultados:
                    w.writerow([r["par"], r["plazo"], r["momentos"],
                                round(r["mediana"], 2), round(r["p90"], 2),
                                round(r["costo"], 1), round(r["pct_rentable"], 2),
                                r["episodios"], round(r["dur_mediana"], 0),
                                round(r["size_mediano"], 0),
                                round(r["med_frescos"], 2) if r["med_frescos"] is not None else "",
                                round(r["med_viejos"], 2) if r["med_viejos"] is not None else ""])
            inf.w(f"CSV: {ruta_csv}")
        inf.w("FIN OK")
    except Exception:
        inf.w("")
        inf.w("!! EL ANÁLISIS FALLÓ — traceback completo abajo")
        inf.w(traceback.format_exc())
        inf.cerrar()
        print(f"\nInforme parcial guardado en: {ruta_informe}", file=sys.stderr)
        sys.exit(1)
    inf.cerrar()
    print(f"\nInforme: {ruta_informe}")


if __name__ == "__main__":
    main()
