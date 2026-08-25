"""
Tasa implícita CI vs 24hs — la operación de tesorería.

=== QUÉ ES ESTO, Y QUÉ NO ES ===
Comprar un título en CI (liquida hoy) y venderlo en 24hs (liquida mañana)
significa: **pagás hoy y cobrás mañana**. Eso NO es un arbitraje: es
**prestar plata**. La diferencia de precio entre los dos plazos no es un
desvío que alguien se olvidó de corregir — es la tasa implícita entre fechas
de liquidación, y está ahí a propósito.

Consecuencia directa sobre el criterio de éxito:

> **El punto de comparación NO es cero, es la caución colocadora.**
> La plata se va a estacionar en algún lado igual. La pregunta no es
> "¿da positivo?" sino "¿le gana a la caución, después de costos?".

=== DIRECCIÓN OPERABLE ===
- **Prestar** (comprar CI, vender 24hs): se puede. Lo que comprás liquida
  primero, así que tenés el título para entregar mañana.
- **Tomar** (comprar 24hs, vender CI): es tomar prestado. Requiere tener el
  título o venderlo en descubierto. Se mide y se reporta, pero se marca como
  probablemente NO operable a nivel retail.

=== POR QUÉ EL FIN DE SEMANA CAMBIA TODO ===
Los costos son fijos por operación; la tasa se devenga por día. Un viernes,
CI liquida el viernes y 24hs liquida el lunes: **tres días de tasa con un
solo juego de costos**. El break-even anualizado se divide por tres. Con
fin de semana largo, por cuatro. Por eso los días de liquidación se calculan
contra el calendario real (`config/feriados_byma.json`), nunca se asume 1.

=== COSTOS (confirmados con el operador de BIND, 2026-08-25) ===
Entrada y salida se cobran **×2, no ×4** — y aplica también cuando las dos
patas son del mismo día en plazos distintos.
    comisión  : 0,10 % (10 bps) tanto bonos como CEDEARs
    derechos  : 0,01 % (1 bp) bonos · 0,08 % (8 bps) CEDEARs
    costo total = 2 × (comisión + derechos)

=== MEDICIÓN ===
Se reportan DOS tasas y la distancia entre ellas importa:
- **de mercado** (con punto medio): la tasa que el mercado está pricing.
  Sirve para comparar contra la caución y ver si el mercado es coherente.
- **ejecutable** (comprando en la punta vendedora, vendiendo en la
  compradora): lo que te queda a vos. La diferencia entre las dos ES el
  costo de cruzar los spreads.

=== CÓMO SE CORRE ===
    source /home/argo/argo/.venv/bin/activate
    cd /home/argo/argo
    python scripts/tasa_implicita.py --dias 2 --tasa-caucion 35
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

# Costos por operación, en bps. Confirmados con BIND el 2026-08-25.
COMISION_BPS = 10.0
DERECHOS_BPS = {"renta_fija": 1.0, "default": 8.0}
TIPOS_RENTA_FIJA = {"bono_usd_ar", "bopreal", "boncer", "lecap", "boncap", "on"}
# Entrada + salida. NO son 4 aunque el circuito toque dos plazos distintos.
OPERACIONES = 2

TRAMOS_FRESCURA = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 999)]


def es_renta_fija(tipo):
    return (tipo or "").lower() in TIPOS_RENTA_FIJA


def costo_bps(tipo):
    d = DERECHOS_BPS["renta_fija"] if es_renta_fija(tipo) else DERECHOS_BPS["default"]
    return OPERACIONES * (COMISION_BPS + d)


def cargar_json(nombre):
    return json.loads((RAIZ_PROYECTO / "config" / nombre).read_text(encoding="utf-8"))


def cargar_ruta_base(ruta_override=None):
    if ruta_override:
        return Path(ruta_override)
    return RAIZ_PROYECTO / cargar_json("config.json")["paths"]["database"]


def dias_liquidacion(fecha, feriados):
    """
    Días calendario entre la liquidación de CI (hoy) y la de 24hs (próxima
    rueda). Un jueves normal da 1; un viernes da 3; un viernes antes de
    feriado da 4. Asumir 1 siempre sobrestima la tasa anualizada por un
    factor de hasta cuatro.
    """
    siguiente = fecha + timedelta(days=1)
    while siguiente.weekday() >= 5 or siguiente.isoformat() in feriados:
        siguiente += timedelta(days=1)
    return (siguiente - fecha).days


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
    """Trae ticks de instrumentos que cotizan en CI y en 24hs a la vez."""
    ruta_base = cargar_ruta_base(args.db)
    inf.w(f"Base            : {ruta_base}")
    if not ruta_base.exists():
        raise FileNotFoundError(f"No existe la base en {ruta_base}")

    desde = (datetime.now(timezone.utc) - timedelta(days=args.dias)).strftime(
        "%Y-%m-%d %H:%M:%S")
    excluidos = {t.strip().upper() for t in args.excluir.split(",") if t.strip()}
    inf.w(f"Ventana         : últimos {args.dias} día(s) → desde {desde} UTC")
    inf.w(f"Moneda          : {args.moneda}")
    inf.w(f"Grilla          : cada {args.grilla} s · frescura máx {args.frescura} s")
    inf.w(f"Recorte         : {args.recorte} min al abrir y al cerrar")
    inf.w(f"Costos          : {OPERACIONES} operaciones × "
          f"({COMISION_BPS:.0f} comisión + derechos) → "
          f"{costo_bps('bono_usd_ar'):.0f} bps renta fija · "
          f"{costo_bps('cedear'):.0f} bps resto")
    inf.w(f"Caución de comparación: {args.tasa_caucion:.1f} % TNA")
    inf.w("")

    con = sqlite3.connect(f"file:{ruta_base}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sql = """
        SELECT i.ticker, i.tipo,
               MAX(CASE WHEN m.plazo='CI'   THEN m.id END) AS id_ci,
               MAX(CASE WHEN m.plazo='24hs' THEN m.id END) AS id_24
        FROM instrumento_broker_mapping m
        JOIN instrumentos i ON i.id = m.instrumento_id
        WHERE m.activo = 1 AND i.tipo != 'subyacente_us'
          AND m.moneda_liquidacion = ?
        GROUP BY i.ticker, i.tipo
        HAVING id_ci IS NOT NULL AND id_24 IS NOT NULL
    """
    mapping = {}
    for r in con.execute(sql, (args.moneda,)):
        if r["ticker"].upper() in excluidos:
            continue
        mapping[r["id_ci"]] = (r["ticker"], r["tipo"], "CI")
        mapping[r["id_24"]] = (r["ticker"], r["tipo"], "24hs")
    if not mapping:
        raise RuntimeError(f"Ningún instrumento con CI y 24hs en {args.moneda}.")
    inf.w(f"Instrumentos con CI y 24hs: {len(mapping)//2}")

    ids = ",".join(str(i) for i in mapping)
    q = f"""
        SELECT mapping_id, ts_mensaje, bid_price, bid_size, offer_price, offer_size
        FROM ticks_crudos
        WHERE ts_mensaje >= ? AND mapping_id IN ({ids})
          AND bid_price > 0 AND offer_price > 0 AND offer_price >= bid_price
        ORDER BY ts_mensaje
    """
    eventos, conteos = [], {}
    for r in con.execute(q, (desde,)):
        ts = a_dt(r["ts_mensaje"])
        if ts is None:
            continue
        clave = mapping[r["mapping_id"]]
        eventos.append((ts, clave, r["bid_price"], r["bid_size"],
                        r["offer_price"], r["offer_size"]))
        k = (clave[0], clave[2])
        conteos[k] = conteos.get(k, 0) + 1
    con.close()
    if not eventos:
        raise RuntimeError("Sin ticks utilizables.")

    inf.w(f"Ticks utilizables : {len(eventos):,}")
    inf.w(f"Rango             : {eventos[0][0]} → {eventos[-1][0]} UTC")

    actividad = {}
    for (ticker, plazo), n in conteos.items():
        actividad[ticker] = min(actividad.get(ticker, n), n)

    por_dia = {}
    for ts, *_ in eventos:
        d = ts.date()
        lo, hi = por_dia.get(d, (ts, ts))
        por_dia[d] = (min(lo, ts), max(hi, ts))
    ventanas = {d: (lo + timedelta(minutes=args.recorte),
                    hi - timedelta(minutes=args.recorte))
                for d, (lo, hi) in por_dia.items()}

    feriados = set(cargar_json("feriados_byma.json")["feriados"])
    for d in sorted(ventanas):
        inf.w(f"Rueda {d} : {dias_liquidacion(d, feriados)} día(s) de "
              f"liquidación entre CI y 24hs")
    inf.w("")
    return eventos, actividad, ventanas, feriados


def recorrer(args, eventos, ventanas, feriados, permitidos):
    """Muestrea la grilla y calcula la tasa implícita de cada instrumento."""
    estado = {}
    filas = []
    frescura = timedelta(seconds=args.frescura)
    paso = timedelta(seconds=args.grilla)
    corte = eventos[0][0] + paso
    fin = eventos[-1][0]
    i, n = 0, len(eventos)
    idx = 0

    while corte <= fin:
        while i < n and eventos[i][0] <= corte:
            ts, (ticker, tipo, plazo), bid, bsz, offer, osz = eventos[i]
            i += 1
            if ticker in permitidos:
                estado.setdefault(ticker, {"tipo": tipo})[plazo] = (
                    ts, bid, bsz, offer, osz)

        lo, hi = ventanas.get(corte.date(), (None, None))
        if lo is not None and lo <= corte <= hi:
            dias = dias_liquidacion(corte.date(), feriados)
            for ticker, d in estado.items():
                ci, v24 = d.get("CI"), d.get("24hs")
                if not ci or not v24:
                    continue
                if corte - ci[0] > frescura or corte - v24[0] > frescura:
                    continue
                mid_ci = (ci[1] + ci[3]) / 2.0
                mid_24 = (v24[1] + v24[3]) / 2.0
                if mid_ci <= 0:
                    continue
                # De mercado: lo que el mercado está pricing (punto medio).
                bruto_mercado = (mid_24 / mid_ci - 1) * 10000
                # Ejecutable: compro CI en la punta vendedora, vendo 24hs en
                # la compradora. El spread ya está adentro.
                bruto_ejec = (v24[1] / ci[3] - 1) * 10000
                costo = costo_bps(d["tipo"])
                neto = bruto_ejec - costo
                filas.append({
                    "idx": idx, "ts": corte, "ticker": ticker, "tipo": d["tipo"],
                    "dias": dias,
                    "tna_mercado": bruto_mercado / 10000 * 365 / dias * 100,
                    "tna_ejecutable": bruto_ejec / 10000 * 365 / dias * 100,
                    "tna_neta": neto / 10000 * 365 / dias * 100,
                    "bruto_ejec": bruto_ejec, "neto": neto, "costo": costo,
                    "size": min(ci[4] or 0, v24[2] or 0),
                    "edad": max((corte - ci[0]).total_seconds(),
                                (corte - v24[0]).total_seconds()),
                })
            idx += 1
        corte += paso
    return filas


def reportar(args, inf, filas):
    if not filas:
        inf.w("Sin momentos comparables.")
        return []

    por_ticker = {}
    for f in filas:
        por_ticker.setdefault(f["ticker"], []).append(f)

    resumen = []
    for ticker, fs in por_ticker.items():
        if len(fs) < args.min_momentos:
            continue
        tna_m = sorted(f["tna_mercado"] for f in fs)
        tna_n = sorted(f["tna_neta"] for f in fs)
        gana = [f for f in fs if f["tna_neta"] > args.tasa_caucion]
        resumen.append({
            "ticker": ticker, "tipo": fs[0]["tipo"], "momentos": len(fs),
            "costo": fs[0]["costo"],
            "tna_mercado": statistics.median(tna_m),
            "tna_neta_med": statistics.median(tna_n),
            "tna_neta_p90": pct(tna_n, 0.90),
            "pct_gana_caucion": 100.0 * len(gana) / len(fs),
            "size_med": statistics.median([f["size"] for f in fs]),
        })
    resumen.sort(key=lambda r: -r["tna_neta_med"])

    inf.w("=" * 78)
    inf.w("TASA IMPLÍCITA POR INSTRUMENTO — anualizada (TNA %)")
    inf.w("=" * 78)
    inf.w("mercado = punto medio, lo que el mercado pricea")
    inf.w("neta    = ejecutable menos costos, lo que te queda a vos")
    inf.w(f"Compite contra caución a {args.tasa_caucion:.1f} % TNA")
    inf.w("")
    inf.w(f"{'Ticker':<10}{'Tipo':<14}{'Mom.':>7}{'Costo':>7}{'mercado':>10}"
          f"{'neta med':>10}{'neta p90':>10}{'%gana':>8}{'Size':>9}")
    inf.w("-" * 78)
    for r in resumen[: args.top]:
        inf.w(f"{r['ticker']:<10}{r['tipo']:<14}{r['momentos']:>7,}"
              f"{r['costo']:>7.0f}{r['tna_mercado']:>10.1f}"
              f"{r['tna_neta_med']:>10.1f}{r['tna_neta_p90']:>10.1f}"
              f"{r['pct_gana_caucion']:>8.1f}{r['size_med']:>9.0f}")
    if len(resumen) > args.top:
        inf.w(f"... y {len(resumen)-args.top} instrumentos más (ver el CSV).")
    inf.w("")

    todas_m = sorted(f["tna_mercado"] for f in filas)
    todas_n = sorted(f["tna_neta"] for f in filas)
    inf.w("=" * 78)
    inf.w("VISTA AGREGADA")
    inf.w("=" * 78)
    inf.w(f"Momentos totales            : {len(filas):,}")
    inf.w(f"Días de liquidación         : "
          f"{sorted(set(f['dias'] for f in filas))}")
    inf.w("")
    inf.w("Tasa de MERCADO (punto medio, TNA %):")
    for e, p in [("p10", .10), ("mediana", .50), ("p90", .90)]:
        inf.w(f"  {e:<10}: {pct(todas_m, p):>10.1f}")
    inf.w("")
    inf.w("Tasa NETA ejecutable (TNA %):")
    for e, p in [("p10", .10), ("mediana", .50), ("p90", .90)]:
        inf.w(f"  {e:<10}: {pct(todas_n, p):>10.1f}")
    gana = [f for f in filas if f["tna_neta"] > args.tasa_caucion]
    inf.w("")
    inf.w(f"Momentos que le ganan a la caución ({args.tasa_caucion:.1f} %): "
          f"{len(gana):,} de {len(filas):,} ({100.0*len(gana)/len(filas):.2f} %)")
    inf.w("")
    inf.w("El hueco entre 'mercado' y 'neta' es lo que cuesta cruzar los")
    inf.w("spreads más las comisiones. Si el mercado pricea 40 % y a vos te")
    inf.w("queda 5 %, el negocio se lo lleva la estructura, no vos.")
    inf.w("")

    inf.w("=" * 78)
    inf.w("DIAGNÓSTICO DE FRESCURA")
    inf.w("=" * 78)
    inf.w("CI y 24hs son dos libros distintos: si la tasa crece con la")
    inf.w("antigüedad de las puntas, hay rezago y no tasa.")
    inf.w("")
    inf.w(f"{'Antigüedad':<16}{'Momentos':>10}{'TNA neta med':>15}{'p90':>10}")
    inf.w("-" * 78)
    for lo, hi in TRAMOS_FRESCURA:
        sub = sorted(f["tna_neta"] for f in filas if lo <= f["edad"] < hi)
        if not sub:
            inf.w(f"{f'{lo}-{hi} s':<16}{0:>10}              —         —")
            continue
        inf.w(f"{f'{lo}-{hi} s':<16}{len(sub):>10,}"
              f"{statistics.median(sub):>15.1f}{pct(sub,0.90):>10.1f}")
    inf.w("")
    return resumen


def main():
    ap = argparse.ArgumentParser(description="Tasa implícita CI vs 24hs.")
    ap.add_argument("--dias", type=int, default=2)
    ap.add_argument("--moneda", default="ARS",
                    choices=["ARS", "USD_MEP", "USD_CCL"])
    ap.add_argument("--tasa-caucion", type=float, default=35.0,
                    help="TNA %% de la caución colocadora del momento (default 35)")
    ap.add_argument("--frescura", type=int, default=60)
    ap.add_argument("--grilla", type=int, default=10)
    ap.add_argument("--recorte", type=int, default=15)
    ap.add_argument("--excluir", default="BA.C")
    ap.add_argument("--min-ticks", type=int, default=500)
    ap.add_argument("--min-momentos", type=int, default=100)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    sello = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dir_logs = RAIZ_PROYECTO / "logs"
    dir_logs.mkdir(exist_ok=True)
    ruta_informe = dir_logs / f"tasa_implicita_{sello}.txt"
    ruta_csv = dir_logs / f"tasa_implicita_{sello}.csv"

    inf = Informe(ruta_informe)
    try:
        inf.w("=" * 78)
        inf.w("TASA IMPLÍCITA CI vs 24hs — Argo")
        inf.w("=" * 78)
        inf.w(f"Corrida         : {datetime.now(timezone.utc).isoformat()} UTC")
        eventos, actividad, ventanas, feriados = cargar(args, inf)
        permitidos = {t for t, v in actividad.items() if v >= args.min_ticks}
        inf.w(f"Instrumentos con ≥{args.min_ticks} ticks por plazo: {len(permitidos)}")
        inf.w("")
        if not permitidos:
            raise RuntimeError("Ningún instrumento pasa el filtro. Bajá --min-ticks.")
        filas = recorrer(args, eventos, ventanas, feriados, permitidos)
        resumen = reportar(args, inf, filas)
        with open(ruta_csv, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ticker", "tipo", "momentos", "costo_bps", "tna_mercado",
                        "tna_neta_mediana", "tna_neta_p90", "pct_gana_caucion",
                        "size_mediano"])
            for r in resumen:
                w.writerow([r["ticker"], r["tipo"], r["momentos"],
                            round(r["costo"], 1), round(r["tna_mercado"], 2),
                            round(r["tna_neta_med"], 2), round(r["tna_neta_p90"], 2),
                            round(r["pct_gana_caucion"], 2), round(r["size_med"], 0)])
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
