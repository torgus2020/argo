"""
Punto de entrada del roadmap engine para ejecución vía systemd timer.

Lo invoca el systemd timer una vez al día a las 09:00 hora Buenos Aires.
Su función es delegar al engine y devolver el código de salida correcto
para que systemd registre el estado de la corrida.

Diseño minimalista: cualquier complejidad real vive en src/roadmap/engine.py.
Este script solo hace de adaptador entre systemd y la lógica del proyecto.
"""

import sys
from pathlib import Path

# Agregar la raíz del proyecto al sys.path para poder importar src/
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

from src.roadmap.engine import ejecutar


def main() -> int:
    """
    Ejecuta una corrida del engine y devuelve código de salida.

    Devuelve 0 si todo OK (incluso si no hubo nada que reportar).
    Devuelve 1 si hubo algún error en la carga, validación o envío.

    El código de salida queda registrado en el journal de systemd y
    puede usarse para detectar corridas fallidas.
    """
    exito = ejecutar()
    return 0 if exito else 1


if __name__ == "__main__":
    sys.exit(main())