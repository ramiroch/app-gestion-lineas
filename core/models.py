"""Modelos de datos maestros: Cliente → Planta → Linea → Equipo → PuntoMuestreo.

Cada clase es una tabla de la base. Las relaciones son "uno a muchos":
un cliente tiene muchas plantas, una planta muchas líneas, etc.
"""

from datetime import time

from sqlalchemy import Float, ForeignKey, String, Time
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

ESTADOS_EQUIPO = ("Operativo", "Pendiente de instalación", "Fuera de servicio")


class Base(DeclarativeBase):
    """Clase madre de todos los modelos. SQLAlchemy la usa para crear las tablas."""


class Cliente(Base):
    __tablename__ = "cliente"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100))
    pais: Mapped[str] = mapped_column(String(50))

    plantas: Mapped[list["Planta"]] = relationship(back_populates="cliente")


class Planta(Base):
    """Planta de un cliente, con los parámetros que usan sus cálculos.

    pais va en la planta porque un mismo cliente puede tener plantas en
    distintos países. Los valores por defecto son los de LEDESMA (docs/mapeo_ledesma.md, sección 5):
    - blanco_ml: blanco de la titulación Scott, en mL.
    - factor_aceite_fruta: aceite en fruta (kg/t) = (mL − blanco) × N × factor / g.
    - factor_aceite_emulsion: aceite en emulsión (%) = (mL − blanco) × N × factor / g.
    - hora_inicio_dia: hora en que empieza el día de producción.
    """

    __tablename__ = "planta"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"))
    nombre: Mapped[str] = mapped_column(String(100))
    pais: Mapped[str] = mapped_column(String(50))
    blanco_ml: Mapped[float] = mapped_column(Float, default=0.2)
    factor_aceite_fruta: Mapped[float] = mapped_column(Float, default=68.8416)
    factor_aceite_emulsion: Mapped[float] = mapped_column(Float, default=3.45)
    hora_inicio_dia: Mapped[time] = mapped_column(Time, default=time(5, 0))

    cliente: Mapped["Cliente"] = relationship(back_populates="plantas")
    lineas: Mapped[list["Linea"]] = relationship(back_populates="planta")
    rangos: Mapped[list["RangoReferencia"]] = relationship(back_populates="planta")


class Linea(Base):
    __tablename__ = "linea"

    id: Mapped[int] = mapped_column(primary_key=True)
    planta_id: Mapped[int] = mapped_column(ForeignKey("planta.id"))
    nombre: Mapped[str] = mapped_column(String(100))

    planta: Mapped["Planta"] = relationship(back_populates="lineas")
    equipos: Mapped[list["Equipo"]] = relationship(back_populates="linea")


class Equipo(Base):
    """Equipo de una línea (eBOE, mBOE, JEX, desludger, etc.).

    codigo: identificador único que nunca cambia, SIGLA_PLANTA-TIPO-NN (por ejemplo
    LED-BOE-01); ver core/codigos_equipo.py.
    nombre: el nombre del equipo en Maintenance Connection (MC).
    estado: uno de ESTADOS_EQUIPO; por defecto "Operativo".
    modelo, serie_mc, numero_serie, codigo_activo_mc y numero_activo_fijo son
    opcionales porque muchas veces no se conocen al dar de alta el equipo.
    """

    __tablename__ = "equipo"

    id: Mapped[int] = mapped_column(primary_key=True)
    linea_id: Mapped[int] = mapped_column(ForeignKey("linea.id"))
    codigo: Mapped[str] = mapped_column(String(30), unique=True)  # por ejemplo "LED-BOE-01"
    nombre: Mapped[str] = mapped_column(String(100))  # por ejemplo "JEX N° 2"
    tipo: Mapped[str] = mapped_column(String(50))  # por ejemplo "eBOE"
    numero: Mapped[int]  # eBOE 1, eBOE 2, ...
    estado: Mapped[str] = mapped_column(String(30), default="Operativo")
    modelo: Mapped[str | None] = mapped_column(String(50))  # por ejemplo "6107E"
    serie_mc: Mapped[str | None] = mapped_column(String(50))  # campo "Series" de MC
    numero_serie: Mapped[str | None] = mapped_column(String(50))
    codigo_activo_mc: Mapped[str | None] = mapped_column(String(50))  # Maintenance Connection
    # "FIXED ASSET NBR" de MC. No es único: hay números repetidos entre equipos.
    numero_activo_fijo: Mapped[str | None] = mapped_column(String(50))

    linea: Mapped["Linea"] = relationship(back_populates="equipos")
    puntos_muestreo: Mapped[list["PuntoMuestreo"]] = relationship(back_populates="equipo")

    @validates("codigo")
    def validar_codigo(self, clave, codigo):
        """Controla que el código no esté vacío y que nunca cambie una vez asignado."""
        if not codigo:
            raise ValueError("El equipo necesita un código.")
        if self.codigo is not None and codigo != self.codigo:
            raise ValueError(f'El código de un equipo nunca cambia ("{self.codigo}" → "{codigo}").')
        return codigo

    @validates("estado")
    def validar_estado(self, clave, estado):
        """Controla que el estado sea uno de ESTADOS_EQUIPO."""
        if estado not in ESTADOS_EQUIPO:
            raise ValueError(
                f'Estado "{estado}" no válido. Usá uno de: {", ".join(ESTADOS_EQUIPO)}.'
            )
        return estado


class PuntoMuestreo(Base):
    """Lugar de un equipo donde se toma una muestra.

    tipo: "fruta entrada", "fruta salida" o "emulsión".
    """

    __tablename__ = "punto_muestreo"

    id: Mapped[int] = mapped_column(primary_key=True)
    equipo_id: Mapped[int] = mapped_column(ForeignKey("equipo.id"))
    tipo: Mapped[str] = mapped_column(String(50))
    nombre: Mapped[str] = mapped_column(String(100))

    equipo: Mapped["Equipo"] = relationship(back_populates="puntos_muestreo")


class RangoReferencia(Base):
    """Rango objetivo de una variable en una planta (por ejemplo, eficiencia eBOE 80–90 %).

    punto_o_equipo es texto libre ("BOE (todos)", "Emulsión (licor)", "Planta").
    minimo y maximo son opcionales: hay rangos con un solo límite (°Brix ≤ 5).
    """

    __tablename__ = "rango_referencia"

    id: Mapped[int] = mapped_column(primary_key=True)
    planta_id: Mapped[int] = mapped_column(ForeignKey("planta.id"))
    variable: Mapped[str] = mapped_column(String(100))
    punto_o_equipo: Mapped[str | None] = mapped_column(String(100))
    fruta: Mapped[str | None] = mapped_column(String(50))
    minimo: Mapped[float | None] = mapped_column(Float)
    maximo: Mapped[float | None] = mapped_column(Float)
    unidad: Mapped[str | None] = mapped_column(String(50))
    fuente: Mapped[str | None] = mapped_column(String(200))

    planta: Mapped["Planta"] = relationship(back_populates="rangos")
