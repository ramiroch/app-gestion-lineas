"""Página Operación (PLC): cómo trabajaron los eBOE según su PLC (una lectura cada 5 minutos).

Usa la barra de filtros global (fechas de producción, eBOE, turno y programa; el proceso no aplica porque
el PLC no lo registra). Arriba, un resumen; debajo, una pestaña por bloque: horas de operación (con su
propia ventana de fruta y temporada), marcha y paradas, carga, programas y laboratorio vs PLC. Solo se
dibuja la pestaña abierta; cada gráfico tiene botón PNG, cada tabla Excel y cada pestaña "Descargar todo".
Solo muestra: la lógica está en core/operacion.py.
"""

from dataclasses import replace

import pandas as pd
import streamlit as st

from core.analisis import filtrar
from core.formato import numero
from core.operacion import (FRACCION_CARGA_BAJA, NOTA_HOROMETRO, NOTA_TON, carga, especie, filtrar_plc, frase_carga,
                            frase_horas, frase_lab_plc, frase_marcha, horas_operacion, horometro, laboratorio_vs_plc,
                            marcha_y_paradas, texto_horas, uso_programas_plc, ventana_temporada)
from descargas import Descargas
from filtros import barra_filtros, filtros_elegidos, hora_inicio_planta, planta_elegida, plc_planta, tabla_planta
from graficos import columnas_agrupadas, lineas_por_eboe


def con_coma(df: pd.DataFrame, decimales: int = 1, **por_columna):
    estilo = df.style.format(precision=decimales, decimal=",", thousands=".", na_rep="—")
    for columna, d in por_columna.items():
        estilo = estilo.format(precision=d, decimal=",", thousands=".", na_rep="—", subset=[columna])
    return estilo


def texto_eboe(serie: pd.Series) -> pd.Series:
    return serie.map(lambda e: f"eBOE {e}")


# ---------------------------------------------------------------- pestañas

def pestana_horas(registro: Descargas) -> None:
    st.caption("Ventana de la temporada: desde la primera variación del horómetro en o después del primer análisis de "
               "laboratorio de esa fruta y temporada, hasta el fin del día de producción del último análisis. Esta "
               "pestaña no usa las fechas ni el turno de la barra (sí el eBOE).")
    especies = sorted({e for e in analisis_planta["fruta"].map(especie).dropna()})
    if not especies:
        st.info("No hay análisis de laboratorio con fruta cargada.")
        return
    izquierda, derecha = st.columns(2)
    fruta = izquierda.selectbox("Fruta", especies, key="operacion_fruta")
    temporadas = sorted(analisis_planta.loc[analisis_planta["fruta"].map(especie) == fruta, "temporada"].unique(),
                        reverse=True)
    temporada = derecha.selectbox("Temporada", temporadas, key="operacion_temporada")
    ventana = ventana_temporada(analisis_planta, fruta, temporada, hora_inicio_planta(planta_id))
    datos = plc[plc["eboe"].isin(filtros.eboes)] if filtros.eboes else plc
    tabla = horas_operacion(datos, *ventana)
    st.markdown(f"**{frase_horas(tabla, fruta, temporada)}**")
    if tabla.empty:
        return
    st.caption(f"Primer análisis: {ventana[0]:%d/%m/%Y %H:%M} · fin del último día de producción: "
               f"{ventana[1]:%d/%m/%Y %H:%M}.")
    con_avisos = tabla[(tabla["reseteos"] > 0) | (tabla["saltos"] > 0)]
    for fila in con_avisos.to_dict("records"):
        st.warning(f"eBOE {fila['eboe']}: el horómetro tiene {fila['reseteos']} reseteos (el contador baja) y "
                   f"{fila['saltos']} saltos de más de 1 h. La diferencia del contador no sirve: usá la suma de "
                   f"incrementos ({numero(fila['suma_incrementos'], 0)} h).")

    largo = tabla.melt(id_vars="eboe", value_vars=["horas", "horas_elevador", "horas_con_fruta", "horas_sin_fruta"],
                       var_name="medida", value_name="valor").assign(eboe=lambda d: texto_eboe(d["eboe"]), n=99)
    registro.grafico(columnas_agrupadas(largo, "eboe", "medida", "valor", "Horas", "h", 0, nombres_grupo={
        "horas": "Horómetro", "horas_elevador": "Elevador en marcha", "horas_con_fruta": "Con TON calculadas > 0",
        "horas_sin_fruta": "En marcha sin fruta"}), f"Horas de operación {fruta} {temporada}")
    registro.tabla(con_coma(pd.DataFrame({
        "eBOE": texto_eboe(tabla["eboe"]), "Inicio": tabla["inicio"].dt.strftime("%d/%m/%Y %H:%M"),
        "Fin": tabla["fin"].dt.strftime("%d/%m/%Y %H:%M"), "Contador al inicio": tabla["contador_inicio"].map(texto_horas),
        "Contador al final": tabla["contador_fin"].map(texto_horas), "Horas (contador)": tabla["horas"],
        "Suma de incrementos (h)": tabla["suma_incrementos"], "Reseteos": tabla["reseteos"], "Saltos": tabla["saltos"],
        "Horas del período": tabla["horas_periodo"], "Utilización (%)": tabla["utilizacion"],
        "Elevador en marcha (h)": tabla["horas_elevador"], "Con fruta (h)": tabla["horas_con_fruta"],
        "En marcha sin fruta (h)": tabla["horas_sin_fruta"]}), 1), f"Horas de operación {fruta} {temporada}")
    st.caption("Horas = diferencia del horómetro entre el inicio y el final; la suma de incrementos es el control (sin "
               "reseteos ni saltos). Utilización = horas / horas del período (desde el inicio de la ventana). Elevador, "
               "con fruta y sin fruta suman la duración de cada lectura de 5 min.")

    st.subheader("Horómetro")
    acumulado, diario = horometro(datos)
    for columna, fila in zip(st.columns(len(acumulado)), acumulado.to_dict("records")):
        columna.metric(f"eBOE {fila['eboe']}", texto_horas(fila["horas"]),
                       help=f"Última lectura: {fila['fecha_hora']:%d/%m/%Y %H:%M}")
    registro.grafico(lineas_por_eboe(diario, "fecha_produccion", "horas", "Horómetro (h)", "h", 0,
                                     "Día de producción"), "Horómetro por día")
    st.info(NOTA_HOROMETRO)


def pestana_marcha(registro: Descargas) -> None:
    st.caption("Respeta las fechas, el eBOE, el turno y el programa de la barra. Marcha = elevador en 1; parada = "
               "lecturas seguidas con el elevador en 0 (incluye las paradas largas entre procesos).")
    tabla = marcha_y_paradas(plc_filtrado)
    st.markdown(f"**{frase_marcha(tabla)}**")
    if tabla.empty:
        return
    vista = tabla.assign(eboe=texto_eboe(tabla["eboe"]), n=tabla["dias"])
    izquierda, derecha = st.columns(2)
    registro.grafico(columnas_agrupadas(vista.assign(grupo="Marcha"), "eboe", "grupo", "pct_marcha",
                                        "Tiempo en marcha (%)", "%", 1), "Tiempo en marcha", donde=izquierda)
    registro.grafico(columnas_agrupadas(vista.assign(grupo="Paradas"), "eboe", "grupo", "paradas_dia",
                                        "Paradas por día", "paradas", 1), "Paradas por día", donde=derecha)
    registro.tabla(con_coma(pd.DataFrame({
        "eBOE": vista["eboe"], "Días": tabla["dias"], "Horas registradas": tabla["horas"],
        "Horas en marcha": tabla["horas_marcha"], "En marcha (%)": tabla["pct_marcha"], "Paradas": tabla["paradas"],
        "Paradas por día": tabla["paradas_dia"], "Duración media (h)": tabla["duracion_media_h"],
        "Duración mediana (h)": tabla["duracion_mediana_h"]}), 1, **{"Días": 0, "Paradas": 0}), "Marcha y paradas")


def pestana_carga(registro: Descargas) -> None:
    st.caption(f"Con el elevador en marcha y setpoint > 0; promedios ponderados por la duración de cada lectura. "
               f"Respeta los filtros de la barra. Carga baja: TON calculadas < {numero(FRACCION_CARGA_BAJA * 100, 0)} % "
               "del setpoint. Variación dentro de la hora: desvío estándar de las TON calculadas en cada hora de reloj, "
               "promediado.")
    tabla = carga(plc_filtrado)
    st.markdown(f"**{frase_carga(tabla)}**")
    st.info(NOTA_TON)
    if tabla.empty:
        return
    largo = tabla.melt(id_vars="eboe", value_vars=["seteadas", "calculadas"], var_name="medida", value_name="valor")
    largo = largo.assign(eboe=texto_eboe(largo["eboe"]), n=99)
    registro.grafico(columnas_agrupadas(largo, "eboe", "medida", "valor", "Toneladas por hora (MTPH)", "MTPH", 1,
                                        nombres_grupo={"seteadas": "TON seteadas", "calculadas": "TON calculadas"}),
                     "TON seteadas y calculadas")
    registro.tabla(con_coma(pd.DataFrame({
        "eBOE": texto_eboe(tabla["eboe"]), "Horas en marcha": tabla["horas"], "TON seteadas": tabla["seteadas"],
        "TON calculadas": tabla["calculadas"], "Calculadas / seteadas (%)": tabla["relacion"] * 100,
        f"Tiempo < {numero(FRACCION_CARGA_BAJA * 100, 0)} % del setpoint (%)": tabla["pct_bajo"],
        "Variación dentro de la hora (MTPH)": tabla["variacion_hora"], "Variación dentro de la hora (%)": tabla["cv_hora"]}),
        1, **{"Horas en marcha": 0}), "Carga")


def pestana_programas(registro: Descargas) -> None:
    st.caption("Uso de cada programa por tiempo de marcha (horas con el elevador en 1), no por muestras. El programa "
               "va tal como lo registra el PLC: \"N3\" o numérico (\"9\"). Respeta los filtros de la barra.")
    usos = uso_programas_plc(plc_filtrado)
    if usos.empty:
        st.info("No hay lecturas en marcha con estos filtros.")
        return
    numericos = usos[~usos["programa"].str.startswith("N")]
    if not numericos.empty:
        st.caption(f"Programas numéricos: {numero(numericos['horas'].sum() / usos['horas'].sum() * 100, 1)} % del "
                   "tiempo en marcha. Son otros programas (los de limón y pomelo); en naranja aparecen en pruebas. No "
                   "equivalen a los N0–N9 (por lo general menos agresivos): se muestran aparte y no se mezclan.")
    vista = usos.assign(eboe=texto_eboe(usos["eboe"]), n=99)
    registro.grafico(columnas_agrupadas(vista, "programa", "eboe", "pct", "% del tiempo en marcha", "%", 1,
                                        titulo_x="Programa (PLC)", etiquetas=False), "Uso de programas por tiempo")
    with st.expander("Tabla de uso"):
        registro.tabla(con_coma(pd.DataFrame({"eBOE": vista["eboe"], "Programa": usos["programa"],
                                              "Horas en marcha": usos["horas"], "% del tiempo en marcha": usos["pct"]}),
                                1), "Uso de programas")


def pestana_laboratorio(registro: Descargas) -> None:
    st.caption("Cada muestra de entrada del laboratorio contra la lectura del PLC más cercana del mismo eBOE (± 10 min). "
               "Respeta todos los filtros de la barra. Coincide la MTPH si la diferencia es de 0,5 t/h o menos.")
    resultado = laboratorio_vs_plc(datos)
    st.markdown(f"**{frase_lab_plc(resultado)}**")
    if not resultado["n"]:
        return
    uno, dos, tres, cuatro = st.columns(4)
    uno.metric("Mismo programa", f"{numero(resultado['pct_programa'], 0)} %", help=f"N = {resultado['n']} muestras")
    dos.metric("Ton/H = setpoint", f"{numero(resultado['pct_set'], 0)} %", help=f"N = {resultado['n_mtph']} muestras")
    tres.metric("Lab − seteadas", f"{numero(resultado['dif_set'], 2)} MTPH")
    cuatro.metric("Lab − calculadas", f"{numero(resultado['dif_calc'], 2)} MTPH",
                  help="Solo las lecturas con TON calculadas > 0")
    st.markdown("**Programa: laboratorio (filas) × PLC (columnas)**")
    cruzada = resultado["cruzada"]
    registro.tabla(cruzada.rename_axis(index="Laboratorio", columns="PLC").reset_index(), "Programa laboratorio vs PLC")


# ---------------------------------------------------------------- página

st.set_page_config(page_title="Operación (PLC)", layout="wide")
st.title("Operación (PLC)")

datos = barra_filtros()
filtros = filtros_elegidos()
if filtros is None:             # la planta no tiene análisis: la barra ya lo avisó
    st.stop()
planta_id = planta_elegida()
plc = plc_planta(planta_id)
if plc.empty:
    st.info("No hay datos del PLC de esta planta. Cargalos con: python -m core.importar_plc")
    st.stop()
analisis_planta = filtrar(tabla_planta(planta_id), replace(filtros, desde=None, hasta=None, turnos=[], procesos=[],
                                                           programas=[]))
plc_filtrado = filtrar_plc(plc, filtros)
st.caption(f"Lecturas del PLC del {plc['fecha_hora'].min():%d/%m/%Y} al {plc['fecha_hora'].max():%d/%m/%Y}; con los "
           f"filtros: {numero(len(plc_filtrado), 0)} lecturas. El filtro de proceso no aplica al PLC.")

st.subheader("Resumen")
temporada_reciente = analisis_planta["temporada"].max()
especie_reciente = analisis_planta.loc[analisis_planta["temporada"] == temporada_reciente, "fruta"].map(especie).mode()
if not especie_reciente.empty:
    ventana = ventana_temporada(analisis_planta, especie_reciente.iloc[0], temporada_reciente, hora_inicio_planta(planta_id))
    datos_horas = plc[plc["eboe"].isin(filtros.eboes)] if filtros.eboes else plc
    st.markdown(f"- {frase_horas(horas_operacion(datos_horas, *ventana), especie_reciente.iloc[0], temporada_reciente)}")
for frase in (frase_marcha(marcha_y_paradas(plc_filtrado)), frase_carga(carga(plc_filtrado)),
              frase_lab_plc(laboratorio_vs_plc(datos))):
    st.markdown(f"- {frase}")

PESTANAS = {
    "Horas de operación": pestana_horas,
    "Marcha y paradas": pestana_marcha,
    "Carga": pestana_carga,
    "Programas": pestana_programas,
    "Laboratorio vs PLC": pestana_laboratorio,
}
for (nombre, dibujar), pestana in zip(PESTANAS.items(), st.tabs(list(PESTANAS), key="pestana_operacion",
                                                                    on_change="rerun", bind="query-params")):
    if pestana.open:
        with pestana:
            arriba = st.container()
            registro = Descargas(nombre)
            dibujar(registro)
            registro.boton_todo(arriba)
