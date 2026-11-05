"""Pestaña Factores de la página Análisis: qué explica la eficiencia y el aceite residual de los eBOE.

Bloques: limpieza, calibre, descomposición entre temporadas, turno / calidad / día del proceso, modelo
multivariable y aceite perdido, con un resumen arriba. Los bloques que comparan temporadas ignoran las
fechas y el proceso de la barra (respetan eBOE, turno y programa); el modelo y el aceite perdido por
eBOE respetan todos los filtros. Solo muestra: la lógica está en core/factores.py.
"""

import pandas as pd
import streamlit as st

from core.analisis import filtrar, filtros_sin_periodo, pares_con_emulsion
from core.factores import (CATEGORIAS, RESPUESTAS, VIF_MAXIMO, aceite_perdido, agregar_dia_proceso, comparar_planta,
                           cortes_quintiles, descomponer, etiquetas_quintiles, frase_aceite_perdido, frase_calibre,
                           frase_categoria, frase_descomposicion, frase_eboe_calibre, frase_modelo, frase_planta,
                           frutos_por_eboe, modelo_multivariable, por_categoria, por_quintil, preparar_pares,
                           residual_planta, resumen_factores, separar_errores, toneladas_por_eboe)
from core.formato import numero, texto_p
from descargas import Descargas
from filtros import filtros_elegidos, planta_elegida, tabla_emulsion_planta, tabla_planta, toneladas_planta
from graficos import barras_importancia, cascada, columnas_agrupadas, intervalos_agrupados

INDICADORES = {"entrada": "Entrada", "removido": "Removido", "residual": "Residual"}
ETIQUETAS_CORTAS = {1: "Q1 grande", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5 chica"}
NOTA_MODELO = "Son datos históricos, no un ensayo: muestran asociaciones, no prueban causas."
VARIABLES_CATEGORIA = [("eficiencia_pct", "Eficiencia (%)", "la eficiencia", "%", 1),
                       ("residual", "Aceite residual (kg/MT)", "el residual", "kg/MT", 2)]


# ---------------------------------------------------------------- datos (en caché)

@st.cache_data(ttl=600, show_spinner="Armando los pares con los datos de la fruta…")
def pares_planta(planta_id: int) -> pd.DataFrame:
    """Pares de toda la planta (sin filtrar) con calibre, calidad, día del proceso, residual y removido."""
    fruta = agregar_dia_proceso(tabla_planta(planta_id))     # el día del proceso, con la planta completa
    return preparar_pares(pares_con_emulsion(fruta, tabla_emulsion_planta(planta_id)))


@st.cache_data(ttl=600, show_spinner=False)
def categoria_en_cache(pares: pd.DataFrame, factor: str, variable: str) -> pd.DataFrame:
    return por_categoria(pares, factor, variable)


@st.cache_data(ttl=600, show_spinner="Ajustando el modelo…")
def modelo_en_cache(pares: pd.DataFrame, respuesta: str) -> dict:
    return modelo_multivariable(pares, respuesta)


def con_coma(df: pd.DataFrame, decimales: int = 2, **por_columna):
    estilo = df.style.format(precision=decimales, decimal=",", na_rep="—")
    for columna, d in por_columna.items():
        estilo = estilo.format(precision=d, decimal=",", na_rep="—", subset=[columna])
    return estilo


# ---------------------------------------------------------------- bloques

def bloque_limpieza(excluidos: pd.DataFrame, registro: Descargas) -> None:
    if excluidos.empty:
        return
    st.warning(f"Se excluyeron {len(excluidos)} pares con más de 150 frutos en 10 kg (errores de carga).")
    with st.expander("Pares excluidos"):
        lista = pd.DataFrame({"Fecha y hora": excluidos["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"),
                              "eBOE": excluidos["eboe"], "Frutos en 10 kg": excluidos["frutos_10kg"],
                              "N.º de frutos": excluidos["n_frutos"], "Peso de la fruta (kg)": excluidos["peso_fruta_kg"]})
        registro.tabla(con_coma(lista, 1), "Pares excluidos por calibre")


def bloque_calibre(quintiles: pd.DataFrame, eboes: pd.DataFrame, cortes: list[float], registro: Descargas) -> None:
    st.subheader("1. Calibre")
    etiquetas = etiquetas_quintiles(cortes)
    st.caption("Quintiles de frutos en 10 kg con cortes de todo el histórico (iguales en las dos temporadas): "
               + ", ".join(etiquetas.values()) + ". Más frutos en 10 kg = fruta más chica. Medias ponderadas por las "
               "toneladas del turno.")
    for temporada in sorted(quintiles["temporada"].unique(), reverse=True):
        st.markdown(f"**{frase_calibre(quintiles, temporada)}**")
    chicos = quintiles[quintiles["n"] < 3]
    if not chicos.empty:
        st.warning("Hay quintiles con menos de 3 pares: " + ", ".join(
            f"{f['etiqueta']} {f['temporada']}" for f in chicos.to_dict("records")) + ".")

    tabla = quintiles.assign(temporada=quintiles["temporada"].astype(str),
                             corta=quintiles["quintil"].map(ETIQUETAS_CORTAS))
    registro.grafico(columnas_agrupadas(tabla, "etiqueta", "temporada", "eficiencia", "Eficiencia eBOE (%)", "%", 1,
                                        titulo_x="Calibre (frutos en 10 kg)"), "Eficiencia por calibre")
    largo = tabla.melt(id_vars=["corta", "temporada", "n"], value_vars=list(INDICADORES), var_name="indicador",
                       value_name="valor")
    registro.grafico(columnas_agrupadas(largo, "corta", "indicador", "valor", "Aceite en fruta (kg/MT)", "kg/MT", 2,
                                        panel="temporada", nombres_grupo=INDICADORES),
                     "Entrada, removido y residual por calibre")
    with st.expander("Tabla por calibre"):
        registro.tabla(con_coma(quintiles.drop(columns=["quintil", "ponderada"]).rename(columns={
            "etiqueta": "Calibre", "temporada": "Temporada", "n": "N", "frutos": "Frutos en 10 kg",
            "eficiencia": "Eficiencia (%)", "entrada": "Entrada (kg/MT)", "removido": "Removido (kg/MT)",
            "residual": "Residual (kg/MT)"}), 2, **{"Frutos en 10 kg": 1, "Eficiencia (%)": 1}), "Calibre")

    st.markdown("**Calibre por eBOE**")
    for temporada in sorted(eboes["temporada"].unique(), reverse=True):
        st.markdown(f"**{frase_eboe_calibre(eboes, temporada)}**")
    tabla_eboes = eboes.assign(eboe="eBOE " + eboes["eboe"].astype(str), temporada=eboes["temporada"].astype(str))
    izquierda, derecha = st.columns(2)
    registro.grafico(columnas_agrupadas(tabla_eboes, "eboe", "temporada", "frutos", "Frutos en 10 kg", "frutos", 0),
                     "Frutos en 10 kg por eBOE", donde=izquierda)
    registro.tabla(con_coma(eboes.rename(columns={"eboe": "eBOE", "temporada": "Temporada", "n": "N",
                                                  "frutos": "Frutos en 10 kg"}), 1),
                   "Frutos en 10 kg por eBOE", donde=derecha)


def bloque_descomposicion(quintiles: pd.DataFrame, base, nueva, registro: Descargas) -> dict | None:
    st.subheader(f"2. Descomposición de la diferencia entre {base} y {nueva}")
    st.caption(f"Parte del calibre: lo que cambió por tener otra mezcla de calibres ({nueva} estandarizado a la mezcla "
               f"de quintiles de {base}). Resto: lo que cambió dentro de cada calibre. Cada temporada se arma con sus "
               "quintiles pesados por la cantidad de pares, así que el total puede diferir un poco del promedio de "
               "la pestaña Proceso.")
    resultados = {}
    columnas = st.columns(2)
    for columna, (indicador, titulo, unidad, decimales, mejor_si_sube) in zip(columnas, (
            ("eficiencia", "Eficiencia eBOE (%)", "%", 1, True), ("residual", "Aceite residual (kg/MT)", "kg/MT", 2, False))):
        resultado = descomponer(quintiles, indicador, base, nueva)
        resultados[indicador] = resultado
        columna.markdown(f"**{frase_descomposicion(resultado, indicador)}**")
        if resultado is not None:
            registro.grafico(cascada(resultado, titulo, unidad, decimales, mejor_si_sube), f"Cascada {indicador}",
                             donde=columna)
    return resultados.get("eficiencia")


def bloque_categorias(pares: pd.DataFrame, registro: Descargas) -> None:
    st.subheader("3. Turno, calidad de fruta y día del proceso")
    st.caption("Media e IC del 95 % por categoría y temporada (promedio simple, para poder calcular el IC). La calidad "
               "es la de la muestra de entrada. p: ANOVA dentro de cada temporada.")
    for factor, nombre in CATEGORIAS.items():
        st.markdown(f"##### {nombre}")
        columnas = st.columns(2)
        for columna, (variable, titulo, sujeto, unidad, decimales) in zip(columnas, VARIABLES_CATEGORIA):
            tabla = categoria_en_cache(pares, factor, variable)
            columna.markdown(f"**{frase_categoria(tabla, factor, sujeto, unidad, decimales)}**")
            if tabla.empty:
                continue
            chicos = tabla[tabla["n"] < 3]
            if not chicos.empty:
                columna.warning("Menos de 3 pares en: " + ", ".join(
                    f"{f['grupo']} {f['temporada']}" for f in chicos.to_dict("records")) + ".")
            registro.grafico(intervalos_agrupados(tabla, titulo, unidad, decimales, nombre),
                             f"{nombre} - {titulo}", donde=columna)
            with columna.expander("Tabla"):
                registro.tabla(con_coma(tabla.rename(columns={
                    "temporada": "Temporada", "grupo": nombre, "n": "N", "media": "Media", "ic_inf": "IC inferior",
                    "ic_sup": "IC superior", "p": "p"}), decimales, p=4), f"{nombre} - {titulo}")


def bloque_modelo(pares: pd.DataFrame, registro: Descargas) -> None:
    st.subheader("4. Modelo multivariable")
    st.caption("Regresión lineal con todos los factores a la vez (respeta todos los filtros de la barra): eBOE, "
               "temporada, programa, MTPH, frutos en 10 kg, calidad, turno, caudal de fase pesada, aceite en emulsión "
               "y día del proceso. Entran los pares que tienen todos los datos.")
    respuesta = st.radio("Variable de respuesta", list(RESPUESTAS), horizontal=True, key="factores_respuesta",
                         format_func=lambda r: f"{RESPUESTAS[r]['nombre']} ({RESPUESTAS[r]['unidad']})")
    resultado = modelo_en_cache(pares, respuesta)
    st.markdown(f"**{frase_modelo(resultado, respuesta)}**")
    if resultado["r2"] is None:
        return
    uno, dos, tres = st.columns(3)
    uno.metric("R²", numero(resultado["r2"], 2))
    dos.metric("R² ajustado", numero(resultado["r2_ajustado"], 2))
    tres.metric("N (pares)", numero(resultado["n"], 0))
    if resultado["excluidos"]:
        st.caption("Sin variación con estos filtros (no entran): " + ", ".join(resultado["excluidos"]) + ".")

    izquierda, derecha = st.columns([3, 2])
    registro.grafico(barras_importancia(resultado["importancia"], resultado["sin_explicar"], "¿Qué pesa más?"),
                     f"Qué pesa más - {RESPUESTAS[respuesta]['nombre']}", donde=izquierda)
    derecha.caption("% de la variación que explica cada factor por sí solo, una vez que están los demás (suma de "
                    "cuadrados tipo II sobre la total). No suman el R²: lo que comparten dos factores no se le asigna a "
                    "ninguno.")
    vif = resultado["vif"]
    altos = vif[vif["vif"] > VIF_MAXIMO]
    if not altos.empty:
        derecha.warning(f"VIF mayor que {VIF_MAXIMO} en: {', '.join(altos['nombre'])}. Esos factores se mueven juntos "
                        "con otros: sus efectos por separado no son confiables.")
    registro.tabla(con_coma(vif.drop(columns="factor").rename(columns={"nombre": "Factor", "vif": "VIF"}), 2),
                   f"VIF - {RESPUESTAS[respuesta]['nombre']}", donde=derecha)

    st.markdown("**Efectos** (cada uno, con los demás factores fijos)")
    efectos = resultado["efectos"]
    decimales = RESPUESTAS[respuesta]["decimales"]
    registro.tabla(con_coma(pd.DataFrame({
        "Efecto": efectos["texto"], "IC 95 % inferior": efectos["ic_inf"], "IC 95 % superior": efectos["ic_sup"],
        "p": efectos["p"].map(texto_p)}), decimales), f"Efectos - {RESPUESTAS[respuesta]['nombre']}")
    st.info(NOTA_MODELO)


def bloque_aceite_perdido(pares_filtrados: pd.DataFrame, pares_temporadas: pd.DataFrame, toneladas: pd.DataFrame,
                          base, nueva, registro: Descargas) -> tuple[pd.DataFrame, dict | None]:
    st.subheader("5. Aceite perdido")
    st.caption("Toneladas por eBOE (supuesto): la fruta procesada de cada turno (registro de producción) se reparte "
               "entre los eBOE que tienen muestras en ese turno, en proporción a su MTPH promedio del turno; si a "
               "alguno le falta el MTPH, partes iguales. Residual del eBOE ponderado por esas toneladas. Aceite perdido "
               "= (residual − el del eBOE con menor residual de la temporada) × toneladas del eBOE. Esta parte respeta "
               "todos los filtros.")
    reparto = toneladas_por_eboe(pares_filtrados, toneladas)
    perdido = aceite_perdido(reparto)
    if perdido.empty:
        st.info("No hay turnos con toneladas y pares con estos filtros.")
        return perdido, None
    sin_mtph = reparto[reparto["reparto"] == "partes iguales"][["fecha_produccion", "turno"]].drop_duplicates()
    if not sin_mtph.empty:
        st.caption(f"{len(sin_mtph)} turnos repartidos en partes iguales por falta de MTPH.")
    for temporada in sorted(perdido["temporada"].unique(), reverse=True):
        sub = perdido[perdido["temporada"] == temporada]
        st.markdown(f"**{frase_aceite_perdido(perdido, temporada)}**")
        for columna, fila in zip(st.columns(len(sub)), sub.to_dict("records")):
            columna.metric(f"eBOE {fila['eboe']} · {temporada}", f"{numero(fila['kg_perdidos'], 0)} kg",
                           help="Aceite perdido contra el eBOE con menor residual de la temporada")
            columna.caption(f"Residual {numero(fila['residual'], 2)} kg/MT · {numero(fila['toneladas'], 0)} MT · "
                            f"N {numero(fila['n'], 0)} pares")
    tabla = perdido.assign(eboe="eBOE " + perdido["eboe"].astype(str), temporada=perdido["temporada"].astype(str))
    izquierda, derecha = st.columns(2)
    registro.grafico(columnas_agrupadas(tabla, "eboe", "temporada", "residual", "Aceite residual (kg/MT)", "kg/MT", 2),
                     "Residual por eBOE", donde=izquierda)
    registro.grafico(columnas_agrupadas(tabla, "eboe", "temporada", "kg_perdidos", "Aceite perdido (kg)", "kg", 0),
                     "Aceite perdido por eBOE", donde=derecha)
    with st.expander("Tabla por eBOE"):
        registro.tabla(con_coma(perdido.rename(columns={
            "temporada": "Temporada", "eboe": "eBOE", "toneladas": "Toneladas (MT)", "turnos": "Turnos", "n": "Pares",
            "residual": "Residual (kg/MT)", "referencia": "eBOE de referencia", "diferencia": "Diferencia (kg/MT)",
            "kg_perdidos": "Aceite perdido (kg)"}), 2, **{"Toneladas (MT)": 0, "Aceite perdido (kg)": 0}),
            "Aceite perdido por eBOE")

    st.markdown(f"**Toda la planta: {nueva} contra {base}** (ignora fechas y proceso de la barra)")
    planta = residual_planta(toneladas_por_eboe(pares_temporadas, toneladas))
    comparacion = comparar_planta(planta, base, nueva)
    st.markdown(f"**{frase_planta(comparacion)}**")
    if comparacion is not None:
        uno, dos, tres = st.columns(3)
        uno.metric(f"Residual {base}", f"{numero(comparacion['residual_base'], 2)} kg/MT")
        dos.metric(f"Residual {nueva}", f"{numero(comparacion['residual_nueva'], 2)} kg/MT",
                   delta=f"{numero(comparacion['diferencia'], 2)} kg/MT", delta_color="inverse")
        tres.metric(f"Aceite de diferencia en {nueva}", f"{numero(comparacion['kg'], 0)} kg")
    return perdido, comparacion


# ---------------------------------------------------------------- pestaña

def pestana_factores(registro: Descargas) -> None:
    filtros = filtros_elegidos()
    planta_id = planta_elegida()
    todos, excluidos = separar_errores(pares_planta(planta_id))
    toneladas = toneladas_planta(planta_id)
    pares_temporadas = filtrar(todos, filtros_sin_periodo(filtros))
    pares_filtrados = filtrar(todos, filtros)
    st.caption("Los bloques que comparan temporadas (calibre, descomposición, categorías y la comparación de la planta) "
               "ignoran las fechas y el proceso de la barra; respetan eBOE, turno y programa. El modelo y el aceite "
               "perdido por eBOE respetan todos los filtros.")
    temporadas = sorted(pares_temporadas["temporada"].unique())
    if pares_temporadas.empty:
        st.info("No hay pares con estos filtros.")
        return
    base, nueva = (temporadas[-2], temporadas[-1]) if len(temporadas) >= 2 else (temporadas[0], temporadas[0])
    cortes = cortes_quintiles(todos["frutos_10kg"])        # todo el histórico, sin filtros
    quintiles = por_quintil(pares_temporadas, cortes, toneladas)
    eboes = frutos_por_eboe(pares_temporadas)

    resumen = st.container()
    bloque_limpieza(excluidos, registro)
    bloque_calibre(quintiles, eboes, cortes, registro)
    if len(temporadas) >= 2:
        descomposicion = bloque_descomposicion(quintiles, base, nueva, registro)
    else:
        st.info("La descomposición necesita dos temporadas.")
        descomposicion = None
    bloque_categorias(pares_temporadas, registro)
    bloque_modelo(pares_filtrados, registro)
    perdido, comparacion = bloque_aceite_perdido(pares_filtrados, pares_temporadas, toneladas, base, nueva, registro)

    with resumen:
        st.subheader("Resumen")
        modelo = modelo_en_cache(pares_filtrados, "eficiencia_pct")
        for frase in resumen_factores(quintiles, eboes, descomposicion, modelo, perdido, comparacion, nueva):
            st.markdown(f"- {frase}")
