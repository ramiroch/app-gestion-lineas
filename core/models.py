"""Modelos de datos.

Maestros: Cliente → Planta → Linea → Equipo → PuntoMuestreo, más Turno, Proceso y
RangoReferencia. Datos de planta: AnalisisScott, LecturaOperacion y RegistroProduccion.

Cada clase es una tabla de la base. Las relaciones son "uno a muchos":
un cliente tiene muchas plantas, una planta muchas líneas, etc.
Regla: se guardan los datos crudos; los resultados (aceite %, kg/MT, eficiencia,
turno, fecha de producción) se calculan, nunca se guardan.
"""

from datetime import date, datetime, time

from sqlalchemy import (Date, DateTime, Float, ForeignKey, String, Text, Time,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

ESTADOS_EQUIPO = ("Operativo", "Pendiente de instalación", "Fuera de servicio")


class Base(DeclarativeBase):
    """Clase madre de todos los modelos. SQLAlchemy la usa para crear las tablas."""


class Auditoria:
    """Quién cargó el registro, cuándo y de dónde salió. Las tablas de datos de planta lo heredan.

    cargado_por: usuario, o el importador.
    cargado_en: se completa solo con la fecha y hora de carga.
    origen: archivo, hoja y fila del dato importado (ver texto_origen()); vacío si se cargó a mano.
    """

    cargado_por: Mapped[str] = mapped_column(String(100))
    cargado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    origen: Mapped[str | None] = mapped_column(String(300))


def texto_origen(archivo: str, hoja: str, fila: int) -> str:
    """Arma el campo origen: "eficiencia eBOE Naranja 25 2.xlsm › BOE › fila 125"."""
    return f"{archivo} › {hoja} › fila {fila}"


def _mayor_que_cero(nombre: str, valor):
    """Da error si el valor no es mayor que 0. None se acepta (campo opcional)."""
    if valor is not None and valor <= 0:
        raise ValueError(f'"{nombre}" tiene que ser mayor que 0 (dice {valor}).')
    return valor


def _no_negativo(nombre: str, valor):
    """Da error si el valor es negativo. None se acepta (campo opcional)."""
    if valor is not None and valor < 0:
        raise ValueError(f'"{nombre}" no puede ser negativo (dice {valor}).')
    return valor


class Cliente(Base):
    __tablename__ = "cliente"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100))
    pais: Mapped[str] = mapped_column(String(50))

    plantas: Mapped[list["Planta"]] = relationship(back_populates="cliente")


class Planta(Base):
    """Planta de un cliente, con los parámetros que usan sus cálculos.

    pais va en la planta porque un mismo cliente puede tener plantas en
    distintos países. Parámetros de cálculo (opcionales, sin valor por defecto: cada
    planta tiene los suyos; los cálculos los leen con core/parametros.py):
    - blanco_ml: blanco de la titulación Scott, en mL.
    - factor_aceite_fruta: aceite en fruta (kg/MT) = (mL − blanco) × N × factor / g.
    - factor_aceite_emulsion: aceite en emulsión (%) = (mL − blanco) × N × factor / g.
    - hora_inicio_dia: hora en que empieza el día de producción.
    """

    __tablename__ = "planta"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("cliente.id"))
    nombre: Mapped[str] = mapped_column(String(100))
    pais: Mapped[str] = mapped_column(String(50))
    blanco_ml: Mapped[float | None] = mapped_column(Float)
    factor_aceite_fruta: Mapped[float | None] = mapped_column(Float)
    factor_aceite_emulsion: Mapped[float | None] = mapped_column(Float)
    hora_inicio_dia: Mapped[time | None] = mapped_column(Time)

    cliente: Mapped["Cliente"] = relationship(back_populates="plantas")
    lineas: Mapped[list["Linea"]] = relationship(back_populates="planta")
    rangos: Mapped[list["RangoReferencia"]] = relationship(back_populates="planta")
    turnos: Mapped[list["Turno"]] = relationship(back_populates="planta")
    procesos: Mapped[list["Proceso"]] = relationship(back_populates="planta")
    registros_produccion: Mapped[list["RegistroProduccion"]] = relationship(back_populates="planta")


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
    lecturas: Mapped[list["LecturaOperacion"]] = relationship(back_populates="equipo")

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
    analisis: Mapped[list["AnalisisScott"]] = relationship(back_populates="punto_muestreo")


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


class Turno(Base):
    """Turno de trabajo de una planta (por ejemplo M de 05:00 a 12:59).

    hora_fin es el último minuto del turno. Si hora_inicio > hora_fin, el turno
    cruza la medianoche (N de 21:00 a 04:59). El turno de una hora se calcula con
    core.turnos.turno_de().
    """

    __tablename__ = "turno"
    __table_args__ = (UniqueConstraint("planta_id", "nombre"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    planta_id: Mapped[int] = mapped_column(ForeignKey("planta.id"))
    nombre: Mapped[str] = mapped_column(String(20))  # "M", "T", "N"
    hora_inicio: Mapped[time] = mapped_column(Time)
    hora_fin: Mapped[time] = mapped_column(Time)

    planta: Mapped["Planta"] = relationship(back_populates="turnos")


class Proceso(Base):
    """Proceso (corrida de producción) de una planta, numerado dentro de cada temporada."""

    __tablename__ = "proceso"
    __table_args__ = (UniqueConstraint("planta_id", "temporada", "numero"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    planta_id: Mapped[int] = mapped_column(ForeignKey("planta.id"))
    numero: Mapped[int]
    temporada: Mapped[int]  # año, por ejemplo 2025

    planta: Mapped["Planta"] = relationship(back_populates="procesos")
    analisis: Mapped[list["AnalisisScott"]] = relationship(back_populates="proceso")


class AnalisisScott(Auditoria, Base):
    """Análisis de aceite por titulación Scott: solo datos crudos, nunca el resultado.

    El resultado se calcula: (vol_ml − blanco_ml) × normalidad × factor / peso_muestra_g,
    con el factor de la planta según el punto (fruta en kg/MT o emulsión en %).
    Datos crudos: peso_muestra_g (g), vol_ml (mL de titulante), blanco_ml (mL),
    normalidad (N del BrO3, ≈ 0,100 en fruta de entrada y ≈ 0,010 en salida).
    Opcionales de fruta: peso_fruta_kg, n_frutos, calidad, rallado.
    Opcionales de emulsión: ph, brix (°Brix), solidos_centrifugables (%).
    Único por punto de muestreo + fecha_hora.
    """

    __tablename__ = "analisis_scott"
    __table_args__ = (UniqueConstraint("punto_muestreo_id", "fecha_hora"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    punto_muestreo_id: Mapped[int] = mapped_column(ForeignKey("punto_muestreo.id"))
    fecha_hora: Mapped[datetime] = mapped_column(DateTime)
    proceso_id: Mapped[int] = mapped_column(ForeignKey("proceso.id"))
    analista: Mapped[str] = mapped_column(String(100))
    fruta: Mapped[str | None] = mapped_column(String(50))  # especie, por ejemplo "N Val S/R"
    peso_muestra_g: Mapped[float] = mapped_column(Float)
    vol_ml: Mapped[float] = mapped_column(Float)
    blanco_ml: Mapped[float] = mapped_column(Float)
    normalidad: Mapped[float] = mapped_column(Float)
    peso_fruta_kg: Mapped[float | None] = mapped_column(Float)
    n_frutos: Mapped[int | None]
    calidad: Mapped[str | None] = mapped_column(String(50))  # Firme, Blanda, Podrido, Partida
    rallado: Mapped[str | None] = mapped_column(String(50))  # Ok, Poco rallada, Sobre rallada
    ph: Mapped[float | None] = mapped_column(Float)
    brix: Mapped[float | None] = mapped_column(Float)
    solidos_centrifugables: Mapped[float | None] = mapped_column(Float)
    ticket: Mapped[str | None] = mapped_column(String(50))
    licuadora: Mapped[str | None] = mapped_column(String(50))
    observaciones: Mapped[str | None] = mapped_column(Text)

    punto_muestreo: Mapped["PuntoMuestreo"] = relationship(back_populates="analisis")
    proceso: Mapped["Proceso"] = relationship(back_populates="analisis")

    @validates("peso_muestra_g", "normalidad", "peso_fruta_kg")
    def validar_positivos(self, clave, valor):
        return _mayor_que_cero(clave, valor)

    @validates("vol_ml", "blanco_ml", "n_frutos", "brix", "solidos_centrifugables")
    def validar_no_negativos(self, clave, valor):
        return _no_negativo(clave, valor)

    @validates("ph")
    def validar_ph(self, clave, valor):
        if valor is not None and not 0 <= valor <= 14:
            raise ValueError(f'"ph" tiene que estar entre 0 y 14 (dice {valor}).')
        return valor


class LecturaOperacion(Auditoria, Base):
    """Lectura de operación de un equipo en un momento dado.

    programa: N0–N9. flip_gate: posición 1–12. tph: toneladas por hora.
    caudal_fase_pesada: medido, en m³/h. Única por equipo + fecha_hora.
    """

    __tablename__ = "lectura_operacion"
    __table_args__ = (UniqueConstraint("equipo_id", "fecha_hora"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    equipo_id: Mapped[int] = mapped_column(ForeignKey("equipo.id"))
    fecha_hora: Mapped[datetime] = mapped_column(DateTime)
    operador: Mapped[str | None] = mapped_column(String(100))
    programa: Mapped[str | None] = mapped_column(String(10))
    flip_gate: Mapped[int | None]
    tph: Mapped[float | None] = mapped_column(Float)
    caudal_fase_pesada: Mapped[float | None] = mapped_column(Float)

    equipo: Mapped["Equipo"] = relationship(back_populates="lecturas")

    @validates("flip_gate")
    def validar_flip_gate(self, clave, valor):
        if valor is not None and not 1 <= valor <= 12:
            raise ValueError(f'"flip_gate" tiene que estar entre 1 y 12 (dice {valor}).')
        return valor

    @validates("tph", "caudal_fase_pesada")
    def validar_no_negativos(self, clave, valor):
        return _no_negativo(clave, valor)


class RegistroProduccion(Auditoria, Base):
    """Producción de una planta en un turno de un día de producción (carga manual).

    fruta_t: fruta procesada (t). aceite_envasado_kg, jugo_conc_kg y reproceso_kg en kg.
    qf3 y brix_fritz: datos de laboratorio de otros archivos.
    Único por planta + fecha de producción + turno + proceso: un turno puede tener dos
    registros si en él termina un proceso y empieza otro. Las toneladas del turno se
    obtienen sumando sus registros (core.produccion.toneladas_turno). El proceso es
    obligatorio en lo importado del Excel.
    """

    __tablename__ = "registro_produccion"
    __table_args__ = (UniqueConstraint("planta_id", "fecha_produccion", "turno_id", "proceso_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    planta_id: Mapped[int] = mapped_column(ForeignKey("planta.id"))
    fecha_produccion: Mapped[date] = mapped_column(Date)
    turno_id: Mapped[int] = mapped_column(ForeignKey("turno.id"))
    proceso_id: Mapped[int | None] = mapped_column(ForeignKey("proceso.id"))
    fruta_t: Mapped[float | None] = mapped_column(Float)
    aceite_envasado_kg: Mapped[float | None] = mapped_column(Float)
    jugo_conc_kg: Mapped[float | None] = mapped_column(Float)
    reproceso_kg: Mapped[float | None] = mapped_column(Float)
    qf3: Mapped[float | None] = mapped_column(Float)
    brix_fritz: Mapped[float | None] = mapped_column(Float)

    planta: Mapped["Planta"] = relationship(back_populates="registros_produccion")
    turno: Mapped["Turno"] = relationship()
    proceso: Mapped["Proceso | None"] = relationship()

    @validates("fruta_t", "aceite_envasado_kg", "jugo_conc_kg", "reproceso_kg", "qf3", "brix_fritz")
    def validar_no_negativos(self, clave, valor):
        return _no_negativo(clave, valor)
