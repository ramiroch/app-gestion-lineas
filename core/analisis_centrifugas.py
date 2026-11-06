"""Análisis de las centrífugas de aceite, sin pantalla (Paso 4a).

- tabla_muestras(): una fila por muestra, con el aceite % recalculado, día de producción y turno.
- por_muestreo() y balance_por_muestreo(): balance de cada equipo en cada muestreo (core.centrifugas).
- primarias_por_turno(): primarias en marcha por turno (INFERIDO) y caudal total de emulsión.
- agua_por_eboe(): agua disponible por eBOE (m³/h) contra el caudal que carga el laboratorio del BOE.
- valores_por_variable() y resumen_por_temporada(): N, mediana, cuartiles (Minitab) y % fuera de rango.
- perdida_descarga_temporada() y perdida_por_tonelada(): pérdida en descarga ESTIMADA (kg y kg/MT).
- relacion_boe(): aceite en emulsión de centrífugas contra el laboratorio del BOE, por día de producción.

Reglas (CLAUDE.md):
- Muestras "copiada de la otra primaria": fuera de las comparaciones entre equipos (incluir_copiadas=True para
  sumarlas).
- CP con marca "no medible": fuera de estadísticas y del % fuera de rango; se cuentan aparte.
- Eficiencia fuera de rango físico (< 0 o > 100 %): se marca, no se borra.
- Fase pesada: pérdida del proceso solo si el destino es "canal (pérdida)"; "recircula al BOE" → recirculado;
  "no aplica" (pulidora: no tiene fase pesada ni eficiencia por balance) → no se calcula.
- Sin volumen por descarga: balance y pérdida en descarga "a definir".
- Los rangos salen de RangoReferencia (rangos_centrifugas); nada fijo en el código.
"""

from datetime import date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.centrifugas import (A_DEFINIR, aceite_pct, aceite_perdido_kgh, alimentacion_secundaria, balance_centrifuga,
                              caudal_descarga_lh)
from core.estadistica import cuartiles
from core.importar_centrifugas import REVISAR_COPIADA
from core.models import Equipo, Linea, MuestraCentrifuga, Planta, Proceso, RangoReferencia
from core.parametros import validar_parametros
from core.programa import numero_programa
from core.turnos import turno_de

PRIMARIA, SECUNDARIA, PULIDORA = "Centrífuga primaria", "Centrífuga secundaria", "Pulidora"
CANAL, RECIRCULA, NO_APLICA = "canal (pérdida)", "recircula al BOE", "no aplica"
CALCULADO = "calculado"
SIN_DATOS = "sin datos (no se muestrea)"
SIN_TITULACION = "sin datos (no se titula)"
INCOMPLETO = "sin datos (muestreo incompleto)"
RECIRCULADO = "recirculado al BOE"
PARALELO, UNA_SOLA = "en paralelo", "una sola"
EBOE_POR_DEFECTO = 3
INTERVALO_MAXIMO_H = 4
N_MINIMO = 3

# Variable → (nombre, unidad). El nombre es el de RangoReferencia (punto o equipo = tipo de equipo).
VARIABLES = {
    "aceite_emulsion": ("Aceite en emulsión", "%"),
    "aceite_liviana": ("Aceite en fase liviana", "%"),
    "aceite_pesada": ("Aceite en fase pesada", "%"),
    "aceite_descarga": ("Aceite en descarga", "%"),
    "solidos": ("Sólidos centrifugables", "%"),
    "cp_liviana": ("CP fase liviana", "bar"),
    "cp_pesada": ("CP fase pesada", "bar"),
    "t_descarga": ("Intervalo de descarga", "s"),
    "caudal_emulsion": ("Caudal de emulsión", "L/h"),
    "eficiencia": ("Eficiencia", "%"),
    "perdida_descarga": ("Pérdida en descarga", "kg/h"),
    "perdida_pesada": ("Pérdida en fase pesada", "kg/h"),
}
ACEITE_DE_FASE = {"emulsion": "aceite_emulsion", "fase_liviana": "aceite_liviana", "fase_pesada": "aceite_pesada",
                  "descarga": "aceite_descarga"}
A_LA_UNIDAD = {("min", "s"): 60}               # rango en minutos, dato en segundos
COLUMNAS_MUESTRAS = ["id", "codigo", "equipo", "tipo", "volumen_descarga_l", "destino", "fecha_hora", "temporada",
                     "proceso", "fase", "fruta", "caudal_lh", "aceite_pct", "titulada", "solidos_pct", "solidos_marca",
                     "cp_liviana_bar", "cp_liviana_marca", "cp_pesada_bar", "t_descarga_s", "revisar", "copiada"]


# ---------------------------------------------------------------- lectura

def tabla_muestras(sesion: Session, planta_id: int) -> pd.DataFrame:
    """Una fila por muestra de centrífuga de la planta (COLUMNAS_MUESTRAS + fecha_produccion y turno).

    aceite_pct (%): (mL − blanco) × N × factor_aceite_emulsion / g si la muestra se tituló (titulada=True);
    si no, el % anotado (aceite_pct_medido: fase liviana o valor pegado). copiada: revisar dice "copiada de la
    otra primaria". fecha_produccion y turno, con core.turnos (no se guardan).
    """
    planta = sesion.get(Planta, planta_id)
    factor = validar_parametros(planta, ["factor_aceite_emulsion"])["factor_aceite_emulsion"]
    filas = sesion.execute(
        select(MuestraCentrifuga, Equipo, Proceso.temporada, Proceso.numero)
        .join(Equipo, MuestraCentrifuga.equipo_id == Equipo.id).join(Linea, Equipo.linea_id == Linea.id)
        .outerjoin(Proceso, MuestraCentrifuga.proceso_id == Proceso.id)
        .where(Linea.planta_id == planta_id)).all()
    registros = []
    for m, e, temporada, proceso in filas:
        titulada = None not in (m.gramos, m.ml, m.normalidad, m.blanco_ml)
        registros.append({
            "id": m.id, "codigo": e.codigo, "equipo": e.nombre, "tipo": e.tipo, "volumen_descarga_l": e.volumen_descarga_l,
            "destino": e.destino_fase_pesada, "fecha_hora": m.fecha_hora, "temporada": temporada or m.fecha_hora.year,
            "proceso": proceso, "fase": m.fase, "fruta": m.fruta, "caudal_lh": m.caudal_lh,
            "aceite_pct": _aceite(m, factor) if titulada else m.aceite_pct_medido, "titulada": titulada,
            "solidos_pct": m.solidos_pct, "solidos_marca": m.solidos_marca, "cp_liviana_bar": m.cp_liviana_bar,
            "cp_liviana_marca": m.cp_liviana_marca, "cp_pesada_bar": m.cp_pesada_bar, "t_descarga_s": m.t_descarga_s,
            "revisar": m.revisar, "copiada": REVISAR_COPIADA in (m.revisar or "")})
    return con_turno(pd.DataFrame(registros, columns=COLUMNAS_MUESTRAS), planta)


def con_turno(tabla: pd.DataFrame, planta: Planta) -> pd.DataFrame:
    """Agrega fecha_produccion (fecha de pandas) y turno a una tabla con fecha_hora (como core.operacion)."""
    inicio = validar_parametros(planta, ["hora_inicio_dia"])["hora_inicio_dia"]
    tabla = tabla.copy()
    tabla["fecha_hora"] = pd.to_datetime(tabla["fecha_hora"])
    minuto = tabla["fecha_hora"].dt.floor("min").dt.time
    turnos = {hora: turno_de(planta, datetime.combine(date(2000, 1, 1), hora)).nombre for hora in minuto.unique()}
    corrimiento = pd.Timedelta(hours=inicio.hour, minutes=inicio.minute)
    tabla["fecha_produccion"] = (tabla["fecha_hora"] - corrimiento).dt.normalize()
    tabla["turno"] = minuto.map(turnos)
    return tabla


def _aceite(muestra: MuestraCentrifuga, factor: float) -> float | None:
    try:
        return aceite_pct(muestra.ml, muestra.blanco_ml, muestra.normalidad, muestra.gramos, factor)
    except ValueError:
        return None


def rangos_centrifugas(sesion: Session, planta_id: int) -> dict:
    """Rangos de RangoReferencia de las centrífugas: {(variable, tipo de equipo): rango}.

    rango = {"minimo", "maximo", "unidad", "fuente", "genera_alerta"}, en la unidad del dato (VARIABLES; por
    ejemplo, un intervalo en minutos pasa a segundos). Si hay uno de la planta (con fruta) y uno general (sin
    fruta, como GEA), se usa el de la planta.
    """
    nombres = {nombre: clave for clave, (nombre, _) in VARIABLES.items()}
    rangos = sesion.scalars(select(RangoReferencia).where(RangoReferencia.planta_id == planta_id)
                            .where(RangoReferencia.punto_o_equipo.in_([PRIMARIA, SECUNDARIA, PULIDORA]))).all()
    resultado = {}
    for rango in sorted(rangos, key=lambda r: r.fruta is not None):           # los de la planta pisan a los generales
        clave = nombres.get(rango.variable)
        if clave is None:
            continue
        factor = A_LA_UNIDAD.get((rango.unidad, VARIABLES[clave][1]), 1)
        resultado[(clave, rango.punto_o_equipo)] = {
            "minimo": None if rango.minimo is None else rango.minimo * factor,
            "maximo": None if rango.maximo is None else rango.maximo * factor,
            "unidad": VARIABLES[clave][1], "fuente": rango.fuente, "genera_alerta": rango.genera_alerta is not False}
    return resultado


def caudal_nominal_primaria(rangos: dict) -> float:
    """Caudal máximo nominal de emulsión de una primaria (L/h), del rango "Caudal de emulsión"."""
    rango = rangos.get(("caudal_emulsion", PRIMARIA))
    if rango is None or rango["maximo"] is None:
        raise ValueError('Falta el rango "Caudal de emulsión" de la centrífuga primaria en RangoReferencia.')
    return rango["maximo"]


# ---------------------------------------------------------------- balance por muestreo

def por_muestreo(tabla: pd.DataFrame) -> pd.DataFrame:
    """Una fila por equipo y muestreo (codigo + fecha_hora): aceite % de cada fase, caudal de emulsión (L/h),
    t de descarga (s), revisar (motivos juntos) y qué fases vienen copiadas de la otra primaria:
    copiada_descarga (afecta la pérdida en descarga) y copiada (pesada o descarga: afecta el balance)."""
    clave = ["codigo", "fecha_hora"]
    base = tabla.groupby(clave, as_index=False).agg(
        equipo=("equipo", "first"), tipo=("tipo", "first"), volumen_descarga_l=("volumen_descarga_l", "first"),
        destino=("destino", "first"), temporada=("temporada", "first"), fecha_produccion=("fecha_produccion", "first"),
        turno=("turno", "first"),
        revisar=("revisar", lambda s: "; ".join(sorted({m for v in s.dropna() for m in v.split("; ")}))))
    for fase, nombre in ACEITE_DE_FASE.items():
        de_fase = tabla.loc[tabla["fase"] == fase, clave + ["aceite_pct"]].rename(columns={"aceite_pct": nombre})
        base = base.merge(de_fase, on=clave, how="left")
    caudal = tabla.loc[tabla["fase"] == "emulsion", clave + ["caudal_lh"]].rename(columns={"caudal_lh": "caudal_emulsion"})
    t = tabla.loc[tabla["fase"] == "descarga", clave + ["t_descarga_s"]]
    copiadas = tabla[tabla["copiada"]]
    base["copiada_descarga"] = _en(base, copiadas[copiadas["fase"] == "descarga"], clave)
    base["copiada"] = _en(base, copiadas[copiadas["fase"].isin(["fase_pesada", "descarga"])], clave)
    return base.merge(caudal, on=clave, how="left").merge(t, on=clave, how="left")


def _en(base: pd.DataFrame, otras: pd.DataFrame, clave: list) -> pd.Series:
    """¿Cada fila de base tiene la misma clave que alguna de otras?"""
    return pd.MultiIndex.from_frame(base[clave]).isin(pd.MultiIndex.from_frame(otras[clave]))


def balance_por_muestreo(muestreos: pd.DataFrame) -> pd.DataFrame:
    """Balance de cada equipo en cada muestreo, con balance_centrifuga (core.centrifugas).

    Agrega caudal_liviana, caudal_pesada, caudal_descarga (L/h); eficiencia_pct (%) con fuera_fisico y
    estado_eficiencia; perdida_pesada_kgh y, según el destino, perdida_pesada_proceso_kgh (canal) o
    recirculado_kgh (recircula al BOE), con estado_pesada; perdida_descarga_kgh (x · ρ · volumen × 3600 / t)
    con estado_descarga. Estados: "calculado", "a definir" (sin volumen), "sin datos (no se muestrea)",
    "sin datos (muestreo incompleto)", "sin datos (no se titula)", "no aplica" (pulidora), "recirculado al BOE".
    Secundaria: su alimentación es la suma de las fases livianas de las primarias del mismo muestreo
    (alimentacion_secundaria); sin fase pesada, su eficiencia es "sin datos (no se muestrea)".
    """
    filas = muestreos.to_dict("records")
    primarias = [f for f in filas if f["tipo"] == PRIMARIA]
    for fila in primarias:
        fila.update(_balance_primaria(fila))
    livianas = {}                                   # fecha_hora → [(caudal, aceite %)] de las primarias
    for fila in primarias:
        if pd.notna(fila["caudal_liviana"]) and pd.notna(fila["aceite_liviana"]):
            livianas.setdefault(fila["fecha_hora"], []).append((fila["caudal_liviana"], fila["aceite_liviana"]))
    for fila in filas:
        if fila["tipo"] == SECUNDARIA:
            fila.update(_balance_secundaria(fila, livianas.get(fila["fecha_hora"], [])))
        elif fila["tipo"] != PRIMARIA:
            fila.update(_balance_pulidora(fila))
    return pd.DataFrame(filas)


def _vacio() -> dict:
    return {"caudal_liviana": None, "caudal_pesada": None, "caudal_descarga": None, "eficiencia_pct": None,
            "fuera_fisico": False, "estado_eficiencia": None, "perdida_pesada_kgh": None,
            "perdida_pesada_proceso_kgh": None, "recirculado_kgh": None, "estado_pesada": None,
            "perdida_descarga_kgh": None, "estado_descarga": None}


def _faltan(fila: dict, *columnas) -> bool:
    return any(pd.isna(fila.get(c)) for c in columnas)


def _descarga(fila: dict) -> dict:
    """Pérdida en descarga (kg/h): solo necesita el aceite de la descarga, el t y el volumen del equipo."""
    if _faltan(fila, "t_descarga_s") or not fila["t_descarga_s"]:
        return {"estado_descarga": SIN_DATOS}
    if _faltan(fila, "aceite_descarga"):
        return {"estado_descarga": SIN_TITULACION}
    if _faltan(fila, "volumen_descarga_l"):
        return {"estado_descarga": A_DEFINIR}
    caudal = caudal_descarga_lh(fila["volumen_descarga_l"], fila["t_descarga_s"])
    return {"caudal_descarga": caudal, "perdida_descarga_kgh": aceite_perdido_kgh(caudal, fila["aceite_descarga"] / 100),
            "estado_descarga": CALCULADO}


def _pesada(fila: dict, caudal_pesada: float | None) -> dict:
    """Aceite que se va con la fase pesada (kg/h), separado según el destino del equipo."""
    if fila["destino"] == NO_APLICA:
        return {"estado_pesada": NO_APLICA}
    if _faltan(fila, "aceite_pesada"):
        return {"estado_pesada": SIN_DATOS}
    if caudal_pesada is None:
        return {"estado_pesada": A_DEFINIR if _faltan(fila, "volumen_descarga_l") else INCOMPLETO}
    kgh = aceite_perdido_kgh(caudal_pesada, fila["aceite_pesada"] / 100)
    if fila["destino"] == RECIRCULA:
        return {"perdida_pesada_kgh": kgh, "recirculado_kgh": kgh, "estado_pesada": RECIRCULADO}
    if fila["destino"] == CANAL:
        return {"perdida_pesada_kgh": kgh, "perdida_pesada_proceso_kgh": kgh, "estado_pesada": CALCULADO}
    return {"perdida_pesada_kgh": kgh, "estado_pesada": "destino a definir"}


def _balance(fila: dict, caudal: float, aceite_alimentacion: float) -> dict:
    """Balance de una centrífuga con su alimentación (caudal L/h y aceite %)."""
    resultado = _vacio() | _descarga(fila)
    if _faltan(fila, "aceite_liviana", "aceite_pesada", "aceite_descarga", "t_descarga_s") or not fila["t_descarga_s"]:
        resultado["estado_eficiencia"] = SIN_DATOS if _faltan(fila, "aceite_pesada") else INCOMPLETO
        return resultado | _pesada(fila, None)
    if _faltan(fila, "volumen_descarga_l"):
        resultado["estado_eficiencia"] = A_DEFINIR
        return resultado | _pesada(fila, None)
    try:
        balance = balance_centrifuga(
            caudal_emulsion=caudal, aceite_emulsion_pct=aceite_alimentacion, aceite_liviana_pct=fila["aceite_liviana"],
            aceite_pesada_pct=fila["aceite_pesada"], aceite_descarga_pct=fila["aceite_descarga"],
            volumen_descarga_l=fila["volumen_descarga_l"], t_descarga_s=fila["t_descarga_s"])
    except ValueError:
        resultado["estado_eficiencia"] = INCOMPLETO
        return resultado | _pesada(fila, None)
    eficiencia = balance["eficiencia"]
    resultado.update(caudal_liviana=balance["caudal_liviana"], caudal_pesada=balance["caudal_pesada"],
                     eficiencia_pct=eficiencia.pct, fuera_fisico=eficiencia.fuera_de_rango_fisico,
                     estado_eficiencia=CALCULADO)
    return resultado | _pesada(fila, balance["caudal_pesada"])


def _balance_primaria(fila: dict) -> dict:
    if _faltan(fila, "caudal_emulsion", "aceite_emulsion") or not fila["caudal_emulsion"]:
        return _vacio() | _descarga(fila) | {"estado_eficiencia": INCOMPLETO} | _pesada(fila, None)
    return _balance(fila, fila["caudal_emulsion"], fila["aceite_emulsion"])


def _balance_secundaria(fila: dict, livianas: list) -> dict:
    if _faltan(fila, "aceite_pesada"):            # la fase pesada de la secundaria casi nunca se muestrea
        return _vacio() | _descarga(fila) | {"estado_eficiencia": SIN_DATOS, "estado_pesada": SIN_DATOS}
    if not livianas:
        return _vacio() | _descarga(fila) | {"estado_eficiencia": INCOMPLETO} | _pesada(fila, None)
    caudal, aceite = alimentacion_secundaria([c for c, _ in livianas], [a for _, a in livianas])
    return _balance(fila, caudal, aceite)


def _balance_pulidora(fila: dict) -> dict:
    return _vacio() | _descarga(fila) | {"estado_eficiencia": NO_APLICA, "estado_pesada": NO_APLICA}


# ---------------------------------------------------------------- primarias en marcha y agua por eBOE

def primarias_por_turno(tabla: pd.DataFrame, caudal_maximo: float) -> pd.DataFrame:
    """Primarias en marcha por turno de producción (INFERIDO: en 2026 se muestreaba una sola por ronda).

    Regla: las dos primarias aparecen en el turno y la mediana del caudal de emulsión de cada una es ≤ caudal_maximo
    (nominal, de RangoReferencia) → "en paralelo"; si aparece una sola o alguna supera el nominal → "una sola".
    caudal_total_lh: en paralelo, la suma de las medianas de cada primaria; una sola, la mediana del turno.
    Columnas: fecha_produccion, turno, temporada, primarias, n_muestras, modo, caudal_total_lh, inferido.
    """
    emulsion = tabla[(tabla["tipo"] == PRIMARIA) & (tabla["fase"] == "emulsion") & tabla["caudal_lh"].notna()]
    filas = []
    for (dia, turno), grupo in emulsion.groupby(["fecha_produccion", "turno"]):
        medianas = grupo.groupby("equipo")["caudal_lh"].median()
        paralelo = len(medianas) >= 2 and medianas.max() <= caudal_maximo
        filas.append({"fecha_produccion": dia, "turno": turno, "temporada": grupo["temporada"].iloc[0],
                      "primarias": ", ".join(medianas.index), "n_muestras": len(grupo),
                      "modo": PARALELO if paralelo else UNA_SOLA,
                      "caudal_total_lh": medianas.sum() if paralelo else grupo["caudal_lh"].median(), "inferido": True})
    return pd.DataFrame(filas, columns=["fecha_produccion", "turno", "temporada", "primarias", "n_muestras", "modo",
                                        "caudal_total_lh", "inferido"])


def agua_por_eboe(balance: pd.DataFrame, turnos: pd.DataFrame, analisis: pd.DataFrame) -> pd.DataFrame:
    """Agua disponible por eBOE (m³/h) por turno = caudal de fase pesada de las primarias en marcha / eBOE en marcha.

    Caudal de fase pesada: del balance de cada muestreo (L/h); en paralelo, suma de las medianas de cada primaria;
    una sola, mediana del turno. eBOE en marcha: los que tienen análisis de Entrada en ese turno (tabla_analisis);
    si no hay, EBOE_POR_DEFECTO (eboe_por_defecto=True). agua_lab_m3h: mediana del caudal que carga el
    laboratorio del BOE (columna Caudal de la hoja BOE → LecturaOperacion.caudal_fase_pesada, m³/h por eBOE).
    """
    pesada = balance[(balance["tipo"] == PRIMARIA) & balance["caudal_pesada"].notna()]
    entrada = analisis[analisis["lugar"] == "Entrada"]
    eboe = entrada.groupby(["fecha_produccion", "turno"])["eboe"].nunique()
    lab = entrada.groupby(["fecha_produccion", "turno"])["caudal_fase_pesada"].agg(["median", "count"])
    grupos = dict(list(pesada.groupby(["fecha_produccion", "turno"])))
    filas = []
    for _, turno in turnos.iterrows():
        clave = (turno["fecha_produccion"], turno["turno"])
        grupo = grupos.get(clave)
        if grupo is None:
            continue
        medianas = grupo.groupby("equipo")["caudal_pesada"].median()
        total = medianas.sum() if turno["modo"] == PARALELO else grupo["caudal_pesada"].median()
        n_eboe = eboe.get(clave)
        filas.append({"fecha_produccion": clave[0], "turno": clave[1], "temporada": turno["temporada"],
                      "modo": turno["modo"], "caudal_pesada_m3h": total / 1000,
                      "eboe_en_marcha": int(n_eboe) if n_eboe else EBOE_POR_DEFECTO, "eboe_por_defecto": not n_eboe,
                      "agua_lab_m3h": lab["median"].get(clave), "n_lab": int(lab["count"].get(clave, 0))})
    tabla = pd.DataFrame(filas, columns=["fecha_produccion", "turno", "temporada", "modo", "caudal_pesada_m3h",
                                         "eboe_en_marcha", "eboe_por_defecto", "agua_lab_m3h", "n_lab"])
    tabla["agua_por_eboe_m3h"] = tabla["caudal_pesada_m3h"] / tabla["eboe_en_marcha"]
    return tabla


# ---------------------------------------------------------------- resumen por temporada y equipo

def valores_por_variable(tabla: pd.DataFrame, balance: pd.DataFrame, incluir_copiadas: bool = False) -> pd.DataFrame:
    """Tabla larga (temporada, equipo, tipo, variable, valor, no_medible, fuera_fisico) para resumir.

    Las muestras copiadas quedan afuera salvo incluir_copiadas=True; en el balance, la eficiencia y la fase
    pesada de los muestreos con la pesada o la descarga copiadas, y la pérdida en descarga de los muestreos con la
    descarga copiada. Las CP con marca van con no_medible=True.
    """
    muestras = tabla if incluir_copiadas else tabla[~tabla["copiada"]]
    partes = []

    def agregar(datos, variable, columna, no_medible=None, fuera_fisico=None):
        datos = datos[datos[columna].notna()]
        partes.append(pd.DataFrame({
            "temporada": datos["temporada"], "equipo": datos["equipo"], "tipo": datos["tipo"], "variable": variable,
            "valor": datos[columna].astype(float),
            "no_medible": False if no_medible is None else no_medible.loc[datos.index],
            "fuera_fisico": False if fuera_fisico is None else fuera_fisico.loc[datos.index]}))

    for fase, variable in ACEITE_DE_FASE.items():
        agregar(muestras[muestras["fase"] == fase], variable, "aceite_pct")
    emulsion = muestras[muestras["fase"] == "emulsion"]
    agregar(emulsion, "solidos", "solidos_pct")
    agregar(emulsion, "caudal_emulsion", "caudal_lh")
    agregar(muestras, "cp_liviana", "cp_liviana_bar", no_medible=muestras["cp_liviana_marca"].notna())
    agregar(muestras, "cp_pesada", "cp_pesada_bar")
    agregar(muestras, "t_descarga", "t_descarga_s")
    balances = balance if incluir_copiadas else balance[~balance["copiada"]]
    agregar(balances, "eficiencia", "eficiencia_pct", fuera_fisico=balances["fuera_fisico"])
    agregar(balances, "perdida_pesada", "perdida_pesada_kgh")
    agregar(balance if incluir_copiadas else balance[~balance["copiada_descarga"]], "perdida_descarga",
            "perdida_descarga_kgh")
    return pd.concat(partes, ignore_index=True)


def resumen_por_temporada(valores: pd.DataFrame, rangos: dict) -> pd.DataFrame:
    """N, mediana, Q1 y Q3 (método de Minitab) y % fuera de rango por temporada, equipo y variable.

    Los valores no_medible no entran (n_no_medible los cuenta aparte). % fuera de rango contra el rango de
    rangos_centrifugas() para ese tipo de equipo (vacío si no hay rango); n_fuera_fisico cuenta las eficiencias
    fuera de rango físico, que sí entran. pocos_datos: N < 3.
    """
    filas = []
    for (temporada, equipo, tipo, variable), grupo in valores.groupby(["temporada", "equipo", "tipo", "variable"]):
        validos = grupo.loc[~grupo["no_medible"], "valor"]
        rango = rangos.get((variable, tipo))
        fila = {"temporada": temporada, "equipo": equipo, "tipo": tipo, "variable": variable,
                "nombre": VARIABLES[variable][0], "unidad": VARIABLES[variable][1], "n": len(validos),
                "q1": None, "mediana": None, "q3": None, "n_no_medible": int(grupo["no_medible"].sum()),
                "n_fuera_fisico": int(grupo["fuera_fisico"].sum()), "pocos_datos": len(validos) < N_MINIMO,
                "minimo": None, "maximo": None, "fuente": None, "genera_alerta": None, "n_fuera": None, "pct_fuera": None}
        if len(validos):
            fila["q1"], fila["mediana"], fila["q3"] = cuartiles(validos)
        if rango is not None and len(validos):
            fuera = pd.Series(False, index=validos.index)
            if rango["minimo"] is not None:
                fuera |= validos < rango["minimo"]
            if rango["maximo"] is not None:
                fuera |= validos > rango["maximo"]
            fila.update(minimo=rango["minimo"], maximo=rango["maximo"], fuente=rango["fuente"],
                        genera_alerta=rango["genera_alerta"], n_fuera=int(fuera.sum()),
                        pct_fuera=100 * fuera.mean())
        filas.append(fila)
    return pd.DataFrame(filas)


# ---------------------------------------------------------------- pérdida en descarga estimada

def integrar_descarga(balance: pd.DataFrame, incluir_copiadas: bool = False) -> pd.DataFrame:
    """Muestreos con pérdida en descarga calculada, con horas y kg ESTIMADOS hasta el muestreo siguiente.

    horas: tiempo hasta el muestreo siguiente del mismo equipo, como máximo INTERVALO_MAXIMO_H (4 h); el último
    muestreo de cada equipo no suma horas. kg = kg/h × horas.
    """
    datos = balance[balance["perdida_descarga_kgh"].notna()]
    if not incluir_copiadas:
        datos = datos[~datos["copiada_descarga"]]
    datos = datos.sort_values(["codigo", "fecha_hora"]).copy()
    siguiente = datos.groupby("codigo")["fecha_hora"].shift(-1)
    datos["horas"] = ((siguiente - datos["fecha_hora"]).dt.total_seconds() / 3600).clip(upper=INTERVALO_MAXIMO_H)
    datos["horas"] = datos["horas"].fillna(0)
    datos["kg"] = datos["perdida_descarga_kgh"] * datos["horas"]
    return datos


def perdida_descarga_temporada(balance: pd.DataFrame, incluir_copiadas: bool = False) -> pd.DataFrame:
    """Pérdida en descarga por temporada y equipo: N, mediana (kg/h), kg ESTIMADOS y horas que cubre la estimación."""
    datos = integrar_descarga(balance, incluir_copiadas)
    return datos.groupby(["temporada", "equipo"], as_index=False).agg(
        n=("perdida_descarga_kgh", "size"), mediana_kgh=("perdida_descarga_kgh", "median"), kg=("kg", "sum"),
        horas=("horas", "sum"))


def perdida_por_tonelada(balance: pd.DataFrame, produccion: pd.DataFrame, incluir_copiadas: bool = False) -> pd.DataFrame:
    """Pérdida en descarga por tonelada de fruta (kg/MT) por día de producción.

    kg del día (todos los equipos, integrar_descarga) / toneladas procesadas del día (tabla_produccion, suma de
    fruta_t). Sin toneladas ese día, kg_mt queda vacío.
    """
    datos = integrar_descarga(balance, incluir_copiadas)
    por_dia = datos.groupby("fecha_produccion", as_index=False).agg(kg=("kg", "sum"), horas=("horas", "sum"),
                                                                   n=("kg", "size"))
    toneladas = produccion.groupby("fecha_produccion", as_index=False)["fruta_t"].sum(min_count=1)
    tabla = por_dia.merge(toneladas, on="fecha_produccion", how="left")
    tabla["kg_mt"] = tabla["kg"] / tabla["fruta_t"].where(tabla["fruta_t"] > 0)
    return tabla


# ---------------------------------------------------------------- relación con el BOE

def relacion_boe(tabla: pd.DataFrame, emulsion_boe: pd.DataFrame, fruta_boe: pd.DataFrame) -> pd.DataFrame:
    """Por día de producción: mediana del aceite en emulsión de las primarias (%) contra la del laboratorio del BOE
    (%), y la mediana del aceite residual en fruta a la salida del eBOE (kg/MT), con el programa medio del día.

    El programa medio (N0–N9 → 0–9, de las Entradas del laboratorio) va porque el agua está confundida con el
    programa. Cada columna lleva su N.
    """
    cen = tabla[(tabla["tipo"] == PRIMARIA) & (tabla["fase"] == "emulsion") & tabla["aceite_pct"].notna()]
    partes = [
        cen.groupby("fecha_produccion").agg(aceite_emulsion_cen=("aceite_pct", "median"), n_cen=("aceite_pct", "size")),
        emulsion_boe.dropna(subset=["aceite_emulsion"]).groupby("fecha_produccion").agg(
            aceite_emulsion_boe=("aceite_emulsion", "median"), n_boe=("aceite_emulsion", "size")),
        fruta_boe[fruta_boe["lugar"] == "Salida"].dropna(subset=["aceite_fruta"]).groupby("fecha_produccion").agg(
            aceite_salida=("aceite_fruta", "median"), n_salida=("aceite_fruta", "size")),
        fruta_boe[fruta_boe["lugar"] == "Entrada"].assign(numero=lambda d: d["programa"].map(numero_programa))
        .dropna(subset=["numero"]).groupby("fecha_produccion").agg(programa_medio=("numero", "mean"))]
    resultado = pd.concat(partes, axis=1).reset_index()
    resultado["temporada"] = resultado["fecha_produccion"].dt.year
    return resultado.dropna(subset=["aceite_emulsion_cen"])
