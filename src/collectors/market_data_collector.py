"""
Collector de market data en tiempo real desde Primary/BIND vía WebSocket.

=== VERSIÓN v1 — CAPTURA POR TANDAS (escribe a ticks_crudos) ===

v0 validó la mecánica y confirmó la forma del mensaje contra producción
(2026-06-08, 4450 msg, 0 sin matchear). v1 agrega la persistencia: lo que
entra por el WebSocket termina en la tabla ticks_crudos.

El patrón es productor / consumidor desacoplado:

    WebSocket (hilo de pyRofex)
          │  callback: parsea + encola   (microsegundos, solo memoria)
          ▼
       [ cola en memoria ]               (cinta transportadora, thread-safe)
          │  escritor (hilo aparte): drena la cola cada N segundos
          ▼
       SQLite  —  UN insert por tanda (~240 filas en una transacción)

Por qué desacoplar:
  - SQLite admite un solo escritor a la vez y cada transacción hace fsync a
    disco. A ~48 msg/seg, escribir fila por fila es 48 viajes al disco por
    segundo. Agrupar en tandas → 1 viaje cada 5s, órdenes de magnitud menos.
  - El callback corre en el hilo del WebSocket. Si se bloqueara esperando el
    disco, frenaría la recepción y Primary podría dropearnos. Por eso el
    callback NUNCA toca disco: parsea, encola y vuelve.

ts_recepcion se estampa en el callback (lo hace el parser con su default
now()), que es el momento real de recepción — NO en el flush de la tanda,
que mediría 5 segundos tarde.

Lo que v1 NO hace (queda para v2): heartbeat 30s, reconexión activa, control
por horario de mercado, systemd service. v1 es solo "que lo recibido llegue
a disco de forma eficiente y sin frenar la recepción".

El cache symbol -> mapping_id se arma SIEMPRE contra la tabla viva (los 366),
re-resuelto al arrancar (principio H1.6: no confiar en ids que traiga un JSON).
"""

import queue
import threading
import time

import pyRofex

from src.collectors.primary_conexion import (
    conectar_primary_produccion,
    ErrorConexionPrimary,
)
from src.collectors.parser_market_data import parsear_tick, ErrorParseoTick
from src.utils.db import get_session
from src.utils.logger import obtener_logger_collector
from src.utils.models import InstrumentoBrokerMapping


log = obtener_logger_collector("market_data")

# Entries que pedimos en la suscripción: punta compradora (BI), punta
# vendedora (OF) y última operación (LA). depth queda en 1 (default) → L1.
_ENTRIES = [
    pyRofex.MarketDataEntry.BIDS,
    pyRofex.MarketDataEntry.OFFERS,
    pyRofex.MarketDataEntry.LAST,
]

# Cada cuántos mensajes recibidos logueamos una línea de progreso. En v0 se
# logueaba CADA mensaje (ese era el punto: leer la forma); en v1, con captura
# sostenida, eso sería un diluvio ilegible. Solo progreso periódico + avisos
# de anomalías (symbol sin match, error de parseo, error de escritura).
_LOG_CADA_N = 500

# Muestra de símbolos para la PRIMERA validación de v1: lista corta y líquida.
# Misma muestra que v0. La idea es probar el camino de escritura (parsear →
# encolar → grabar → fila en la base) con volumen bajo antes de soltar los 366.
# Una vez validado el escritor, se corre con usar_universo_completo=True.
_MUESTRA_V1 = [
    "MERV - XMEV - AL30 - CI",    # bono soberano ARS (flagship, hiperlíquido)
    "MERV - XMEV - AL30D - CI",   # mismo bono, variante USD MEP
    "MERV - XMEV - GD30 - CI",    # bono soberano ARS
    "MERV - XMEV - GGAL - CI",    # acción líder (Grupo Galicia)
    "MERV - XMEV - AAPL - CI",    # CEDEAR ARS (Apple)
    "MERV - XMEV - MELID - CI",   # CEDEAR USD MEP (MercadoLibre)
]


class ColectorMarketData:
    """
    Collector de market data v1. Conecta, suscribe, recibe, parsea, y persiste
    a ticks_crudos por tandas vía un hilo escritor separado.
    """

    def __init__(
        self,
        usar_universo_completo: bool = False,
        intervalo_flush_seg: float = 5.0,
    ):
        self._log = log
        self._corriendo = False

        # Cache symbol_externo -> mapping_id, re-resuelto contra la tabla viva.
        self._cache_symbol_a_mapping: dict[str, int] = {}
        self._simbolos_suscriptos: list[str] = []
        self._usar_universo_completo = usar_universo_completo

        # --- Maquinaria del escritor por tandas ---
        # La cola: cinta transportadora entre el callback (productor) y el
        # escritor (consumidor). queue.Queue es thread-safe de fábrica: maneja
        # el candado entre los dos hilos sin que tengamos que tocarlo a mano.
        self._cola: "queue.Queue" = queue.Queue()
        # Cada cuánto el escritor vacía la cola a disco.
        self._intervalo_flush_seg = intervalo_flush_seg
        # Señal de corte para el escritor (la prende detener()).
        self._evento_corte = threading.Event()
        # Referencia al hilo escritor (se crea en iniciar()).
        self._hilo_escritor: threading.Thread | None = None

        # --- Contadores de la corrida ---
        self._n_mensajes = 0              # mensajes recibidos del WebSocket
        self._n_sin_symbol = 0            # mensajes sin instrumentId.symbol
        self._n_symbols_no_matcheados = 0 # symbols que no resolvieron en cache
        self._n_errores_parseo = 0        # mensajes descartados por el parser
        self._n_encolados = 0             # ticks puestos en la cola
        self._n_persistidos = 0           # ticks efectivamente grabados
        self._n_tandas = 0                # tandas escritas a disco
        self._n_errores_escritura = 0     # tandas que fallaron al grabar
        # Symbols pusheados que no matchearon (para reportar al cierre, sin
        # repetir el warning una vez por mensaje).
        self._symbols_desconocidos: set[str] = set()

    def cargar_universo(self) -> None:
        """
        Lee instrumento_broker_mapping (broker='primary', activo=True) y arma:
          - el cache symbol_externo -> mapping_id con TODO el universo (366);
          - la lista de símbolos a suscribir (muestra por defecto, completo
            si usar_universo_completo=True).

        El cache se arma siempre con los 366 porque es contra esa tabla que se
        resuelve el match; la suscripción es la que se recorta por defecto.
        """
        with get_session() as session:
            filas = (
                session.query(InstrumentoBrokerMapping)
                .filter_by(broker="primary", activo=True)
                .all()
            )

        if not filas:
            raise RuntimeError(
                "No hay filas activas de primary en instrumento_broker_mapping. "
                "¿Está poblada la tabla? (esperado: 366 filas)"
            )

        self._cache_symbol_a_mapping = {f.symbol_externo: f.id for f in filas}
        self._log.info(
            f"Universo cargado: {len(self._cache_symbol_a_mapping)} símbolos "
            f"activos de primary (cache symbol -> mapping_id armado)."
        )

        # Defensa: si hubiera symbol_externo duplicados, el cache perdería filas.
        if len(self._cache_symbol_a_mapping) != len(filas):
            self._log.warning(
                f"¡Atención! {len(filas)} filas pero "
                f"{len(self._cache_symbol_a_mapping)} claves en el cache: hay "
                f"symbol_externo duplicados. Revisar antes de seguir."
            )

        if self._usar_universo_completo:
            self._simbolos_suscriptos = sorted(self._cache_symbol_a_mapping.keys())
            self._log.info(
                f"Modo universo COMPLETO: suscribiendo "
                f"{len(self._simbolos_suscriptos)} símbolos."
            )
        else:
            disponibles = set(self._cache_symbol_a_mapping.keys())
            self._simbolos_suscriptos = [s for s in _MUESTRA_V1 if s in disponibles]
            salteados = [s for s in _MUESTRA_V1 if s not in disponibles]
            if salteados:
                self._log.warning(
                    f"Símbolos de la muestra que NO existen en la tabla "
                    f"(se saltean): {salteados}"
                )
            if not self._simbolos_suscriptos:
                raise RuntimeError(
                    "Ninguno de los símbolos de la muestra existe en la tabla. "
                    "Revisar _MUESTRA_V1 contra los symbol_externo reales."
                )
            self._log.info(
                f"Modo MUESTRA: suscribiendo "
                f"{len(self._simbolos_suscriptos)} símbolos: "
                f"{self._simbolos_suscriptos}"
            )

    # --- Handlers (corren en el hilo interno de pyRofex, no en el principal) ---

    def _manejar_market_data(self, mensaje: dict) -> None:
        """
        Handler de cada mensaje. Resuelve el symbol contra el cache, parsea
        (función pura, rápida) y ENCOLA el TickCrudo. NO escribe a disco: eso
        es trabajo del hilo escritor. El callback tiene que ser microsegundos.
        """
        self._n_mensajes += 1

        instrumento = mensaje.get("instrumentId") or {}
        symbol = instrumento.get("symbol")

        if symbol is None:
            self._n_sin_symbol += 1
            self._log.warning(
                f"[MD #{self._n_mensajes}] mensaje sin instrumentId.symbol: "
                f"{mensaje}"
            )
            return

        mapping_id = self._cache_symbol_a_mapping.get(symbol)
        if mapping_id is None:
            self._n_symbols_no_matcheados += 1
            # Avisar una sola vez por symbol desconocido, no por cada mensaje.
            if symbol not in self._symbols_desconocidos:
                self._symbols_desconocidos.add(symbol)
                self._log.warning(
                    f"symbol pusheado SIN match en el cache: {symbol!r}. No se "
                    f"persiste (no hay mapping_id al que colgar la fila)."
                )
            return

        # Parsear: estampa ts_recepcion = ahora (momento real de recepción) y
        # arma el TickCrudo. Si el mensaje no tiene 'timestamp', el parser lanza
        # ErrorParseoTick: lo logueamos y salteamos, SIN tirar el proceso.
        try:
            tick = parsear_tick(mensaje, mapping_id)
        except ErrorParseoTick as e:
            self._n_errores_parseo += 1
            self._log.warning(f"[MD #{self._n_mensajes}] tick descartado: {e}")
            return

        # Encolar y volver. Lo más rápido posible.
        self._cola.put(tick)
        self._n_encolados += 1

        if self._n_mensajes % _LOG_CADA_N == 0:
            self._log.info(
                f"progreso: {self._n_mensajes} recibidos | "
                f"{self._n_encolados} encolados | "
                f"{self._n_persistidos} persistidos | "
                f"cola ~{self._cola.qsize()}"
            )

    def _manejar_error(self, mensaje: dict) -> None:
        """Mensajes de ERROR del servidor (distinto de excepciones locales)."""
        self._log.error(f"Mensaje de ERROR del servidor: {mensaje}")

    def _manejar_excepcion(self, excepcion: Exception) -> None:
        """Excepciones de la conexión WebSocket."""
        self._log.error(
            f"Excepción en la conexión WebSocket: {excepcion}", exc_info=True
        )

    # --- Escritor por tandas (corre en su propio hilo) ---

    def _drenar_cola(self) -> list:
        """
        Saca de la cola TODO lo que haya en este instante y lo devuelve como
        lista. No bloquea: si la cola está vacía, devuelve [].
        """
        tanda = []
        while True:
            try:
                tanda.append(self._cola.get_nowait())
            except queue.Empty:
                break
        return tanda

    def _volcar_tanda(self) -> None:
        """
        Drena la cola y graba la tanda en UNA sola transacción. Si está vacía,
        no hace nada (no abre transacción al pedo).
        """
        tanda = self._drenar_cola()
        if not tanda:
            return

        try:
            with get_session() as session:
                session.add_all(tanda)
                session.commit()
            self._n_persistidos += len(tanda)
            self._n_tandas += 1
            self._log.info(
                f"Tanda #{self._n_tandas} persistida: {len(tanda)} ticks "
                f"(acumulado: {self._n_persistidos})."
            )
        except Exception as e:
            # Decisión "grabar crudo en vivo": ante un error de disco, perder
            # esta tanda de ~5s y loguearlo FUERTE es preferible a un loop de
            # reintento que bloquee el escritor o, peor, duplique filas (no hay
            # constraint único que nos proteja). El histórico se reconstruye
            # yendo hacia adelante; una tanda perdida no es catástrofe, una
            # corrupción silenciosa sí.
            self._n_errores_escritura += 1
            self._log.error(
                f"ERROR persistiendo tanda de {len(tanda)} ticks (se PIERDE "
                f"esta tanda): {e}",
                exc_info=True,
            )

    def _bucle_escritor(self) -> None:
        """
        Loop del hilo escritor: cada intervalo_flush_seg, drena y graba.
        Despierta antes si llega la señal de corte. Al cortar, hace un flush
        final para no perder la última tanda parcial.
        """
        self._log.info(
            f"Hilo escritor arrancado (flush cada {self._intervalo_flush_seg}s)."
        )
        while not self._evento_corte.is_set():
            # Esperar el intervalo, pero despertar de inmediato si se prende
            # la señal de corte (shutdown responsivo, no esperamos 5s al cerrar).
            self._evento_corte.wait(timeout=self._intervalo_flush_seg)
            self._volcar_tanda()

        # Flush final: drenar lo que haya quedado encolado tras el corte.
        self._log.info("Escritor: señal de corte recibida → flush final.")
        self._volcar_tanda()
        self._log.info("Hilo escritor terminado.")

    # --- Ciclo de vida ---

    def iniciar(self) -> None:
        """
        Arranca v1:
          1. Carga universo + arma cache contra la tabla viva.
          2. Autentica contra BIND.
          3. Abre el WebSocket con los handlers.
          4. Suscribe los símbolos.
          5. Arranca el hilo escritor.
          6. Bloquea el hilo principal hasta Ctrl+C.
        """
        self._log.info("=== Collector market data v1 (captura por tandas) ===")

        # 1. Universo + cache.
        self.cargar_universo()

        # 2. Autenticación REST contra BIND.
        try:
            info = conectar_primary_produccion()
        except ErrorConexionPrimary as e:
            self._log.error(f"No se pudo conectar a BIND, abortando: {e}")
            raise
        self._log.info(f"Autenticado contra BIND: {info}")

        # 3. WebSocket con handlers.
        self._log.info("Abriendo conexión WebSocket...")
        pyRofex.init_websocket_connection(
            market_data_handler=self._manejar_market_data,
            error_handler=self._manejar_error,
            exception_handler=self._manejar_excepcion,
        )

        # 4. Suscripción a market data (L1, depth default = 1).
        self._log.info(
            f"Suscribiendo {len(self._simbolos_suscriptos)} símbolos a market "
            f"data (entries: BIDS, OFFERS, LAST)..."
        )
        pyRofex.market_data_subscription(
            tickers=self._simbolos_suscriptos,
            entries=_ENTRIES,
        )
        self._log.info("Suscripción enviada.")

        # 5. Arrancar el hilo escritor (listo para drenar a medida que entren).
        self._evento_corte.clear()
        self._hilo_escritor = threading.Thread(
            target=self._bucle_escritor,
            name="escritor-ticks",
            daemon=True,
        )
        self._hilo_escritor.start()
        self._log.info("Esperando mensajes... (Ctrl+C para cortar.)")

        # 6. Bloqueo del hilo principal hasta Ctrl+C.
        self._corriendo = True
        try:
            while self._corriendo:
                time.sleep(1)
        except KeyboardInterrupt:
            self._log.info("Ctrl+C recibido: cerrando...")
        finally:
            self.detener()

    def detener(self) -> None:
        """
        Cierre limpio y ORDENADO:
          1. Cerrar el WebSocket primero → dejan de entrar mensajes (corta el
             productor antes que el consumidor).
          2. Señalar al escritor y esperar a que termine su flush final → así
             todo lo encolado antes del corte queda grabado.
          3. Reportar el resumen de la corrida.
        """
        if not self._corriendo and self._hilo_escritor is None:
            return  # ya se cerró, idempotente
        self._corriendo = False

        # 1. Cortar el productor.
        try:
            pyRofex.close_websocket_connection()
            self._log.info("Conexión WebSocket cerrada.")
        except Exception as e:
            self._log.warning(f"Error cerrando WebSocket (no crítico): {e}")

        # 2. Señalar y esperar al escritor (garantiza el flush final).
        if self._hilo_escritor is not None:
            self._evento_corte.set()
            self._hilo_escritor.join(timeout=30)
            if self._hilo_escritor.is_alive():
                self._log.error(
                    "El hilo escritor NO terminó en 30s: puede haber quedado "
                    "una tanda sin persistir. Revisar."
                )
            self._hilo_escritor = None

        # 3. Resumen de la corrida.
        self._log.info(
            f"=== Resumen v1 ===\n"
            f"  mensajes recibidos:    {self._n_mensajes}\n"
            f"    sin symbol:          {self._n_sin_symbol}\n"
            f"    symbols sin match:   {self._n_symbols_no_matcheados}\n"
            f"    errores de parseo:   {self._n_errores_parseo}\n"
            f"  encolados:             {self._n_encolados}\n"
            f"  persistidos:           {self._n_persistidos} "
            f"(en {self._n_tandas} tandas)\n"
            f"  errores de escritura:  {self._n_errores_escritura}"
        )
        if self._symbols_desconocidos:
            self._log.warning(
                f"Symbols pusheados que NO matchearon: "
                f"{sorted(self._symbols_desconocidos)}"
            )
        # Reconciliación: encolados debería == persistidos si no hubo errores.
        if self._n_encolados != self._n_persistidos:
            self._log.warning(
                f"DESCUADRE: encolados ({self._n_encolados}) != persistidos "
                f"({self._n_persistidos}). Diferencia: "
                f"{self._n_encolados - self._n_persistidos}. "
                f"Si errores de escritura = 0, investigar (flush final, cola)."
            )
        elif self._n_encolados > 0:
            self._log.info(
                "Reconciliación OK: todo lo encolado quedó persistido."
            )