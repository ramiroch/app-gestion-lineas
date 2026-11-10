"""Carga de una ronda de análisis Scott desde el formulario (tarea 18). Sin Streamlit.

Una ronda tiene una fecha y hora, un proceso, un analista y la fruta, y para CADA eBOE en marcha:
- entrada: g, mL, peso de la fruta (kg), n.º de frutos y calidad;
- salida: g, mL y rallado (opcional);
- emulsión: g, mL, °Brix y pH;
- operación: programa, MTPH y caudal de fase pesada.
No hay una entrada única: cada eBOE tiene la suya. Normalidad: una por tipo de punto (entrada, salida, emulsión). El
blanco es el de la planta (blanco_ml). Formato de la ronda (diccionario):
    {"fecha_hora", "proceso_id", "analista", "fruta", "normalidad": {"entrada", "salida", "emulsion"},
     "eboes": {n.º: {"entrada": {...}, "salida": {...}, "emulsion": {...}, "operacion": {...}}}}

- hora_siguiente(), valores_por_defecto(): lo que se propone al abrir el formulario.
- calcular_ronda(): resultados en vivo; semaforo(): cada resultado contra RangoReferencia (lado_capacidad).
- validar_ronda(): errores (no deja guardar) y avisos (deja guardar); contexto_validacion() lee lo que necesita.
- guardar_ronda(): AnalisisScott y LecturaOperacion en una transacción, marca de entradas repetidas y alertas.
- chequeo_inmediato(): regla 1 más allá de sigma_alerta_inmediata (lado malo) en el turno en curso, si tiene 2
  rondas o más.
- evaluar_turnos_cerrados(): evaluación completa (reglas_alerta, persistencia, comunes) de los turnos que ya
  terminaron y no se evaluaron; los marca en TurnoEvaluado.
"""

from collections import Counter
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.alertas import (ORIGEN_NELSON, POR_TURNO, guardar_evaluados, evaluar_nuevos, leer_reglas,
                          violaciones_por_turno)
from core.analisis import tabla_analisis, tabla_emulsion
from core.calculos import aceite_en_emulsion, aceite_en_fruta
from core.limites import TIPO_EBOE, VARIABLES_BOE, VARIABLES_CONTROL, limites_vigentes, series_boe, subgrupos_por_turno
from core.models import (Alerta, AnalisisScott, Equipo, LecturaOperacion, Linea, Planta, Proceso, PuntoMuestreo,
                         RangoReferencia, TurnoEvaluado)
from core.operacion import especie
from core.parametros import validar_parametros
from core.repetidas import entradas_repetidas, marcar_entradas_repetidas
from core.turnos import fecha_produccion, fin_de_turno, turno_de

# Lugar de la ronda → tipo de PuntoMuestreo
PUNTOS = {"entrada": "fruta entrada", "salida": "fruta salida", "emulsion": "emulsión"}
NOMBRES_LUGAR = {"entrada": "entrada", "salida": "salida", "emulsion": "emulsión"}
CALIDADES = ("Firme", "Blanda", "Podrido", "Partida")
RALLADOS = ("Ok", "Poco rallada", "Sobre rallada")
PROGRAMAS = tuple(f"N{n}" for n in range(10)) + tuple(str(n) for n in range(13))   # naranja N0–N9; otros 0–12
INTERVALO_POR_DEFECTO = timedelta(hours=2)
RONDAS_PARA_INTERVALO = 30               # rondas recientes para estimar el intervalo típico
FACTOR_NORMALIDAD = 5                    # aviso si la normalidad cambia en un factor ≥ 5
GRAMOS_MINIMOS = 1.0
PERCENTILES = (0.5, 99.5)
N_MINIMO_PERCENTILES = 50                # datos del histórico de un punto para usar sus percentiles
MINUTOS_FUTURO = 10                      # una ronda más allá de ahora + 10 min es "en el futuro"
DIAS_TURNOS_CERRADOS = 14                # se buscan turnos sin evaluar en los últimos 14 días
DIAS_PREVIOS = 10                        # datos anteriores para las reglas que miran hacia atrás (14 turnos)
RONDAS_INMEDIATO = 2

# Resultado → nombre, unidad, decimales y rango de RangoReferencia (variable, punto o equipo)
RESULTADOS = {
    "aceite_entrada": ("Aceite entrada", "kg/MT", 2, None),
    "aceite_salida": ("Aceite salida", "kg/MT", 2, None),
    "eficiencia": ("Eficiencia", "%", 1, ("Eficiencia eBOE", "BOE (todos)")),
    "rendimiento": ("Rendimiento", "kg/MT", 2, ("Rendimiento eBOE", "BOE (todos)")),
    "aceite_emulsion": ("Aceite en emulsión", "%", 2, ("Aceite en emulsión", "Emulsión (licor)")),
    "brix": ("°Brix", "°Brix", 1, ("°Brix emulsión", "Emulsión (licor)")),
    "ph": ("pH", "", 2, ("pH emulsión", "Emulsión (licor)")),
    "frutos_10kg": ("Frutos en 10 kg", "", 0, None),
}
# Campo del formulario → resultado calculado con el que se compara contra el histórico del punto
CAMPOS_HISTORICO = {"entrada": ("g", "ml", "peso_fruta_kg", "n_frutos", "aceite"),
                    "salida": ("g", "ml", "aceite"),
                    "emulsion": ("g", "ml", "brix", "ph", "aceite")}
NOMBRES_CAMPO = {"g": "g", "ml": "mL", "peso_fruta_kg": "peso de fruta", "n_frutos": "n.º de frutos",
                 "brix": "°Brix", "ph": "pH", "aceite": "aceite"}


def _hay(valor) -> bool:
    return valor is not None and not (isinstance(valor, float) and np.isnan(valor)) and valor != ""


# ---------------------------------------------------------------- valores iniciales

def hora_siguiente(planta: Planta, rondas: list[datetime], ahora: datetime) -> datetime:
    """Hora propuesta para la ronda nueva: la última + el intervalo más frecuente entre rondas recientes (2 h si no
    se puede estimar), si la última ronda es del turno en curso; si no, la hora actual (al minuto).

    rondas: fechas y horas de las rondas anteriores (cualquier orden). Fuente: en el histórico se muestrea en
    horarios fijos (05:10, 07:10, 09:10…).
    """
    actual = ahora.replace(second=0, microsecond=0)
    if not rondas:
        return actual
    ordenadas = sorted(set(rondas))[-RONDAS_PARA_INTERVALO:]
    ultima = ordenadas[-1]
    mismo_turno = (fecha_produccion(planta, ultima) == fecha_produccion(planta, ahora)
                   and turno_de(planta, ultima).id == turno_de(planta, ahora).id)
    if not mismo_turno:
        return actual
    diferencias = Counter(b - a for a, b in zip(ordenadas, ordenadas[1:]) if timedelta(0) < b - a <= timedelta(hours=8))
    intervalo = diferencias.most_common(1)[0][0] if diferencias else INTERVALO_POR_DEFECTO
    return ultima + intervalo


def _analisis_planta(sesion: Session, planta_id: int, desde: datetime | None = None):
    """(AnalisisScott, n.º de eBOE, lugar) de los eBOE de la planta."""
    consulta = (select(AnalisisScott, Equipo.numero, PuntoMuestreo.tipo)
                .join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo).join(Equipo.linea)
                .where(Linea.planta_id == planta_id, Equipo.tipo == TIPO_EBOE,
                       PuntoMuestreo.tipo.in_(PUNTOS.values())))
    if desde is not None:
        consulta = consulta.where(AnalisisScott.fecha_hora >= desde)
    lugar = {tipo: clave for clave, tipo in PUNTOS.items()}
    return [(a, numero, lugar[tipo]) for a, numero, tipo in sesion.execute(consulta).all()]


def valores_por_defecto(sesion: Session, planta: Planta, ahora: datetime) -> dict:
    """Lo que el formulario propone: fecha_hora (hora_siguiente), proceso, fruta y analista de la última ronda,
    la última normalidad de cada lugar, los eBOE de la última ronda (en marcha) y las opciones (eBOE de la planta,
    procesos: [(id, "2026 · proceso 12")], más recientes primero)."""
    eboes = sorted(sesion.scalars(select(Equipo.numero).join(Linea).where(
        Linea.planta_id == planta.id, Equipo.tipo == TIPO_EBOE)).all())
    procesos = sesion.scalars(select(Proceso).where(Proceso.planta_id == planta.id)
                              .order_by(Proceso.temporada.desc(), Proceso.numero.desc())).all()
    ultimas = sesion.scalars(select(AnalisisScott.fecha_hora).distinct()
                             .join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo).join(Equipo.linea)
                             .where(Linea.planta_id == planta.id, Equipo.tipo == TIPO_EBOE)
                             .order_by(AnalisisScott.fecha_hora.desc()).limit(RONDAS_PARA_INTERVALO)).all()
    resultado = {"fecha_hora": hora_siguiente(planta, ultimas, ahora), "proceso_id": procesos[0].id if procesos else None,
                 "fruta": None, "analista": None, "normalidad": {}, "en_marcha": eboes, "eboes": eboes,
                 "procesos": [(p.id, f"{p.temporada} · proceso {p.numero}") for p in procesos]}
    if ultimas:
        ultima = _analisis_planta(sesion, planta.id, desde=max(ultimas))
        if ultima:
            resultado["proceso_id"] = ultima[0][0].proceso_id
            resultado["fruta"] = ultima[0][0].fruta
            resultado["analista"] = ultima[0][0].analista
            resultado["en_marcha"] = sorted({numero for _, numero, _ in ultima})
    resultado["normalidad"] = normalidades_previas(sesion, planta.id)
    return resultado


def normalidades_previas(sesion: Session, planta_id: int) -> dict:
    """La última normalidad usada en cada lugar (entrada, salida, emulsión)."""
    previas = {}
    for lugar, tipo in PUNTOS.items():
        valor = sesion.scalars(select(AnalisisScott.normalidad)
                               .join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo).join(Equipo.linea)
                               .where(Linea.planta_id == planta_id, Equipo.tipo == TIPO_EBOE, PuntoMuestreo.tipo == tipo)
                               .order_by(AnalisisScott.fecha_hora.desc()).limit(1)).first()
        if valor is not None:
            previas[lugar] = valor
    return previas


# ---------------------------------------------------------------- cálculo en vivo y semáforo

def calcular_ronda(planta: Planta, ronda: dict) -> dict:
    """Resultados de cada eBOE de la ronda: {n.º: {resultado: valor o None}} (claves de RESULTADOS).

    aceite_entrada y aceite_salida (kg/MT) = (mL − blanco) × N × factor_aceite_fruta / g; eficiencia (%) =
    100 × (1 − salida / entrada); rendimiento (kg/MT) = entrada − salida; aceite_emulsion (%) = (mL − blanco) × N ×
    factor_aceite_emulsion / g; frutos_10kg = n.º de frutos × 10 / peso de la fruta (kg). Fuente: técnica Scott y
    manual BOE de Brown (core.calculos). Un resultado queda en None si falta un dato o es inválido.
    """
    blanco = planta.blanco_ml
    normalidad = ronda.get("normalidad", {})
    resultados = {}
    for numero, datos in ronda.get("eboes", {}).items():
        r = dict.fromkeys(RESULTADOS)
        for lugar, clave, funcion in (("entrada", "aceite_entrada", aceite_en_fruta),
                                      ("salida", "aceite_salida", aceite_en_fruta),
                                      ("emulsion", "aceite_emulsion", aceite_en_emulsion)):
            punto = datos.get(lugar, {})
            if _hay(punto.get("g")) and _hay(punto.get("ml")):
                try:
                    r[clave] = funcion(planta, punto["ml"], blanco, normalidad.get(lugar), punto["g"])
                except ValueError:
                    r[clave] = None
        if r["aceite_entrada"] and r["aceite_entrada"] > 0 and r["aceite_salida"] is not None:
            r["eficiencia"] = 100 * (1 - r["aceite_salida"] / r["aceite_entrada"])
            r["rendimiento"] = r["aceite_entrada"] - r["aceite_salida"]
        emulsion, entrada = datos.get("emulsion", {}), datos.get("entrada", {})
        r["brix"] = emulsion.get("brix") if _hay(emulsion.get("brix")) else None
        r["ph"] = emulsion.get("ph") if _hay(emulsion.get("ph")) else None
        if _hay(entrada.get("n_frutos")) and _hay(entrada.get("peso_fruta_kg")) and entrada["peso_fruta_kg"] > 0:
            r["frutos_10kg"] = entrada["n_frutos"] * 10 / entrada["peso_fruta_kg"]
        resultados[numero] = r
    return resultados


def rangos_resultados(sesion: Session, planta_id: int, fruta: str | None) -> dict:
    """{resultado: {"minimo", "maximo", "lado"}} de RangoReferencia para el semáforo (con o sin genera_alerta: el
    semáforo es una referencia). Si hay uno de la fruta (especie) y uno general, gana el de la fruta."""
    rangos = {}
    for clave, (_, _, _, definicion) in RESULTADOS.items():
        if definicion is None:
            continue
        candidatos = sesion.scalars(select(RangoReferencia).where(
            RangoReferencia.planta_id == planta_id, RangoReferencia.variable == definicion[0],
            RangoReferencia.punto_o_equipo == definicion[1])).all()
        de_la_fruta = [r for r in candidatos if fruta and r.fruta and especie(r.fruta) == especie(fruta)]
        elegido = (de_la_fruta or [r for r in candidatos if r.fruta is None] or [None])[0]
        if elegido is not None:
            rangos[clave] = {"minimo": elegido.minimo, "maximo": elegido.maximo, "lado": elegido.lado_capacidad}
    return rangos


def semaforo(valor, rango: dict | None) -> str | None:
    """"verde" (dentro del rango), "rojo" (fuera) o None (sin valor o sin rango). lado "inferior": solo cuenta el
    mínimo; "superior": solo el máximo; "ambos" o vacío: los dos."""
    if valor is None or rango is None:
        return None
    lado = rango.get("lado") or "ambos"
    if lado != "superior" and rango.get("minimo") is not None and valor < rango["minimo"]:
        return "rojo"
    if lado != "inferior" and rango.get("maximo") is not None and valor > rango["maximo"]:
        return "rojo"
    return "verde"


# ---------------------------------------------------------------- validación

def percentiles_historicos(sesion: Session, planta_id: int, minimo: int = N_MINIMO_PERCENTILES) -> dict:
    """{(n.º de eBOE, lugar, campo): (p0,5, p99,5)} del histórico de cada punto, para los campos de
    CAMPOS_HISTORICO ("aceite": el resultado de la titulación). Solo con al menos `minimo` datos."""
    planta = sesion.get(Planta, planta_id)
    filas = []
    for a, numero, lugar in _analisis_planta(sesion, planta_id):
        funcion = aceite_en_emulsion if lugar == "emulsion" else aceite_en_fruta
        try:
            aceite = funcion(planta, a.vol_ml, a.blanco_ml, a.normalidad, a.peso_muestra_g)
        except ValueError:
            aceite = None
        filas.append({"eboe": numero, "lugar": lugar, "g": a.peso_muestra_g, "ml": a.vol_ml,
                      "peso_fruta_kg": a.peso_fruta_kg, "n_frutos": a.n_frutos, "brix": a.brix, "ph": a.ph,
                      "aceite": aceite})
    tabla = pd.DataFrame(filas)
    resultado = {}
    if tabla.empty:
        return resultado
    for (numero, lugar), sub in tabla.groupby(["eboe", "lugar"]):
        for campo in CAMPOS_HISTORICO[lugar]:
            valores = pd.to_numeric(sub[campo], errors="coerce").dropna()
            if len(valores) >= minimo:
                resultado[(numero, lugar, campo)] = tuple(np.percentile(valores, PERCENTILES))
    return resultado


def contexto_validacion(sesion: Session, planta: Planta, ronda: dict, ahora: datetime) -> dict:
    """Lo que validar_ronda() necesita de la base: normalidades previas, percentiles del histórico, puntos y
    operación ya cargados a esa hora, y la hora actual."""
    if ronda.get("fecha_hora") is not None:
        cargados = {(n, l) for a, n, l in _analisis_planta(sesion, planta.id, desde=ronda["fecha_hora"])
                    if a.fecha_hora == ronda["fecha_hora"]}
        operacion = set(sesion.scalars(select(Equipo.numero).join(LecturaOperacion.equipo).join(Equipo.linea).where(
            Linea.planta_id == planta.id, Equipo.tipo == TIPO_EBOE,
            LecturaOperacion.fecha_hora == ronda["fecha_hora"])).all())
    else:
        cargados, operacion = set(), set()
    return {"normalidades_previas": normalidades_previas(sesion, planta.id),
            "percentiles": percentiles_historicos(sesion, planta.id), "cargados": cargados,
            "operacion_cargada": operacion, "ahora": ahora}


def validar_ronda(planta: Planta, ronda: dict, contexto: dict) -> tuple[list[str], list[str]]:
    """(errores, avisos) de la ronda. Los errores no dejan guardar; los avisos sí.

    Errores: falta la fecha y hora, el proceso o el analista; ningún eBOE en marcha; titulación incompleta (g sin mL
    o mL sin g); g ≤ 0; mL menores que el blanco; falta la normalidad de un lugar con titulación; pH fuera de 0–14;
    un punto o la operación de un eBOE ya cargados a esa hora (editar: tarea 19).
    Avisos: entrada con los mismos g y mL que la de otro eBOE ("¿copiaste la entrada?"); salida mayor o igual que la
    entrada; ronda en el futuro; eBOE en marcha sin entrada, salida o emulsión; g < 1 ("muestra muy chica");
    normalidad que difiere de la última en un factor ≥ 5 ("¿normalidad equivocada?"); valor fuera del percentil
    0,5–99,5 del histórico de ese punto ("valor poco habitual, ¿está bien tipeado?").
    """
    errores, avisos = [], []
    blanco = planta.blanco_ml if planta.blanco_ml is not None else 0
    if ronda.get("fecha_hora") is None:
        errores.append("Falta la fecha y hora de la ronda.")
    if not ronda.get("proceso_id"):
        errores.append("Falta el proceso.")
    if not (ronda.get("analista") or "").strip():
        errores.append("Falta el analista.")
    eboes = ronda.get("eboes", {})
    if not eboes:
        errores.append("No hay ningún eBOE en marcha.")
    normalidad = ronda.get("normalidad", {})
    usados = set()
    for numero, datos in eboes.items():
        for lugar in PUNTOS:
            punto = datos.get(lugar, {})
            g, ml = punto.get("g"), punto.get("ml")
            nombre = f"eBOE {numero}, {NOMBRES_LUGAR[lugar]}"
            if not _hay(g) and not _hay(ml):
                if any(_hay(v) for v in punto.values()):
                    errores.append(f"{nombre}: faltan los g y los mL.")
                else:
                    avisos.append(f"{nombre}: sin datos.")
                continue
            if not _hay(g) or not _hay(ml):
                errores.append(f"{nombre}: titulación incompleta (hacen falta los g y los mL).")
                continue
            usados.add(lugar)
            if g <= 0:
                errores.append(f"{nombre}: los g tienen que ser mayores que 0 (dice {g}).")
            elif g < GRAMOS_MINIMOS:
                avisos.append(f"{nombre}: muestra muy chica ({g} g).")
            if ml < blanco:
                errores.append(f"{nombre}: los mL ({ml}) son menores que el blanco ({blanco} mL).")
            if (numero, lugar) in contexto.get("cargados", set()):
                errores.append(f"{nombre}: ya hay un análisis cargado a esa hora (para corregirlo, usá la edición).")
            ph = punto.get("ph")
            if lugar == "emulsion" and _hay(ph) and not 0 <= ph <= 14:
                errores.append(f"{nombre}: el pH tiene que estar entre 0 y 14 (dice {ph}).")
        operacion = datos.get("operacion", {})
        if any(_hay(v) for v in operacion.values()) and numero in contexto.get("operacion_cargada", set()):
            errores.append(f"eBOE {numero}: ya hay una lectura de operación cargada a esa hora.")
    for lugar in usados:
        actual, previa = normalidad.get(lugar), contexto.get("normalidades_previas", {}).get(lugar)
        if not _hay(actual) or actual <= 0:
            errores.append(f"Falta la normalidad de {NOMBRES_LUGAR[lugar]} (tiene que ser mayor que 0).")
        elif previa and max(actual / previa, previa / actual) >= FACTOR_NORMALIDAD:
            avisos.append(f"Normalidad de {NOMBRES_LUGAR[lugar]} {actual} contra {previa} de la última vez: "
                          "¿normalidad equivocada?")

    entradas = pd.DataFrame([{"analisis_id": numero, "fecha_hora": ronda.get("fecha_hora"), "eboe": numero,
                              "peso_muestra_g": d.get("entrada", {}).get("g"), "vol_ml": d.get("entrada", {}).get("ml")}
                             for numero, d in eboes.items()],
                            columns=["analisis_id", "fecha_hora", "eboe", "peso_muestra_g", "vol_ml"])
    repetidas = sorted(entradas_repetidas(entradas.assign(fecha_hora=0)))
    if repetidas:
        avisos.append(f"La entrada de los eBOE {_lista(repetidas)} tiene los mismos g y mL: ¿copiaste la entrada?")
    resultados = calcular_ronda(planta, ronda)
    for numero, r in resultados.items():
        if r["eficiencia"] is not None and r["eficiencia"] <= 0:
            avisos.append(f"eBOE {numero}: la salida tiene tanto o más aceite que la entrada (eficiencia "
                          f"{r['eficiencia']:.1f} %).")
        avisos += _poco_habituales(numero, eboes[numero], r, contexto.get("percentiles", {}))
    ahora = contexto.get("ahora")
    if ahora and ronda.get("fecha_hora") and ronda["fecha_hora"] > ahora + timedelta(minutes=MINUTOS_FUTURO):
        avisos.append(f"La ronda es en el futuro ({ronda['fecha_hora']:%d/%m/%Y %H:%M}).")
    return errores, avisos


def _poco_habituales(numero: int, datos: dict, resultados: dict, percentiles: dict) -> list[str]:
    """Avisos de valores fuera del percentil 0,5–99,5 del histórico de cada punto."""
    avisos = []
    aceite = {"entrada": resultados["aceite_entrada"], "salida": resultados["aceite_salida"],
              "emulsion": resultados["aceite_emulsion"]}
    for lugar, campos in CAMPOS_HISTORICO.items():
        for campo in campos:
            valor = aceite[lugar] if campo == "aceite" else datos.get(lugar, {}).get(campo)
            limites = percentiles.get((numero, lugar, campo))
            if _hay(valor) and limites and not limites[0] <= valor <= limites[1]:
                avisos.append(f"eBOE {numero}, {NOMBRES_LUGAR[lugar]}: {NOMBRES_CAMPO[campo]} {valor:.4g} fuera de lo "
                              f"habitual ({limites[0]:.4g}–{limites[1]:.4g}): valor poco habitual, ¿está bien tipeado?")
    return avisos


def _lista(numeros) -> str:
    textos = [str(n) for n in numeros]
    return textos[0] if len(textos) == 1 else ", ".join(textos[:-1]) + " y " + textos[-1]


# ---------------------------------------------------------------- guardar

def guardar_ronda(sesion: Session, planta: Planta, ronda: dict, cargado_por: str, ahora: datetime) -> dict:
    """Guarda la ronda en una transacción: un AnalisisScott por punto con g y mL (datos crudos, blanco de la planta,
    normalidad del lugar) y una LecturaOperacion por eBOE con algún dato de operación, con cargado_por y
    cargado_en. Después marca las entradas repetidas, hace el chequeo inmediato del turno en curso y evalúa los
    turnos cerrados.

    Da error (ValueError) si validar_ronda() encuentra errores o falta cargado_por. Devuelve {"analisis": [ids],
    "lecturas": [ids], "alertas": cambios_de_alertas(), "turnos_evaluados": [(fecha, turno)]}.
    """
    if not (cargado_por or "").strip():
        raise ValueError("Falta quién carga la ronda (cargado por).")
    errores, _ = validar_ronda(planta, ronda, contexto_validacion(sesion, planta, ronda, ahora))
    if errores:
        raise ValueError("No se puede guardar la ronda: " + " ".join(errores))
    blanco = validar_parametros(planta, ["blanco_ml"])["blanco_ml"]
    puntos = {(p.equipo.numero, lugar): p for lugar, tipo in PUNTOS.items() for p in sesion.scalars(
        select(PuntoMuestreo).join(PuntoMuestreo.equipo).join(Equipo.linea).where(
            Linea.planta_id == planta.id, Equipo.tipo == TIPO_EBOE, PuntoMuestreo.tipo == tipo))}
    equipos = {e.numero: e for e in sesion.scalars(select(Equipo).join(Linea).where(
        Linea.planta_id == planta.id, Equipo.tipo == TIPO_EBOE))}
    nuevos, lecturas = [], []
    for numero, datos in ronda["eboes"].items():
        for lugar in PUNTOS:
            punto = datos.get(lugar, {})
            if not (_hay(punto.get("g")) and _hay(punto.get("ml"))):
                continue
            if (numero, lugar) not in puntos:
                raise ValueError(f"El eBOE {numero} no tiene punto de muestreo de {NOMBRES_LUGAR[lugar]}.")
            extra = {k: punto.get(k) for k in {"entrada": ("peso_fruta_kg", "n_frutos", "calidad"),
                                               "salida": ("rallado",), "emulsion": ("brix", "ph")}[lugar]
                     if _hay(punto.get(k))}
            nuevos.append(AnalisisScott(
                punto_muestreo=puntos[(numero, lugar)], proceso_id=ronda["proceso_id"], fecha_hora=ronda["fecha_hora"],
                analista=ronda["analista"].strip(), fruta=ronda.get("fruta"), peso_muestra_g=punto["g"],
                vol_ml=punto["ml"], blanco_ml=blanco, normalidad=ronda["normalidad"][lugar], cargado_por=cargado_por,
                cargado_en=ahora, **extra))
        operacion = {k: v for k, v in datos.get("operacion", {}).items() if _hay(v)}
        if operacion:
            lecturas.append(LecturaOperacion(equipo=equipos[numero], fecha_hora=ronda["fecha_hora"],
                                             cargado_por=cargado_por, cargado_en=ahora, **operacion))
    sesion.add_all(nuevos + lecturas)
    sesion.commit()
    marcar_entradas_repetidas(sesion, planta.id)

    antes = estado_alertas(sesion)
    chequeo_inmediato(sesion, planta, ronda["fecha_hora"], cargado_por)
    evaluados = evaluar_turnos_cerrados(sesion, planta, ahora, cargado_por)
    return {"analisis": [a.id for a in nuevos], "lecturas": [l.id for l in lecturas],
            "alertas": cambios_de_alertas(sesion, antes), "turnos_evaluados": evaluados}


# ---------------------------------------------------------------- alertas

def series_recientes(sesion: Session, planta: Planta, desde: datetime) -> dict:
    """Series de los eBOE (core.limites.series_boe) con los análisis desde esa fecha y hora (sin PLC: rápido)."""
    eboes = {e.numero: (e.id, e.nombre) for e in sesion.scalars(select(Equipo).join(Linea).where(
        Linea.planta_id == planta.id, Equipo.tipo == TIPO_EBOE))}
    return series_boe(tabla_analisis(sesion, planta.id, desde=desde, con_plc=False),
                      tabla_emulsion(sesion, planta.id, desde=desde, con_plc=False), eboes, planta.id)


def chequeo_inmediato(sesion: Session, planta: Planta, fecha_hora: datetime, creado_por: str) -> dict:
    """Regla 1 más allá de ±sigma_alerta_inmediata σ (de los límites vigentes, del lado malo) sobre el promedio del
    turno en curso de cada ámbito, solo si ese turno ya tiene RONDAS_INMEDIATO rondas o más. Crea la alerta
    "abierta" (inmediata) o le suma los datos si ya existe. Solo variables con la regla 1 en reglas_alerta.
    Devuelve el resumen de guardar_evaluados() de todas las variables."""
    parametros = validar_parametros(planta, ["turnos_fin_episodio", "turnos_para_alertar"])
    dia, turno = pd.Timestamp(fecha_produccion(planta, fecha_hora)), turno_de(planta, fecha_hora).nombre
    series = series_recientes(sesion, planta, fecha_hora - timedelta(days=DIAS_PREVIOS))
    resumen = {"creadas": 0, "inmediatas": 0, "turnos": 0, "por_regla": Counter(), "sin_limites": []}
    for variable in VARIABLES_BOE:
        serie = series.get(variable)
        if serie is None or serie.empty:
            continue
        del_turno = serie[(serie["fecha_produccion"] == dia) & (serie["turno"] == turno) & serie["invalido"].isna()]
        evaluados = []
        for ambito, grupo in subgrupos_por_turno(del_turno).groupby("ambito", sort=False):
            rondas = del_turno[del_turno["ambito"] == ambito]["fecha_hora"].nunique()
            equipo_id = None if pd.isna(grupo["equipo_id"].iloc[0]) else int(grupo["equipo_id"].iloc[0])
            planta_id = None if equipo_id is not None else int(grupo["planta_id"].iloc[0])
            fruta = grupo["fruta"].iloc[0]
            limites = limites_vigentes(sesion, variable, fruta, equipo_id, planta_id)
            if (rondas < RONDAS_INMEDIATO or limites is None or limites.tipo_carta not in POR_TURNO
                    or limites.sigma_alerta_inmediata is None or 1 not in leer_reglas(limites.reglas_alerta)[0]):
                continue
            violaciones = [v | {"origen": ORIGEN_NELSON, "limites_id": limites.id} for v in violaciones_por_turno(
                grupo, None, limites.lc, limites.sigma, "1", limites.tipo_carta, limites.sigma_dentro, None,
                VARIABLES_CONTROL[variable]["lado_alerta"], limites.sigma_alerta_inmediata)
                if v["alerta"] and v["inmediata"]]
            if violaciones:
                linea = serie[serie["ambito"] == ambito][["fecha_produccion", "turno", "fecha_hora"]]
                linea = linea.groupby(["fecha_produccion", "turno"], as_index=False)["fecha_hora"].min()
                evaluados.append({"ambito": ambito, "equipo_id": equipo_id, "planta_id": planta_id,
                                  "subgrupos": grupo, "violaciones": violaciones, "linea": linea})
        if evaluados:
            guardar_evaluados(sesion, planta, variable, evaluados, parametros, creado_por=creado_por,
                              comunes=(del_turno["tipo"] == TIPO_EBOE).all(), resumen=resumen)
    return resumen


def turnos_cerrados_sin_evaluar(sesion: Session, planta: Planta, ahora: datetime,
                                dias: int = DIAS_TURNOS_CERRADOS) -> list[tuple]:
    """Turnos de producción (fecha, turno) con análisis de los eBOE en los últimos `dias` días, que ya terminaron
    (fin_de_turno ≤ ahora) y no están en TurnoEvaluado. En orden de tiempo."""
    turnos = {t.nombre: t for t in planta.turnos}
    momentos = sesion.scalars(select(AnalisisScott.fecha_hora).distinct()
                              .join(AnalisisScott.punto_muestreo).join(PuntoMuestreo.equipo).join(Equipo.linea)
                              .where(Linea.planta_id == planta.id, Equipo.tipo == TIPO_EBOE,
                                     AnalisisScott.fecha_hora >= ahora - timedelta(days=dias))).all()
    evaluados = {(e.fecha_produccion, e.turno_id) for e in sesion.scalars(
        select(TurnoEvaluado).where(TurnoEvaluado.planta_id == planta.id))}
    pendientes = {}
    for momento in momentos:
        dia, turno = fecha_produccion(planta, momento), turno_de(planta, momento)
        if (dia, turno.id) not in evaluados and fin_de_turno(planta, dia, turno) <= ahora:
            pendientes.setdefault((dia, turno.nombre), fin_de_turno(planta, dia, turno))
    return [clave for clave, _ in sorted(pendientes.items(), key=lambda par: par[1])]


def evaluar_turnos_cerrados(sesion: Session, planta: Planta, ahora: datetime, evaluado_por: str) -> list[tuple]:
    """Evaluación completa (core.alertas.evaluar_nuevos: reglas_alerta, persistencia y comunes) de cada turno
    cerrado sin evaluar, en orden, y su marca en TurnoEvaluado (no se repite). Las alertas quedan "abiertas".
    Devuelve [(fecha de producción, turno)] evaluados."""
    pendientes = turnos_cerrados_sin_evaluar(sesion, planta, ahora)
    if not pendientes:
        return []
    turnos = {t.nombre: t for t in planta.turnos}
    primero = datetime.combine(pendientes[0][0], datetime.min.time())
    series = series_recientes(sesion, planta, primero - timedelta(days=DIAS_PREVIOS))
    for dia, turno in pendientes:
        for variable, serie in series.items():
            nuevos = serie[(serie["fecha_produccion"] == pd.Timestamp(dia)) & (serie["turno"] == turno)]
            if nuevos.empty:
                continue
            previos = serie[serie["fecha_hora"] < nuevos["fecha_hora"].min()]
            evaluar_nuevos(sesion, planta, variable, nuevos, previos, historico=False, creado_por=evaluado_por)
        sesion.add(TurnoEvaluado(planta_id=planta.id, fecha_produccion=dia, turno_id=turnos[turno].id,
                                 evaluado_en=ahora, evaluado_por=evaluado_por))
        sesion.commit()
    return pendientes


def estado_alertas(sesion: Session) -> dict:
    """{id: (turnos_afectados, fin, cantidad de datos)} de todas las alertas, para ver qué cambió después."""
    datos = dict(sesion.execute(select(Alerta.id, func.count()).join(Alerta.datos).group_by(Alerta.id)).all())
    return {a.id: (a.turnos_afectados, a.fin, datos.get(a.id, 0)) for a in sesion.scalars(select(Alerta))}


def cambios_de_alertas(sesion: Session, antes: dict) -> list[dict]:
    """Alertas nuevas o actualizadas desde `antes` (estado_alertas()): [{alerta, cambio: "nueva"/"actualizada"}]."""
    despues = estado_alertas(sesion)
    cambios = []
    for alerta_id, estado in despues.items():
        if alerta_id not in antes:
            cambios.append({"alerta": sesion.get(Alerta, alerta_id), "cambio": "nueva"})
        elif antes[alerta_id] != estado:
            cambios.append({"alerta": sesion.get(Alerta, alerta_id), "cambio": "actualizada"})
    return sorted(cambios, key=lambda c: c["alerta"].fecha_hora)
