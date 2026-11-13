"""Gestión de alertas (tarea 19): lista con filtros, estados, causa / acción / comentario, datos y carta del período.

Sin Streamlit. Estados: abierta → en análisis → cerrada, o abierta → cerrada. Una cerrada no se reabre (si vuelve a
pasar, el sistema crea otra), pero se le pueden agregar comentarios. Las "histórico" no cambian de estado, pero se les
puede cargar causa, acción y comentario. Todo cambio queda en Cambio (tabla "alerta"), con quién y cuándo.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import or_, select

from core.alertas import CERRADA, ENTRE_DENTRO
from core.calculos import aceite_en_emulsion, aceite_en_fruta
from core.cambios import nuevo_grupo, registrar
from core.carga_scott import series_recientes
from core.limites import VARIABLES_BOE, VARIABLES_CONTROL, limites_vigentes, series_de_planta, subgrupos_por_turno
from core.models import (Alerta, AnalisisScott, Equipo, LimitesControl, Linea, MuestraCentrifuga, Planta,
                         RangoReferencia)
from core.spc import limites_xbar_s_para
from core.turnos import fecha_produccion, turno_de

EN_ANALISIS = "en análisis"
SIGUIENTES = {"abierta": (EN_ANALISIS, CERRADA), EN_ANALISIS: (CERRADA,), CERRADA: (), "histórico": ()}
CAMPOS_SEGUIMIENTO = {"causa": "causa", "accion": "acción", "comentario": "comentario"}
TURNOS_ALREDEDOR = 10          # la carta muestra 10 turnos antes del inicio y 10 después del fin
DIAS_ALREDEDOR = 5             # ~15 turnos: alcanza para TURNOS_ALREDEDOR
LUGARES = {"fruta entrada": "entrada", "fruta salida": "salida", "emulsión": "emulsión"}


def _de_planta(planta_id: int):
    """Condición: alertas de los equipos de la planta o de ámbito planta."""
    equipos = select(Equipo.id).join(Linea).where(Linea.planta_id == planta_id)
    return or_(Alerta.equipo_id.in_(equipos), Alerta.planta_id == planta_id)


def episodio_se_superpone(planta: Planta, alerta: Alerta, desde=None, hasta=None) -> bool:
    """¿El episodio de la alerta (del día de producción de su inicio al de su fin) toca el período desde–hasta (días
    de producción, inclusive; None: sin límite)? Sin fin, el episodio es su primer turno."""
    ultimo = max(alerta.fecha_produccion, fecha_produccion(planta, alerta.fin)) if alerta.fin else alerta.fecha_produccion
    return (hasta is None or alerta.fecha_produccion <= hasta) and (desde is None or ultimo >= desde)


def listar_alertas(sesion, planta_id: int, estados=("abierta", EN_ANALISIS), desde=None, hasta=None,
                   variables=None, ambitos=None, reglas=None, solo_inmediatas: bool = False,
                   solo_comunes: bool = False, con_causa: bool | None = None,
                   superpuestas: bool = False) -> pd.DataFrame:
    """Alertas de la planta, de la más nueva a la más vieja. Filtros: estados, días de producción del inicio
    (desde/hasta; con superpuestas=True, las que tienen algún turno del episodio en el período aunque hayan empezado
    antes: episodio_se_superpone), variables (claves), ámbitos (texto como se muestra: "BOE Nº 1", "Planta"…),
    reglas, solo inmediatas, solo comunes y con_causa (True: con causa cargada; False: sin causa; None: todas)."""
    planta = sesion.get(Planta, planta_id)
    consulta = select(Alerta).where(_de_planta(planta_id)).order_by(Alerta.fecha_hora.desc())
    if estados:
        consulta = consulta.where(Alerta.estado.in_(list(estados)))
    filas = []
    for a in sesion.scalars(consulta):
        ambito = a.equipo.nombre if a.equipo else ("Planta (común eBOE)" if a.comun_eboe else "Planta")
        tiene_causa = bool((a.causa or "").strip())
        en_periodo = (episodio_se_superpone(planta, a, desde, hasta) if superpuestas
                      else not ((desde and a.fecha_produccion < desde) or (hasta and a.fecha_produccion > hasta)))
        if (not en_periodo or (variables and a.variable not in variables) or (ambitos and ambito not in ambitos)
                or (reglas and a.regla not in reglas) or (solo_inmediatas and not a.inmediata)
                or (solo_comunes and not a.comun_eboe) or (con_causa is not None and tiene_causa != con_causa)):
            continue
        filas.append({"id": a.id, "inicio": a.fecha_hora, "fin": a.fin, "turno": a.turno.nombre,
                      "turnos": a.turnos_afectados, "variable": a.variable,
                      "nombre": VARIABLES_CONTROL[a.variable]["nombre"], "ambito": ambito, "regla": a.regla,
                      "valor": a.valor, "limite": a.limite, "estado": a.estado, "inmediata": bool(a.inmediata),
                      "confirmada": a.confirmada, "comun": bool(a.comun_eboe), "con_causa": tiene_causa,
                      "comentario": a.comentario})
    tabla = pd.DataFrame(filas, columns=["id", "inicio", "fin", "turno", "turnos", "variable", "nombre", "ambito",
                                         "regla", "valor", "limite", "estado", "inmediata", "confirmada", "comun",
                                         "con_causa", "comentario"])
    return tabla.astype({"inicio": "datetime64[ns]", "fin": "datetime64[ns]"})     # fechas también sin filas


def cambiar_estado(sesion, alerta_id: int, nuevo: str, usuario: str, ahora: datetime, motivo: str | None = None
                   ) -> Alerta:
    """Pasa la alerta a "en análisis" o "cerrada" (SIGUIENTES). Cerrada: guarda cerrada_por y cerrada_en. Queda en
    Cambio. Error si el cambio no está permitido (una cerrada no se reabre; una "histórico" no cambia de estado)."""
    alerta = _alerta(sesion, alerta_id)
    if not (usuario or "").strip():
        raise ValueError("Falta quién hace el cambio.")
    if nuevo not in SIGUIENTES.get(alerta.estado, ()):
        if alerta.estado == "histórico":
            raise ValueError("Una alerta histórico no cambia de estado (sí se le puede cargar causa, acción y "
                             "comentario).")
        raise ValueError(f'No se puede pasar de "{alerta.estado}" a "{nuevo}".')
    registrar(sesion, "alerta", alerta.id, "estado", motivo or f"pasa a {nuevo}", usuario, ahora, nuevo_grupo(),
              campo="estado", anterior=alerta.estado, nuevo=nuevo)
    alerta.estado = nuevo
    if nuevo == CERRADA:
        alerta.cerrada_por, alerta.cerrada_en = usuario, ahora
    sesion.commit()
    return alerta


def guardar_seguimiento(sesion, alerta_id: int, usuario: str, ahora: datetime, causa: str | None = None,
                        accion: str | None = None, comentario: str | None = None) -> int:
    """Guarda causa, acción y comentario (los que vienen y cambiaron), en cualquier estado (también histórico y
    cerrada). Un registro de Cambio por campo. Devuelve cuántos campos cambiaron."""
    alerta = _alerta(sesion, alerta_id)
    if not (usuario or "").strip():
        raise ValueError("Falta quién hace el cambio.")
    grupo, cambiados = nuevo_grupo(), 0
    for campo, valor in (("causa", causa), ("accion", accion), ("comentario", comentario)):
        if valor is None:
            continue
        valor = valor.strip() or None
        if valor != getattr(alerta, campo):
            registrar(sesion, "alerta", alerta.id, "seguimiento", f"seguimiento: {CAMPOS_SEGUIMIENTO[campo]}", usuario,
                      ahora, grupo, campo=campo, anterior=getattr(alerta, campo), nuevo=valor)
            setattr(alerta, campo, valor)
            cambiados += 1
    sesion.commit()
    return cambiados


def _alerta(sesion, alerta_id: int) -> Alerta:
    alerta = sesion.get(Alerta, alerta_id)
    if alerta is None:
        raise ValueError(f"No existe la alerta {alerta_id}.")
    return alerta


# ---------------------------------------------------------------- datos y carta de una alerta

def datos_de_alerta(sesion, planta: Planta, alerta: Alerta) -> pd.DataFrame:
    """Los datos que sostienen la alerta: fecha_hora, turno, equipo, lugar o fase, resultado (aceite en fruta kg/MT,
    en emulsión %, o el valor medido), °Brix, pH, analista y revisar. En orden de tiempo."""
    filas = []
    for enlace in alerta.datos:
        if enlace.tabla == "analisis_scott":
            a = sesion.get(AnalisisScott, enlace.dato_id)
            if a is None:
                continue
            lugar = LUGARES.get(a.punto_muestreo.tipo, a.punto_muestreo.tipo)
            funcion = aceite_en_emulsion if lugar == "emulsión" else aceite_en_fruta
            try:
                resultado = funcion(planta, a.vol_ml, a.blanco_ml, a.normalidad, a.peso_muestra_g)
            except ValueError:
                resultado = None
            filas.append({"dato_id": a.id, "fecha_hora": a.fecha_hora, "turno": turno_de(planta, a.fecha_hora).nombre,
                          "equipo": a.punto_muestreo.equipo.nombre, "lugar": lugar, "resultado": resultado,
                          "brix": a.brix, "ph": a.ph, "analista": a.analista, "revisar": a.revisar})
        else:
            m = sesion.get(MuestraCentrifuga, enlace.dato_id)
            if m is None:
                continue
            filas.append({"dato_id": m.id, "fecha_hora": m.fecha_hora, "turno": turno_de(planta, m.fecha_hora).nombre,
                          "equipo": m.equipo.nombre, "lugar": m.fase, "resultado": m.aceite_pct_medido,
                          "brix": None, "ph": None, "analista": m.analista, "revisar": m.revisar})
    tabla = pd.DataFrame(filas, columns=["dato_id", "fecha_hora", "turno", "equipo", "lugar", "resultado", "brix", "ph",
                                         "analista", "revisar"])
    return tabla.sort_values("fecha_hora", ignore_index=True)


def carta_de_alerta(sesion, planta: Planta, alerta: Alerta, turnos_alrededor: int = TURNOS_ALREDEDOR) -> list[dict]:
    """Datos de la carta del período de la alerta: una por ámbito (en una alerta común de planta, una por eBOE).

    Cada una: {"titulo", "unidad", "tipo_carta", "rango" (mínimo, máximo) o None, "tabla"}: un turno por fila
    (fecha_hora, fecha_produccion, turno, n, media, desvio, lc, lcs, lci, s_lc, s_lcs, s_lci, en_episodio), desde
    `turnos_alrededor` turnos antes del inicio hasta otros tantos después del fin. Límites: los de la alerta (o los
    vigentes del eBOE, en una común); con límites entre/dentro, los del promedio son iguales para todo n y los de S
    dependen del n del turno; sin límites (alerta de rango), vacíos.
    """
    variable = alerta.variable
    desde = alerta.fecha_hora - timedelta(days=DIAS_ALREDEDOR + 10)
    if variable in VARIABLES_BOE:
        series = series_recientes(sesion, planta, desde)
    else:
        series = series_de_planta(sesion, planta.id)
    serie = series.get(variable)
    if serie is None or serie.empty:
        return []
    if alerta.comun_eboe:
        ambitos = sorted(set(serie.loc[serie["tipo"] == "BOE", "ambito"]))
    else:
        ambitos = [alerta.ambito]
    rango = None
    if alerta.rango_id:
        r = sesion.get(RangoReferencia, alerta.rango_id)
        rango = (r.minimo, r.maximo) if r else None
    cartas = []
    for ambito in ambitos:
        subgrupos = subgrupos_por_turno(serie[serie["ambito"] == ambito]).sort_values("fecha_hora", ignore_index=True)
        if subgrupos.empty:
            continue
        dentro = (subgrupos["fecha_hora"] >= alerta.fecha_hora) & (subgrupos["fecha_hora"] <= (alerta.fin or
                                                                                               alerta.fecha_hora))
        posiciones = np.flatnonzero(dentro.to_numpy())
        if len(posiciones):
            primero, ultimo = posiciones[0], posiciones[-1]
        else:
            primero = ultimo = int(np.searchsorted(subgrupos["fecha_hora"].to_numpy(),
                                                   np.datetime64(alerta.fecha_hora)))
        ventana = subgrupos.iloc[max(0, primero - turnos_alrededor):ultimo + turnos_alrededor + 1].copy()
        ventana["en_episodio"] = dentro.loc[ventana.index].to_numpy()
        limites = _limites_de(sesion, alerta, ventana)
        ventana = _con_limites(ventana, limites)
        cartas.append({"titulo": f"{VARIABLES_CONTROL[variable]['nombre']} · {ventana['equipo'].iloc[0]}",
                       "unidad": VARIABLES_CONTROL[variable]["unidad"],
                       "tipo_carta": limites.tipo_carta if limites else None, "rango": rango,
                       "tabla": ventana.reset_index(drop=True)})
    return cartas


def _limites_de(sesion, alerta: Alerta, ventana: pd.DataFrame) -> LimitesControl | None:
    """Los límites de la alerta o, en una común (o una de rango), los vigentes del ámbito de la ventana."""
    if alerta.limites_id and not alerta.comun_eboe:
        return sesion.get(LimitesControl, alerta.limites_id)
    equipo_id = ventana["equipo_id"].iloc[0]
    equipo_id = None if pd.isna(equipo_id) else int(equipo_id)
    planta_id = None if equipo_id is not None else int(ventana["planta_id"].iloc[0])
    frutas = ventana["fruta"].dropna()
    return limites_vigentes(sesion, alerta.variable, frutas.mode().iloc[0] if not frutas.empty else None,
                            equipo_id, planta_id)


def _con_limites(ventana: pd.DataFrame, limites: LimitesControl | None) -> pd.DataFrame:
    """Agrega lc, lcs, lci del promedio y s_lc, s_lcs, s_lci de la carta S de cada turno."""
    columnas = ["lc", "lcs", "lci", "s_lc", "s_lcs", "s_lci"]
    if limites is None:
        return ventana.assign(**{c: np.nan for c in columnas})
    filas = []
    for n in ventana["n"].astype(int):
        if limites.tipo_carta == ENTRE_DENTRO:
            s = limites_xbar_s_para(limites.lc, limites.sigma_dentro, n) if limites.sigma_dentro else {}
            filas.append({"lc": limites.lc, "lcs": limites.lcs, "lci": limites.lci, "s_lc": s.get("s_lc"),
                          "s_lcs": s.get("s_lcs"), "s_lci": s.get("s_lci")})
        else:
            s = limites_xbar_s_para(limites.lc, limites.sigma, n)
            filas.append({"lc": limites.lc, "lcs": s["lcs"], "lci": s["lci"], "s_lc": s["s_lc"], "s_lcs": s["s_lcs"],
                          "s_lci": s["s_lci"]})
    return pd.concat([ventana.reset_index(drop=True), pd.DataFrame(filas, columns=columnas)], axis=1)
