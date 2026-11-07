"""Límites de control congelados (fase I) por variable, equipo y fruta.

- series_de_planta(): una serie por variable, un dato por fila con su tabla e id (para rastrear de qué análisis o
  muestra sale cada punto), en orden de tiempo.
- calcular_limites_fase1(): límites I-MR (core.spc.limites_imr) con los datos del período base. Solo se excluyen
  los datos que el usuario elige (cada uno con su motivo) y los datos inválidos de las centrífugas (copiados de la
  otra primaria, fuera de rango físico, valor pegado, normalidad dudosa), con el motivo "dato inválido: …".
- congelar_limites(): guarda el registro vigente (el anterior queda como historial) y sus exclusiones.
- congelar_temporada(): congela una temporada completa para varias variables y todos sus equipos.
Fase II: core/alertas.py evalúa los datos nuevos contra los límites vigentes.
"""

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.importar_centrifugas import REVISAR_COPIADA, REVISAR_FISICO, REVISAR_NORMALIDAD, REVISAR_PEGADO
from core.models import REGLAS_POR_DEFECTO, Equipo, Linea, LimitesControl, LimitesExclusion
from core.spc import limites_imr

TABLA_ANALISIS, TABLA_CENTRIFUGA = "analisis_scott", "muestra_centrifuga"
TIPO_EBOE = "BOE"
INVALIDOS = (REVISAR_COPIADA, REVISAR_FISICO, REVISAR_PEGADO, REVISAR_NORMALIDAD)
PREFIJO_INVALIDO = "dato inválido: "
COLUMNAS_SERIE = ["tabla", "dato_id", "equipo_id", "equipo", "tipo", "fecha_hora", "fecha_produccion", "turno",
                  "temporada", "fruta", "valor", "invalido"]

# Variable → nombre, unidad y el rango de RangoReferencia que la alerta "fuera de rango" mira:
# (variable del rango, punto o equipo; None = el tipo de equipo, como en las centrífugas).
VARIABLES_CONTROL = {
    "eficiencia": {"nombre": "Eficiencia eBOE", "unidad": "%", "rango": ("Eficiencia eBOE", "BOE (todos)")},
    "aceite_salida": {"nombre": "Aceite en fruta de salida", "unidad": "kg/MT", "rango": None},
    "aceite_emulsion": {"nombre": "Aceite en emulsión", "unidad": "%", "rango": ("Aceite en emulsión",
                                                                                  "Emulsión (licor)")},
    "brix": {"nombre": "°Brix emulsión", "unidad": "°Brix", "rango": ("°Brix emulsión", "Emulsión (licor)")},
    "ph": {"nombre": "pH emulsión", "unidad": "pH", "rango": ("pH emulsión", "Emulsión (licor)")},
    "cp_pesada": {"nombre": "CP fase pesada", "unidad": "bar", "rango": ("CP fase pesada", None)},
    "aceite_descarga": {"nombre": "Aceite en descarga", "unidad": "%", "rango": ("Aceite en descarga", None)},
}
VARIABLES_BOE = ("eficiencia", "aceite_salida", "aceite_emulsion", "brix", "ph")


# ---------------------------------------------------------------- series

def motivo_invalido(revisar: str | None) -> str | None:
    """"dato inválido: <motivo>" si el campo revisar de una muestra tiene un motivo que la invalida; si no, None."""
    if not isinstance(revisar, str) or not revisar:          # vacío (None o NaN de pandas)
        return None
    motivos = revisar.split("; ")
    invalidos = [m for m in INVALIDOS if m in motivos]
    return PREFIJO_INVALIDO + "; ".join(invalidos) if invalidos else None


def series_boe(analisis: pd.DataFrame, emulsion: pd.DataFrame, equipos: dict[int, tuple[int, str]]) -> dict:
    """Series de los eBOE: {variable: DataFrame COLUMNAS_SERIE}.

    analisis y emulsion: core.analisis.tabla_analisis y tabla_emulsion. equipos: {n.º de eBOE: (equipo_id, nombre)}.
    eficiencia (%): eficiencia del par × 100, en la fila de Entrada; aceite_salida (kg/MT): filas de Salida;
    aceite_emulsion (%), brix y ph: análisis de emulsión, con la fruta de la Entrada del mismo eBOE y hora.
    """
    from core.operacion import especie
    entradas = analisis[analisis["lugar"] == "Entrada"]
    frutas = entradas.set_index(["eboe", "fecha_hora"])["fruta"]
    fuentes = {
        "eficiencia": entradas.assign(valor=entradas["eficiencia_par"] * 100),
        "aceite_salida": analisis[analisis["lugar"] == "Salida"].assign(valor=lambda d: d["aceite_fruta"]),
        "aceite_emulsion": emulsion.assign(valor=emulsion["aceite_emulsion"]),
        "brix": emulsion.assign(valor=emulsion["brix"]),
        "ph": emulsion.assign(valor=emulsion["ph"]),
    }
    series = {}
    for variable, datos in fuentes.items():
        datos = datos.dropna(subset=["valor"])
        if "fruta" not in datos:
            datos = datos.assign(fruta=[frutas.get((e, f)) for e, f in zip(datos["eboe"], datos["fecha_hora"])])
        series[variable] = _serie(pd.DataFrame({
            "tabla": TABLA_ANALISIS, "dato_id": datos["analisis_id"],
            "equipo_id": datos["eboe"].map(lambda e: equipos.get(e, (None, None))[0]),
            "equipo": datos["eboe"].map(lambda e: equipos.get(e, (None, f"eBOE {e}"))[1]), "tipo": TIPO_EBOE,
            "fecha_hora": datos["fecha_hora"], "fecha_produccion": datos["fecha_produccion"], "turno": datos["turno"],
            "temporada": datos["temporada"], "fruta": datos["fruta"].map(especie), "valor": datos["valor"],
            "invalido": None}))
    return series


def series_centrifugas(muestras: pd.DataFrame, equipos: dict[str, int]) -> dict:
    """Series de las centrífugas: cp_pesada (bar) y aceite_descarga (%), una fila por muestra.

    muestras: core.analisis_centrifugas.tabla_muestras; equipos: {código: equipo_id}. invalido: "dato inválido:
    …" si la muestra está copiada de la otra primaria, fuera de rango físico, con valor pegado o normalidad dudosa.
    """
    from core.operacion import especie
    fuentes = {"cp_pesada": muestras.assign(valor=muestras["cp_pesada_bar"]),
               "aceite_descarga": muestras[muestras["fase"] == "descarga"].assign(valor=lambda d: d["aceite_pct"])}
    series = {}
    for variable, datos in fuentes.items():
        datos = datos.dropna(subset=["valor"])
        series[variable] = _serie(pd.DataFrame({
            "tabla": TABLA_CENTRIFUGA, "dato_id": datos["id"], "equipo_id": datos["codigo"].map(equipos),
            "equipo": datos["equipo"], "tipo": datos["tipo"], "fecha_hora": datos["fecha_hora"],
            "fecha_produccion": datos["fecha_produccion"], "turno": datos["turno"], "temporada": datos["temporada"],
            "fruta": datos["fruta"].map(especie), "valor": datos["valor"], "invalido": datos["revisar"].map(motivo_invalido)}))
    return series


def series_de_planta(sesion: Session, planta_id: int) -> dict:
    """Todas las series de VARIABLES_CONTROL de la planta (eBOE y centrífugas)."""
    from core.analisis import tabla_analisis, tabla_emulsion
    from core.analisis_centrifugas import tabla_muestras
    equipos = sesion.scalars(select(Equipo).join(Linea).where(Linea.planta_id == planta_id)).all()
    eboes = {e.numero: (e.id, e.nombre) for e in equipos if e.tipo == TIPO_EBOE}
    series = series_boe(tabla_analisis(sesion, planta_id), tabla_emulsion(sesion, planta_id), eboes)
    muestras = tabla_muestras(sesion, planta_id)
    if not muestras.empty:
        series |= series_centrifugas(muestras, {e.codigo: e.id for e in equipos})
    return series


def _serie(tabla: pd.DataFrame) -> pd.DataFrame:
    tabla = tabla.astype({"valor": float})
    return tabla[COLUMNAS_SERIE].sort_values(["equipo_id", "fecha_hora"], ignore_index=True)


# ---------------------------------------------------------------- fase I

def calcular_limites_fase1(serie: pd.DataFrame, desde=None, hasta=None, excluir: dict | None = None) -> dict:
    """Límites I-MR de un equipo con los datos del período base (días de producción desde/hasta, incluidos).

    serie: COLUMNAS_SERIE de un solo equipo. excluir: {dato_id: motivo} elegidos por el usuario; cada uno necesita
    un motivo. Además quedan afuera los datos inválidos (columna invalido), con ese motivo. Nunca se excluye un dato
    por ser extremo: los outliers se marcan en la carta, no se borran.
    Devuelve base_desde, base_hasta, n (datos usados), lc, lcs, lci, sigma, mr_lc, mr_lcs y excluidos (lista de
    {tabla, dato_id, fecha_hora, valor, motivo}).
    """
    if serie["equipo_id"].nunique() > 1:
        raise ValueError("Los límites son de un solo equipo: filtrá la serie antes.")
    excluir = excluir or {}
    sin_motivo = [d for d, motivo in excluir.items() if not motivo or not str(motivo).strip()]
    if sin_motivo:
        raise ValueError(f"Para excluir un dato hace falta un motivo (faltan en: {', '.join(map(str, sin_motivo))}).")
    base = serie.sort_values("fecha_hora")
    if desde is not None:
        base = base[base["fecha_produccion"] >= pd.Timestamp(desde)]
    if hasta is not None:
        base = base[base["fecha_produccion"] <= pd.Timestamp(hasta)]
    no_encontrados = set(excluir) - set(base["dato_id"])
    if no_encontrados:
        raise ValueError(f"Los datos {sorted(no_encontrados)} no están en el período base.")
    motivos = pd.Series([excluir[d] if d in excluir else (i if isinstance(i, str) else None)
                         for d, i in zip(base["dato_id"], base["invalido"])], index=base.index, dtype=object)
    usados, excluidos = base[motivos.isna()], base[motivos.notna()]
    limites = limites_imr(usados["valor"])
    return {"base_desde": base["fecha_produccion"].min().date(), "base_hasta": base["fecha_produccion"].max().date(),
            "n": len(usados), **{k: limites[k] for k in ("lc", "lcs", "lci", "sigma", "mr_lc", "mr_lcs")},
            "excluidos": [{"tabla": f["tabla"], "dato_id": int(f["dato_id"]), "fecha_hora": f["fecha_hora"],
                           "valor": float(f["valor"]), "motivo": motivos[i]} for i, f in excluidos.iterrows()]}


def congelar_limites(sesion: Session, variable: str, equipo_id: int, fruta: str | None, calculo: dict,
                     congelado_por: str, reglas: str = REGLAS_POR_DEFECTO, comentario: str | None = None,
                     guardar: bool = True) -> LimitesControl:
    """Guarda los límites de calcular_limites_fase1() como el registro vigente de variable + equipo + fruta.

    El vigente anterior (si hay) pasa a vigente = False y queda como historial. Las exclusiones se guardan con su
    motivo. reglas: las reglas de Nelson y "MR" que se van a evaluar ("1,2,3,5,MR" por defecto).
    """
    if variable not in VARIABLES_CONTROL:
        raise ValueError(f'Variable "{variable}" no válida. Usá una de: {", ".join(VARIABLES_CONTROL)}.')
    anterior = limites_vigentes(sesion, variable, equipo_id, fruta)
    if anterior is not None:
        anterior.vigente = False
    registro = LimitesControl(
        variable=variable, equipo_id=equipo_id, fruta=fruta, tipo_carta="I-MR", base_desde=calculo["base_desde"],
        base_hasta=calculo["base_hasta"], n=calculo["n"], lc=calculo["lc"], lcs=calculo["lcs"], lci=calculo["lci"],
        sigma=calculo["sigma"], mr_lc=calculo["mr_lc"], mr_lcs=calculo["mr_lcs"], reglas=reglas, vigente=True,
        congelado_por=congelado_por, comentario=comentario,
        exclusiones=[LimitesExclusion(**excluido) for excluido in calculo["excluidos"]])
    sesion.add(registro)
    if guardar:
        sesion.commit()
    return registro


def limites_vigentes(sesion: Session, variable: str, equipo_id: int, fruta: str | None) -> LimitesControl | None:
    """El registro vigente de variable + equipo + fruta, o None."""
    consulta = select(LimitesControl).where(LimitesControl.variable == variable, LimitesControl.equipo_id == equipo_id,
                                            LimitesControl.vigente.is_(True))
    consulta = consulta.where(LimitesControl.fruta.is_(None) if fruta is None else LimitesControl.fruta == fruta)
    return sesion.scalars(consulta).first()


def congelar_temporada(sesion: Session, series: dict, temporada: int, variables, congelado_por: str,
                       comentario: str | None = None) -> list[LimitesControl]:
    """Congela los límites con una temporada completa (sin exclusiones del usuario) para cada variable, equipo y
    fruta con datos en esa temporada. series: series_de_planta()."""
    registros = []
    for variable in variables:
        serie = series.get(variable)
        if serie is None:
            continue
        de_temporada = serie[serie["temporada"] == temporada]
        for (equipo_id, fruta), grupo in de_temporada.groupby(["equipo_id", "fruta"], dropna=False):
            if pd.isna(equipo_id) or grupo["invalido"].isna().sum() < 2:
                continue
            calculo = calcular_limites_fase1(grupo)
            registros.append(congelar_limites(sesion, variable, int(equipo_id), None if pd.isna(fruta) else fruta,
                                              calculo, congelado_por, comentario=comentario, guardar=False))
    sesion.commit()
    return registros
