"""Operación de los eBOE según el PLC (LecturaPLC): horas, marcha y paradas, carga y programas.

Las lecturas llegan cada 5 minutos. Cada lectura representa el tiempo hasta la siguiente del mismo
eBOE; si la siguiente tarda más de INTERVALO_MAXIMO (faltan datos), cuenta INTERVALO_NOMINAL.
- Horómetro = horas_maquina + minutos / 60 (h).
- En marcha: elevador = 1. Con fruta: TON calculadas > 0. En marcha sin fruta: elevador = 1 y TON
  calculadas = 0. Parada: lecturas seguidas con elevador = 0.
- Las TON calculadas salen de baldes por minuto × kg por balde: no son un peso real.
Las funciones reciben la planta ya leída (con sus turnos), como core.turnos: no consultan la base.
"""

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.formato import numero
from core.models import Equipo, LecturaPLC, Linea, Planta
from core.parametros import validar_parametros
from core.programa import numero_programa
from core.turnos import turno_de

COLUMNAS_PLC = ["equipo_id", "eboe", "fecha_hora", "horas_maquina", "minutos", "elevador", "programa",
                "ton_seteadas", "ton_calculadas"]
INTERVALO_NOMINAL = pd.Timedelta(minutes=5)
INTERVALO_MAXIMO = pd.Timedelta(minutes=10)
SALTO_MAXIMO_H = 1.0            # el horómetro avanza más de 1 h que el reloj entre dos lecturas: salto
FRACCION_CARGA_BAJA = 0.8       # TON calculadas por debajo del 80 % del setpoint
LECTURAS_MINIMAS_HORA = 3       # lecturas en marcha para medir la variación dentro de una hora
ESPECIES = {"N": "naranja", "L": "limón", "P": "pomelo", "M": "mandarina"}
NOTA_TON = "Las TON calculadas salen de baldes por minuto × kg por balde: no son un peso real."
NOTA_HOROMETRO = "Confirmar si hubo overhaul o cambio de horómetro."


def lecturas_plc(sesion: Session, planta_id: int) -> pd.DataFrame:
    """Lecturas del PLC de los eBOE de la planta, ordenadas por eBOE y fecha y hora (COLUMNAS_PLC)."""
    filas = sesion.execute(
        select(LecturaPLC.equipo_id, Equipo.numero, LecturaPLC.fecha_hora, LecturaPLC.horas_maquina,
               LecturaPLC.minutos, LecturaPLC.elevador, LecturaPLC.programa, LecturaPLC.ton_seteadas,
               LecturaPLC.ton_calculadas)
        .join(Equipo, LecturaPLC.equipo_id == Equipo.id).join(Linea, Equipo.linea_id == Linea.id)
        .where(Linea.planta_id == planta_id)).all()
    tabla = pd.DataFrame(filas, columns=COLUMNAS_PLC)
    tabla["fecha_hora"] = pd.to_datetime(tabla["fecha_hora"])
    return tabla.sort_values(["eboe", "fecha_hora"], ignore_index=True)


def especie(fruta) -> str | None:
    """Especie de la fruta del laboratorio: "N Val S/R" → "naranja" (primera palabra, ESPECIES)."""
    if not isinstance(fruta, str) or not fruta.strip():
        return None
    codigo = fruta.split()[0]
    return ESPECIES.get(codigo.upper(), codigo.lower())


# ---------------------------------------------------------------- preparación

def preparar_plc(plc: pd.DataFrame, planta: Planta) -> pd.DataFrame:
    """Agrega horometro (h), duracion_h, fecha_produccion, turno, marcha, con_fruta y sin_fruta.

    duracion_h: tiempo hasta la lectura siguiente del mismo eBOE (INTERVALO_NOMINAL si falta la
    siguiente o tarda más de INTERVALO_MAXIMO). fecha_produccion y turno según la planta (core.turnos).
    """
    inicio = validar_parametros(planta, ["hora_inicio_dia"])["hora_inicio_dia"]
    datos = plc.sort_values(["eboe", "fecha_hora"], ignore_index=True)
    siguiente = datos.groupby("eboe")["fecha_hora"].shift(-1) - datos["fecha_hora"]
    siguiente = siguiente.where(siguiente <= INTERVALO_MAXIMO, INTERVALO_NOMINAL)
    minuto = datos["fecha_hora"].dt.floor("min").dt.time
    turnos = {hora: turno_de(planta, datetime.combine(date(2000, 1, 1), hora)).nombre for hora in minuto.unique()}
    corrimiento = pd.Timedelta(hours=inicio.hour, minutes=inicio.minute)
    ton = datos["ton_calculadas"]
    return datos.assign(
        horometro=datos["horas_maquina"] + datos["minutos"] / 60,
        duracion_h=siguiente.dt.total_seconds() / 3600,
        fecha_produccion=(datos["fecha_hora"] - corrimiento).dt.normalize(),
        turno=minuto.map(turnos),
        marcha=datos["elevador"] == 1, con_fruta=ton > 0, sin_fruta=(datos["elevador"] == 1) & (ton == 0))


def filtrar_plc(plc: pd.DataFrame, filtros) -> pd.DataFrame:
    """Lecturas dentro de los filtros globales que le aplican: fechas de producción, eBOE, turno y programa.

    El programa del filtro ("N3") se compara con el del PLC. El proceso no aplica (el PLC no lo registra).
    """
    seleccion = pd.Series(True, index=plc.index)
    if filtros.desde is not None:
        seleccion &= plc["fecha_produccion"] >= pd.Timestamp(filtros.desde)
    if filtros.hasta is not None:
        seleccion &= plc["fecha_produccion"] <= pd.Timestamp(filtros.hasta)
    if filtros.eboes:
        seleccion &= plc["eboe"].isin(filtros.eboes)
    if filtros.turnos:
        seleccion &= plc["turno"].isin(filtros.turnos)
    if filtros.programas:
        seleccion &= plc["programa"].isin(filtros.programas)
    return plc[seleccion].reset_index(drop=True)


# ---------------------------------------------------------------- horas de operación

def ventana_temporada(analisis: pd.DataFrame, especie_elegida: str, temporada, hora_inicio_dia: time):
    """(desde, hasta) de una fruta y temporada según el laboratorio, o None si no hay análisis.

    desde = fecha y hora del primer análisis; hasta = fin del día de producción del último análisis
    (hora_inicio_dia del día siguiente a su fecha de producción). analisis: core.analisis.tabla_analisis().
    """
    sub = analisis[(analisis["fruta"].map(especie) == especie_elegida) & (analisis["temporada"] == temporada)]
    if sub.empty:
        return None
    ultimo = sub["fecha_produccion"].max()
    return sub["fecha_hora"].min(), pd.Timestamp(datetime.combine(ultimo.date() + timedelta(days=1), hora_inicio_dia))


def horas_operacion(plc: pd.DataFrame, desde, hasta) -> pd.DataFrame:
    """Horas de operación de cada eBOE dentro de la ventana de la temporada.

    plc: preparar_plc(). Por eBOE: inicio = primera lectura en o después de `desde` en la que cambia el
    horómetro (contador_inicio = el valor de la lectura anterior, antes de arrancar); fin = última lectura
    antes de `hasta` (contador_fin). horas = contador_fin − contador_inicio. suma_incrementos = suma de los
    aumentos del horómetro entre lecturas seguidas (control), sin reseteos (el contador baja) ni saltos (avanza
    más de SALTO_MAXIMO_H por encima del tiempo transcurrido). horas_periodo = hasta − inicio; utilizacion =
    horas / horas_periodo × 100. horas_elevador (elevador = 1), horas_con_fruta (TON calculadas > 0) y
    horas_sin_fruta (elevador = 1 y TON calculadas = 0), sumando la duración de cada lectura.
    """
    filas = []
    desde, hasta = pd.Timestamp(desde), pd.Timestamp(hasta)
    for eboe, datos in plc.groupby("eboe", sort=True):
        datos = datos.sort_values("fecha_hora")
        cambio = datos["horometro"].diff().fillna(0) != 0
        arranques = datos[(datos["fecha_hora"] >= desde) & (datos["fecha_hora"] < hasta) & cambio]
        if arranques.empty:
            continue
        posicion = datos.index.get_loc(arranques.index[0])
        inicio = datos["fecha_hora"].iloc[posicion]
        contador_inicio = datos["horometro"].iloc[posicion - 1] if posicion > 0 else datos["horometro"].iloc[posicion]
        ventana = datos[(datos["fecha_hora"] >= inicio) & (datos["fecha_hora"] < hasta)]
        fin = ventana.iloc[-1]
        aumentos = pd.concat([pd.Series([contador_inicio]), ventana["horometro"]], ignore_index=True).diff().iloc[1:]
        transcurrido = pd.concat([pd.Series([inicio]), ventana["fecha_hora"]], ignore_index=True).diff().iloc[1:]
        transcurrido_h = transcurrido.dt.total_seconds().to_numpy() / 3600
        reseteo = aumentos.to_numpy() < 0
        salto = aumentos.to_numpy() > transcurrido_h + SALTO_MAXIMO_H
        horas_periodo = (hasta - inicio).total_seconds() / 3600
        horas = float(fin["horometro"] - contador_inicio)
        filas.append({
            "eboe": eboe, "inicio": inicio, "fin": fin["fecha_hora"], "contador_inicio": float(contador_inicio),
            "contador_fin": float(fin["horometro"]), "horas": horas,
            "suma_incrementos": float(aumentos.to_numpy()[~reseteo & ~salto].sum()),
            "reseteos": int(reseteo.sum()), "saltos": int(salto.sum()), "horas_periodo": horas_periodo,
            "utilizacion": 100 * horas / horas_periodo if horas_periodo > 0 else None,
            "horas_elevador": float(ventana.loc[ventana["marcha"], "duracion_h"].sum()),
            "horas_con_fruta": float(ventana.loc[ventana["con_fruta"], "duracion_h"].sum()),
            "horas_sin_fruta": float(ventana.loc[ventana["sin_fruta"], "duracion_h"].sum())})
    return pd.DataFrame(filas, columns=["eboe", "inicio", "fin", "contador_inicio", "contador_fin", "horas",
                                        "suma_incrementos", "reseteos", "saltos", "horas_periodo", "utilizacion",
                                        "horas_elevador", "horas_con_fruta", "horas_sin_fruta"])


def texto_horas(horas: float) -> str:
    """31549.4833 → "31.549 h 29 min"."""
    enteras = int(np.floor(horas + 1e-9))
    return f"{numero(enteras, 0)} h {int(round((horas - enteras) * 60)) % 60:02d} min"


def horometro(plc: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(acumulado, diario) del horómetro de cada eBOE.

    acumulado: eboe, fecha_hora y horas de la última lectura. diario: eboe, fecha_produccion y horas
    (última lectura de cada día de producción), para ver la evolución.
    """
    ultimo = plc.sort_values("fecha_hora").groupby("eboe").tail(1)
    acumulado = ultimo[["eboe", "fecha_hora", "horometro"]].rename(columns={"horometro": "horas"})
    diario = (plc.sort_values("fecha_hora").groupby(["eboe", "fecha_produccion"])["horometro"].last()
              .reset_index().rename(columns={"horometro": "horas"}))
    return acumulado.sort_values("eboe", ignore_index=True), diario


def frase_horas(tabla: pd.DataFrame, especie_elegida: str, temporada) -> str:
    """Ejemplo: "En naranja 2026 los tres eBOE trabajaron entre 2.382 y 2.408 h (85 % del período). El eBOE 3
    acumula 14.976 h contra más de 31.000 de los otros dos."

    tabla: horas_operacion(). Lo acumulado es el horómetro al final de la ventana; se menciona si un eBOE tiene
    menos del 80 % de las horas del que menos tiene entre los otros.
    """
    if tabla.empty:
        return f"No hay lecturas del PLC en la ventana de {especie_elegida} {temporada}."
    cuantos = {2: "los dos eBOE", 3: "los tres eBOE"}.get(len(tabla), f"los {len(tabla)} eBOE")
    utilizacion = tabla["utilizacion"]
    if len(tabla) == 1:
        texto = (f"En {especie_elegida} {temporada} el eBOE {int(tabla['eboe'].iloc[0])} trabajó "
                 f"{numero(tabla['horas'].iloc[0], 0)} h ({numero(utilizacion.iloc[0], 0)} % del período).")
    else:
        rango = (f"{numero(utilizacion.mean(), 0)} %" if utilizacion.max() - utilizacion.min() < 2
                 else f"{numero(utilizacion.min(), 0)}–{numero(utilizacion.max(), 0)} %")
        texto = (f"En {especie_elegida} {temporada} {cuantos} trabajaron entre {numero(tabla['horas'].min(), 0)} y "
                 f"{numero(tabla['horas'].max(), 0)} h ({rango} del período).")
    if len(tabla) >= 2:
        menor = tabla.loc[tabla["contador_fin"].idxmin()]
        otros = tabla[tabla["eboe"] != menor["eboe"]]["contador_fin"]
        if menor["contador_fin"] < 0.8 * otros.min():
            redondeo = np.floor(otros.min() / 1000) * 1000
            de_quien = "del otro" if len(otros) == 1 else "de los otros dos" if len(otros) == 2 else "de los otros"
            texto += (f" El eBOE {int(menor['eboe'])} acumula {numero(menor['contador_fin'], 0)} h contra más de "
                      f"{numero(redondeo, 0)} {de_quien}.")
    avisos = tabla[(tabla["reseteos"] > 0) | (tabla["saltos"] > 0)]
    if not avisos.empty:
        texto += (" Ojo: el horómetro tiene reseteos o saltos en el eBOE "
                  + ", ".join(str(int(e)) for e in avisos["eboe"]) + " (ver la suma de incrementos).")
    return texto


# ---------------------------------------------------------------- marcha y paradas

def marcha_y_paradas(plc: pd.DataFrame) -> pd.DataFrame:
    """% del tiempo en marcha, paradas por día y duración de las paradas, por eBOE.

    Parada = lecturas seguidas con elevador = 0 (un hueco de datos mayor que INTERVALO_MAXIMO la corta).
    Columnas: eboe, dias (días de producción con datos), horas (registradas), horas_marcha, pct_marcha,
    paradas, paradas_dia, duracion_media_h y duracion_mediana_h.
    """
    filas = []
    for eboe, datos in plc.groupby("eboe", sort=True):
        datos = datos.sort_values("fecha_hora")
        hueco = datos["fecha_hora"].diff() > INTERVALO_MAXIMO
        tramo = ((datos["marcha"] != datos["marcha"].shift()) | hueco).cumsum()
        tramos = datos.groupby(tramo).agg(marcha=("marcha", "first"), horas=("duracion_h", "sum"))
        paradas = tramos.loc[~tramos["marcha"], "horas"]
        dias = datos["fecha_produccion"].nunique()
        horas = float(datos["duracion_h"].sum())
        marcha = float(datos.loc[datos["marcha"], "duracion_h"].sum())
        filas.append({"eboe": eboe, "dias": dias, "horas": horas, "horas_marcha": marcha,
                      "pct_marcha": 100 * marcha / horas if horas else None, "paradas": len(paradas),
                      "paradas_dia": len(paradas) / dias if dias else None,
                      "duracion_media_h": float(paradas.mean()) if len(paradas) else None,
                      "duracion_mediana_h": float(paradas.median()) if len(paradas) else None})
    return pd.DataFrame(filas, columns=["eboe", "dias", "horas", "horas_marcha", "pct_marcha", "paradas",
                                        "paradas_dia", "duracion_media_h", "duracion_mediana_h"])


def _rango(serie: pd.Series, decimales: int) -> str:
    """"86" si todos redondean igual; si no, "86–87"."""
    menor, mayor = numero(serie.min(), decimales), numero(serie.max(), decimales)
    return menor if menor == mayor else f"{menor}–{mayor}"


def frase_marcha(tabla: pd.DataFrame) -> str:
    """Ejemplo: "Los eBOE estuvieron en marcha el 86–87 % del tiempo registrado, con 1,2–1,5 paradas por día
    de 2,1–2,7 h en promedio (mediana 0,3–1,0 h)."
    """
    tabla = tabla.dropna(subset=["pct_marcha"])
    if tabla.empty:
        return "No hay lecturas del PLC con estos filtros."
    sujeto = f"El eBOE {int(tabla['eboe'].iloc[0])} estuvo" if len(tabla) == 1 else "Los eBOE estuvieron"
    texto = f"{sujeto} en marcha el {_rango(tabla['pct_marcha'], 0)} % del tiempo registrado"
    paradas = tabla.dropna(subset=["duracion_media_h"])
    if paradas.empty:
        return texto + ", sin paradas."
    return (texto + f", con {_rango(paradas['paradas_dia'], 1)} paradas por día de {_rango(paradas['duracion_media_h'], 1)} h "
            f"en promedio (mediana {_rango(paradas['duracion_mediana_h'], 1)} h).")


# ---------------------------------------------------------------- carga

def carga(plc: pd.DataFrame, fraccion: float = FRACCION_CARGA_BAJA) -> pd.DataFrame:
    """TON seteadas vs calculadas por eBOE, con el elevador en marcha y setpoint > 0.

    Promedios ponderados por la duración de cada lectura. relacion = calculadas / seteadas (promedios);
    pct_bajo = % del tiempo con calculadas < fraccion × seteadas; variacion_hora = desvío estándar de las
    calculadas dentro de cada hora de reloj (con al menos LECTURAS_MINIMAS_HORA lecturas), promediado;
    cv_hora = variacion_hora / calculadas promedio × 100. Columnas: eboe, horas, seteadas, calculadas,
    relacion, pct_bajo, variacion_hora y cv_hora.
    """
    filas = []
    datos = plc[plc["marcha"] & (plc["ton_seteadas"] > 0)]
    for eboe, sub in datos.groupby("eboe", sort=True):
        peso = sub["duracion_h"]
        seteadas = float(np.average(sub["ton_seteadas"], weights=peso))
        calculadas = float(np.average(sub["ton_calculadas"], weights=peso))
        bajo = sub["ton_calculadas"] < fraccion * sub["ton_seteadas"]
        por_hora = sub.groupby(sub["fecha_hora"].dt.floor("h"))["ton_calculadas"].agg(["std", "size"])
        variacion = por_hora.loc[por_hora["size"] >= LECTURAS_MINIMAS_HORA, "std"].mean()
        filas.append({"eboe": eboe, "horas": float(peso.sum()), "seteadas": seteadas, "calculadas": calculadas,
                      "relacion": calculadas / seteadas, "pct_bajo": 100 * float(peso[bajo].sum() / peso.sum()),
                      "variacion_hora": float(variacion), "cv_hora": 100 * float(variacion) / calculadas})
    return pd.DataFrame(filas, columns=["eboe", "horas", "seteadas", "calculadas", "relacion", "pct_bajo",
                                        "variacion_hora", "cv_hora"])


def frase_carga(tabla: pd.DataFrame) -> str:
    """Ejemplo: "Con fruta, las TON calculadas son el 92–97 % del setpoint; el eBOE 2 pasa el 18 % del tiempo
    por debajo del 80 % del setpoint."
    """
    if tabla.empty:
        return "No hay lecturas en marcha con setpoint para comparar la carga."
    relacion = tabla["relacion"] * 100
    rango = (f"el {numero(relacion.iloc[0], 0)} %" if len(tabla) == 1
             else f"el {numero(relacion.min(), 0)}–{numero(relacion.max(), 0)} %")
    peor = tabla.loc[tabla["pct_bajo"].idxmax()]
    return (f"En marcha, las TON calculadas son {rango} del setpoint; el eBOE {int(peor['eboe'])} pasa el "
            f"{numero(peor['pct_bajo'], 0)} % del tiempo por debajo del {numero(FRACCION_CARGA_BAJA * 100, 0)} % del "
            "setpoint.")


# ---------------------------------------------------------------- programas

def _orden_programa(programa) -> tuple:
    """N0…N9 primero, después los numéricos ("8", "10") y al final el resto."""
    n = numero_programa(programa)
    if n is not None:
        return (0, n, "")
    if isinstance(programa, str) and programa.isdigit():
        return (1, int(programa), "")
    return (2, 0, str(programa))


def uso_programas_plc(plc: pd.DataFrame) -> pd.DataFrame:
    """Horas en marcha (elevador = 1) con cada programa del PLC, por eBOE, y su % del tiempo en marcha del eBOE.

    Columnas: eboe, programa (tal como lo registra el PLC), horas y pct. Ordenado N0–N9 y después los
    numéricos.
    """
    marcha = plc[plc["marcha"]].assign(programa=lambda d: d["programa"].fillna("sin dato"))
    tabla = marcha.groupby(["eboe", "programa"])["duracion_h"].sum().rename("horas").reset_index()
    tabla["pct"] = 100 * tabla["horas"] / tabla.groupby("eboe")["horas"].transform("sum")
    orden = tabla["programa"].map(_orden_programa)
    return tabla.assign(_orden=orden).sort_values(["eboe", "_orden"]).drop(columns="_orden").reset_index(drop=True)


# ---------------------------------------------------------------- laboratorio vs PLC

def laboratorio_vs_plc(analisis: pd.DataFrame, tolerancia_mtph: float = 0.5) -> dict:
    """Programa y MTPH anotados en el laboratorio contra la lectura del PLC más cercana.

    analisis: core.analisis.tabla_analisis() (se usan las muestras de entrada, una por momento y eBOE).
    Devuelve n (muestras con programa en los dos), pct_programa (% con el mismo programa), cruzada (tabla
    laboratorio × PLC), n_mtph, dif_set y dif_calc (Ton/H del laboratorio − TON seteadas / calculadas del PLC,
    promedio; las calculadas solo cuando son > 0), pct_set y pct_calc (% con diferencia ≤ tolerancia).
    """
    entradas = analisis[analisis["lugar"] == "Entrada"]
    programas = entradas.dropna(subset=["programa", "programa_plc"])
    cruzada = pd.crosstab(programas["programa"], programas["programa_plc"])
    cruzada = cruzada.reindex(index=sorted(cruzada.index, key=_orden_programa),
                              columns=sorted(cruzada.columns, key=_orden_programa))
    mtph = entradas.dropna(subset=["tph", "ton_set_plc"])
    calc = entradas.dropna(subset=["tph", "ton_calc_plc"])
    calc = calc[calc["ton_calc_plc"] > 0]
    return {
        "n": len(programas),
        "pct_programa": 100 * float((programas["programa"] == programas["programa_plc"]).mean()) if len(programas) else None,
        "cruzada": cruzada, "n_mtph": len(mtph),
        "dif_set": float((mtph["tph"] - mtph["ton_set_plc"]).mean()) if len(mtph) else None,
        "dif_calc": float((calc["tph"] - calc["ton_calc_plc"]).mean()) if len(calc) else None,
        "pct_set": 100 * float(((mtph["tph"] - mtph["ton_set_plc"]).abs() <= tolerancia_mtph).mean()) if len(mtph) else None,
        "pct_calc": 100 * float(((calc["tph"] - calc["ton_calc_plc"]).abs() <= tolerancia_mtph).mean()) if len(calc) else None,
    }


def frase_lab_plc(resultado: dict) -> str:
    """Ejemplo: "La Ton/H del laboratorio es el setpoint (coincide en el 74 %); el programa anotado coincide con
    el del PLC en el 76 %."
    """
    if not resultado["n"] or resultado["pct_set"] is None:
        return "No hay muestras con lectura del PLC para comparar."
    if resultado["pct_calc"] is None or resultado["pct_set"] >= resultado["pct_calc"]:
        mtph = f"La Ton/H del laboratorio es el setpoint (coincide en el {numero(resultado['pct_set'], 0)} %)"
    else:
        mtph = (f"La Ton/H del laboratorio se parece más a las TON calculadas (coincide en el "
                f"{numero(resultado['pct_calc'], 0)} %) que al setpoint ({numero(resultado['pct_set'], 0)} %)")
    return f"{mtph}; el programa anotado coincide con el del PLC en el {numero(resultado['pct_programa'], 0)} %."
