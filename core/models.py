"""Modelos de datos maestros: Cliente → Planta → Linea → Equipo → PuntoMuestreo.

Cada clase es una tabla de la base. Las relaciones son "uno a muchos":
un cliente tiene muchas plantas, una planta muchas líneas, etc.
"""

from datetime import time

from sqlalchemy import Float, ForeignKey, String, Time
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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

    Los valores por defecto son los de LEDESMA (docs/mapeo_ledesma.md, sección 5):
    - blanco_ml: blanco de la titulación Scott, en mL.
    - factor_aceite_fruta: aceite en fruta (kg/t) = (mL − blanco) × N × factor / g.
    - factor_aceite_emulsion: aceite en emulsión (%) = (mL − blanco) × N × factor / g.
    - hora_inicio_dia: hora en que empieza el día de producción.
    """

    __tablename__ = "planta"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"))
    nombre: Mapped[str] = mapped_column(String(100))
    blanco_ml: Mapped[float] = mapped_column(Float, default=0.2)
    factor_aceite_fruta: Mapped[float] = mapped_column(Float, default=68.8416)
    factor_aceite_emulsion: Mapped[float] = mapped_column(Float, default=3.45)
    hora_inicio_dia: Mapped[time] = mapped_column(Time, default=time(5, 0))

    cliente: Mapped["Cliente"] = relationship(back_populates="plantas")
    lineas: Mapped[list["Linea"]] = relationship(back_populates="planta")


class Linea(Base):
    __tablename__ = "linea"

    id: Mapped[int] = mapped_column(primary_key=True)
    planta_id: Mapped[int] = mapped_column(ForeignKey("planta.id"))
    nombre: Mapped[str] = mapped_column(String(100))

    planta: Mapped["Planta"] = relationship(back_populates="lineas")
    equipos: Mapped[list["Equipo"]] = relationship(back_populates="linea")


class Equipo(Base):
    """Equipo de una línea (eBOE, mBOE, JEX, desludger, etc.).

    modelo, numero_serie y codigo_activo_mc son opcionales porque muchas veces
    no se conocen al dar de alta el equipo.
    """

    __tablename__ = "equipo"

    id: Mapped[int] = mapped_column(primary_key=True)
    linea_id: Mapped[int] = mapped_column(ForeignKey("linea.id"))
    tipo: Mapped[str] = mapped_column(String(50))  # por ejemplo "eBOE"
    numero: Mapped[int]  # eBOE 1, eBOE 2, ...
    modelo: Mapped[str | None] = mapped_column(String(50))  # por ejemplo "6107E"
    numero_serie: Mapped[str | None] = mapped_column(String(50))
    codigo_activo_mc: Mapped[str | None] = mapped_column(String(50))  # Maintenance Connection

    linea: Mapped["Linea"] = relationship(back_populates="equipos")
    puntos_muestreo: Mapped[list["PuntoMuestreo"]] = relationship(back_populates="equipo")


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
