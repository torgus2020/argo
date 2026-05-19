"""
Modelos de SQLAlchemy para la base de datos de Argo.

Define las cinco tablas principales del sistema:
- instrumentos: catálogo maestro de instrumentos del universo
- cotizaciones_1min: datos intradía con granularidad de 1 minuto
- cotizaciones_diarias: datos OHLCV diarios
- macro_indicadores: indicadores BCRA, INDEC, otras fuentes macro
- log_collectors: auditoría de corridas de collectors

Decisiones de diseño documentadas en docs/db_schema.md (creado en H1.1.8).

Los modelos NO contienen lógica de negocio. Solo definen estructura y relaciones.
La lógica vive en src/utils/db.py (conexión, sesiones) y módulos específicos
(collectors, estrategias).
"""

from datetime import datetime
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
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relaciones inversas (no crean columnas, solo facilitan navegar desde el código)
    cotizaciones_1min = relationship(
        "Cotizacion1Min", back_populates="instrumento", cascade="all, delete-orphan"
    )
    cotizaciones_diarias = relationship(
        "CotizacionDiaria", back_populates="instrumento", cascade="all, delete-orphan"
    )
    __table_args__ = (
        UniqueConstraint("ticker", "mercado", name="uq_instrumento_ticker_mercado"),
    )

    def __repr__(self):
        return f"<Instrumento(id={self.id}, ticker='{self.ticker}', tipo='{self.tipo}')>"


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
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

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
    histórico de Rava (que tiene granularidad diaria por años) o agregada
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
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

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
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

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
    veces falló Rava en la última semana?'.

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