"""
Modelos de SQLAlchemy para la base de datos de Argo.

Define las tablas principales del sistema:
- instrumentos: catálogo maestro de instrumentos del universo
- instrumento_broker_mapping: mapeo de instrumentos a símbolos de cada broker
- cotizaciones_1min: datos intradía con granularidad de 1 minuto
- cotizaciones_diarias: datos OHLCV diarios
- macro_indicadores: indicadores BCRA, INDEC, otras fuentes macro
- log_collectors: auditoría de corridas de collectors

Decisiones de diseño documentadas en docs/db_schema.md.

Los modelos NO contienen lógica de negocio. Solo definen estructura y relaciones.
La lógica vive en src/utils/db.py (conexión, sesiones) y módulos específicos
(collectors, estrategias).

Convención de fechas: TODO datetime persistido en Argo es UTC-aware (con
tzinfo=timezone.utc explícito). Los timestamps del sistema son UTC; declararlo
en el tipo evita mezclar naive y aware, que en Python lanza TypeError al
comparar. Ver helper _ahora_utc abajo.
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
    created_at = Column(DateTime, nullable=False, default=_ahora_utc)
    updated_at = Column(
        DateTime, nullable=False, default=_ahora_utc, onupdate=_ahora_utc
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
    fecha_validacion = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_ahora_utc)
    updated_at = Column(
        DateTime, nullable=False, default=_ahora_utc, onupdate=_ahora_utc
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
    timestamp = Column(DateTime, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Integer, nullable=False, default=0)
    volume_dolarizado = Column(Float, nullable=True)
    cantidad_operaciones = Column(Integer, nullable=True)
    fuente = Column(String(30), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_ahora_utc)

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
    created_at = Column(DateTime, nullable=False, default=_ahora_utc)

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
    created_at = Column(DateTime, nullable=False, default=_ahora_utc)

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
    timestamp_inicio = Column(DateTime, nullable=False)
    timestamp_fin = Column(DateTime, nullable=True)
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