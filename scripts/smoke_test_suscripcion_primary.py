"""
Smoke test de suscripción a market data de Primary/BIND.

Qué prueba, y qué NO prueba
---------------------------
Prueba UNA sola cosa: si el servidor ACEPTA o RECHAZA el mensaje de suscripción
con el universo activo. Nada más.

No prueba que entren datos. Son dos fallas distintas y hay que poder
distinguirlas, porque el incidente 2026-06-24 → 2026-08-21 fue exactamente
una falla del primer tipo disfrazada de "todo bien": el service vivo, el log
prolijo, la conexión establecida, y el mensaje de suscripción rechazado entero
a los 2 segundos.

Por eso este test sirve con el mercado CERRADO: el rechazo del batch ocurre
igual, no depende de que haya rueda. Con mercado abierto además vas a ver
mensajes de market data llegando, que es información extra pero no el objetivo.

Cómo leer el resultado
----------------------
    VEREDICTO: SUSCRIPCION ACEPTADA  -> el servidor no rechazó el batch.
    VEREDICTO: SUSCRIPCION RECHAZADA -> hay al menos un símbolo inválido.

OJO con el rechazo: Primary nombra UN símbolo en el `description`, pero ese es
el primero que encontró, no necesariamente el único. Para la lista completa se
usa scripts/validar_universo_vs_catalogo.py, que cruza contra el catálogo vivo.

Uso
---
    # universo activo, un solo mensaje (replica exactamente lo que hace hoy
    # el collector en market_data_collector.py:510):
    python scripts/smoke_test_suscripcion_primary.py

    # incluir tambien los símbolos desactivados (activo=0): sirve para
    # RE-REPRODUCIR la falla después de haberla arreglado, y así confirmar
    # que este test efectivamente detecta rechazos.
    python scripts/smoke_test_suscripcion_primary.py --incluir-inactivos

    # suscribir en lotes de N (prueba del fix arquitectónico pendiente):
    python scripts/smoke_test_suscripcion_primary.py --lote 20

    # ventana de escucha en segundos (default 20):
    python scripts/smoke_test_suscripcion_primary.py --espera 30

SEGURIDAD: read-only absoluto. Este script solo abre WebSocket y se suscribe a
market data. No importa, no referencia y no puede llamar ninguna función de
envío de órdenes. No escribe en la base.

Conviene detener el collector antes de correrlo, para no tener dos WebSockets
del mismo usuario compitiendo:
    sudo systemctl stop argo-collector
    ... correr el test ...
    sudo systemctl start argo-collector
"""

import argparse
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Convención del proyecto: inyectar la raíz al sys.path antes de importar src.
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))

import pyRofex  # noqa: E402
from src.collectors.primary_conexion import (  # noqa: E402
    conectar_primary_produccion,
    ErrorConexionPrimary,
)

RUTA_DB = RAIZ_PROYECTO / "data" / "argo.sqlite"
DIR_LOGS = RAIZ_PROYECTO / "logs"

# Mismos entries que usa el collector productivo: si el test pidiera otra cosa,
# no estaría probando lo que realmente pasa en producción.
_ENTRIES = [
    pyRofex.MarketDataEntry.BIDS,
    pyRofex.MarketDataEntry.OFFERS,
    pyRofex.MarketDataEntry.LAST,
]


class Informe:
    """Escritor con volcado inmediato: si el proceso muere, el rastro ya está."""

    def __init__(self, ruta: Path):
        self.ruta = ruta
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(ruta, "w", encoding="utf-8")
        self._lock = threading.Lock()

    def p(self, txt="") -> None:
        linea = str(txt)
        # Los handlers de pyRofex corren en el hilo del WebSocket: sin lock,
        # dos líneas pueden entreverarse en el archivo.
        with self._lock:
            print(linea)
            self._f.write(linea + "\n")
            self._f.flush()

    def cerrar(self) -> None:
        try:
            self._f.close()
        except Exception:  # noqa: BLE001
            pass


class Contadores:
    """Estado compartido entre el hilo principal y el del WebSocket."""

    def __init__(self):
        self.lock = threading.Lock()
        self.errores = []          # mensajes de ERROR del servidor
        self.excepciones = []      # excepciones del cliente
        self.mensajes_md = 0       # mensajes de market data recibidos
        self.simbolos_vistos = set()


def leer_universo(incluir_inactivos: bool) -> list:
    """
    Lee los símbolos tal como los lee el collector.

    Replica la consulta de market_data_collector.py (broker='primary',
    activo=True). Si el test leyera de otra forma, probaría un universo que
    no es el que se suscribe en producción — y no probaría nada útil.
    """
    if not RUTA_DB.exists():
        raise SystemExit(f"No existe la base en {RUTA_DB}")
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row
    filtro = "" if incluir_inactivos else " AND activo = 1"
    filas = con.execute(
        f"""
        SELECT symbol_externo, activo
          FROM instrumento_broker_mapping
         WHERE broker = 'primary'{filtro}
         ORDER BY symbol_externo
        """
    ).fetchall()
    con.close()
    return [dict(f) for f in filas]


def main(inf: Informe, args: argparse.Namespace) -> int:
    cont = Contadores()

    inf.p("=" * 78)
    inf.p("SMOKE TEST DE SUSCRIPCION A MARKET DATA - PRIMARY/BIND")
    inf.p(f"UTC   : {datetime.now(timezone.utc).isoformat()}")
    inf.p(f"Base  : {RUTA_DB}")
    inf.p(f"Modo  : {'lotes de ' + str(args.lote) if args.lote else 'UN SOLO MENSAJE (como el collector hoy)'}")
    inf.p(f"Espera: {args.espera} s")
    inf.p("=" * 78)

    inf.p("")
    inf.p("[1] Universo a suscribir")
    universo = leer_universo(args.incluir_inactivos)
    simbolos = [u["symbol_externo"] for u in universo]
    if args.incluir_inactivos:
        n_inact = sum(1 for u in universo if not u["activo"])
        inf.p(f"    {len(simbolos)} símbolos ({n_inact} de ellos con activo=0, incluidos a propósito).")
    else:
        inf.p(f"    {len(simbolos)} símbolos activos.")
    if not simbolos:
        inf.p("    No hay símbolos que suscribir. Abortando.")
        return 1

    inf.p("")
    inf.p("[2] Conectando a produccion BIND")
    info = conectar_primary_produccion()
    inf.p(f"    cuenta autenticada: {info['cuenta']}  |  host: {info['host_rest']}")

    # --- Handlers. Corren en el hilo del WebSocket. ---
    def on_market_data(mensaje):
        with cont.lock:
            cont.mensajes_md += 1
            try:
                sym = mensaje.get("instrumentId", {}).get("symbol")
                if sym:
                    cont.simbolos_vistos.add(sym)
            except Exception:  # noqa: BLE001
                pass

    def on_error(mensaje):
        with cont.lock:
            cont.errores.append(mensaje)
        # Se loguea solo la descripción, no el payload entero: cuando Primary
        # rechaza un batch devuelve los N símbolos completos, y eso inunda el
        # log sin agregar nada (fue justamente lo que hizo ilegible el log del
        # incidente).
        desc = None
        if isinstance(mensaje, dict):
            desc = mensaje.get("description") or mensaje.get("message")
        inf.p(f"    !! ERROR del servidor: {desc if desc else str(mensaje)[:200]}")

    def on_exception(e):
        with cont.lock:
            cont.excepciones.append(str(e))
        inf.p(f"    !! EXCEPCION del cliente: {type(e).__name__}: {e}")

    inf.p("")
    inf.p("[3] Abriendo WebSocket")
    pyRofex.init_websocket_connection(
        market_data_handler=on_market_data,
        error_handler=on_error,
        exception_handler=on_exception,
    )
    inf.p("    WebSocket abierto.")

    inf.p("")
    inf.p("[4] Enviando suscripcion")
    if args.lote and args.lote > 0:
        lotes = [simbolos[i:i + args.lote] for i in range(0, len(simbolos), args.lote)]
        inf.p(f"    {len(lotes)} lotes de hasta {args.lote} símbolos.")
        for n, lote in enumerate(lotes, start=1):
            pyRofex.market_data_subscription(tickers=lote, entries=_ENTRIES)
            inf.p(f"    lote {n}/{len(lotes)} enviado ({len(lote)} símbolos): "
                  f"{lote[0]} ... {lote[-1]}")
            time.sleep(0.25)   # no atropellar al servidor
    else:
        pyRofex.market_data_subscription(tickers=simbolos, entries=_ENTRIES)
        inf.p(f"    un único mensaje con {len(simbolos)} símbolos.")

    inf.p("")
    inf.p(f"[5] Escuchando {args.espera} segundos")
    inf.p("    (el rechazo del batch, si lo hay, llega en 1-3 segundos)")
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.espera:
        time.sleep(1)
        transcurrido = int(time.monotonic() - t0)
        if transcurrido % 5 == 0:
            with cont.lock:
                inf.p(f"    t={transcurrido:>3}s  errores={len(cont.errores)}  "
                      f"mensajes_md={cont.mensajes_md}  símbolos_distintos={len(cont.simbolos_vistos)}")

    try:
        pyRofex.close_websocket_connection()
    except Exception:  # noqa: BLE001
        pass

    # --- Veredicto ---
    inf.p("")
    inf.p("=" * 78)
    with cont.lock:
        n_err = len(cont.errores)
        n_exc = len(cont.excepciones)
        n_md = cont.mensajes_md
        n_sym = len(cont.simbolos_vistos)

    if n_err == 0 and n_exc == 0:
        inf.p("VEREDICTO: SUSCRIPCION ACEPTADA")
        inf.p(f"  El servidor no rechazó el mensaje. {len(simbolos)} símbolos suscriptos.")
        codigo = 0
    else:
        inf.p("VEREDICTO: SUSCRIPCION RECHAZADA")
        inf.p(f"  {n_err} error(es) del servidor, {n_exc} excepción(es) del cliente.")
        inf.p("  RECORDATORIO: el 'description' nombra el PRIMER símbolo inválido")
        inf.p("  que el servidor encontró, NO necesariamente el único. Para la")
        inf.p("  lista completa: scripts/validar_universo_vs_catalogo.py")
        codigo = 2

    inf.p("")
    inf.p(f"  mensajes de market data recibidos : {n_md}")
    inf.p(f"  símbolos distintos con dato       : {n_sym} de {len(simbolos)}")
    if n_md == 0:
        inf.p("  (0 mensajes con la rueda cerrada es NORMAL y no invalida el veredicto:")
        inf.p("   este test mide si la suscripción fue aceptada, no si hay datos.)")
    inf.p("=" * 78)
    return codigo


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lote", type=int, default=0,
                    help="Suscribir en lotes de N símbolos. 0 = un solo mensaje (default, "
                         "replica el comportamiento actual del collector).")
    ap.add_argument("--espera", type=int, default=20,
                    help="Segundos de escucha antes de emitir el veredicto (default 20).")
    ap.add_argument("--incluir-inactivos", action="store_true",
                    help="Incluir tambien los símbolos con activo=0 (para reproducir "
                         "una falla ya corregida y confirmar que el test la detecta).")
    _args = ap.parse_args()

    _stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _ruta_log = DIR_LOGS / f"smoke_suscripcion_{_stamp}.txt"
    _inf = Informe(_ruta_log)

    try:
        _codigo = main(_inf, _args)
    except ErrorConexionPrimary as e:
        _inf.p("")
        _inf.p("!! ERROR DE CONEXION CONTRA PRIMARY/BIND")
        _inf.p(f"   {e}")
        _inf.p(traceback.format_exc())
        _codigo = 3
    except SystemExit as e:
        _inf.p("")
        _inf.p(f"!! ABORTADO: {e}")
        _codigo = int(e.code) if isinstance(e.code, int) else 1
    except Exception:  # noqa: BLE001
        _inf.p("")
        _inf.p("!! EXCEPCION NO PREVISTA")
        _inf.p(traceback.format_exc())
        _codigo = 4
    finally:
        _inf.p("")
        _inf.p(f"Informe: {_ruta_log}")
        _inf.cerrar()

    sys.exit(_codigo)
