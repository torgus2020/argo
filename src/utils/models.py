"""
Modelos de SQLAlchemy para la base de datos de Argo.

Define las tablas principales del sistema:
- instrumentos: catálogo maestro de instrumentos del universo
- instrumento_broker_mapping: mapeo de instrumentos a símbolos de cada broker
- ticks_crudos: ticks crudos de market data en tiempo real (capa de captura)
- cotizaciones_1min: datos intradía con granularidad de 1 minuto
- cotizaciones_diarias: datos OHLCV diarios
- macro_indicadores: indicadores BCRA, INDEC, otras fuentes macro
- log_collectors: auditoría de corridas de collectors

Decisiones de diseño documentadas en docs/db_schema.md.

Los modelos NO contienen lógica de negocio. Solo definen estructura y relaciones.
La lógica vive en src/utils/db.py (conexión, sesiones) y módulos específicos
(collectors, estrategias).

Convención de fechas: TODO datetime persistido en Argo es UTC-aware (con
tzinfo=timezone.utc explícito). Los timestamps del sistema son UTC. Para que
esa convención se cumpla de verdad en SQLite —que no tiene tipo nativo de
timestamp con zona y devuelve naive al leer— las columnas de fecha-y-hora NO
usan DateTime a secas, sino el tipo custom DateTimeUTC definido abajo, que
fuerza UTC al escribir y adjunta tzinfo=UTC al leer (incluso a filas viejas
guardadas naive). Ver DateTimeUTC y el helper _ahora_utc.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    Date,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import declarative_base, relationship


def _ahora_utc() -> datetime:
    """
    Devuelve el instante actual como datetime UTC-aware.

    Reemplaza a datetime.utcnow() (deprecado en Python 3.12), que además
    devolvía un datetime naive. Acá devolvemos aware (tzinfo=UTC explícito):
    es la convención de fechas de Argo. Un solo lugar donde vive la regla,
    para que todos los defaults/onupdate de los modelos la compartan.
    """
    return datetime.now(timezone.utc)


class DateTimeUTC(TypeDecorator):
    """
    Tipo de columna datetime que garantiza UTC-aware en ambos sentidos.

    Problema que resuelve: SQLite no tiene tipo nativo de timestamp con zona.
    Guarda el datetime como texto, y al leer SQLAlchemy lo reconstruye NAIVE
    (sin tzinfo). DateTime(timezone=True) en SQLite NO alcanza: solo preserva
    el offset si el texto ya lo tuviera, y el texto histórico de Argo se guardó
    sin offset. Resultado: lecturas naive, que rompen comparaciones contra los
    datetimes aware que genera el código (TypeError naive vs aware) y violan la
    convención del proyecto.

    Cómo lo resuelve, en un solo lugar (igual que _ahora_utc centraliza la regla):
      - Al ESCRIBIR (process_bind_param): si el valor es aware, lo convierte a
        UTC y lo guarda como naive (formato uniforme con lo ya escrito en disco).
        Si llegara naive, se asume UTC por convención y se guarda tal cual.
      - Al LEER (process_result_value): si el texto sale naive (todo lo viejo),
        le adjunta tzinfo=UTC. Si por algún motivo viniera aware, lo normaliza
        a UTC. Toda lectura sale UTC-aware.

    Es RETROACTIVO: las filas escritas antes de adoptar este tipo son UTC pero
    están guardadas naive; al pasar por process_result_value salen aware, sin
    reescribir un solo byte en disco. El contrato "lo que está en la base es
    UTC" lo enforcea el software (no SQLite), pero ahora lo cumple de verdad.
    """

    impl = DateTime
    cache_ok = True  # el tipo no tiene parámetros mutables: cacheable por SQLAlchemy

    def process_bind_param(self, value, dialect):
        """Al escribir: normaliza a UTC y guarda naive (formato uniforme)."""
        if value is None:
            return None
        if value.tzinfo is None:
            # Por convención no debería entrar naive; si pasa, se asume UTC.
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        """Al leer: si sale naive (lo histórico), adjunta UTC; si no, normaliza."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


# Base declarativa para todos los modelos. Cualquier clase que herede de Base
# se registra automáticamente para que Alembic detecte cambios.
Base = declarative_base()


class Instrumento(Base):
    """
    Catálogo maestro de instrumentos.

    Una fila por cada instrumento del universe.json. El ID interno (autoincremental)
    es la referencia estable; el ticker puede cambiar por splits o renombramientos.

    El campo metadata_json guarda info específica del tipo (vencimiento de bono,
    ratio de CEDEAR, sector de acción, etc.) como JSON serializado. Esto permite
    flexibilidad sin un schema rígido por tipo de instrumento.
    """

    __tablename__ = "instrumentos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    tipo = Column(String(30), nullable=False)
    nombre = Column(String(200), nullable=False)
    mercado = Column(String(30), nullable=False)
    moneda = Column(String(20), nullable=False)
    fuente = Column(String(30), nullable=False)
    activo = Column(Boolean, nullable=False, default=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTimeUTC, nullable=False, default=_ahora_utc)
    updated_at = Column(
        DateTimeUTC, nullable=False, default=_ahora_utc, onupdate=_ahora_utc
    )

    # Relaciones inversas (no crean columnas, solo facilitan navegar desde el código)
    cotizaciones_1min = relationship(
        "Cotizacion1Min", back_populates="instrumento", cascade="all, delete-orphan"
    )
    cotizaciones_diarias = relationship(
        "CotizacionDiaria", back_populates="instrumento", cascade="all, delete-orphan"
    )
    broker_mappings = relationship(
        "InstrumentoBrokerMapping",
        back_populates="instrumento",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("ticker", "mercado", name="uq_instrumento_ticker_mercado"),
    )

    def __repr__(self):
        return f"<Instrumento(id={self.id}, ticker='{self.ticker}', tipo='{self.tipo}')>"


class InstrumentoBrokerMapping(Base):
    """
    Mapeo entre instrumentos del universo Argo y símbolos específicos de cada broker.

    Permite que un instrumento Argo (ej. AL30) tenga múltiples símbolos asociados
    en distintos brokers (primary, iol, cocos, polygon) y, dentro del mismo broker,
    múltiples variantes:
      - moneda de liquidación: ARS, USD_MEP (sufijo D), USD_CCL (sufijo C)
      - plazos: CI (T+0), 24hs (T+1), 48hs

    Ejemplo para AL30 vía Primary:
      MERV - XMEV - AL30  - CI    → ARS, plazo CI
      MERV - XMEV - AL30  - 24hs  → ARS, plazo 24hs
      MERV - XMEV - AL30D - CI    → USD_MEP, plazo CI
      MERV - XMEV - AL30C - CI    → USD_CCL, plazo CI

    El campo segmento registra el segmento de mercado del símbolo (ej. MERV =
    BYMA, donde opera Argo; TIVA = MAE, mayorista; DUAL = futuros). Es dato
    estructural de ejecución: una estrategia debe saber en qué segmento se
    negocia el instrumento. Argo opera exclusivamente en MERV.

    El campo es_default define qué símbolo responde cuando una estrategia pide
    "el precio de AL30" sin especificar moneda/plazo. Default inicial: plazo CI,
    moneda ARS. Se ajusta con data real una vez que tengamos 2-3 días de market
    data (ver PENDIENTES.md).

    El campo activo=False marca mapeos obsoletos sin borrarlos: los backtests
    históricos siguen pudiendo resolver símbolos que ya no están en producción.

    La unicidad se garantiza sobre (broker, symbol_externo, plazo) porque un
    mismo símbolo externo no puede mapear a dos plazos distintos en el mismo
    broker. NO incluye instrumento_id en el constraint porque el símbolo externo
    debe ser único globalmente dentro del broker.
    """

    __tablename__ = "instrumento_broker_mapping"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrumento_id = Column(
        Integer, ForeignKey("instrumentos.id"), nullable=False, index=True
    )
    broker = Column(String(20), nullable=False, index=True)
    symbol_externo = Column(String(80), nullable=False)
    segmento = Column(String(20), nullable=False)
    moneda_liquidacion = Column(String(15), nullable=False)
    plazo = Column(String(10), nullable=False)
    es_default = Column(Boolean, nullable=False, default=False)
    activo = Column(Boolean, nullable=False, default=True)
    metadata_json = Column(Text, nullable=True)
    fecha_validacion = Column(DateTimeUTC, nullable=True)
    created_at = Column(DateTimeUTC, nullable=False, default=_ahora_utc)
    updated_at = Column(
        DateTimeUTC, nullable=False, default=_ahora_utc, onupdate=_ahora_utc
    )

    instrumento = relationship("Instrumento", back_populates="broker_mappings")

    __table_args__ = (
        UniqueConstraint(
            "broker", "symbol_externo", "plazo", name="uq_broker_symbol_plazo"
        ),
        Index("ix_inst_broker_default", "instrumento_id", "broker", "es_default"),
    )

    def __repr__(self):
        return (
            f"<InstrumentoBrokerMapping(instrumento_id={self.instrumento_id}, "
            f"broker='{self.broker}', symbol='{self.symbol_externo}', "
            f"segmento='{self.segmento}', plazo='{self.plazo}', "
            f"moneda='{self.moneda_liquidacion}')>"
        )


class TickCrudo(Base):
    """
    Ticks crudos de market data en tiempo real (Opción A: grabar crudo + derivar).

    Es la capa de captura SIN PROCESAR del feed de Primary/BIND vía WebSocket.
    Una fila por cada mensaje de market data recibido. De esta tabla se derivan
    después las barras agregadas (cotizaciones_1min) en una capa separada; el
    crudo se conserva como fuente de verdad y se purga con política de retención
    (~cada 3 meses) una vez derivado y validado. Cadena del dato:
        ticks_crudos  →  cotizaciones_1min  →  cotizaciones_diarias

    --- Por qué NO hay constraint UNIQUE (a diferencia de cotizaciones_1min) ---
    cotizaciones_1min SÍ tiene único sobre (instrumento_id, timestamp): una barra
    de 1 minuto de un instrumento es única por definición. Acá es al revés, y a
    propósito. El campo `timestamp` del mensaje de Primary es la hora del PUSH del
    servidor, no la de cada actualización individual: en una misma ráfaga,
    instrumentos distintos comparten el mismo timestamp al milisegundo (verificado
    en la captura del 2026-06-03: seis símbolos distintos, idéntico timestamp). Un
    único sobre (mapping_id, ts_mensaje) colisionaría. Pero la razón de fondo es
    más profunda: un constraint único es un DEDUPLICADOR, y deduplicar contradice
    el principio de grabar crudo —tiraría mensajes legítimos que cayeran en el
    mismo instante—. Acá se prioriza FIDELIDAD sobre idempotencia: se graba TODO.
    La defensa contra duplicados por reconexión vive en el collector (no reprocesar
    una sesión de WebSocket), no en un constraint que sacrifique información
    irrecuperable. La PK es sintética (id autoincremental), sin clave natural.

    --- Por qué blob (raw_json) + columnas extraídas ---
    raw_json guarda el mensaje verbatim: es la fuente de verdad y asegura contra
    campos que todavía no observamos (la captura fue una muestra chica de L1 un día
    tranquilo; no vimos L2, subastas, ni libros atípicos). Las columnas bid/offer/
    last son un atajo para consultar y agregar sin parsear JSON en cada lectura.
    Redundancia deliberada: ~20% más de disco a cambio de no quedar rehenes de lo
    que no modelamos hoy. Si mañana aparece un campo nuevo, está en el blob.

    --- Los tres tiempos (no confundirlos) ---
    - ts_mensaje:      del `timestamp` de nivel superior del mensaje (push del
                       servidor, epoch ms → UTC-aware). Es el eje temporal de la
                       agregación a 1 minuto.
    - ts_recepcion:    cuándo lo recibió Argo (_ahora_utc al insertar). Es el único
                       orden que controlamos nosotros; útil para medir latencia.
    - ts_ultimo_trade: del `LA.date` (cuándo fue la última operación efectiva).
                       Nullable: un instrumento con puntas pero sin operar todavía
                       trae LA=null (visto en TX26C el 2026-06-03).

    --- La punta ---
    En el mensaje, BI (bid) y OF (offer) vienen como LISTA de niveles. En L1
    capturamos el primer nivel (mejor punta) en las columnas. Si algún día se
    captura L2 (profundidad), el libro completo queda igual preservado en raw_json
    sin tocar el schema.

    --- La relación con el mapeo ---
    mapping_id apunta a instrumento_broker_mapping.id (la fila ya encadena
    instrumento + broker + plazo + moneda). El collector resuelve symbol → mapping_id
    una sola vez al arrancar (cache en memoria, re-resuelto contra la tabla viva),
    no en cada tick. La relación se declara de UNA sola vía (desde el tick) y SIN
    cascade de borrado: borrar un mapeo NO debe evaporar millones de ticks crudos
    —sería la pérdida irreversible que esta tabla existe para evitar—. Los mapeos
    se retiran con activo=False, nunca se borran (decisión 1.3), y la FK bloquea el
    borrado de un mapeo con ticks colgando.
    """

    __tablename__ = "ticks_crudos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mapping_id = Column(
        Integer, ForeignKey("instrumento_broker_mapping.id"), nullable=False
    )
    ts_mensaje = Column(DateTimeUTC, nullable=False)
    ts_recepcion = Column(DateTimeUTC, nullable=False, default=_ahora_utc)
    ts_ultimo_trade = Column(DateTimeUTC, nullable=True)
    bid_price = Column(Float, nullable=True)
    bid_size = Column(Integer, nullable=True)
    offer_price = Column(Float, nullable=True)
    offer_size = Column(Integer, nullable=True)
    last_price = Column(Float, nullable=True)
    last_size = Column(Integer, nullable=True)
    raw_json = Column(Text, nullable=False)

    # Relación de una sola vía (sin back_populates, sin cascade). Ver docstring.
    mapping = relationship("InstrumentoBrokerMapping")

    __table_args__ = (
        # Índice compuesto que arranca por mapping_id: sirve tanto para "todos los
        # ticks del instrumento X en tal ventana" (lo que lee la agregación) como
        # para resolver la FK. No se pone index simple en mapping_id: sería
        # redundante con este y se pagaría en cada insert.
        Index("ix_ticks_crudos_mapping_ts", "mapping_id", "ts_mensaje"),
    )

    def __repr__(self):
        return (
            f"<TickCrudo(mapping_id={self.mapping_id}, "
            f"ts_mensaje={self.ts_mensaje}, bid={self.bid_price}, "
            f"offer={self.offer_price}, last={self.last_price})>"
        )


class Cotizacion1Min(Base):
    """
    Datos intradía con granularidad de 1 minuto.

    Es la tabla más grande del sistema: ~10M filas/año estimadas con el universo
    actual. Timestamps en UTC. Volumen dolarizado calculado al insertar para
    acelerar queries (redundante pero el trade-off vale).

    Constraint UNIQUE en (instrumento_id, timestamp) garantiza no duplicados,
    defensa contra bugs de re-procesamiento.
    """

    __tablename__ = "cotizaciones_1min"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrumento_id = Column(
        Integer, ForeignKey("instrumentos.id"), nullable=False
    )
    timestamp = Column(DateTimeUTC, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False, default=0)
    volume_dolarizado = Column(Float, nullable=True)
    cantidad_operaciones = Column(Integer, nullable=True)
    fuente = Column(String(30), nullable=False)
    created_at = Column(DateTimeUTC, nullable=False, default=_ahora_utc)

    instrumento = relationship("Instrumento", back_populates="cotizaciones_1min")

    __table_args__ = (
        UniqueConstraint("instrumento_id", "timestamp", name="uq_cot1min_instr_ts"),
        Index("ix_cot1min_instr_ts_desc", "instrumento_id", "timestamp"),
        Index("ix_cot1min_timestamp", "timestamp"),
    )

    def __repr__(self):
        return (
            f"<Cotizacion1Min(instrumento_id={self.instrumento_id}, "
            f"timestamp={self.timestamp}, close={self.close})>"
        )


class CotizacionDiaria(Base):
    """
    Datos OHLCV diarios.

    Más chica que cotizaciones_1min: ~25K filas/año. Puede venir directo del
    histórico de Primary (que tiene granularidad diaria por años) o agregada
    desde cotizaciones_1min.

    H1.5 va a incluir job que verifica consistencia entre 1-min y daily.
    """

    __tablename__ = "cotizaciones_diarias"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrumento_id = Column(
        Integer, ForeignKey("instrumentos.id"), nullable=False
    )
    fecha = Column(Date, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False, default=0)
    volume_dolarizado = Column(Float, nullable=True)
    cantidad_operaciones = Column(Integer, nullable=True)
    fuente = Column(String(30), nullable=False)
    created_at = Column(DateTimeUTC, nullable=False, default=_ahora_utc)

    instrumento = relationship("Instrumento", back_populates="cotizaciones_diarias")

    __table_args__ = (
        UniqueConstraint("instrumento_id", "fecha", name="uq_cotdiaria_instr_fecha"),
        Index("ix_cotdiaria_instr_fecha_desc", "instrumento_id", "fecha"),
        Index("ix_cotdiaria_fecha", "fecha"),
    )

    def __repr__(self):
        return (
            f"<CotizacionDiaria(instrumento_id={self.instrumento_id}, "
            f"fecha={self.fecha}, close={self.close})>"
        )


class MacroIndicador(Base):
    """
    Indicadores macro: reservas BCRA, IPC INDEC, tasa de política, etc.

    No están atados a instrumentos. Una fila por (indicador, fecha). La
    granularidad varía: diaria para reservas/TC, mensual para IPC/EMAE.

    El campo metadata_json permite especificar detalles del indicador
    (sub-componente, fuente exacta de la serie, etc.).
    """

    __tablename__ = "macro_indicadores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    indicador = Column(String(50), nullable=False, index=True)
    fecha = Column(Date, nullable=False)
    valor = Column(Float, nullable=False)
    unidad = Column(String(30), nullable=False)
    fuente = Column(String(30), nullable=False)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTimeUTC, nullable=False, default=_ahora_utc)

    __table_args__ = (
        UniqueConstraint("indicador", "fecha", name="uq_macro_indicador_fecha"),
        Index("ix_macro_indicador_fecha_desc", "indicador", "fecha"),
    )

    def __repr__(self):
        return (
            f"<MacroIndicador(indicador='{self.indicador}', "
            f"fecha={self.fecha}, valor={self.valor})>"
        )


class LogCollector(Base):
    """
    Auditoría de corridas de collectors. Central a la filosofía 'loggear TODO'.

    Una fila por cada corrida (job) de cualquier collector. Permite responder
    preguntas como '¿tuvimos datos completos en este período?' o '¿cuántas
    veces falló Primary en la última semana?'.

    El estado 'en_curso' es transitorio: cuando una corrida arranca, se
    inserta la fila con estado en_curso; al terminar, se actualiza con el
    estado final.
    """

    __tablename__ = "log_collectors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    collector = Column(String(30), nullable=False, index=True)
    timestamp_inicio = Column(DateTimeUTC, nullable=False)
    timestamp_fin = Column(DateTimeUTC, nullable=True)
    instrumentos_procesados = Column(Integer, nullable=False, default=0)
    instrumentos_exitosos = Column(Integer, nullable=False, default=0)
    instrumentos_fallidos = Column(Integer, nullable=False, default=0)
    filas_insertadas = Column(Integer, nullable=False, default=0)
    estado = Column(String(20), nullable=False)
    errores_json = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_logcol_collector_inicio_desc", "collector", "timestamp_inicio"),
        Index("ix_logcol_estado", "estado"),
    )

    def __repr__(self):
        return (
            f"<LogCollector(collector='{self.collector}', "
            f"inicio={self.timestamp_inicio}, estado='{self.estado}')>"
        )