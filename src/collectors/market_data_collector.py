"""
Collector de market data en tiempo real desde Primary/BIND vía WebSocket.

=== VERSIÓN v2 — ROBUSTEZ (reconexión reactiva + watchdog + horario) ===

v1 (cerrado y validado, prueba de carga 366 OK) resolvió la captura por tandas:
lo que entra por el WebSocket llega a ticks_crudos de forma eficiente y sin
frenar la recepción. Lo que v1 explícitamente NO hacía —y v2 agrega— es
SOBREVIVIR a una rueda completa sin intervención manual.

Hallazgo que reorientó v2 (sesión 2026-06-10, inspección de la pyRofex 0.5.0
instalada):
  - El "heartbeat 30s" NO había que construirlo: pyRofex ya llama a run_forever
    de websocket-client con ping_interval=30 (environment["heartbeat"]=30, también
    contra BIND). El ping protocolar nunca faltó.
  - La caída del 08/06 (12:16:26) NO fue por inactividad: había ~22 msg/s, la
    conexión estaba viva de tráfico. Fue un corte real del otro lado, y el ping
    de 30s lo DETECTÓ -> websocket-client levantó "Connection to remote host was
    lost" -> corrió nuestro _manejar_excepcion. La detección funcionó.
  - El bug real: se detectó y NO se reaccionó. _manejar_excepcion solo logueaba.
    El proceso quedó ~5 min vivo-pero-inerte hasta el Ctrl+C. "Vivo pero inerte"
    no era falta de detección, era falta de REACCIÓN.

Por eso v2 NO agrega heartbeat (ya está) ni detección nueva (la señal ya llega):
agrega REACCIÓN. Arquitectura:

    Hilo de pyRofex (WebSocket)
        callback _manejar_excepcion: ante caída, SOLO señaliza (prende un Event).
        NO reconecta desde acá: este es el hilo que se está muriendo.
        v

    Hilo principal (supervisor)        <-- en v1 estaba ocioso (time.sleep(1))
        - vigila horario de rueda (¿sigue abierta?)
        - vigila la señal de reconexión y el watchdog de silencio
        - orquesta la reconexión con backoff exponencial
        - decide reconectar (rueda abierta) vs cerrar (rueda cerró / N fallas)

    Hilo escritor (sin cambios respecto de v1)
        drena la cola cada N segundos. Vive toda la sesión, sobrevive a las
        reconexiones (la cola simplemente se queda vacía durante el gap).

Doble red de detección de caída:
  1. REACTIVA (primaria): excepción de websocket-client -> _manejar_excepcion
     prende _evento_reconectar. Es la que cazó la caída del 08/06.
  2. PROACTIVA (defensa en profundidad): watchdog de silencio. Si en plena rueda
     no llega un solo tick por > segundos_silencio_watchdog, se asume muerte
     half-open silenciosa (la rara que la excepción podría no levantar). Se arma
     recién tras el primer mensaje, para no dar falso positivo en el arranque.

Alcance deliberado: v2 maneja UNA rueda. No cruza la noche (el token de BIND
expiraría y reautenticar repetido contra pyRofex no está validado). "Correr todos
los días" lo resuelve el systemd service del próximo sub-paso, arrancando un
proceso fresco por rueda: cada proceso = un auth = una sesión.

La reconexión NO reautentica: reusa el token (que dura la rueda) y solo rearma el
WebSocket. Leído del fuente de pyRofex: tras una caída, ws_thread muere, y
connect() vuelve a armar un WebSocketApp nuevo sin problema. Reauth solo lo
agregaríamos si observamos fallas por token expirado DENTRO de una rueda (medir
antes de agregar).

ts_recepcion se sigue estampando en el callback (parser con default now()), el
momento real de recepción — NO en el flush.

El cache symbol -> mapping_id se arma SIEMPRE contra la tabla viva (los 366),
re-resuelto al arrancar (principio H1.6: no confiar en ids que traiga un JSON).
"""

import queue
import threading
import time
from datetime import datetime, timezone, time as hora_del_dia, timedelta

import pytz
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

# Zona horaria del mercado. Argentina NO observa horario de verano (desde 2009),
# así que el offset es constante -03; por eso replace(hour=...) sobre un datetime
# localizado con pytz es seguro acá (no hay salto de DST que normalizar).
_TZ_BA = pytz.timezone("America/Argentina/Buenos_Aires")

# Entries que pedimos en la suscripción: punta compradora (BI), punta
# vendedora (OF) y última operación (LA). depth queda en 1 (default) -> L1.
_ENTRIES = [
    pyRofex.MarketDataEntry.BIDS,
    pyRofex.MarketDataEntry.OFFERS,
    pyRofex.MarketDataEntry.LAST,
]

# Cada cuántos mensajes recibidos logueamos una línea de progreso.
_LOG_CADA_N = 500

# Muestra corta y líquida para validación con volumen bajo antes de soltar los 366.
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
    Collector de market data v2. Conecta, suscribe, recibe, parsea y persiste a
    ticks_crudos por tandas (igual que v1), y además: reconecta ante caídas con
    backoff, vigila el horario de rueda, y para limpio cuando corresponde.
    """

    def __init__(
        self,
        usar_universo_completo: bool = False,
        intervalo_flush_seg: float = 5.0,
        # --- Parámetros de horario de rueda ---
        # NOTA (principio 8 "configuración, no código"): estos horarios pertenecen
        # a config.json. Quedan acá como parámetros con default hasta cablear el
        # loader de config en el sub-paso siguiente. Considerar un buffer post-17:00
        # para capturar la subasta de cierre (config, no hardcode).
        hora_apertura: hora_del_dia = hora_del_dia(11, 0),
        hora_cierre: hora_del_dia = hora_del_dia(17, 0),
        # --- Parámetros de robustez ---
        segundos_silencio_watchdog: float = 120.0,
        max_reconexiones_consecutivas: int = 8,
        backoff_inicial_seg: float = 2.0,
        backoff_max_seg: float = 60.0,
    ):
        self._log = log
        self._corriendo = False

        # Cache symbol_externo -> mapping_id, re-resuelto contra la tabla viva.
        self._cache_symbol_a_mapping: dict[str, int] = {}
        self._simbolos_suscriptos: list[str] = []
        self._usar_universo_completo = usar_universo_completo

        # --- Maquinaria del escritor por tandas (idéntica a v1) ---
        self._cola: "queue.Queue" = queue.Queue()
        self._intervalo_flush_seg = intervalo_flush_seg
        self._evento_corte = threading.Event()      # corta el escritor
        self._hilo_escritor: threading.Thread | None = None

        # --- Maquinaria de robustez v2 ---
        # Señal prendida por _manejar_excepcion (hilo WS) y leída por el
        # supervisor (hilo principal). El callback NO reconecta: solo señaliza.
        self._evento_reconectar = threading.Event()
        # Marca de vida para el watchdog. Se actualiza con CADA mensaje recibido
        # (monotonic: inmune a ajustes del reloj de pared). El supervisor mide
        # "segundos desde el último mensaje" contra esto.
        self._ts_ultimo_mensaje_mono = time.monotonic()
        # El watchdog se arma recién cuando la conexión actual entregó al menos
        # un mensaje (evita falso positivo en arranque lento de rueda).
        self._ws_recibio_algo = False

        self._hora_apertura = hora_apertura
        self._hora_cierre = hora_cierre
        self._segundos_silencio_watchdog = segundos_silencio_watchdog
        self._max_reconexiones = max_reconexiones_consecutivas
        self._backoff_inicial_seg = backoff_inicial_seg
        self._backoff_max_seg = backoff_max_seg

        # --- Contadores de la corrida ---
        self._n_mensajes = 0              # mensajes recibidos del WebSocket
        self._n_sin_symbol = 0            # mensajes sin instrumentId.symbol
        self._n_symbols_no_matcheados = 0 # symbols que no resolvieron en cache
        self._n_errores_parseo = 0        # mensajes descartados por el parser
        self._n_encolados = 0             # ticks puestos en la cola
        self._n_persistidos = 0           # ticks efectivamente grabados
        self._n_tandas = 0                # tandas escritas a disco
        self._n_errores_escritura = 0     # tandas que fallaron al grabar
        self._n_reconexiones = 0          # intentos de reconexión disparados
        self._n_reconexiones_fallidas = 0 # reconexiones que no recuperaron datos
        self._symbols_desconocidos: set[str] = set()

    def cargar_universo(self) -> None:
        """
        Lee instrumento_broker_mapping (broker='primary', activo=True) y arma el
        cache symbol_externo -> mapping_id con TODO el universo (366) y la lista
        de símbolos a suscribir (muestra por defecto, completo si corresponde).
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

    # --- Horario de mercado (lógica de decisión: opera sobre UTC, convierte a -03
    #     solo para preguntar "¿qué hora es en la rueda?", que es su semántica) ---

    def _ahora_ba(self) -> datetime:
        """Ahora, UTC-aware, convertido a hora de Buenos Aires para decidir horario."""
        return datetime.now(timezone.utc).astimezone(_TZ_BA)

    def _mercado_abierto(self) -> bool:
        """
        True si estamos en horario de rueda: día hábil (lun-vie) y hora dentro de
        [apertura, cierre). NO contempla feriados de BYMA (limitación conocida: en
        feriado el collector esperará/conectará sin datos, lo cual es inocuo —el
        watchdog no se arma sin primer mensaje—; una lista de feriados en config
        es mejora futura).
        """
        ahora = self._ahora_ba()
        if ahora.weekday() >= 5:  # 5 = sábado, 6 = domingo
            return False
        return self._hora_apertura <= ahora.time() < self._hora_cierre

    def _segundos_hasta_apertura(self) -> float:
        """
        Segundos hasta la próxima apertura de rueda (hoy si todavía no abrió y es
        hábil; si no, el próximo día hábil). Solo se llama cuando el mercado está
        cerrado.
        """
        ahora = self._ahora_ba()
        candidato = ahora.replace(
            hour=self._hora_apertura.hour,
            minute=self._hora_apertura.minute,
            second=0,
            microsecond=0,
        )
        # Avanzar de a un día hasta caer en un futuro hábil.
        while candidato <= ahora or candidato.weekday() >= 5:
            candidato = candidato + timedelta(days=1)
        return (candidato - ahora).total_seconds()

    def _dormir(self, segundos: float) -> None:
        """
        Sleep en cachos de 1s para seguir siendo responsivo al corte (Ctrl+C
        interrumpe igual, pero esto evita esperar de más al cerrar).
        """
        fin = time.monotonic() + segundos
        while self._corriendo and time.monotonic() < fin:
            time.sleep(min(1.0, max(0.0, fin - time.monotonic())))

    def _esperar_apertura(self) -> None:
        """Bloquea hasta que abra la rueda (re-chequeo cada <=5 min)."""
        while self._corriendo and not self._mercado_abierto():
            secs = self._segundos_hasta_apertura()
            apertura = self._ahora_ba() + timedelta(seconds=secs)
            self._log.info(
                f"Mercado cerrado. Próxima apertura ~{apertura:%H:%M del %d/%m} "
                f"(en ~{secs/3600:.1f} h). Esperando..."
            )
            # Re-chequear a lo sumo cada 5 min para cazar la apertura a tiempo.
            self._dormir(min(secs, 300.0))

    # --- Handlers (corren en el hilo interno de pyRofex) ---

    def _manejar_market_data(self, mensaje: dict) -> None:
        """
        Handler de cada mensaje. Resuelve el symbol contra el cache, parsea
        (función pura, rápida) y ENCOLA el TickCrudo. NO escribe a disco.
        """
        self._n_mensajes += 1
        # v2: marca de vida. CUALQUIER mensaje (matchee o no) prueba que la
        # conexión está viva -> reinicia el reloj del watchdog y lo arma.
        self._ts_ultimo_mensaje_mono = time.monotonic()
        self._ws_recibio_algo = True

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
            if symbol not in self._symbols_desconocidos:
                self._symbols_desconocidos.add(symbol)
                self._log.warning(
                    f"symbol pusheado SIN match en el cache: {symbol!r}. No se "
                    f"persiste (no hay mapping_id al que colgar la fila)."
                )
            return

        try:
            tick = parsear_tick(mensaje, mapping_id)
        except ErrorParseoTick as e:
            self._n_errores_parseo += 1
            self._log.warning(f"[MD #{self._n_mensajes}] tick descartado: {e}")
            return

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
        """
        Mensajes de ERROR del servidor (distinto de una caída de conexión). Se
        loguea pero NO dispara reconexión: un ERROR de servidor (ej. suscripción
        mal armada) no es una conexión muerta.
        """
        self._log.error(f"Mensaje de ERROR del servidor: {mensaje}")

    def _manejar_excepcion(self, excepcion: Exception) -> None:
        """
        Excepciones de la conexión WebSocket (ej. "Connection to remote host was
        lost"). v2: SEÑALIZA reconexión al supervisor; NO reconecta desde acá,
        que es el hilo del WebSocket que se está muriendo. El supervisor en el
        hilo principal orquesta la reconexión con backoff.
        """
        self._log.error(
            f"Excepción en la conexión WebSocket: {excepcion}", exc_info=True
        )
        if self._corriendo:
            self._evento_reconectar.set()

    # --- Escritor por tandas (corre en su propio hilo; idéntico a v1) ---

    def _drenar_cola(self) -> list:
        """Saca de la cola todo lo que haya en este instante. No bloquea."""
        tanda = []
        while True:
            try:
                tanda.append(self._cola.get_nowait())
            except queue.Empty:
                break
        return tanda

    def _volcar_tanda(self) -> None:
        """Drena la cola y graba la tanda en UNA transacción. Vacía -> no hace nada."""
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
            # Ante error de disco: perder esta tanda de ~5s y loguearlo FUERTE es
            # preferible a un loop de reintento que bloquee o duplique filas (no
            # hay constraint único que proteja). El histórico se reconstruye hacia
            # adelante; una tanda perdida no es catástrofe, una corrupción sí.
            self._n_errores_escritura += 1
            self._log.error(
                f"ERROR persistiendo tanda de {len(tanda)} ticks (se PIERDE "
                f"esta tanda): {e}",
                exc_info=True,
            )

    def _bucle_escritor(self) -> None:
        """
        Loop del hilo escritor: cada intervalo_flush_seg, drena y graba. Despierta
        antes si llega la señal de corte. Al cortar, flush final. Vive toda la
        sesión: sobrevive a las reconexiones (la cola se queda vacía en el gap).
        """
        self._log.info(
            f"Hilo escritor arrancado (flush cada {self._intervalo_flush_seg}s)."
        )
        while not self._evento_corte.is_set():
            self._evento_corte.wait(timeout=self._intervalo_flush_seg)
            self._volcar_tanda()

        self._log.info("Escritor: señal de corte recibida -> flush final.")
        self._volcar_tanda()
        self._log.info("Hilo escritor terminado.")

    # --- Conexión WebSocket (factorizada: se usa en arranque y en reconexión) ---

    def _abrir_websocket_y_suscribir(self) -> None:
        """
        (Re)abre la conexión WebSocket y (re)envía la suscripción. Sirve tanto
        para la primera conexión como para cada reconexión. Resetea las señales
        de liveness para la conexión nueva.

        Lectura del fuente de pyRofex 0.5.0: tras una caída, on_error cerró la
        conexión y el ws_thread murió; connect() (dentro de init_websocket_
        connection) arma un WebSocketApp nuevo porque is_alive() es False. Los
        handlers se re-agregan idempotentes (chequean pertenencia). El token sigue
        en globals: NO reautenticamos.
        """
        # Cierre best-effort de cualquier conexión previa (en reconexión).
        try:
            pyRofex.close_websocket_connection()
        except Exception:
            pass

        # Reset de liveness para esta conexión nueva: el watchdog se desarma hasta
        # que lleguen datos de nuevo.
        self._ws_recibio_algo = False
        self._ts_ultimo_mensaje_mono = time.monotonic()
        self._evento_reconectar.clear()

        pyRofex.init_websocket_connection(
            market_data_handler=self._manejar_market_data,
            error_handler=self._manejar_error,
            exception_handler=self._manejar_excepcion,
        )
        pyRofex.market_data_subscription(
            tickers=self._simbolos_suscriptos,
            entries=_ENTRIES,
        )
        self._log.info(
            f"Suscripción (re)enviada: {len(self._simbolos_suscriptos)} símbolos "
            f"(entries: BIDS, OFFERS, LAST)."
        )

    def _silencio_excedido(self) -> bool:
        """
        Watchdog proactivo: True si la conexión entregó datos alguna vez y desde
        el último mensaje pasó más que el umbral (muerte half-open silenciosa).
        Desarmado hasta el primer mensaje de la conexión actual.
        """
        if not self._ws_recibio_algo:
            return False
        return (time.monotonic() - self._ts_ultimo_mensaje_mono) > self._segundos_silencio_watchdog

    def _esperar_recuperacion(self, grace_seg: float = 20.0) -> bool:
        """
        Tras un intento de reconexión, espera hasta grace_seg a que la conexión
        nueva entregue datos. True si llegaron datos; False si se agotó el tiempo
        o si connect() volvió a fallar (re-prendió _evento_reconectar).
        """
        fin = time.monotonic() + grace_seg
        while self._corriendo and time.monotonic() < fin:
            if self._ws_recibio_algo:
                return True
            if self._evento_reconectar.is_set():
                return False  # connect() falló de nuevo
            time.sleep(0.5)
        return self._ws_recibio_algo

    def _alertar(self, nivel: str, mensaje: str) -> None:
        """
        Punto único de alerta. Por ahora solo loguea con el nivel adecuado; el
        ruteo a Telegram (telegram_notifier) se cablea en el sub-paso siguiente,
        una vez confirmada la firma del notificador. NO inventamos su API acá:
        todos los call-sites ya están puestos, cablear Telegram = implementar este
        método una sola vez.
        """
        if nivel == "CRITICAL":
            self._log.critical(f"[ALERTA CRÍTICA] {mensaje}")
        elif nivel == "WARN":
            self._log.warning(f"[ALERTA] {mensaje}")
        else:
            self._log.info(f"[ALERTA] {mensaje}")

    # --- Supervisor (corre en el hilo principal; era el time.sleep(1) ocioso) ---

    def _supervisar(self) -> None:
        """
        Loop maestro de la sesión. Mientras la rueda esté abierta: si no hay
        caída, duerme el tick; si hay caída (excepción o silencio), reconecta con
        backoff. Sale del loop cuando cierra la rueda o se agotan los reintentos.
        """
        self._log.info("Supervisor activo (vigila conexión y horario de rueda).")
        backoff = self._backoff_inicial_seg
        fallas = 0

        while self._corriendo:
            # 1. ¿Cerró la rueda? -> fin de sesión limpio.
            if not self._mercado_abierto():
                self._log.info("Rueda cerrada: terminando la sesión de captura.")
                break

            # 2. ¿Caída detectada? (excepción del WS o silencio prolongado)
            caida_excepcion = self._evento_reconectar.is_set()
            caida_silencio = self._silencio_excedido()
            if caida_excepcion or caida_silencio:
                motivo = (
                    "excepción de conexión" if caida_excepcion
                    else f"silencio > {self._segundos_silencio_watchdog:.0f}s"
                )
                fallas += 1
                self._n_reconexiones += 1
                self._log.warning(
                    f"Conexión caída ({motivo}). Reconectando "
                    f"(intento {fallas}/{self._max_reconexiones})..."
                )

                try:
                    self._abrir_websocket_y_suscribir()
                except Exception as e:
                    self._log.error(f"Error al reabrir WS: {e}", exc_info=True)

                if self._esperar_recuperacion():
                    self._log.info(
                        f"Reconexión OK: datos fluyendo de nuevo "
                        f"(tras intento {fallas})."
                    )
                    backoff = self._backoff_inicial_seg
                    fallas = 0
                else:
                    self._n_reconexiones_fallidas += 1
                    self._alertar(
                        "WARN",
                        f"Reconexión sin datos tras intento {fallas}.",
                    )
                    if fallas >= self._max_reconexiones:
                        self._alertar(
                            "CRITICAL",
                            f"Agotados {fallas} intentos de reconexión "
                            f"consecutivos. Deteniendo collector.",
                        )
                        break
                    self._log.info(
                        f"Backoff {backoff:.0f}s antes del próximo intento."
                    )
                    self._dormir(backoff)
                    backoff = min(backoff * 2.0, self._backoff_max_seg)
                continue

            # 3. Sano: dormir el tick del supervisor.
            time.sleep(1)

    # --- Ciclo de vida ---

    def iniciar(self) -> None:
        """
        Arranca v2:
          1. Carga universo + arma cache contra la tabla viva.
          2. Si la rueda está cerrada, espera la apertura (Ctrl+C interrumpe).
          3. Autentica contra BIND (una vez por sesión; el token dura la rueda).
          4. Arranca el hilo escritor (vive toda la sesión).
          5. Abre el WebSocket + suscribe.
          6. Entra al supervisor (reconexión + horario) hasta cierre o Ctrl+C.
        """
        self._log.info("=== Collector market data v2 (robustez) ===")
        self._corriendo = True
        try:
            # 1. Universo + cache.
            self.cargar_universo()

            # 2. Esperar apertura si hace falta.
            if not self._mercado_abierto():
                self._esperar_apertura()
            if not self._corriendo:
                return  # cortado durante la espera

            # 3. Autenticación (una vez; el token dura la rueda).
            try:
                info = conectar_primary_produccion()
            except ErrorConexionPrimary as e:
                self._log.error(f"No se pudo autenticar contra BIND, abortando: {e}")
                return
            self._log.info(f"Autenticado contra BIND: {info}")

            # 4. Hilo escritor (una vez; sobrevive reconexiones).
            self._evento_corte.clear()
            self._hilo_escritor = threading.Thread(
                target=self._bucle_escritor,
                name="escritor-ticks",
                daemon=True,
            )
            self._hilo_escritor.start()

            # 5. Primera conexión WS + suscripción.
            self._log.info("Conexión inicial al WebSocket...")
            try:
                self._abrir_websocket_y_suscribir()
            except Exception as e:
                # Si la conexión inicial falla, el supervisor lo retoma como
                # reconexión (la excepción de connect() ya pudo prender el evento).
                self._log.error(f"Falló la conexión inicial WS: {e}", exc_info=True)
                self._evento_reconectar.set()

            self._log.info("Capturando... (Ctrl+C para cortar.)")

            # 6. Supervisor.
            self._supervisar()

        except KeyboardInterrupt:
            self._log.info("Ctrl+C recibido: cerrando...")
        finally:
            self.detener()

    def detener(self) -> None:
        """
        Cierre limpio y ORDENADO:
          1. Cortar el productor (WebSocket) -> dejan de entrar mensajes.
          2. Señalar al escritor y esperar su flush final -> lo encolado se graba.
          3. Reportar el resumen de la corrida.
        Idempotente.
        """
        if not self._corriendo and self._hilo_escritor is None:
            return
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
            f"=== Resumen v2 ===\n"
            f"  mensajes recibidos:    {self._n_mensajes}\n"
            f"    sin symbol:          {self._n_sin_symbol}\n"
            f"    symbols sin match:   {self._n_symbols_no_matcheados}\n"
            f"    errores de parseo:   {self._n_errores_parseo}\n"
            f"  encolados:             {self._n_encolados}\n"
            f"  persistidos:           {self._n_persistidos} "
            f"(en {self._n_tandas} tandas)\n"
            f"  errores de escritura:  {self._n_errores_escritura}\n"
            f"  reconexiones:          {self._n_reconexiones} "
            f"(fallidas: {self._n_reconexiones_fallidas})"
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