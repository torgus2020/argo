"""
Diagnóstico: cobertura de instrumentos del universo Argo por segmento.

Script EXPLORATORIO y DESCARTABLE. Para cada ticker de muestra, verifica
si aparece en el snapshot bajo el segmento MERV (BYMA, formato largo) o
solo bajo formato corto (MAE / TIVA), o en ninguno.

Objetivo: entender si los instrumentos SIN_COBERTURA del reporte de mapeo
faltan porque reMarkets sandbox no los tiene en MERV, o porque solo se
negocian en MAE.

Uso:
    python scripts/diagnostico_segmentos.py
"""

import json
import sys
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

_DIR_RAW = RAIZ_PROYECTO / "data" / "raw"
_RUTA_UNIVERSE = RAIZ_PROYECTO / "config" / "universe.json"

# Grupos del universe que se mapean a Primary
_GRUPOS = ["bonos_usd_ar", "bonos_pesos_ar", "acciones_panel_lider", "cedears"]


def encontrar_snapshot() -> Path:
    candidatos = sorted(_DIR_RAW.glob("remarkets_snapshot_*.json"))
    if not candidatos:
        raise FileNotFoundError("No hay snapshot en data/raw/")
    return candidatos[-1]


def main() -> int:
    ruta = encontrar_snapshot()
    print(f"Snapshot: {ruta.name}\n")

    with open(ruta, encoding="utf-8") as f:
        instrumentos = json.load(f)["instrumentos"]

    # Set de todos los símbolos del snapshot
    simbolos = {i["instrumentId"]["symbol"] for i in instrumentos}

    with open(_RUTA_UNIVERSE, encoding="utf-8") as f:
        universe = json.load(f)

    # Clasificar cada instrumento del universo
    resumen = {"solo_MERV": 0, "solo_corto": 0, "ambos": 0, "ninguno": 0}
    detalle = []

    for grupo in _GRUPOS:
        for entrada in universe.get(grupo, []):
            ticker = entrada["ticker"]
            # ¿Está en MERV (formato largo)?
            en_merv = any(
                s == f"MERV - XMEV - {ticker} - CI"
                or s == f"MERV - XMEV - {ticker} - 24hs"
                for s in simbolos
            )
            # ¿Está en formato corto (MAE)?
            en_corto = any(
                s.startswith(f"{ticker}/") for s in simbolos
            )

            if en_merv and en_corto:
                categoria = "ambos"
            elif en_merv:
                categoria = "solo_MERV"
            elif en_corto:
                categoria = "solo_corto"
            else:
                categoria = "ninguno"

            resumen[categoria] += 1
            detalle.append((ticker, grupo, categoria))

    # Imprimir resumen
    print("=" * 64)
    print("COBERTURA POR SEGMENTO - RESUMEN")
    print("=" * 64)
    for cat, cant in resumen.items():
        print(f"  {cat:<14} {cant}")
    print(f"  {'TOTAL':<14} {sum(resumen.values())}")

    # Detalle de los que NO están en MERV (el caso problemático)
    print("\n" + "=" * 64)
    print("INSTRUMENTOS QUE NO ESTAN EN MERV (BYMA)")
    print("=" * 64)
    for ticker, grupo, cat in detalle:
        if cat in ("solo_corto", "ninguno"):
            nota = ("solo en MAE/corto" if cat == "solo_corto"
                    else "en ningun segmento")
            print(f"  {ticker:<10} ({grupo:<22}) - {nota}")

    return 0


if __name__ == "__main__":
    sys.exit(main())