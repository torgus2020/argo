"""
Entrypoint del collector de market data (v0 — validación de mecánica).

Corre el collector en primer plano. En v0 NO escribe a la base: conecta a
BIND, suscribe una muestra de símbolos y loguea los mensajes crudos para
validar (a) la mecánica de conexión/WS/suscripción y (b) que el símbolo que
pushea Primary matchea el symbol_externo guardado.

Uso (con venv activo):
    python scripts/correr_collector_market_data.py             # muestra (v0)
    python scripts/correr_collector_market_data.py --completo  # los 366 (v1+)

Cortar con Ctrl+C: cierra el WebSocket limpio y reporta el resumen.
"""

import argparse
import sys
from pathlib import Path

# Inyección de la raíz del proyecto al sys.path (convención del proyecto,
# ref. heartbeat.py): permite 'from src...' corriendo el script suelto.
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

from src.collectors.market_data_collector import ColectorMarketData


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collector de market data Primary/BIND (v0)."
    )
    parser.add_argument(
        "--completo",
        action="store_true",
        help="Suscribir el universo completo (366). Default: muestra v0.",
    )
    args = parser.parse_args()

    collector = ColectorMarketData(usar_universo_completo=args.completo)
    collector.iniciar()


if __name__ == "__main__":
    main()