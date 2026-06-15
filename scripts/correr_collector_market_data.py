"""
Entrypoint del collector de market data (v2 — robustez: reconexión + horario).

Corre el collector en primer plano. Conecta a BIND, suscribe los símbolos
(muestra por defecto, universo completo con --completo), parsea cada mensaje
y persiste a ticks_crudos por tandas vía un hilo escritor separado.

Uso (con venv activo):
    python scripts/correr_collector_market_data.py             # muestra (6)
    python scripts/correr_collector_market_data.py --completo  # los 366

Cierre ordenado (flush final de lo encolado + join del escritor + resumen):
  - Ctrl+C (SIGINT) cuando se corre a mano.
  - SIGTERM cuando lo para systemd (systemctl stop / restart). Sin este manejo,
    Python ante un SIGTERM crudo termina el proceso SIN ejecutar el bloque
    finally de iniciar(), y se perdería la última tanda encolada. El handler de
    abajo traduce SIGTERM al mismo camino de cierre que Ctrl+C.
"""
import argparse
import signal
import sys
from pathlib import Path

# Inyección de la raíz del proyecto al sys.path (convención del proyecto,
# ref. heartbeat.py): permite 'from src...' corriendo el script suelto.
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

from src.collectors.market_data_collector import ColectorMarketData


def _manejar_sigterm(signum, frame):
    """
    systemd para los servicios con SIGTERM, no con Ctrl+C. Python, de fábrica,
    ante un SIGTERM crudo termina el proceso SIN correr los bloques finally
    (se perdería el flush final del collector). Convertimos SIGTERM en
    KeyboardInterrupt para reusar el camino de cierre ordenado que iniciar() ya
    maneja (idéntico al de Ctrl+C): flush de lo encolado + join del hilo
    escritor + resumen de la corrida.
    """
    raise KeyboardInterrupt()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collector de market data Primary/BIND (v2)."
    )
    parser.add_argument(
        "--completo",
        action="store_true",
        help="Suscribir el universo completo (366). Default: muestra (6).",
    )
    args = parser.parse_args()

    # Cierre ordenado ante SIGTERM (systemd). SIGINT (Ctrl+C) ya lo maneja
    # Python de fábrica como KeyboardInterrupt; registrar SIGTERM acá es inocuo
    # en Windows (donde no se usa para parar el proceso) y necesario en el VPS
    # Linux, donde el service lo para con SIGTERM en cada stop/restart.
    signal.signal(signal.SIGTERM, _manejar_sigterm)

    collector = ColectorMarketData(usar_universo_completo=args.completo)
    collector.iniciar()


if __name__ == "__main__":
    main()
