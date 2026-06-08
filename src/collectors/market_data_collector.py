"""
Collector de market data en tiempo real desde Primary/BIND vía WebSocket.

=== VERSIÓN v0 — VALIDACIÓN DE MECÁNICA (NO escribe a la base) ===

El objetivo de v0 NO es capturar datos todavía, sino validar tres cosas
antes de meter persistencia:
  1. Que la mecánica conecta → abre WebSocket → suscribe → recibe → corta
     limpio funciona end-to-end contra producción BIND.
  2. Ver la ESTRUCTURA CRUDA del mensaje de market data (no la asumimos: la
     miramos). El handler loguea el mensaje verbatim.
  3. EL SUPUESTO CENTRAL DE ESTA FASE: que el `symbol` que pushea Primary en
     cada mensaje matchea EXACTAMENTE un symbol_externo guardado en
     instrumento_broker_mapping. Si no matchea, el cache symbol → mapping_id
     no resuelve y toda la persistencia de v1 se cae. v0 prueba ese supuesto.

Lo que v0 NO hace (queda para v1/v2): parsear a columnas, escribir a
ticks_crudos, reconexión, heartbeat, control por horario de mercado.

El cache symbol → mapping_id se arma SIEMPRE contra la tabla viva (los 366),
re-resuelto al arrancar (principio H1.6: no confiar en ids que traiga un JSON).
La suscripción, en cambio, en v0 se recorta a una muestra chica y líquida para
poder LEER los mensajes en consola (los 366 a ~48 msg/seg son ilegibles).
"""

import time

import pyRofex

from src.collectors.primary_conexion import (
    conectar_primary_produccion,
    ErrorConexionPrimary,
)
from src.utils.db import get_session
from src.utils.logger import obtener_logger_collector
from src.utils.models import InstrumentoBrokerMapping


log = obtener_logger_collector("market_data")

# Entries que pedimos en la suscripción. En v0 alcanza con punta compradora
# (BI), punta vendedora (OF) y última operación (LA): es exactamente lo que
# el tick va a persistir en v1. depth queda en 1 (default) → solo L1.
_ENTRIES = [
    pyRofex.MarketDataEntry.BIDS,
    pyRofex.MarketDataEntry.OFFERS,
    pyRofex.MarketDataEntry.LAST,
]

# Muestra de símbolos para v0: lista corta y líquida para poder leer los
# mensajes. Cubre variedad de monedas (bono ARS, bono MEP, acción ARS,
# CEDEAR ARS, CEDEAR MEP) para confirmar que el match funciona en todas.
# Se INTERSECTA con el universo real cargado de la base: si alguno no existe
# como fila, se saltea sin romper (no inventamos símbolos). En v1 se
# reemplaza por el universo completo (los 366).
_MUESTRA_V0 = [
    "MERV - XMEV - AL30 - CI",    # bono soberano ARS (flagship, hiperlíquido)
    "MERV - XMEV - AL30D - CI",   # mismo bono, variante USD MEP
    "MERV - XMEV - GD30 - CI",    # bono soberano ARS
    "MERV - XMEV - GGAL - CI",    # acción líder (Grupo Galicia)
    "MERV - XMEV - AAPL - CI",    # CEDEAR ARS (Apple)
    "MERV - XMEV - MELID - CI",   # CEDEAR USD MEP (MercadoLibre)
]


class ColectorMarketData:
    """
    Collector de market data v0. Conecta, suscribe, recibe y loguea.
    No persiste nada (eso es v1).
    """

    def __init__(self, usar_universo_completo: bool = False):
        self._log = log
        self._corriendo = False
        # Cache symbol_externo -> mapping_id, re-resuelto contra la tabla viva
        # al arrancar. Es la tabla de verdad contra la que validamos el match.
        self._cache_symbol_a_mapping: dict[str, int] = {}
        # Símbolos a los que efectivamente nos suscribimos.
        self._simbolos_suscriptos: list[str] = []
        # Si True, suscribe los 366; si False (default v0), solo la muestra.
        self._usar_universo_completo = usar_universo_completo
        # Contadores de la corrida (lo que v0 viene a medir).
        self._n_mensajes = 0
        self._n_symbols_no_matcheados = 0
        # Símbolos pusheados que no matchearon el cache (para reportar al cierre).
        self._symbols_desconocidos: set[str] = set()

    def cargar_universo(self) -> None:
        """
        Lee instrumento_broker_mapping (broker='primary', activo=True) y arma:
          - el cache symbol_externo -> mapping_id con TODO el universo (366);
          - la lista de símbolos a suscribir (muestra en v0, completo en v1+).

        El cache se arma siempre con los 366 porque es contra esa tabla que
        validamos el match; la suscripción es la que se recorta en v0.
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

        # Cache completo: symbol_externo -> id de la fila de mapeo.
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

        # Universo de suscripción.
        if self._usar_universo_completo:
            self._simbolos_suscriptos = sorted(self._cache_symbol_a_mapping.keys())
            self._log.info(
                f"Modo universo COMPLETO: suscribiendo "
                f"{len(self._simbolos_suscriptos)} símbolos."
            )
        else:
            # Intersección de la muestra con el universo real: solo símbolos
            # que existen como fila. No inventamos símbolos.
            disponibles = set(self._cache_symbol_a_mapping.keys())
            self._simbolos_suscriptos = [s for s in _MUESTRA_V0 if s in disponibles]
            salteados = [s for s in _MUESTRA_V0 if s not in disponibles]
            if salteados:
                self._log.warning(
                    f"Símbolos de la muestra v0 que NO existen en la tabla "
                    f"(se saltean): {salteados}"
                )
            if not self._simbolos_suscriptos:
                raise RuntimeError(
                    "Ninguno de los símbolos de la muestra v0 existe en la "
                    "tabla. Revisar _MUESTRA_V0 contra los symbol_externo reales."
                )
            self._log.info(
                f"Modo MUESTRA v0: suscribiendo "
                f"{len(self._simbolos_suscriptos)} símbolos: "
                f"{self._simbolos_suscriptos}"
            )

    # --- Handlers (corren en el hilo interno de pyRofex, no en el principal) ---

    def _manejar_market_data(self, mensaje: dict) -> None:
        """
        Handler de cada mensaje de market data. En v0 NO parsea ni escribe:
        loguea el mensaje crudo y valida el supuesto central — que el `symbol`
        pusheado matchea EXACTAMENTE un symbol_externo del cache.
        """
        self._n_mensajes += 1

        # Extracción defensiva: la estructura exacta del mensaje es JUSTO lo que
        # estamos validando, así que no la asumimos (todo con .get()).
        instrumento = mensaje.get("instrumentId") or {}
        symbol = instrumento.get("symbol")

        # Loguear el mensaje crudo COMPLETO: ese es el objetivo de v0, ver la forma.
        self._log.info(f"[MD #{self._n_mensajes}] crudo: {mensaje}")

        if symbol is None:
            self._log.warning(
                f"[MD #{self._n_mensajes}] mensaje sin instrumentId.symbol. "
                f"La estructura difiere de lo esperado, revisar el crudo de arriba."
            )
            return

        mapping_id = self._cache_symbol_a_mapping.get(symbol)
        if mapping_id is None:
            self._n_symbols_no_matcheados += 1
            self._symbols_desconocidos.add(symbol)
            self._log.warning(
                f"[MD #{self._n_mensajes}] symbol pusheado NO matchea el cache: "
                f"{symbol!r}. (Si aparece, el formato que pushea Primary difiere "
                f"del symbol_externo guardado: el supuesto que v0 vino a probar.)"
            )
        else:
            self._log.info(
                f"[MD #{self._n_mensajes}] symbol {symbol!r} -> "
                f"mapping_id={mapping_id} OK."
            )

    def _manejar_error(self, mensaje: dict) -> None:
        """Mensajes de ERROR del servidor (distinto de excepciones locales)."""
        self._log.error(f"Mensaje de ERROR del servidor: {mensaje}")

    def _manejar_excepcion(self, excepcion: Exception) -> None:
        """Excepciones de la conexión WebSocket."""
        self._log.error(
            f"Excepción en la conexión WebSocket: {excepcion}", exc_info=True
        )

    # --- Ciclo de vida ---

    def iniciar(self) -> None:
        """
        Arranca v0:
          1. Carga universo + arma cache contra la tabla viva.
          2. Autentica contra BIND (reusa conectar_primary_produccion).
          3. Abre el WebSocket con los handlers.
          4. Suscribe los símbolos.
          5. Bloquea el hilo principal hasta Ctrl+C.
        """
        self._log.info("=== Collector market data v0 (solo lectura/log) ===")

        # 1. Universo + cache.
        self.cargar_universo()

        # 2. Autenticación REST contra BIND (módulo ya validado en handshake).
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
        self._log.info("Suscripción enviada. Esperando mensajes... (Ctrl+C para cortar.)")

        # 5. Bloqueo del hilo principal. El WS corre en un hilo aparte de
        # pyRofex; acá solo mantenemos vivo el proceso y escuchamos Ctrl+C.
        self._corriendo = True
        try:
            while self._corriendo:
                time.sleep(1)
        except KeyboardInterrupt:
            self._log.info("Ctrl+C recibido: cerrando...")
        finally:
            self.detener()

    def detener(self) -> None:
        """Cierre limpio: baja el flag, cierra el WS y reporta el resumen v0."""
        self._corriendo = False
        try:
            pyRofex.close_websocket_connection()
            self._log.info("Conexión WebSocket cerrada.")
        except Exception as e:
            self._log.warning(f"Error cerrando WebSocket (no crítico): {e}")

        # Resumen de la corrida: exactamente lo que v0 vino a medir.
        self._log.info(
            f"=== Resumen v0 === mensajes recibidos: {self._n_mensajes} | "
            f"symbols no matcheados: {self._n_symbols_no_matcheados}"
        )
        if self._symbols_desconocidos:
            self._log.warning(
                f"Symbols pusheados que NO matchearon el cache: "
                f"{sorted(self._symbols_desconocidos)}"
            )
        elif self._n_mensajes > 0:
            self._log.info(
                "Todos los symbols pusheados matchearon el cache. Supuesto "
                "validado: el formato pusheado == symbol_externo."
            )