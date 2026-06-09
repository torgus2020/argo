"""
Entrypoint del collector de market data (v1 — captura por tandas).

Corre el collector en primer plano. Conecta a BIND, suscribe los símbolos
(muestra por defecto, universo completo con --completo), parsea cada mensaje
y persiste a ticks_crudos por tandas vía un hilo escritor separado.

Uso (con venv activo):
    python scripts/correr_collector_market_data.py             # muestra
    python scripts/correr_collector_market_data.py --completo  # los 366

Cortar con Ctrl+C: cierra el WebSocket, hace el flush final de lo encolado,
y reporta el resumen de la corrida (recibidos / encolados / persistidos).
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
        description="Collector de market data Primary/BIND (v1)."
    )
    parser.add_argument(
        "--completo",
        action="store_true",
        help="Suscribir el universo completo (366). Default: muestra.",
    )
    args = parser.parse_args()

    collector = ColectorMarketData(usar_universo_completo=args.completo)
    collector.iniciar()


if __name__ == "__main__":
    main()