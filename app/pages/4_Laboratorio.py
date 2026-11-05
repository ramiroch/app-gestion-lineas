"""Página Confiabilidad del laboratorio: controles sobre los datos crudos de la titulación Scott.

Usa la barra de filtros global. Pestañas: gramaje y mL (protocolo de Brown), preferencia de dígitos,
muestras compartidas, analistas, normalidad del titulante y gramaje vs resultado. Solo se dibuja la
pestaña abierta; cada gráfico tiene botón PNG, cada tabla Excel y cada pestaña "Descargar todo".
Solo muestra: la lógica está en core/laboratorio.py (y la carta I-MR en core/spc.py).
"""

from dataclasses import replace

import pandas as pd
import streamlit as st

from core.analisis import filtrar
from core.formato import numero
from core.laboratorio import (DIAS_MISMA_NORMALIDAD, N_MINIMO_DIGITOS, POSICIONES, VOL_MAXIMO_ML,
                              carta_normalidad, comparar_analistas, compartidas_por_mes, frase_analistas,
                              frase_digitos, frase_gramaje, frase_periodos, fuera_de_protocolo, gramaje_vs_resultado,
                              muestras_compartidas, muestras_vol_alto, normalidad_diaria, periodos_misma_normalidad,
                              preferencia_digitos, rangos_protocolo)
from core.mensual import eje_de_meses
from descargas import Descargas
from filtros import barra_filtros, filtros_elegidos, planta_elegida, tabla_planta
from graficos import (COLORES, boxplot_por_analista, columnas_mensuales, dispersion_por_lugar, grafico_normalidad,
                      histograma_grupos, intervalos, mapa_digitos, marcar_protocolo)

LUGARES = ("Entrada", "Salida")


def con_coma(df: pd.DataFrame, decimales: int = 2, **por_columna):
    """Styler con coma decimal; por_columna: {columna: decimales} para las que llevan otros."""
    estilo = df.style.format(precision=decimales, decimal=",", na_rep="—")
    for columna, d in por_columna.items():
        estilo = estilo.format(precision=d, decimal=",", na_rep="—", subset=[columna])
    return estilo


def avisar_n(n: int, que: str = "muestras") -> None:
    if n < 3:
        st.warning(f"Hay menos de 3 {que}: los resultados no son representativos.")


def columnas_muestras(datos: pd.DataFrame) -> pd.DataFrame:
    """Datos crudos de cada muestra, para las listas."""
    tabla = datos[["fecha_hora", "eboe", "lugar", "analista", "peso_muestra_g", "vol_ml", "blanco_ml", "normalidad",
                   "aceite_fruta"]].copy()
    tabla["fecha_hora"] = tabla["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M")
    return tabla.rename(columns={"fecha_hora": "Fecha y hora", "eboe": "eBOE", "lugar": "Lugar", "analista": "Analista",
                                 "peso_muestra_g": "Peso (g)", "vol_ml": "Volumen (mL)", "blanco_ml": "Blanco (mL)",
                                 "normalidad": "Normalidad (N)", "aceite_fruta": "Aceite (kg/MT)"})


# ---------------------------------------------------------------- pestañas

def pestana_gramaje(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Protocolo de Brown: 15 g de muestra en la entrada y 25 g en la salida. El rango aceptado se "
               "puede cambiar acá abajo.")
    por_defecto = rangos_protocolo()
    columnas = st.columns(4)
    rangos = {}
    for k, lugar in enumerate(LUGARES):
        minimo = columnas[2 * k].number_input(f"{lugar}: mínimo (g)", value=por_defecto[lugar][0], step=0.5,
                                              key=f"lab_min_{lugar}")
        maximo = columnas[2 * k + 1].number_input(f"{lugar}: máximo (g)", value=por_defecto[lugar][1], step=0.5,
                                                  key=f"lab_max_{lugar}")
        rangos[lugar] = (minimo, maximo)
    try:
        por_lugar = fuera_de_protocolo(datos, rangos).set_index("lugar")
    except ValueError as error:
        st.warning(str(error))
        return

    tarjetas = st.columns(3)
    for columna, lugar in zip(tarjetas, LUGARES):
        if lugar in por_lugar.index:
            fila = por_lugar.loc[lugar]
            columna.metric(f"{lugar}: fuera del protocolo", f"{numero(fila['pct_fuera'], 1)} %")
            columna.caption(f"{numero(fila['fuera'], 0)} de {numero(fila['n'], 0)} muestras · "
                            f"{numero(fila['debajo'], 0)} con menos y {numero(fila['encima'], 0)} con más gramos")
    altos = muestras_vol_alto(datos)
    tarjetas[2].metric(f"Muestras con más de {numero(VOL_MAXIMO_ML, 0)} mL", numero(len(altos), 0))
    tarjetas[2].caption(f"de {numero(len(datos), 0)} muestras")
    avisar_n(len(datos))

    figura = marcar_protocolo(histograma_grupos(datos, "peso_muestra_g", "lugar", "Peso de muestra (g)", "g",
                                                colores=COLORES), rangos)
    registro.grafico(figura, "Histograma del gramaje")
    registro.grafico(histograma_grupos(datos, "vol_ml", "lugar", "Volumen de titulante (mL)", "mL", colores=COLORES),
                     "Histograma de los mL")
    izquierda, derecha = st.columns(2)
    registro.grafico(boxplot_por_analista(datos, "peso_muestra_g", "Peso de muestra (g)", "g"),
                     "Gramaje por analista", donde=izquierda)
    registro.grafico(boxplot_por_analista(datos, "vol_ml", "Volumen de titulante (mL)", "mL"),
                     "mL por analista", donde=derecha)

    st.subheader("Fuera del protocolo por analista")
    por_analista = fuera_de_protocolo(datos, rangos, ["analista"])
    registro.tabla(con_coma(por_analista.rename(columns={
        "lugar": "Lugar", "analista": "Analista", "minimo": "Mínimo (g)", "maximo": "Máximo (g)", "n": "N",
        "debajo": "Con menos gramos", "encima": "Con más gramos", "fuera": "Fuera", "pct_fuera": "% fuera"}),
        1), "Fuera del protocolo por analista")
    st.subheader(f"Muestras con más de {numero(VOL_MAXIMO_ML, 0)} mL")
    if altos.empty:
        st.info(f"Ninguna muestra pasa de {numero(VOL_MAXIMO_ML, 0)} mL.")
    else:
        st.caption("Más de 50 mL suele indicar que hubo que recargar la bureta o que la muestra tenía demasiado "
                   "aceite para su peso: conviene revisar el dato.")
        registro.tabla(con_coma(columnas_muestras(altos), 2, **{"Normalidad (N)": 4}),
                       f"Muestras con más de {numero(VOL_MAXIMO_ML, 0)} mL")


def pestana_digitos(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Si los mL se leen bien, cada dígito (0 a 9) aparece cerca del 10 % de las veces. Anotar de más el 0 "
               "y el 5 indica que se redondea la lectura de la bureta. Prueba chi² contra la distribución uniforme, "
               f"solo para analistas con al menos {N_MINIMO_DIGITOS} muestras.")
    for posicion, nombre in POSICIONES.items():
        tabla = preferencia_digitos(datos, posicion)
        if tabla.empty:
            st.info("No hay muestras con estos filtros.")
            return
        st.subheader(nombre.capitalize())
        st.markdown(f"**{frase_digitos(tabla, posicion)}**")
        registro.grafico(mapa_digitos(tabla, f"% de veces que se anotó cada dígito en el {nombre} de los mL"),
                         f"Dígitos - {nombre}")
        with st.expander("Tabla"):
            mostrada = tabla.rename(columns={"grupo": "Analista", "n": "N", "chi2": "Chi²", "p": "p",
                                             "exceso_0_5": "Exceso de 0 y 5", "digito_max": "Dígito más anotado",
                                             "exceso_max": "Exceso del más anotado",
                                             **{f"pct_{d}": f"% {d}" for d in range(10)}})
            for columna in ("Exceso de 0 y 5", "Exceso del más anotado"):
                mostrada[columna] = mostrada[columna] * 100
            registro.tabla(con_coma(mostrada, 1, p=4), f"Dígitos - {nombre}")


def pestana_compartidas(datos_sin_eboe: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Muestras de Entrada con la misma fecha y hora, el mismo peso y los mismos mL en más de un eBOE. Es "
               "muy poco probable que frutas distintas den exactamente lo mismo: lo más probable es que se haya "
               "titulado una sola muestra y se anotó para varios eBOE. Se miran todos los eBOE (no se aplica el "
               "filtro de eBOE).")
    grupos = muestras_compartidas(datos_sin_eboe)
    por_mes = compartidas_por_mes(datos_sin_eboe)
    entradas = int(por_mes["entradas"].sum())
    compartidas = int(por_mes["compartidas"].sum())
    uno, dos, tres = st.columns(3)
    uno.metric("Grupos compartidos", numero(len(grupos), 0))
    dos.metric("Muestras de entrada en esos grupos", numero(compartidas, 0))
    tres.metric("% de las muestras de entrada", f"{numero(100 * compartidas / entradas, 1) if entradas else '—'} %")
    tres.caption(f"N = {numero(entradas, 0)} muestras de entrada")
    avisar_n(entradas, "muestras de entrada")
    if por_mes.empty:
        return
    tabla = por_mes.assign(valor=por_mes["pct"], n=por_mes["entradas"])
    registro.grafico(columnas_mensuales(tabla, eje_de_meses(por_mes["mes"]), "% de muestras de entrada compartidas", "%",
                                        por_eboe=False), "Muestras compartidas por mes")
    with st.expander("Tabla por mes"):
        registro.tabla(con_coma(por_mes[["etiqueta", "entradas", "grupos", "compartidas", "pct"]].rename(columns={
            "etiqueta": "Mes", "entradas": "Muestras de entrada", "grupos": "Grupos", "compartidas": "Compartidas",
            "pct": "%"}), 1), "Muestras compartidas por mes")
    st.subheader("Grupos compartidos")
    if grupos.empty:
        st.info("No hay muestras compartidas con estos filtros.")
        return
    lista = grupos.assign(fecha_hora=grupos["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M")).drop(
        columns="fecha_produccion").rename(columns={
            "fecha_hora": "Fecha y hora", "peso_muestra_g": "Peso (g)", "vol_ml": "Volumen (mL)", "eboes": "eBOE",
            "n_eboes": "Cantidad de eBOE", "analistas": "Analista"})
    registro.tabla(con_coma(lista, 2, **{"Peso (g)": 3}), "Grupos compartidos")


def bloque_analistas(registro: Descargas, titulo: str, datos: pd.DataFrame, variable: str, sujeto: str,
                     unidad: str, decimales: int, unidad_diferencia: str | None = None) -> None:
    st.subheader(titulo)
    resultado = comparar_analistas(datos, variable)
    st.markdown(f"**{frase_analistas(resultado, sujeto, unidad, decimales, unidad_diferencia)}**")
    if resultado["p"] is None:
        return
    st.caption(f"{numero(resultado['n'], 0)} muestras en {numero(resultado['n_bloques'], 0)} combinaciones de eBOE y "
               "semana con 2 o más analistas. Medias ajustadas: la media de cada analista como si hubiera medido "
               "en todas esas semanas y eBOE por igual.")
    medias = resultado["medias"]
    chicos = medias.loc[medias["n"] < 3, "grupo"].tolist()
    if chicos:
        st.warning(f"Menos de 3 muestras en: {', '.join(chicos)}. Esas medias no son representativas.")
    izquierda, derecha = st.columns([3, 2])
    registro.grafico(intervalos(medias, f"{titulo} ({unidad})", unidad, decimales, titulo_x="Analista",
                                titulo="Media ajustada e IC del 95 %"), f"Analistas - {titulo}", donde=izquierda)
    with derecha:
        anova = resultado["anova"].rename(columns={"fuente": "Fuente", "gl": "GL", "sc": "SC", "f": "F", "p": "p"})
        registro.tabla(con_coma(anova, 2, p=4, GL=0), f"ANOVA analistas - {titulo}", donde=derecha)
        registro.tabla(con_coma(medias.rename(columns={"grupo": "Analista", "n": "N", "media": "Media ajustada",
                                                       "ic_inf": "IC inferior", "ic_sup": "IC superior"}), decimales),
                       f"Medias analistas - {titulo}", donde=derecha)


def pestana_analistas(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Para no confundir al analista con la fruta o la temporada, se compara a cada analista contra los "
               "otros que midieron el mismo eBOE en la misma semana: ANOVA de dos factores (analista + eBOE × "
               "semana), solo con las semanas en que trabajaron 2 o más analistas.")
    salidas = datos[datos["lugar"] == "Salida"]
    pares = datos[(datos["lugar"] == "Entrada") & datos["eficiencia_par"].notna()]
    pares = pares.assign(eficiencia_pct=pares["eficiencia_par"] * 100)
    bloque_analistas(registro, "Aceite en fruta de salida", salidas, "aceite_fruta", "el aceite de salida", "kg/MT", 2)
    bloque_analistas(registro, "Eficiencia eBOE", pares, "eficiencia_pct", "la eficiencia", "%", 1, "puntos")


def pestana_normalidad(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Toda la planta usa la misma solución de bromato en cada momento: un punto por día de producción "
               f"(la normalidad más anotada ese día). En naranja, los períodos de más de {DIAS_MISMA_NORMALIDAD} días "
               "con la misma normalidad. Los límites de la carta I-MR salen de un valor por período (cuánto cambia la "
               "normalidad de una valoración a la siguiente); en rojo, los días fuera de esos límites.")
    for lugar in LUGARES:
        st.subheader(f"Normalidad de {lugar.lower()}")
        diaria = normalidad_diaria(datos, lugar)
        if len(diaria) < 2:
            st.info("Hacen falta al menos 2 días con datos para la carta.")
            continue
        periodos = periodos_misma_normalidad(diaria)
        st.markdown(f"**{frase_periodos(periodos, lugar)}**")
        try:
            carta = carta_normalidad(diaria, periodos)
        except ValueError as error:
            st.info(str(error))
            continue
        registro.grafico(grafico_normalidad(carta, periodos, f"Normalidad {lugar.lower()} (N)"),
                         f"Normalidad de {lugar.lower()}")
        varias = diaria[diaria["distintas"] > 1]
        if not varias.empty:
            st.caption(f"{numero(len(varias), 0)} días con más de una normalidad anotada (se muestra la más frecuente).")
        with st.expander("Períodos con la misma normalidad"):
            registro.tabla(con_coma(periodos.assign(
                desde=periodos["desde"].dt.date, hasta=periodos["hasta"].dt.date).drop(columns=["primero", "ultimo"])
                .rename(columns={"desde": "Desde", "hasta": "Hasta", "normalidad": "Normalidad (N)", "dias": "Días",
                                 "dias_con_datos": "Días con datos",
                                 "largo": f"Más de {DIAS_MISMA_NORMALIDAD} días"}), 4),
                f"Períodos normalidad {lugar.lower()}")


def pestana_gramaje_resultado(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Si la titulación funciona bien, el aceite en fruta no debería depender de cuánta muestra se pesó. "
               "Una pendiente significativa indica un efecto del gramaje (por ejemplo, destilación incompleta con "
               "muestras grandes) o que se pesa distinto según la fruta.")
    rectas = gramaje_vs_resultado(datos)
    for fila in rectas.to_dict("records"):
        st.markdown(f"**{frase_gramaje(fila)}**")
    registro.grafico(dispersion_por_lugar(datos, "peso_muestra_g", "aceite_fruta", rectas, "Peso de muestra (g)",
                                          "Aceite en fruta (kg/MT)", "kg/MT"), "Gramaje vs aceite en fruta")
    with st.expander("Rectas"):
        registro.tabla(con_coma(rectas.rename(columns={
            "lugar": "Lugar", "n": "N", "pendiente": "Pendiente (kg/MT por g)", "ordenada": "Ordenada (kg/MT)",
            "r2": "R²", "p": "p", "ic_inf": "IC inferior", "ic_sup": "IC superior"}), 4), "Rectas gramaje")


# ---------------------------------------------------------------- página

st.set_page_config(page_title="Confiabilidad del laboratorio", layout="wide")
st.title("Confiabilidad del laboratorio")

datos = barra_filtros()
filtros = filtros_elegidos()
if filtros is None:      # la planta no tiene análisis: la barra ya lo avisó
    st.stop()
if datos.empty:
    st.info("No hay muestras con estos filtros.")
    st.stop()


def compartidas(registro: Descargas) -> None:
    pestana_compartidas(filtrar(tabla_planta(planta_elegida()), replace(filtros, eboes=[])), registro)


PESTANAS = {
    "Gramaje y mL": lambda registro: pestana_gramaje(datos, registro),
    "Dígitos": lambda registro: pestana_digitos(datos, registro),
    "Muestras compartidas": compartidas,
    "Analistas": lambda registro: pestana_analistas(datos, registro),
    "Normalidad": lambda registro: pestana_normalidad(datos, registro),
    "Gramaje vs resultado": lambda registro: pestana_gramaje_resultado(datos, registro),
}
# Solo se dibuja la pestaña abierta; la pestaña queda en la URL (?pestana_laboratorio=Dígitos).
for (nombre, dibujar), pestana in zip(PESTANAS.items(), st.tabs(list(PESTANAS), key="pestana_laboratorio",
                                                                    on_change="rerun", bind="query-params")):
    if pestana.open:
        with pestana:
            arriba = st.container()             # "Descargar todo" va arriba, pero se arma al final
            registro = Descargas(f"laboratorio {nombre}")
            dibujar(registro)
            registro.boton_todo(arriba)
