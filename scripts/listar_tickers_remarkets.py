"""
Script de validación: busca tickers conocidos de Argo en reMarkets.

Útil para descubrir el formato exacto que usa Primary para identificar
instrumentos del MERVAL (acciones, bonos, CEDEARs argentinos).

Es prueba de concepto, no parte del collector definitivo.

Uso:
    python scripts/listar_tickers_remarkets.py
"""

import json
import sys
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

import pyRofex


_RUTA_SECRETS = RAIZ_PROYECTO / "config" / "secrets.json"

# Tickers de Argo a buscar en reMarkets
TICKERS_A_BUSCAR = ['AL30', 'GD30', 'GGAL', 'AAPL', 'MSFT', 'AE38', 'TX26']


def main() -> int:
    # Cargar credenciales
    with open(_RUTA_SECRETS, encoding="utf-8") as f:
        cred = json.load(f)["primary_remarkets"]

    # Inicializar conexión
    pyRofex.initialize(
        user=cred["user"],
        password=cred["password"],
        account=cred["account"],
        environment=pyRofex.Environment.REMARKET,
    )

    # Obtener todos los instrumentos
    instr = pyRofex.get_all_instruments()
    todos = instr.get("instruments", [])

    print(f"\nBuscando tickers conocidos en {len(todos)} instrumentos disponibles...")
    print("=" * 70)

    for ticker in TICKERS_A_BUSCAR:
        # Buscar todas las ocurrencias del ticker como substring del symbol
        matches = [
            i["instrumentId"]["symbol"]
            for i in todos
            if ticker in i.get("instrumentId", {}).get("symbol", "")
        ]

        print(f"\n  {ticker}:")
        if matches:
            for m in matches[:5]:  # mostrar primeras 5 coincidencias
                print(f"    - {m}")
            if len(matches) > 5:
                print(f"    ... y {len(matches) - 5} más")
        else:
            print(f"    (no encontrado)")

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())