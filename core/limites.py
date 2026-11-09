"""Límites de control congelados (fase I) por variable, ámbito (equipo o planta) y fruta.

- series_de_planta(): una serie por variable, un dato por fila con su tabla e id (para rastrear de qué análisis o
  muestra sale cada punto), en orden de tiempo, con su ámbito: un equipo, o la planta en las variables de planta
  (el pH de la emulsión: una sola serie con los 3 eBOE).
- subgrupos_por_turno(): un subgrupo por ámbito y turno de producción (n, media, desvío), para la carta X̄-S.
- calcular_limites_fase1(): límites "entre/dentro" (I-MR-S, por turno, como se evalúan las alertas), X̄-S o I-MR
  (por muestra, para analizar) con los datos del período base. Solo se excluyen los datos que elige el usuario
  (cada uno con su motivo) y los inválidos de las centrífugas, con el motivo "dato inválido: …". Los motivos de
  dato inválido (copiada, fuera de rango físico, valor pegado, normalidad dudosa) afectan solo las variables que
  salen de g y mL (aceite %, pérdidas); nunca la CP, el caudal ni el tiempo de descarga.
- congelar_limites(): guarda el registro vigente (el anterior queda como historial) y sus exclusiones.
- congelar_temporada(): congela una temporada completa para varias variables y todos sus ámbitos.
Fase II: core/alertas.py evalúa los datos nuevos contra los límites vigentes.
"""

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.formato import numero
from core.importar_centrifugas import REVISAR_COPIADA, REVISAR_FISICO, REVISAR_NORMALIDAD, REVISAR_PEGADO
from core.models import REGLAS_POR_DEFECTO, REGLAS_XBAR_S, Equipo, Linea, LimitesControl, LimitesExclusion
from core.spc import limites_entre_dentro, limites_imr, limites_xbar_s, limites_xbar_s_para

TABLA_ANALISIS, TABLA_CENTRIFUGA = "analisis_scott", "muestra_centrifuga"
TIPO_EBOE, TIPO_PLANTA = "BOE", "planta"
IMR, XBAR_S, ENTRE_DENTRO = "I-MR", "Xbar-S", "I-MR-S"
INVALIDOS = (REVISAR_COPIADA, REVISAR_FISICO, REVISAR_PEGADO, REVISAR_NORMALIDAD)
PREFIJO_INVALIDO = "dato inválido: "
SIN_LIMITE_INFERIOR = "sin límite inferior"
COLUMNAS_SERIE = ["tabla", "dato_id", "ambito", "equipo_id", "planta_id", "equipo", "tipo", "fecha_hora",
                  "fecha_produccion", "turno", "temporada", "fruta", "valor", "invalido"]

# Variable → nombre, unidad, nivel ("equipo" o "planta"), si sale de una titulación (g y mL: la copia la invalida)
# y el rango de RangoReferencia que mira la alerta "fuera de rango" (variable del rango, punto o equipo; None = el
# tipo de equipo, como en las centrífugas).
VARIABLES_CONTROL = {
    "eficiencia": {"nombre": "Eficiencia eBOE", "unidad": "%", "nivel": "equipo", "titulacion": True,
                   "rango": ("Eficiencia eBOE", "BOE (todos)")},
    "aceite_salida": {"nombre": "Aceite en fruta de salida", "unidad": "kg/MT", "nivel": "equipo", "titulacion": True,
                      "rango": None},
    "aceite_emulsion": {"nombre": "Aceite en emulsión", "unidad": "%", "nivel": "equipo", "titulacion": True,
                        "rango": ("Aceite en emulsión", "Emulsión (licor)")},
    "brix": {"nombre": "°Brix emulsión", "unidad": "°Brix", "nivel": "equipo", "titulacion": False,
             "rango": ("°Brix emulsión", "Emulsión (licor)")},
    "ph": {"nombre": "pH emulsión", "unidad": "pH", "nivel": "planta", "titulacion": False,
           "rango": ("pH emulsión", "Emulsión (licor)")},
    "cp_pesada": {"nombre": "CP fase pesada", "unidad": "bar", "nivel": "equipo", "titulacion": False,
                  "rango": ("CP fase pesada", None)},
    "aceite_descarga": {"nombre": "Aceite en descarga", "unidad": "%", "nivel": "equipo", "titulacion": True,
                        "rango": ("Aceite en descarga", None)},
}
VARIABLES_BOE = ("eficiencia", "aceite_salida", "aceite_emulsion", "brix", "ph")


def ambito_de(equipo_id=None, planta_id=None) -> str:
    """"equipo:5" o "planta:8": la clave del ámbito de una serie, unos límites o una alerta."""
    if equipo_id is not None and not pd.isna(equipo_id):
        return f"equipo:{int(equipo_id)}"
    if planta_id is None or pd.isna(planta_id):
        raise ValueError("Hace falta un equipo o una planta.")
    return f"planta:{int(planta_id)}"


def texto_lci(lci: float | None, decimales: int = 2, unidad: str = "") -> str:
    """El LCI para mostrar: "sin límite inferior" si es negativo (la variable no puede bajar de 0)."""
    if lci is None or pd.isna(lci):
        return "—"
    if lci < 0:
        return SIN_LIMITE_INFERIOR
    return f"{numero(lci, decimales)} {unidad}".strip()


# ---------------------------------------------------------------- series

def motivo_invalido(revisar, titulacion: bool = True) -> str | None:
    """"dato inválido: <motivo>" si el campo revisar de una muestra tiene un motivo que la invalida; si no, None.

    titulacion=False (CP, caudal, tiempo de descarga): ninguno de estos motivos los afecta (todos salen de la
    titulación o del balance con g y mL).
    """
    if not titulacion or not isinstance(revisar, str) or not revisar:          # vacío (None o NaN de pandas)
        return None
    motivos = revisar.split("; ")
    invalidos = [m for m in INVALIDOS if m in motivos]
    return PREFIJO_INVALIDO + "; ".join(invalidos) if invalidos else None


def series_boe(analisis: pd.DataFrame, emulsion: pd.DataFrame, equipos: dict[int, tuple[int, str]],
               planta_id: int | None = None) -> dict:
    """Series de los eBOE: {variable: DataFrame COLUMNAS_SERIE}.

    analisis y emulsion: core.analisis.tabla_analisis y tabla_emulsion. equipos: {n.º de eBOE: (equipo_id, nombre)}.
    eficiencia (%): eficiencia del par × 100, en la fila de Entrada; aceite_salida (kg/MT): filas de Salida;
    aceite_emulsion (%), brix y ph: análisis de emulsión, con la fruta de la Entrada del mismo eBOE y hora. Las de
    nivel "planta" (pH) van con ámbito planta: los 3 eBOE en una sola serie.
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
        de_planta = VARIABLES_CONTROL[variable]["nivel"] == "planta"
        equipo_id = None if de_planta else datos["eboe"].map(lambda e: equipos.get(e, (None, None))[0])
        series[variable] = _serie(pd.DataFrame({
            "tabla": TABLA_ANALISIS, "dato_id": datos["analisis_id"], "equipo_id": equipo_id, "planta_id": planta_id,
            "equipo": "Planta" if de_planta else datos["eboe"].map(lambda e: equipos.get(e, (None, f"eBOE {e}"))[1]),
            "tipo": TIPO_PLANTA if de_planta else TIPO_EBOE, "fecha_hora": datos["fecha_hora"],
            "fecha_produccion": datos["fecha_produccion"], "turno": datos["turno"], "temporada": datos["temporada"],
            "fruta": datos["fruta"].map(especie), "valor": datos["valor"], "invalido": None}))
    return series


def series_centrifugas(muestras: pd.DataFrame, equipos: dict[str, int], planta_id: int | None = None) -> dict:
    """Series de las centrífugas: cp_pesada (bar) y aceite_descarga (%), una fila por muestra.

    muestras: core.analisis_centrifugas.tabla_muestras; equipos: {código: equipo_id}. invalido: "dato inválido:
    …" (motivo_invalido: solo en el aceite de la descarga; la CP no se invalida).
    """
    from core.operacion import especie
    fuentes = {"cp_pesada": muestras.assign(valor=muestras["cp_pesada_bar"]),
               "aceite_descarga": muestras[muestras["fase"] == "descarga"].assign(valor=lambda d: d["aceite_pct"])}
    series = {}
    for variable, datos in fuentes.items():
        datos = datos.dropna(subset=["valor"])
        titulacion = VARIABLES_CONTROL[variable]["titulacion"]
        series[variable] = _serie(pd.DataFrame({
            "tabla": TABLA_CENTRIFUGA, "dato_id": datos["id"], "equipo_id": datos["codigo"].map(equipos),
            "planta_id": planta_id, "equipo": datos["equipo"], "tipo": datos["tipo"], "fecha_hora": datos["fecha_hora"],
            "fecha_produccion": datos["fecha_produccion"], "turno": datos["turno"], "temporada": datos["temporada"],
            "fruta": datos["fruta"].map(especie), "valor": datos["valor"],
            "invalido": datos["revisar"].map(lambda r: motivo_invalido(r, titulacion))}))
    return series


def series_de_planta(sesion: Session, planta_id: int) -> dict:
    """Todas las series de VARIABLES_CONTROL de la planta (eBOE y centrífugas)."""
    from core.analisis import tabla_analisis, tabla_emulsion
    from core.analisis_centrifugas import tabla_muestras
    equipos = sesion.scalars(select(Equipo).join(Linea).where(Linea.planta_id == planta_id)).all()
    eboes = {e.numero: (e.id, e.nombre) for e in equipos if e.tipo == TIPO_EBOE}
    series = series_boe(tabla_analisis(sesion, planta_id), tabla_emulsion(sesion, planta_id), eboes, planta_id)
    muestras = tabla_muestras(sesion, planta_id)
    if not muestras.empty:
        series |= series_centrifugas(muestras, {e.codigo: e.id for e in equipos}, planta_id)
    return series


def _serie(tabla: pd.DataFrame) -> pd.DataFrame:
    tabla = tabla.astype({"valor": float})
    tabla["ambito"] = [ambito_de(e, p) for e, p in zip(tabla["equipo_id"], tabla["planta_id"])] if len(tabla) else []
    return tabla[COLUMNAS_SERIE].sort_values(["ambito", "fecha_hora"], ignore_index=True)


def subgrupos_por_turno(serie: pd.DataFrame) -> pd.DataFrame:
    """Un subgrupo por ámbito y turno de producción, con los datos válidos (los inválidos no entran).

    Columnas: ambito, equipo_id, planta_id, equipo, tipo, fecha_produccion, turno, temporada, fruta (la más
    frecuente), n, media, desvio (n − 1; vacío con n = 1), fecha_hora (primera muestra) y datos ([(tabla, id)]).
    En orden de tiempo.
    """
    validos = serie[serie["invalido"].isna()] if "invalido" in serie else serie
    columnas = ["ambito", "equipo_id", "planta_id", "equipo", "tipo", "fecha_produccion", "turno", "temporada",
                "fruta", "n", "media", "desvio", "fecha_hora", "datos"]
    if validos.empty:
        return pd.DataFrame(columns=columnas)
    filas = []
    for (ambito, dia, turno), grupo in validos.groupby(["ambito", "fecha_produccion", "turno"], sort=False):
        frutas = grupo["fruta"].dropna()
        filas.append({"ambito": ambito, "equipo_id": grupo["equipo_id"].iloc[0], "planta_id": grupo["planta_id"].iloc[0],
                      "equipo": grupo["equipo"].iloc[0], "tipo": grupo["tipo"].iloc[0], "fecha_produccion": dia,
                      "turno": turno, "temporada": grupo["temporada"].iloc[0],
                      "fruta": frutas.mode().iloc[0] if not frutas.empty else None, "n": len(grupo),
                      "media": grupo["valor"].mean(), "desvio": grupo["valor"].std(ddof=1) if len(grupo) > 1 else None,
                      "fecha_hora": grupo["fecha_hora"].min(),
                      "datos": list(zip(grupo["tabla"], grupo["dato_id"].astype(int)))})
    return pd.DataFrame(filas, columns=columnas).sort_values("fecha_hora", ignore_index=True)


# ---------------------------------------------------------------- fase I

def calcular_limites_fase1(serie: pd.DataFrame, desde=None, hasta=None, excluir: dict | None = None,
                           tipo_carta: str = ENTRE_DENTRO) -> dict:
    """Límites de un ámbito con los datos del período base (días de producción desde/hasta, incluidos).

    tipo_carta "I-MR-S" (entre/dentro, el de las alertas): un subgrupo por turno; la carta de los promedios usa la
    variación ENTRE turnos (MR̄ de los promedios / d2) y la S la de DENTRO (S barra), core.spc.limites_entre_dentro.
    tipo_carta "Xbar-S": x̿ = Σ nᵢ·x̄ᵢ / Σ nᵢ y σ dentro (S barra) para todo; lcs, lci y los de S para el n típico.
    tipo_carta "I-MR": un punto por muestra (core.spc.limites_imr).
    excluir: {dato_id: motivo} elegidos por el usuario; cada uno necesita un motivo. Además quedan afuera los datos
    inválidos (columna invalido), con ese motivo. Nunca se excluye un dato por ser extremo.
    Devuelve base_desde, base_hasta, n (datos usados), n_subgrupos, n_subgrupo (mediana), lc, lcs, lci, sigma,
    sigma_dentro, mr_lc, mr_lcs y excluidos ({tabla, dato_id, fecha_hora, valor, motivo}).
    """
    if serie["ambito"].nunique() > 1:
        raise ValueError("Los límites son de un solo equipo (o de la planta): filtrá la serie antes.")
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
    if tipo_carta == IMR:
        limites = limites_imr(usados["valor"])
        resultado = {k: limites[k] for k in ("lc", "lcs", "lci", "sigma", "mr_lc", "mr_lcs")} | {
            "n_subgrupos": len(usados), "n_subgrupo": 1, "sigma_dentro": None}
    elif tipo_carta == ENTRE_DENTRO:
        subgrupos = subgrupos_por_turno(usados.assign(invalido=None))
        limites = limites_entre_dentro(subgrupos["media"], subgrupos["desvio"].astype(float), subgrupos["n"])
        resultado = limites | {"n_subgrupos": len(subgrupos), "n_subgrupo": int(subgrupos["n"].median())}
    elif tipo_carta == XBAR_S:
        subgrupos = subgrupos_por_turno(usados.assign(invalido=None))
        parametros = limites_xbar_s(subgrupos["media"], subgrupos["desvio"].astype(float), subgrupos["n"])
        tipico = int(subgrupos["n"].median())
        para_n = limites_xbar_s_para(parametros["lc"], parametros["sigma"], tipico)
        resultado = {"lc": parametros["lc"], "sigma": parametros["sigma"], "lcs": para_n["lcs"], "lci": para_n["lci"],
                     "mr_lc": para_n["s_lc"], "mr_lcs": para_n["s_lcs"], "n_subgrupos": len(subgrupos),
                     "n_subgrupo": tipico, "sigma_dentro": parametros["sigma"]}
    else:
        raise ValueError(f'Tipo de carta "{tipo_carta}" no válido: "{ENTRE_DENTRO}", "{XBAR_S}" o "{IMR}".')
    return {"base_desde": base["fecha_produccion"].min().date(), "base_hasta": base["fecha_produccion"].max().date(),
            "n": len(usados), "tipo_carta": tipo_carta, **resultado,
            "excluidos": [{"tabla": f["tabla"], "dato_id": int(f["dato_id"]), "fecha_hora": f["fecha_hora"],
                           "valor": float(f["valor"]), "motivo": motivos[i]} for i, f in excluidos.iterrows()]}


def congelar_limites(sesion: Session, variable: str, calculo: dict, congelado_por: str, fruta: str | None = None,
                     equipo_id: int | None = None, planta_id: int | None = None, reglas: str | None = None,
                     comentario: str | None = None, guardar: bool = True) -> LimitesControl:
    """Guarda los límites de calcular_limites_fase1() como el registro vigente de variable + ámbito + fruta.

    Ámbito: equipo_id, o planta_id en las variables de planta. El vigente anterior (si hay) pasa a vigente = False
    y queda como historial. Las exclusiones se guardan con su motivo. reglas: por defecto "1,2,3,5,S" por turno
    (entre/dentro y X̄-S) y "1,2,3,5,MR" en I-MR.
    """
    if variable not in VARIABLES_CONTROL:
        raise ValueError(f'Variable "{variable}" no válida. Usá una de: {", ".join(VARIABLES_CONTROL)}.')
    if (equipo_id is None) == (planta_id is None):
        raise ValueError("Los límites van con un equipo o con la planta (uno de los dos).")
    anterior = limites_vigentes(sesion, variable, fruta, equipo_id, planta_id)
    if anterior is not None:
        anterior.vigente = False
    tipo = calculo["tipo_carta"]
    registro = LimitesControl(
        variable=variable, equipo_id=equipo_id, planta_id=planta_id, fruta=fruta, tipo_carta=tipo,
        n_subgrupo=calculo["n_subgrupo"], base_desde=calculo["base_desde"], base_hasta=calculo["base_hasta"],
        n=calculo["n"], lc=calculo["lc"], lcs=calculo["lcs"], lci=calculo["lci"], sigma=calculo["sigma"],
        sigma_dentro=calculo.get("sigma_dentro"), mr_lc=calculo["mr_lc"], mr_lcs=calculo["mr_lcs"],
        reglas=reglas or (REGLAS_POR_DEFECTO if tipo == IMR else REGLAS_XBAR_S), vigente=True,
        congelado_por=congelado_por, comentario=comentario,
        exclusiones=[LimitesExclusion(**excluido) for excluido in calculo["excluidos"]])
    sesion.add(registro)
    if guardar:
        sesion.commit()
    return registro


def limites_vigentes(sesion: Session, variable: str, fruta: str | None, equipo_id: int | None = None,
                     planta_id: int | None = None) -> LimitesControl | None:
    """El registro vigente de variable + ámbito (equipo o planta) + fruta, o None."""
    consulta = select(LimitesControl).where(LimitesControl.variable == variable, LimitesControl.vigente.is_(True))
    for columna, valor in ((LimitesControl.equipo_id, equipo_id), (LimitesControl.planta_id, planta_id),
                           (LimitesControl.fruta, fruta)):
        consulta = consulta.where(columna.is_(None) if valor is None else columna == valor)
    return sesion.scalars(consulta).first()


def congelar_temporada(sesion: Session, series: dict, temporada: int, variables, congelado_por: str,
                       tipo_carta: str = ENTRE_DENTRO, comentario: str | None = None) -> list[LimitesControl]:
    """Congela los límites con una temporada completa (sin exclusiones del usuario) para cada variable, ámbito y
    fruta con datos en esa temporada. series: series_de_planta()."""
    registros = []
    for variable in variables:
        serie = series.get(variable)
        if serie is None:
            continue
        de_temporada = serie[serie["temporada"] == temporada]
        for (_, fruta), grupo in de_temporada.groupby(["ambito", "fruta"], dropna=False):
            if grupo["invalido"].isna().sum() < 2:
                continue
            calculo = calcular_limites_fase1(grupo, tipo_carta=tipo_carta)
            equipo_id, planta_id = grupo["equipo_id"].iloc[0], grupo["planta_id"].iloc[0]
            registros.append(congelar_limites(
                sesion, variable, calculo, congelado_por, None if pd.isna(fruta) else fruta,
                equipo_id=None if pd.isna(equipo_id) else int(equipo_id),
                planta_id=None if not pd.isna(equipo_id) else int(planta_id), comentario=comentario, guardar=False))
    sesion.commit()
    return registros
