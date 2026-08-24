"""
Censo de spreads sobre ticks_crudos.

=== PARA QUÉ ===
El spread bid-ask es lo que decide si una estrategia de arbitraje local es
viable o es ruido. La comisión de Gus (10 bps bonos / 25 bps acciones por
operación) es conocida y chica; el spread NO está medido y pesa más. Este
script mide el spread real por instrumento, en puntos básicos, y lo compara
contra el umbral de costos para responder una pregunta concreta:

    ¿qué desvío mínimo de precio tiene que existir para que un par
    (mismo ticker en ARS vs su variante en dólares) deje plata?

=== POR QUÉ EN PUNTOS BÁSICOS ===
Los precios de bonos vienen en escala cruda (×100: AL30 a 94060 = 940,60).
El spread RELATIVO (diferencia sobre el punto medio) es invariante a esa
escala, así que no hace falta convertir nada y no se puede errar por ahí.
    spread_bps = (offer - bid) / ((offer + bid) / 2) * 10.000

=== CÓMO SE CORRE (VPS) ===
    source /home/argo/argo/.venv/bin/activate
    cd /home/argo/argo
    python scripts/censo_spreads.py --dias 5

Sin dependencias del proyecto (solo stdlib): corre igual en el VPS, donde no
está el CLI de sqlite3, sin arrastrar SQLAlchemy ni el resto de src/.

=== DISCIPLINA DE DIAGNÓSTICO ===
El informe se abre ANTES de trabajar y se escribe con flush inmediato: si el
script muere a mitad de camino, el diagnóstico parcial queda en disco (que es
justo el caso en que hace falta). Toda excepción vuelca traceback completo.
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

# Comisiones reales de Gus, POR OPERACIÓN (tarifa negociada con BIND).
# Se paga una sola vez si se compra y se vende el mismo instrumento en el día.
# OJO: un par usa DOS instrumentos distintos → dos comisiones.
COMISION_BPS = {"renta_fija": 10.0, "default": 25.0}

# Qué cuenta como renta fija a efectos de comisión. Acá se define por INCLUSIÓN
# (al revés que la regla de FK, que define por exclusión) y a propósito: si
# aparece un tipo nuevo que no está en la lista, cae en el default de 25 bps.
# Ante la duda, el costo se sobreestima. Un umbral demasiado exigente descarta
# oportunidades; uno demasiado barato hace operar a pérdida.
TIPOS_RENTA_FIJA = {"bono_usd_ar", "bopreal", "boncer", "lecap", "boncap", "on"}

# Un spread mayor a esto casi seguro es dato basura o punta fantasma, no
# mercado: se reporta aparte en vez de contaminar la mediana en silencio.
SPREAD_ABSURDO_BPS = 5000.0  # 50 %

# Buenos Aires es UTC-3 fijo (sin horario de verano).
OFFSET_LOCAL_HORAS = -3


def cargar_ruta_base(ruta_override=None) -> Path:
    """Resuelve la ruta de la base desde config/config.json (o la que se pase)."""
    if ruta_override:
        return Path(ruta_override)
    config = json.loads(
        (RAIZ_PROYECTO / "config" / "config.json").read_text(encoding="utf-8")
    )
    return RAIZ_PROYECTO / config["paths"]["database"]


def corte_utc(dias: int) -> str:
    """Devuelve el timestamp de corte como texto, comparable contra la columna.

    La base guarda UTC en texto ISO ('YYYY-MM-DD HH:MM:SS...'), que es
    lexicográficamente ordenable: comparar como string es correcto y evita
    los adaptadores datetime de sqlite3 (deprecados en Python 3.12).
    """
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    return desde.strftime("%Y-%m-%d %H:%M:%S")


def hora_local(ts_texto: str):
    """Extrae la hora local (-03) de un timestamp UTC en texto. None si no parsea."""
    try:
        hora_utc = int(ts_texto[11:13])
    except (ValueError, IndexError):
        return None
    return (hora_utc + OFFSET_LOCAL_HORAS) % 24


def mediana(valores):
    return statistics.median(valores) if valores else None


def percentil(valores_ordenados, p: float):
    """Percentil simple por índice. Los valores deben venir ordenados."""
    if not valores_ordenados:
        return None
    idx = int(round((len(valores_ordenados) - 1) * p))
    return valores_ordenados[idx]


def comision_bps(tipo: str) -> float:
    """Comisión por operación según el tipo de instrumento. Default = el caro."""
    if (tipo or "").lower() in TIPOS_RENTA_FIJA:
        return COMISION_BPS["renta_fija"]
    return COMISION_BPS["default"]


class Informe:
    """Escritor que vuelca a archivo Y a pantalla, con flush inmediato."""

    def __init__(self, ruta: Path):
        self.ruta = ruta
        self.fh = open(ruta, "w", encoding="utf-8")

    def w(self, linea: str = ""):
        print(linea)
        self.fh.write(linea + "\n")
        self.fh.flush()

    def cerrar(self):
        self.fh.close()


def censar(args, inf: Informe) -> dict:
    """Recorre los ticks y arma las estadísticas por instrumento."""
    ruta_base = cargar_ruta_base(args.db)
    inf.w(f"Base            : {ruta_base}")
    if not ruta_base.exists():
        raise FileNotFoundError(f"No existe la base en {ruta_base}")
    inf.w(f"Tamaño          : {ruta_base.stat().st_size / 1e9:.2f} GB")

    desde = corte_utc(args.dias)
    inf.w(f"Ventana         : últimos {args.dias} día(s) → desde {desde} UTC")
    inf.w(f"Mínimo de ticks : {args.min_ticks} por instrumento para entrar al censo")
    inf.w("")

    con = sqlite3.connect(f"file:{ruta_base}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # Contexto general antes de entrar en detalle: si esto viene vacío o raro,
    # no tiene sentido leer el resto del informe.
    fila = con.execute(
        "SELECT COUNT(*) AS n, MIN(ts_mensaje) AS desde, MAX(ts_mensaje) AS hasta "
        "FROM ticks_crudos WHERE ts_mensaje >= ?",
        (desde,),
    ).fetchone()
    inf.w("=" * 78)
    inf.w("COBERTURA")
    inf.w("=" * 78)
    inf.w(f"Ticks en ventana: {fila['n']:,}")
    inf.w(f"Primer tick     : {fila['desde']} UTC")
    inf.w(f"Último tick     : {fila['hasta']} UTC")
    if not fila["n"]:
        raise RuntimeError("No hay ticks en la ventana pedida. Nada que censar.")

    por_dia = con.execute(
        "SELECT substr(ts_mensaje,1,10) AS dia, COUNT(*) AS n, "
        "       COUNT(DISTINCT mapping_id) AS simbolos "
        "FROM ticks_crudos WHERE ts_mensaje >= ? GROUP BY dia ORDER BY dia",
        (desde,),
    ).fetchall()
    inf.w("")
    inf.w(f"{'Día (UTC)':<12} {'Ticks':>12} {'Símbolos':>10}")
    for d in por_dia:
        inf.w(f"{d['dia']:<12} {d['n']:>12,} {d['simbolos']:>10}")
    inf.w("")

    # --- Recorrido principal -------------------------------------------------
    # Se streamea: no se traen 130k+ filas a memoria de una.
    sql = """
        SELECT t.mapping_id, t.ts_mensaje, t.bid_price, t.bid_size,
               t.offer_price, t.offer_size,
               m.symbol_externo, m.moneda_liquidacion, m.plazo,
               i.ticker, i.tipo
        FROM ticks_crudos t
        JOIN instrumento_broker_mapping m ON m.id = t.mapping_id
        JOIN instrumentos i ON i.id = m.instrumento_id
        WHERE t.ts_mensaje >= ?
    """
    acc = {}
    descartes = {"cruzado": 0, "absurdo": 0, "sin_precio": 0}

    for r in con.execute(sql, (desde,)):
        clave = r["mapping_id"]
        d = acc.get(clave)
        if d is None:
            d = acc[clave] = {
                "ticker": r["ticker"],
                "tipo": r["tipo"],
                "symbol": r["symbol_externo"],
                "moneda": r["moneda_liquidacion"],
                "plazo": r["plazo"],
                "n_filas": 0,
                "n_ambas": 0,
                "n_solo_bid": 0,
                "n_solo_offer": 0,
                "n_ninguna": 0,
                "spreads": [],
                "por_hora": {},
                "bid_sizes": [],
                "offer_sizes": [],
                "mids": [],
            }
        d["n_filas"] += 1

        bid, offer = r["bid_price"], r["offer_price"]
        # Presencia de puntas. OJO en la interpretación: un mensaje puede traer
        # solo el lado que cambió, así que "faltó el bid" NO prueba que no
        # hubiera bid. Se mide igual porque la proporción distingue las dos
        # hipótesis: si casi todas las filas traen ambas, el feed manda
        # snapshot completo y la ausencia sí es informativa.
        if bid and offer:
            d["n_ambas"] += 1
        elif bid:
            d["n_solo_bid"] += 1
        elif offer:
            d["n_solo_offer"] += 1
        else:
            d["n_ninguna"] += 1
            descartes["sin_precio"] += 1
            continue

        if not (bid and offer):
            continue
        if bid <= 0 or offer <= 0:
            descartes["sin_precio"] += 1
            continue
        if offer < bid:
            # Mercado cruzado: o es dato malo, o son dos actualizaciones
            # desfasadas. No se promedia con lo sano.
            descartes["cruzado"] += 1
            continue

        mid = (offer + bid) / 2.0
        spread = (offer - bid) / mid * 10000.0
        if spread > SPREAD_ABSURDO_BPS:
            descartes["absurdo"] += 1
            continue

        d["spreads"].append(spread)
        d["mids"].append(mid)
        if r["bid_size"]:
            d["bid_sizes"].append(r["bid_size"])
        if r["offer_size"]:
            d["offer_sizes"].append(r["offer_size"])
        h = hora_local(r["ts_mensaje"])
        if h is not None:
            d["por_hora"].setdefault(h, []).append(spread)

    con.close()
    return {"acc": acc, "descartes": descartes}


def reportar(args, inf: Informe, datos: dict):
    acc, descartes = datos["acc"], datos["descartes"]

    inf.w("=" * 78)
    inf.w("DESCARTES (se reportan, no se esconden)")
    inf.w("=" * 78)
    inf.w(f"Filas sin ninguna punta usable : {descartes['sin_precio']:,}")
    inf.w(f"Mercado cruzado (offer < bid)  : {descartes['cruzado']:,}")
    inf.w(f"Spread absurdo (> {SPREAD_ABSURDO_BPS:.0f} bps)  : {descartes['absurdo']:,}")
    inf.w("")

    filas = []
    for d in acc.values():
        if len(d["spreads"]) < args.min_ticks:
            continue
        s = sorted(d["spreads"])
        filas.append(
            {
                "ticker": d["ticker"],
                "tipo": d["tipo"],
                "moneda": d["moneda"],
                "plazo": d["plazo"],
                "symbol": d["symbol"],
                "n_filas": d["n_filas"],
                "n_spreads": len(s),
                "pct_ambas": 100.0 * d["n_ambas"] / d["n_filas"],
                "p25": percentil(s, 0.25),
                "mediana": mediana(s),
                "p75": percentil(s, 0.75),
                "mid_mediano": mediana(d["mids"]),
                "bid_size_med": mediana(d["bid_sizes"]),
                "offer_size_med": mediana(d["offer_sizes"]),
                "por_hora": d["por_hora"],
            }
        )
    filas.sort(key=lambda f: f["mediana"])

    inf.w("=" * 78)
    inf.w("CENSO POR INSTRUMENTO — spread en puntos básicos (1 bp = 0,01 %)")
    inf.w("=" * 78)
    inf.w(
        f"{'Ticker':<10}{'Mon':<9}{'Plz':<6}{'Ticks':>8}{'2 ptas':>8}"
        f"{'p25':>9}{'MEDIANA':>10}{'p75':>9}"
    )
    inf.w("-" * 78)
    for f in filas[: args.top]:
        inf.w(
            f"{f['ticker']:<10}{f['moneda']:<9}{f['plazo']:<6}{f['n_spreads']:>8,}"
            f"{f['pct_ambas']:>7.0f}%{f['p25']:>9.1f}{f['mediana']:>10.1f}{f['p75']:>9.1f}"
        )
    if len(filas) > args.top:
        inf.w(f"... y {len(filas) - args.top} instrumentos más (ver el CSV).")
    inf.w("")

    # --- Pares ARS vs dólar: el número que decide la estrategia --------------
    # Un par es el MISMO ticker y el MISMO plazo en dos monedas de liquidación.
    # Es la versión ejecutable de pairs_cedear: exposición a la acción = cero,
    # las dos patas en BIND.
    por_clave = {}
    for f in filas:
        por_clave[(f["ticker"], f["plazo"], f["moneda"])] = f

    pares = []
    for (ticker, plazo, moneda), f in por_clave.items():
        if moneda != "ARS":
            continue
        for moneda_usd in ("USD_MEP", "USD_CCL"):
            g = por_clave.get((ticker, plazo, moneda_usd))
            if not g:
                continue
            com = comision_bps(f["tipo"]) + comision_bps(g["tipo"])
            spread_total = f["mediana"] + g["mediana"]
            pares.append(
                {
                    "ticker": ticker,
                    "plazo": plazo,
                    "par": f"ARS/{moneda_usd.replace('USD_', '')}",
                    "tipo": f["tipo"],
                    "spread_ars": f["mediana"],
                    "spread_usd": g["mediana"],
                    "comision": com,
                    "be_agresivo": spread_total + com,
                    "be_pasivo": g["mediana"] + com,
                }
            )
    pares.sort(key=lambda p: p["be_agresivo"])

    inf.w("=" * 78)
    inf.w("UMBRAL POR PAR — cuánto tiene que desviarse el precio para no perder")
    inf.w("=" * 78)
    inf.w("be agresivo = cruzo las dos puntas | be pasivo = pongo precio en una pata")
    inf.w("")
    inf.w(
        f"{'Ticker':<10}{'Par':<10}{'Plz':<6}{'sprARS':>9}{'sprUSD':>9}"
        f"{'Com':>7}{'BE agr':>9}{'BE pas':>9}"
    )
    inf.w("-" * 78)
    for p in pares[: args.top]:
        inf.w(
            f"{p['ticker']:<10}{p['par']:<10}{p['plazo']:<6}{p['spread_ars']:>9.1f}"
            f"{p['spread_usd']:>9.1f}{p['comision']:>7.0f}"
            f"{p['be_agresivo']:>9.1f}{p['be_pasivo']:>9.1f}"
        )
    if not pares:
        inf.w("(sin pares completos en la ventana — falta data de alguna pata)")
    inf.w("")

    # --- Spread por hora: ¿hay un momento del día que sea el bueno? ----------
    inf.w("=" * 78)
    inf.w("SPREAD MEDIANO POR HORA (-03) — top instrumentos más líquidos")
    inf.w("=" * 78)
    horas = list(range(11, 18))
    inf.w(f"{'Ticker':<9}{'Mon':<9}{'Plz':<6}" + "".join(f"{h:>7}h" for h in horas))
    inf.w("-" * 78)
    for f in filas[: min(15, args.top)]:
        celdas = ""
        for h in horas:
            v = f["por_hora"].get(h)
            celdas += f"{mediana(v):>7.0f} " if v else f"{'-':>7} "
        inf.w(f"{f['ticker']:<9}{f['moneda']:<9}{f['plazo']:<6}{celdas}")
    inf.w("")

    return filas, pares


def escribir_csv(ruta: Path, filas, pares):
    with open(ruta, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["seccion", "ticker", "tipo", "moneda", "plazo", "n_spreads",
             "pct_ambas_puntas", "p25_bps", "mediana_bps", "p75_bps",
             "mid_mediano", "bid_size_med", "offer_size_med"]
        )
        for f in filas:
            w.writerow(
                ["instrumento", f["ticker"], f["tipo"], f["moneda"], f["plazo"],
                 f["n_spreads"], round(f["pct_ambas"], 1), round(f["p25"], 2),
                 round(f["mediana"], 2), round(f["p75"], 2),
                 f["mid_mediano"], f["bid_size_med"], f["offer_size_med"]]
            )
        w.writerow([])
        w.writerow(
            ["seccion", "ticker", "par", "plazo", "tipo", "spread_ars_bps",
             "spread_usd_bps", "comision_bps", "breakeven_agresivo_bps",
             "breakeven_pasivo_bps"]
        )
        for p in pares:
            w.writerow(
                ["par", p["ticker"], p["par"], p["plazo"], p["tipo"],
                 round(p["spread_ars"], 2), round(p["spread_usd"], 2),
                 p["comision"], round(p["be_agresivo"], 2),
                 round(p["be_pasivo"], 2)]
            )


def main():
    ap = argparse.ArgumentParser(description="Censo de spreads sobre ticks_crudos.")
    ap.add_argument("--dias", type=int, default=5, help="ventana hacia atrás (default 5)")
    ap.add_argument("--min-ticks", type=int, default=50,
                    help="mínimo de spreads válidos para entrar al censo (default 50)")
    ap.add_argument("--top", type=int, default=40, help="filas a mostrar (default 40)")
    ap.add_argument("--db", default=None, help="ruta alternativa a la base")
    args = ap.parse_args()

    sello = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dir_logs = RAIZ_PROYECTO / "logs"
    dir_logs.mkdir(exist_ok=True)
    ruta_informe = dir_logs / f"censo_spreads_{sello}.txt"
    ruta_csv = dir_logs / f"censo_spreads_{sello}.csv"

    inf = Informe(ruta_informe)
    try:
        inf.w("=" * 78)
        inf.w("CENSO DE SPREADS — Argo")
        inf.w("=" * 78)
        inf.w(f"Corrida         : {datetime.now(timezone.utc).isoformat()} UTC")
        inf.w(f"Comisiones      : renta fija {COMISION_BPS['renta_fija']:.0f} bps · "
              f"resto {COMISION_BPS['default']:.0f} bps (por operación)")
        inf.w("")
        datos = censar(args, inf)
        filas, pares = reportar(args, inf, datos)
        escribir_csv(ruta_csv, filas, pares)
        inf.w(f"CSV con el detalle completo: {ruta_csv}")
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
