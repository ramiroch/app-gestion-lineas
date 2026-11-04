"""Tablas de análisis de una planta (fruta y emulsión) y filtros de la barra global.

tabla_analisis() lee la base y arma una fila por análisis de fruta entrada o salida de
los eBOE, con lo que necesitan las páginas de análisis; tabla_emulsion(), lo mismo para
la emulsión. filtrar() aplica los filtros
sobre esa tabla con pandas (sin volver a la base). Nada de esto depende de Streamlit:
la página guarda la tabla en caché y llama a filtrar() cada vez que cambia un filtro.
"""

from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from core.calculos import aceite_en_emulsion, aceite_en_fruta, eficiencia_eboe
from core.models import AnalisisScott, Equipo, LecturaOperacion, Linea, Planta, PuntoMuestreo
from core.turnos import fecha_produccion, turno_de

TIPO_EBOE = "BOE"
LUGARES = {"fruta entrada": "Entrada", "fruta salida": "Salida"}
COLUMNAS = ["analisis_id", "fecha_hora", "fecha_produccion", "turno", "temporada", "proceso_id",
            "proceso", "eboe", "lugar", "analista", "aceite_fruta", "eficiencia_par", "programa", "tph",
            "caudal_fase_pesada"]
TIPO_EMULSION = "emulsión"
COLUMNAS_EMULSION = ["analisis_id", "fecha_hora", "fecha_produccion", "turno", "temporada", "proceso_id",
                     "proceso", "eboe", "analista", "aceite_emulsion", "brix", "ph", "programa", "tph",
                     "caudal_fase_pesada"]
ATAJO_MES = "Último mes"
ATAJO_SEMANA = "Última semana"
ATAJO_PERSONALIZADO = "Personalizado"


@dataclass
class Filtros:
    """Filtros de la barra global. None o lista vacía = sin filtrar por ese campo.

    desde y hasta son fechas de producción (incluidas). procesos lleva ids de Proceso,
    porque el número de proceso se repite en cada temporada.
    """
    desde: date | None = None
    hasta: date | None = None
    eboes: list[int] = field(default_factory=list)
    turnos: list[str] = field(default_factory=list)
    procesos: list[int] = field(default_factory=list)
    programas: list[str] = field(default_factory=list)


def _leer(sesion: Session, planta_id: int, tipos_punto) -> tuple[Planta, list[dict]]:
    """Lee los análisis de los eBOE de la planta en esos tipos de punto de muestreo.

    Devuelve la planta y una fila por análisis con las columnas comunes (fechas, turno,
    temporada, proceso, eBOE, analista; programa, tph y caudal_fase_pesada (m³/h) de la
    lectura del mismo eBOE y fecha_hora) más "_analisis" (el objeto, para calcular) y "_equipo_id".
    """
    planta = sesion.get(Planta, planta_id)
    if planta is None:
        raise ValueError(f"No existe la planta con id {planta_id}.")

    analisis = sesion.scalars(
        select(AnalisisScott)
        .join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo).join(Equipo.linea)
        .where(Linea.planta_id == planta_id, Equipo.tipo == TIPO_EBOE, PuntoMuestreo.tipo.in_(tipos_punto))
        .options(selectinload(AnalisisScott.proceso),
                 selectinload(AnalisisScott.punto_muestreo).selectinload(PuntoMuestreo.equipo))
    ).all()
    lecturas = {(l.equipo_id, l.fecha_hora): l for l in sesion.scalars(
        select(LecturaOperacion).join(LecturaOperacion.equipo).join(Equipo.linea)
        .where(Linea.planta_id == planta_id))}

    filas = []
    for a in analisis:
        equipo = a.punto_muestreo.equipo
        lectura = lecturas.get((equipo.id, a.fecha_hora))
        filas.append({
            "analisis_id": a.id, "fecha_hora": a.fecha_hora,
            "fecha_produccion": fecha_produccion(planta, a.fecha_hora),
            "turno": turno_de(planta, a.fecha_hora).nombre,
            "temporada": a.proceso.temporada, "proceso_id": a.proceso_id, "proceso": a.proceso.numero,
            "eboe": equipo.numero, "analista": a.analista,
            "programa": lectura.programa if lectura else None,
            "tph": lectura.tph if lectura else None,
            "caudal_fase_pesada": lectura.caudal_fase_pesada if lectura else None,
            "_analisis": a, "_equipo_id": equipo.id,
        })
    return planta, filas


def _a_tabla(filas: list[dict], columnas: list[str]) -> pd.DataFrame:
    tabla = pd.DataFrame(filas, columns=columnas + ["_equipo_id"])
    for columna in ("fecha_hora", "fecha_produccion"):   # fechas de pandas, también con la tabla vacía
        tabla[columna] = pd.to_datetime(tabla[columna])
    return tabla


def tabla_analisis(sesion: Session, planta_id: int) -> pd.DataFrame:
    """Una fila por análisis de fruta (entrada o salida) de los eBOE de la planta.

    Columnas (COLUMNAS): fecha_produccion y turno se calculan con core.turnos;
    aceite_fruta (kg/MT) con core.calculos desde los datos crudos; eficiencia_par
    (fracción) va en las dos filas del par entrada/salida del mismo eBOE y la misma
    fecha_hora, y queda vacía si falta una de las dos; programa y tph salen de la lectura
    de operación del mismo eBOE y fecha_hora (si la hay).
    """
    planta, filas = _leer(sesion, planta_id, LUGARES)
    for fila in filas:
        a = fila.pop("_analisis")
        fila["lugar"] = LUGARES[a.punto_muestreo.tipo]
        fila["aceite_fruta"] = aceite_en_fruta(planta, a.vol_ml, a.blanco_ml, a.normalidad, a.peso_muestra_g)
    tabla = _a_tabla(filas, COLUMNAS)
    tabla["eficiencia_par"] = _eficiencias_de_pares(tabla)
    return tabla.drop(columns="_equipo_id").sort_values(["fecha_hora", "eboe", "lugar"], ignore_index=True)


def tabla_emulsion(sesion: Session, planta_id: int) -> pd.DataFrame:
    """Una fila por análisis de emulsión (licor) de los eBOE de la planta.

    Columnas (COLUMNAS_EMULSION): las mismas de filtro que tabla_analisis() (fechas, turno,
    temporada, proceso, eBOE, programa), así se filtra con filtrar(); aceite_emulsion (%)
    con core.calculos.aceite_en_emulsion desde los datos crudos; brix y ph tal como se cargaron.
    """
    planta, filas = _leer(sesion, planta_id, [TIPO_EMULSION])
    for fila in filas:
        a = fila.pop("_analisis")
        fila["aceite_emulsion"] = aceite_en_emulsion(planta, a.vol_ml, a.blanco_ml, a.normalidad, a.peso_muestra_g)
        fila["brix"], fila["ph"] = a.brix, a.ph
    tabla = _a_tabla(filas, COLUMNAS_EMULSION)
    return tabla.drop(columns="_equipo_id").sort_values(["fecha_hora", "eboe"], ignore_index=True)


def _eficiencias_de_pares(tabla: pd.DataFrame) -> pd.Series:
    """Eficiencia de cada par entrada/salida, repetida en las dos filas del par."""
    eficiencias = pd.Series(float("nan"), index=tabla.index)
    for _, par in tabla.groupby(["_equipo_id", "fecha_hora"]):
        lugares = dict(zip(par["lugar"], par["aceite_fruta"]))
        if "Entrada" in lugares and "Salida" in lugares and lugares["Entrada"] > 0:
            eficiencias[par.index] = eficiencia_eboe(lugares["Entrada"], lugares["Salida"])
    return eficiencias


def filtrar(tabla: pd.DataFrame, filtros: Filtros) -> pd.DataFrame:
    """Filas de tabla_analisis() que cumplen todos los filtros (los vacíos no filtran)."""
    seleccion = pd.Series(True, index=tabla.index)
    if filtros.desde is not None:
        seleccion &= tabla["fecha_produccion"] >= pd.Timestamp(filtros.desde)
    if filtros.hasta is not None:
        seleccion &= tabla["fecha_produccion"] <= pd.Timestamp(filtros.hasta)
    for columna, valores in (("eboe", filtros.eboes), ("turno", filtros.turnos),
                             ("proceso_id", filtros.procesos), ("programa", filtros.programas)):
        if valores:
            seleccion &= tabla[columna].isin(valores)
    return tabla[seleccion].reset_index(drop=True)


def pares_con_emulsion(fruta: pd.DataFrame, emulsion: pd.DataFrame) -> pd.DataFrame:
    """Un renglón por par entrada/salida con su eficiencia, el caudal y el aceite en emulsión.

    fruta y emulsion son tabla_analisis() y tabla_emulsion() (ya filtradas). Se toman las filas de
    Entrada con eficiencia_par y se les agrega aceite_emulsion del análisis de emulsión del mismo
    eBOE y fecha_hora (vacío si no hay). eficiencia_pct = eficiencia_par × 100 (puntos de %).
    """
    pares = fruta[(fruta["lugar"] == "Entrada") & fruta["eficiencia_par"].notna()]
    licor = emulsion[["eboe", "fecha_hora", "aceite_emulsion"]]
    unidos = pares.merge(licor, on=["eboe", "fecha_hora"], how="left", validate="one_to_one")
    return unidos.assign(eficiencia_pct=unidos["eficiencia_par"] * 100)


def filtros_sin_periodo(filtros: Filtros) -> Filtros:
    """Copia de los filtros sin fechas ni procesos, para comparar temporadas.

    Cada proceso pertenece a una sola temporada: si se dejara, no habría nada que comparar.
    Se mantienen eBOE, turno y programa.
    """
    return replace(filtros, desde=None, hasta=None, procesos=[])


def analisis_filtrados(sesion: Session, planta_id: int, filtros: Filtros) -> pd.DataFrame:
    """Análisis de fruta de la planta que cumplen los filtros (tabla_analisis + filtrar)."""
    return filtrar(tabla_analisis(sesion, planta_id), filtros)


def opciones(tabla: pd.DataFrame) -> dict:
    """Valores posibles de cada filtro según los datos de la tabla.

    Devuelve eboes, turnos (en orden M, T, N según aparecen en el día), programas,
    procesos ({"2025 · P07": proceso_id}), temporadas ({2025: (primera, última fecha)})
    y ultima_fecha (último día de producción con datos, o None si la tabla está vacía).
    """
    fechas = tabla["fecha_produccion"].dt.date
    por_temporada = fechas.groupby(tabla["temporada"])
    procesos = tabla[["temporada", "proceso", "proceso_id"]].drop_duplicates().sort_values(["temporada", "proceso"])
    # Orden de los turnos dentro del día de producción: por la hora más temprana de cada uno,
    # contada desde el inicio del día (así N, que cruza la medianoche, queda último).
    turnos = tabla.assign(_hora=(tabla["fecha_hora"] - tabla["fecha_produccion"])).groupby("turno")["_hora"].min()
    return {
        "eboes": sorted(tabla["eboe"].unique().tolist()),
        "turnos": turnos.sort_values().index.tolist(),
        "programas": sorted(tabla["programa"].dropna().unique().tolist()),
        "procesos": {f"{t} · P{n:02d}": int(i) for t, n, i in procesos.itertuples(index=False)},
        "temporadas": {int(t): (f.min(), f.max()) for t, f in por_temporada},
        "ultima_fecha": fechas.max() if len(tabla) else None,
    }


def atajos(opciones_: dict) -> list[str]:
    """Atajos de fechas: una temporada por año con datos (la más reciente primero),
    último mes, última semana y personalizado."""
    temporadas = [f"Temporada {t}" for t in sorted(opciones_["temporadas"], reverse=True)]
    return temporadas + [ATAJO_MES, ATAJO_SEMANA, ATAJO_PERSONALIZADO]


def rango_de_atajo(atajo: str, opciones_: dict) -> tuple[date, date] | None:
    """(desde, hasta) de un atajo de fechas, o None para "Personalizado" o si no hay datos.

    Temporada: de su primer a su último día de producción con datos. Último mes y última
    semana: los 30 o 7 días que terminan en el último día con datos (no en la fecha de hoy,
    para que sirvan también con datos históricos).
    """
    ultima = opciones_["ultima_fecha"]
    if atajo == ATAJO_PERSONALIZADO or ultima is None:
        return None
    if atajo == ATAJO_MES:
        return ultima - timedelta(days=29), ultima
    if atajo == ATAJO_SEMANA:
        return ultima - timedelta(days=6), ultima
    if atajo.startswith("Temporada "):
        temporada = int(atajo.removeprefix("Temporada "))
        if temporada in opciones_["temporadas"]:
            return opciones_["temporadas"][temporada]
    raise ValueError(f'Atajo de fechas "{atajo}" no válido.')
