"""Modelos de datos.

Maestros: Cliente → Planta → Linea → Equipo → PuntoMuestreo, más Turno, Proceso y
RangoReferencia. Datos de planta: AnalisisScott, LecturaOperacion, LecturaPLC, MuestraCentrifuga y
RegistroProduccion. Control: LimitesControl (con LimitesExclusion), Alerta (con AlertaDato).

Cada clase es una tabla de la base. Las relaciones son "uno a muchos":
un cliente tiene muchas plantas, una planta muchas líneas, etc.
Regla: se guardan los datos crudos; los resultados (aceite %, kg/MT, eficiencia,
turno, fecha de producción) se calculan, nunca se guardan.
"""

from datetime import date, datetime, time

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, String, Text, Time,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

ESTADOS_EQUIPO = ("Operativo", "Pendiente de instalación", "Fuera de servicio")
DESTINOS_FASE_PESADA = ("recircula al BOE", "canal (pérdida)", "no aplica")   # no aplica: pulidora
LADOS_CAPACIDAD = ("ambos", "inferior", "superior")
TIPOS_CARTA = ("I-MR", "Xbar-S", "I-MR-S")      # I-MR-S: entre/dentro (I-MR de los promedios + S)
REGLAS_ALERTA = "1,2,3"                    # reglas de Nelson que crean alertas (del lado malo de la variable)
REGLAS_MARCA_IMR = "5,MR"                  # I-MR: se marcan en la carta, no alertan ("MR": rango móvil > LCS)
REGLAS_MARCA_POR_TURNO = "5,S"             # X̄-S y entre/dentro: se marcan, no alertan ("S": desvío del turno)
ORIGENES_ALERTA = ("nelson", "rango")
ESTADOS_ALERTA = ("abierta", "en análisis", "cerrada", "histórico")


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
    - blanco_centrifugas_ml: blanco de la titulación Scott de las muestras de centrífugas, en mL
      (distinto del blanco del BOE).
    - umbral_paralelo_lh: caudal de emulsión (L/h) hasta el que dos primarias que aparecen en el mismo turno se
      consideran en paralelo (regla inferida, core.analisis_centrifugas.primarias_por_turno).
    - turnos_fin_episodio: turnos seguidos sin violar una regla que cierran un episodio de alerta (core.alertas).
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
    blanco_centrifugas_ml: Mapped[float | None] = mapped_column(Float)
    umbral_paralelo_lh: Mapped[float | None] = mapped_column(Float)
    turnos_fin_episodio: Mapped[int | None]

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
    volumen_descarga_l: solo centrífugas; volumen que larga en cada descarga, en L (igual al espacio
    de sólidos del tambor). Vacío = a definir: sin él no se calcula la pérdida en descarga.
    destino_fase_pesada: solo centrífugas; adónde va su fase pesada (uno de DESTINOS_FASE_PESADA). El
    aceite de la fase pesada es pérdida del proceso solo si va al canal.
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
    volumen_descarga_l: Mapped[float | None] = mapped_column(Float)
    destino_fase_pesada: Mapped[str | None] = mapped_column(String(30))

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

    @validates("volumen_descarga_l")
    def validar_volumen(self, clave, valor):
        return _mayor_que_cero(clave, valor)

    @validates("destino_fase_pesada")
    def validar_destino(self, clave, valor):
        if valor is not None and valor not in DESTINOS_FASE_PESADA:
            raise ValueError(f'Destino de la fase pesada "{valor}" no válido. Usá uno de: '
                             f'{", ".join(DESTINOS_FASE_PESADA)}.')
        return valor


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
    genera_alerta: False si el rango es solo una referencia (se dibuja, pero estar afuera no genera una
    alerta; por ejemplo, cuando las alertas salen de la carta de control con límites de la planta).
    lado_capacidad: qué límites son especificación para la capacidad (uno de LADOS_CAPACIDAD). Con "inferior" solo
    cuenta el mínimo (eficiencia ≥ 80 %); el rango completo sigue siendo la banda objetivo. Vacío = "ambos".
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
    genera_alerta: Mapped[bool | None] = mapped_column(Boolean, default=True)
    lado_capacidad: Mapped[str | None] = mapped_column(String(10))

    planta: Mapped["Planta"] = relationship(back_populates="rangos")

    @validates("lado_capacidad")
    def validar_lado(self, clave, valor):
        if valor is not None and valor not in LADOS_CAPACIDAD:
            raise ValueError(f'Lado "{valor}" no válido. Usá uno de: {", ".join(LADOS_CAPACIDAD)}.')
        return valor


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


class LecturaPLC(Auditoria, Base):
    """Lectura del PLC de un eBOE (un registro cada 5 minutos, importado con core/importar_plc.py).

    horas_maquina y minutos: horómetro del equipo (horas enteras + minutos). elevador: 1 si el
    elevador de fruta está en marcha, 0 si no. programa: tal como lo registra el PLC, sin el texto
    "Programa" ("N3" o "9"). ton_seteadas: setpoint de toneladas por hora; ton_calculadas: las que
    calcula el PLC (baldes por minuto × kg por balde, no un peso real). Única por equipo + fecha_hora.
    """

    __tablename__ = "lectura_plc"
    __table_args__ = (UniqueConstraint("equipo_id", "fecha_hora"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    equipo_id: Mapped[int] = mapped_column(ForeignKey("equipo.id"))
    fecha_hora: Mapped[datetime] = mapped_column(DateTime)
    horas_maquina: Mapped[float | None] = mapped_column(Float)
    minutos: Mapped[float | None] = mapped_column(Float)
    elevador: Mapped[int | None]
    programa: Mapped[str | None] = mapped_column(String(10))
    ton_seteadas: Mapped[float | None] = mapped_column(Float)
    ton_calculadas: Mapped[float | None] = mapped_column(Float)

    equipo: Mapped["Equipo"] = relationship()

    @validates("elevador")
    def validar_elevador(self, clave, valor):
        if valor is not None and valor not in (0, 1):
            raise ValueError(f'"elevador" tiene que ser 0 o 1 (dice {valor}).')
        return valor

    @validates("horas_maquina", "minutos", "ton_seteadas", "ton_calculadas")
    def validar_no_negativos(self, clave, valor):
        return _no_negativo(clave, valor)


FASES_CENTRIFUGA = ("emulsion", "fase_liviana", "fase_pesada", "descarga")
MARCAS_SOLIDOS = ("menor que", "rango")   # "<1" → 1; ">7 y <8" → 7,5 (punto medio)
# CP liviana de la pulidora en 0: se guarda como viene, fuera de estadísticas y gráficos.
MARCAS_CP = ("no medible (valor cercano a 0 o manómetro a revisar)",)


class MuestraCentrifuga(Auditoria, Base):
    """Muestra de una fase de una centrífuga (primaria, secundaria o pulidora): solo datos crudos.

    fase: una de FASES_CENTRIFUGA. El aceite % se calcula con core/centrifugas.py:
    (ml − blanco_ml) × normalidad × factor / gramos. En la fase liviana no se titula: el aceite %
    se anota directo y va en aceite_pct_medido.
    caudal_lh: caudal medido (L/h); vacío si en la planilla es un cálculo (fase liviana y pesada).
    solidos_pct: sólidos centrifugables (%). Si la planilla trae texto ("<1"), el texto original va en
    solidos_texto, el número en solidos_pct y solidos_marca dice cómo leerlo (una de MARCAS_SOLIDOS).
    cp_liviana_bar y cp_pesada_bar: contrapresión de cada fase (bar). t_descarga_s: intervalo entre
    descargas (s). cp_liviana_marca: cómo leer la CP liviana (una de MARCAS_CP). revisar: motivo por el
    que la fila se importó tal cual pero hay que mirarla (por ejemplo, una secundaria anotada como
    primaria); vacío si está bien. fecha_original: la Fecha de la planilla cuando el importador la
    corrigió (vacía si no se corrigió); sirve para deshacer la corrección.
    El turno y la fecha de producción no se guardan: se calculan con core/turnos.py.
    Única por equipo + fecha_hora + fase.
    """

    __tablename__ = "muestra_centrifuga"
    __table_args__ = (UniqueConstraint("equipo_id", "fecha_hora", "fase"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    equipo_id: Mapped[int] = mapped_column(ForeignKey("equipo.id"))
    fecha_hora: Mapped[datetime] = mapped_column(DateTime)
    fase: Mapped[str] = mapped_column(String(20))
    proceso_id: Mapped[int | None] = mapped_column(ForeignKey("proceso.id"))
    fruta: Mapped[str | None] = mapped_column(String(50))  # especie, por ejemplo "N Val S/R"
    analista: Mapped[str | None] = mapped_column(String(100))  # columna "Químico" de la planilla
    operador: Mapped[str | None] = mapped_column(String(100))
    caudal_lh: Mapped[float | None] = mapped_column(Float)
    gramos: Mapped[float | None] = mapped_column(Float)
    ml: Mapped[float | None] = mapped_column(Float)
    normalidad: Mapped[float | None] = mapped_column(Float)
    blanco_ml: Mapped[float | None] = mapped_column(Float)
    aceite_pct_medido: Mapped[float | None] = mapped_column(Float)
    solidos_pct: Mapped[float | None] = mapped_column(Float)
    solidos_texto: Mapped[str | None] = mapped_column(String(30))
    solidos_marca: Mapped[str | None] = mapped_column(String(20))
    cp_liviana_bar: Mapped[float | None] = mapped_column(Float)
    cp_pesada_bar: Mapped[float | None] = mapped_column(Float)
    cp_liviana_marca: Mapped[str | None] = mapped_column(String(60))
    t_descarga_s: Mapped[float | None] = mapped_column(Float)
    observacion: Mapped[str | None] = mapped_column(Text)
    revisar: Mapped[str | None] = mapped_column(String(200))
    fecha_original: Mapped[date | None] = mapped_column(Date)

    equipo: Mapped["Equipo"] = relationship()
    proceso: Mapped["Proceso | None"] = relationship()

    @validates("fase")
    def validar_fase(self, clave, valor):
        if valor not in FASES_CENTRIFUGA:
            raise ValueError(f'Fase "{valor}" no válida. Usá una de: {", ".join(FASES_CENTRIFUGA)}.')
        return valor

    @validates("solidos_marca", "cp_liviana_marca")
    def validar_marca(self, clave, valor):
        marcas = MARCAS_SOLIDOS if clave == "solidos_marca" else MARCAS_CP
        if valor is not None and valor not in marcas:
            raise ValueError(f'Marca "{valor}" no válida en {clave}. Usá una de: {", ".join(marcas)}.')
        return valor

    @validates("gramos", "normalidad")
    def validar_positivos(self, clave, valor):
        return _mayor_que_cero(clave, valor)

    @validates("caudal_lh", "ml", "blanco_ml", "cp_liviana_bar", "cp_pesada_bar", "t_descarga_s")
    def validar_no_negativos(self, clave, valor):
        return _no_negativo(clave, valor)

    @validates("aceite_pct_medido", "solidos_pct")
    def validar_porcentaje(self, clave, valor):
        if valor is not None and not 0 <= valor <= 100:
            raise ValueError(f'"{clave}" tiene que estar entre 0 y 100 (dice {valor}).')
        return valor


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


class LimitesControl(Base):
    """Límites de una carta de control congelados en la fase I (período base), para evaluar los datos nuevos.

    variable: clave de core.limites.VARIABLES_CONTROL ("eficiencia", "cp_pesada"…); el punto de muestreo sale de
    la variable. Ámbito: un equipo (equipo_id) o, en las variables de planta como el pH de la emulsión (promedio de
    los 3 eBOE), la planta (planta_id); uno de los dos. Un solo registro vigente por variable + ámbito + fruta: al
    congelar otro, el anterior queda como historial (vigente = False).
    tipo_carta "I-MR" (un punto por muestra): lc, lcs, lci y sigma de la carta I; mr_lc y mr_lcs de la MR.
    tipo_carta "Xbar-S" (un punto por turno, n variable): lc = x̿ y sigma = σ del proceso (método S barra); los
    límites de cada turno dependen de su n (core.spc.limites_xbar_s_para). lcs, lci, mr_lc y mr_lcs se guardan para
    el tamaño típico n_subgrupo (mediana): X̄ y carta S (mr_lc = LC de S, mr_lcs = LCS de S).
    tipo_carta "I-MR-S" (entre/dentro, Minitab I-MR-R/S, un punto por turno): lc = media de los promedios y
    sigma = σ entre turnos (MR̄ de los promedios / d2), con lcs y lci iguales para todo n; mr_lc y mr_lcs de la MR
    de los promedios; sigma_dentro = σ dentro del turno (S barra) para la carta S de cada n.
    reglas_alerta: reglas que crean alertas (Nelson "1,2,3"; también "MR" o "S" si se quisiera). reglas_marca: se
    calculan y se marcan en la carta, pero no alertan (por defecto "5,S" por turno y "5,MR" en I-MR).
    """

    __tablename__ = "limites_control"

    id: Mapped[int] = mapped_column(primary_key=True)
    variable: Mapped[str] = mapped_column(String(30))
    equipo_id: Mapped[int | None] = mapped_column(ForeignKey("equipo.id"))
    planta_id: Mapped[int | None] = mapped_column(ForeignKey("planta.id"))
    fruta: Mapped[str | None] = mapped_column(String(20))
    tipo_carta: Mapped[str] = mapped_column(String(10), default="I-MR")
    n_subgrupo: Mapped[int | None]
    base_desde: Mapped[date] = mapped_column(Date)
    base_hasta: Mapped[date] = mapped_column(Date)
    n: Mapped[int]
    lc: Mapped[float] = mapped_column(Float)
    lcs: Mapped[float] = mapped_column(Float)
    lci: Mapped[float] = mapped_column(Float)
    sigma: Mapped[float] = mapped_column(Float)
    sigma_dentro: Mapped[float | None] = mapped_column(Float)
    mr_lc: Mapped[float | None] = mapped_column(Float)        # vacíos en X̄-S si el turno típico tiene 1 muestra
    mr_lcs: Mapped[float | None] = mapped_column(Float)
    reglas_alerta: Mapped[str] = mapped_column(String(40), default=REGLAS_ALERTA)
    reglas_marca: Mapped[str | None] = mapped_column(String(40))
    vigente: Mapped[bool] = mapped_column(Boolean, default=True)
    congelado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    congelado_por: Mapped[str] = mapped_column(String(100))
    comentario: Mapped[str | None] = mapped_column(Text)

    equipo: Mapped["Equipo | None"] = relationship()
    planta: Mapped["Planta | None"] = relationship()
    exclusiones: Mapped[list["LimitesExclusion"]] = relationship(back_populates="limites")

    @validates("tipo_carta")
    def validar_tipo(self, clave, valor):
        if valor not in TIPOS_CARTA:
            raise ValueError(f'Tipo de carta "{valor}" no válido. Usá uno de: {", ".join(TIPOS_CARTA)}.')
        return valor


class LimitesExclusion(Base):
    """Un dato que se dejó afuera del cálculo de unos límites de control, con su motivo (obligatorio).

    tabla y dato_id: de dónde sale el dato ("analisis_scott" o "muestra_centrifuga" y su id).
    """

    __tablename__ = "limites_exclusion"

    id: Mapped[int] = mapped_column(primary_key=True)
    limites_id: Mapped[int] = mapped_column(ForeignKey("limites_control.id"))
    tabla: Mapped[str] = mapped_column(String(30))
    dato_id: Mapped[int]
    fecha_hora: Mapped[datetime | None] = mapped_column(DateTime)
    valor: Mapped[float | None] = mapped_column(Float)
    motivo: Mapped[str] = mapped_column(String(300))

    limites: Mapped["LimitesControl"] = relationship(back_populates="exclusiones")

    @validates("motivo")
    def validar_motivo(self, clave, valor):
        if not valor or not str(valor).strip():
            raise ValueError("Excluir un dato de los límites necesita un motivo.")
        return valor


class Alerta(Base):
    """Alerta de control: una regla de Nelson (o "MR") contra los límites vigentes, o un dato fuera del rango de
    RangoReferencia con genera_alerta = sí ("rango").

    Ámbito: un equipo (equipo_id) o la planta (planta_id, variables de planta como el pH); ambito = "equipo:5" o
    "planta:8" (no vacío, para que la base impida duplicados). Una sola alerta por origen + regla + variable +
    ámbito + fecha de producción + turno: si en el mismo turno vuelve a fallar, se le suman datos (AlertaDato).
    Excepción a la regla general: acá el turno y la fecha de producción SE GUARDAN, porque la alerta es un evento y
    los necesita para no duplicarse.
    fecha_hora: la de la primera muestra del turno; valor: el promedio del turno (o su desvío, regla "S"); limite: el
    que cruzó. estado: uno de ESTADOS_ALERTA ("histórico" = creada al evaluar datos importados, no se gestiona).
    comun_eboe: la misma regla y variable saltó en los 3 eBOE en el mismo turno (posible causa común).
    Episodio: una alerta junta los turnos seguidos que violan la misma regla (fecha_produccion y turno_id son los del
    primero; fecha_hora = inicio; fin = primera muestra del último turno que la violó; turnos_afectados). El episodio
    se cierra solo (episodio_abierto = False) tras Planta.turnos_fin_episodio turnos sin violarla; una alerta cerrada
    a mano tampoco se reabre: en los dos casos, la próxima violación es otra alerta.
    """

    __tablename__ = "alerta"
    __table_args__ = (UniqueConstraint("origen", "regla", "variable", "ambito", "fecha_produccion", "turno_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    origen: Mapped[str] = mapped_column(String(10))
    regla: Mapped[str] = mapped_column(String(10))          # "1"…"8", "MR", "S" o "rango"
    variable: Mapped[str] = mapped_column(String(30))
    ambito: Mapped[str] = mapped_column(String(20))         # "equipo:5" o "planta:8"
    equipo_id: Mapped[int | None] = mapped_column(ForeignKey("equipo.id"))
    planta_id: Mapped[int | None] = mapped_column(ForeignKey("planta.id"))
    fecha_produccion: Mapped[date] = mapped_column(Date)
    turno_id: Mapped[int] = mapped_column(ForeignKey("turno.id"))
    fecha_hora: Mapped[datetime] = mapped_column(DateTime)
    valor: Mapped[float] = mapped_column(Float)
    limite: Mapped[float | None] = mapped_column(Float)
    limites_id: Mapped[int | None] = mapped_column(ForeignKey("limites_control.id"))
    rango_id: Mapped[int | None] = mapped_column(ForeignKey("rango_referencia.id"))
    estado: Mapped[str] = mapped_column(String(15), default="abierta")
    comun_eboe: Mapped[bool] = mapped_column(Boolean, default=False)
    fin: Mapped[datetime | None] = mapped_column(DateTime)
    turnos_afectados: Mapped[int | None] = mapped_column(default=1)
    episodio_abierto: Mapped[bool | None] = mapped_column(Boolean, default=True)
    causa: Mapped[str | None] = mapped_column(Text)
    accion: Mapped[str | None] = mapped_column(Text)
    comentario: Mapped[str | None] = mapped_column(Text)
    creada_por: Mapped[str] = mapped_column(String(100))
    creada_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    cerrada_por: Mapped[str | None] = mapped_column(String(100))
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime)

    equipo: Mapped["Equipo | None"] = relationship()
    planta: Mapped["Planta | None"] = relationship()
    turno: Mapped["Turno"] = relationship()
    datos: Mapped[list["AlertaDato"]] = relationship(back_populates="alerta", cascade="all, delete-orphan")

    @validates("origen")
    def validar_origen(self, clave, valor):
        if valor not in ORIGENES_ALERTA:
            raise ValueError(f'Origen "{valor}" no válido. Usá uno de: {", ".join(ORIGENES_ALERTA)}.')
        return valor

    @validates("estado")
    def validar_estado(self, clave, valor):
        if valor not in ESTADOS_ALERTA:
            raise ValueError(f'Estado "{valor}" no válido. Usá uno de: {", ".join(ESTADOS_ALERTA)}.')
        return valor


class AlertaDato(Base):
    """Un dato que disparó (o se sumó a) una alerta: tabla ("analisis_scott", "muestra_centrifuga") y su id."""

    __tablename__ = "alerta_dato"
    __table_args__ = (UniqueConstraint("alerta_id", "tabla", "dato_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    alerta_id: Mapped[int] = mapped_column(ForeignKey("alerta.id"))
    tabla: Mapped[str] = mapped_column(String(30))
    dato_id: Mapped[int]

    alerta: Mapped["Alerta"] = relationship(back_populates="datos")
