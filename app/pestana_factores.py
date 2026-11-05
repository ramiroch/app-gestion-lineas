"""Pestaña Factores de la página Análisis: qué explica la eficiencia y el aceite residual de los eBOE.

Bloques: limpieza, calibre, descomposición entre temporadas, turno / calidad / día del proceso, modelo
multivariable y aceite perdido, con un resumen arriba. Los bloques que comparan temporadas ignoran las
fechas y el proceso de la barra (respetan eBOE, turno y programa); el modelo y el aceite perdido por
eBOE respetan todos los filtros. Solo muestra: la lógica está en core/factores.py.
"""

import pandas as pd
import streamlit as st

from core.analisis import filtrar, filtros_sin_periodo, pares_con_emulsion
from core.factores import (CATEGORIAS, NOMBRES_MODELO, NOTA_MODELO_B, comparar_modelos, frase_r2_modelos,
                           frase_residuo_eboe, residuos_por_eboe, N_MINIMO_JUSTA, NIVEL_FRASE, NIVELES_JUSTA, NOTA_JUSTA, cascada_justa,
                           celdas_justas, comparacion_justa, eboe_peor, frase_justa, preparar_celdas, cortes_cuartiles, esponja_por_cuartil, esponja_por_eboe, etiquetas_cuartiles,
                           frase_esponja, pendientes_esponja, N_MINIMO_CELDA, RESPUESTAS, VIF_MAXIMO, aceite_perdido, agregar_dia_proceso, comparar_planta,
                           cortes_quintiles, descomponer, etiquetas_quintiles, frase_aceite_perdido, frase_calibre,
                           frase_categoria, frase_descomposicion, frase_eboe_calibre, frase_modelo, frase_planta,
                           frutos_por_eboe, modelo_multivariable, nota_descomposicion, por_categoria, por_quintil,
                           frase_quintil_eboe, por_quintil_eboe, preparar_pares, promedio_por_temporada,
                           residual_planta, resumen_factores, separar_errores, toneladas_por_eboe)
from core.formato import numero, texto_p
from core.precios import (BRIX_REFERENCIA, SIN_PRECIOS, UNIDADES, Precios, guardar_precios, kg_a_65_brix,
                           leer_precios, usd)
from descargas import Descargas
from filtros import filtros_elegidos, planta_elegida, tabla_emulsion_planta, tabla_planta, toneladas_planta
from graficos import GRIS, barras_importancia, cascada, cascada_pasos, columnas_agrupadas, intervalos, intervalos_agrupados

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
def modelo_en_cache(pares: pd.DataFrame, respuesta: str, con_eboe: bool = True) -> dict:
    return modelo_multivariable(pares, respuesta, con_eboe)


def con_coma(df: pd.DataFrame, decimales: int = 2, **por_columna):
    estilo = df.style.format(precision=decimales, decimal=",", na_rep="—")
    for columna, d in por_columna.items():
        estilo = estilo.format(precision=d, decimal=",", na_rep="—", subset=[columna])
    return estilo


# ---------------------------------------------------------------- bloques

def texto_usd(valor) -> str:
    return "—" if valor is None or pd.isna(valor) else f"USD {numero(valor, 0)}"


def bloque_precios() -> Precios:
    """Precios del aceite y del concentrado (data/precios.json). Devuelve los vigentes."""
    precios = leer_precios()
    titulo = "Precios" + ("" if precios.cargados else " (sin cargar)")
    with st.expander(titulo, expanded=not precios.cargados):
        with st.form("factores_precios", border=False):
            aceite, unidad_aceite, jugo, unidad_jugo, brix = st.columns([3, 2, 3, 2, 3])
            valor_aceite = aceite.number_input("Precio del aceite", min_value=0.0, value=precios.aceite, step=0.1,
                                               format="%.3f")
            u_aceite = unidad_aceite.selectbox("Unidad", list(UNIDADES), index=list(UNIDADES).index(precios.unidad_aceite),
                                               key="precios_unidad_aceite")
            valor_jugo = jugo.number_input("Precio del jugo concentrado 65 °Brix", min_value=0.0, value=precios.jugo,
                                           step=0.1, format="%.3f")
            u_jugo = unidad_jugo.selectbox("Unidad", list(UNIDADES), index=list(UNIDADES).index(precios.unidad_jugo),
                                           key="precios_unidad_jugo")
            valor_brix = brix.number_input("°Brix del jugo concentrado registrado en Resumen", min_value=1.0,
                                           value=float(precios.brix_registrado), step=0.5, format="%.1f")
            if st.form_submit_button("Guardar precios"):
                try:
                    precios = guardar_precios(Precios(aceite=valor_aceite or None, unidad_aceite=u_aceite,
                                                      jugo=valor_jugo or None, unidad_jugo=u_jugo,
                                                      brix_registrado=valor_brix))
                    st.success("Precios guardados.")
                except ValueError as error:
                    st.error(str(error))
        partes = [f"Aceite: {numero(precios.aceite_usd_kg, 3)} USD/kg" if precios.aceite else "Aceite: sin precio",
                  f"jugo concentrado 65 °Brix: {numero(precios.jugo_usd_kg, 3)} USD/kg" if precios.jugo
                  else "jugo concentrado: sin precio"]
        if precios.modificado:
            partes.append(f"última modificación: {pd.Timestamp(precios.modificado):%d/%m/%Y %H:%M}")
        st.caption(" · ".join(partes) + ". Se guardan en data/precios.json (no se sube a GitHub).")
        if precios.brix_registrado != BRIX_REFERENCIA:
            st.caption(f"Los kg de concentrado del Resumen están a {numero(precios.brix_registrado, 1)} °Brix: se pasan a "
                       f"65 °Brix (kg × {numero(precios.brix_registrado, 1)} / 65 = kg × "
                       f"{numero(kg_a_65_brix(1.0, precios.brix_registrado), 3)}) antes de valorizarlos.")
    return precios


def bloque_limpieza(excluidos: pd.DataFrame, registro: Descargas) -> None:
    if excluidos.empty:
        return
    st.warning(f"Se excluyeron {len(excluidos)} pares con más de 150 frutos en 10 kg (errores de carga).")
    with st.expander("Pares excluidos"):
        lista = pd.DataFrame({"Fecha y hora": excluidos["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"),
                              "eBOE": excluidos["eboe"], "Frutos en 10 kg": excluidos["frutos_10kg"],
                              "N.º de frutos": excluidos["n_frutos"], "Peso de la fruta (kg)": excluidos["peso_fruta_kg"]})
        registro.tabla(con_coma(lista, 1), "Pares excluidos por calibre")


def bloque_calibre(quintiles: pd.DataFrame, eboes: pd.DataFrame, por_eboe: pd.DataFrame, cortes: list[float],
                   registro: Descargas) -> None:
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

    st.markdown("**Cada eBOE dentro de cada calibre** (fruta del mismo tamaño)")
    for temporada in sorted(por_eboe["temporada"].unique(), reverse=True):
        st.markdown(f"**{frase_quintil_eboe(por_eboe, temporada)}**")
    chicos = por_eboe[por_eboe["chico"]]
    if not chicos.empty:
        st.warning(f"{len(chicos)} celdas con menos de {N_MINIMO_CELDA} pares (marcadas en la tabla): sus valores son "
                   "poco confiables.")
    st.caption("Eficiencia y residual ponderados por las toneladas del turno; programa: promedio del número de programa "
               "(N3 = 3).")
    registro.tabla(con_coma(pd.DataFrame({
        "Calibre": por_eboe["etiqueta"], "Temporada": por_eboe["temporada"].astype(str),
        "eBOE": por_eboe["eboe"].map(lambda e: f"eBOE {e}"), "N": por_eboe["n"], "Eficiencia (%)": por_eboe["eficiencia"],
        "Residual (kg/MT)": por_eboe["residual"], "Programa promedio": por_eboe["programa"],
        "Aviso": por_eboe["chico"].map({True: f"N < {N_MINIMO_CELDA}", False: ""})}).sort_values(
        ["Temporada", "Calibre", "eBOE"], ascending=[False, True, True]), 2,
        **{"Eficiencia (%)": 1, "Programa promedio": 1}), "eBOE dentro de cada calibre")


def bloque_descomposicion(quintiles: pd.DataFrame, crudos: pd.DataFrame, toneladas: pd.DataFrame, base, nueva,
                          registro: Descargas) -> dict | None:
    st.subheader(f"2. Descomposición de la diferencia entre {base} y {nueva}")
    st.caption(f"Total: la diferencia entre temporadas de la pestaña Proceso (promedio de todos los pares de cada "
               f"temporada, ponderado por las toneladas del turno). Parte del calibre: lo que cambió por tener otra "
               f"mezcla de calibres ({nueva} estandarizado a la mezcla de quintiles de {base}). Resto = total − calibre: "
               "lo que cambió dentro de cada calibre.")
    resultados = {}
    columnas = st.columns(2)
    for columna, (indicador, columna_pares, titulo, unidad, decimales, mejor_si_sube) in zip(columnas, (
            ("eficiencia", "eficiencia_pct", "Eficiencia eBOE (%)", "%", 1, True),
            ("residual", "aceite_salida", "Aceite residual (kg/MT)", "kg/MT", 2, False))):
        totales = promedio_por_temporada(crudos, columna_pares, toneladas)
        resultado = descomponer(quintiles, indicador, base, nueva, totales)
        resultados[indicador] = resultado
        columna.markdown(f"**{frase_descomposicion(resultado, indicador)}**")
        nota = nota_descomposicion(resultado, indicador)
        if nota:
            columna.caption(nota)
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


def bloque_esponja(pares: pd.DataFrame, cortes: list[float], toneladas: pd.DataFrame, registro: Descargas) -> None:
    st.subheader("4. Efecto esponja")
    st.caption("¿La cáscara retiene más aceite cuando la emulsión (licor) trae más aceite? Cuartiles de aceite en emulsión "
               "con cortes de todo el histórico: " + ", ".join(etiquetas_cuartiles(cortes).values()) + ". Salida = aceite "
               "en fruta de salida. Todo dentro de cada temporada (ignora fechas y proceso de la barra); medias "
               "ponderadas por las toneladas del turno.")
    por_eboe = esponja_por_eboe(pares, toneladas)
    if por_eboe.empty:
        st.info("No hay pares con aceite en emulsión con estos filtros.")
        return
    pendientes = pendientes_esponja(pares)
    for temporada in sorted(por_eboe["temporada"].unique(), reverse=True):
        st.markdown(f"**{frase_esponja(pendientes, por_eboe, temporada)}**")

    tabla = esponja_por_cuartil(pares, cortes, toneladas)
    tabla = tabla.assign(corta="C" + tabla["cuartil"].astype(str), eboe_texto="eBOE " + tabla["eboe"].astype(str),
                         temporada=tabla["temporada"].astype(str))
    registro.grafico(columnas_agrupadas(tabla, "corta", "eboe_texto", "salida", "Aceite en fruta de salida (kg/MT)",
                                        "kg/MT", 2, panel="temporada", titulo_x="Cuartil de aceite en emulsión"),
                     "Salida por cuartil de emulsión")
    registro.grafico(columnas_agrupadas(tabla, "corta", "eboe_texto", "eficiencia", "Eficiencia eBOE (%)", "%", 1,
                                        panel="temporada", titulo_x="Cuartil de aceite en emulsión",
                                        rango_y=[tabla["eficiencia"].min() - 4, tabla["eficiencia"].max() + 3]),
                     "Eficiencia por cuartil de emulsión")
    with st.expander("Tabla por cuartil de emulsión"):
        registro.tabla(con_coma(pd.DataFrame({
            "Cuartil": tabla["etiqueta"], "Temporada": tabla["temporada"], "eBOE": tabla["eboe_texto"], "N": tabla["n"],
            "Emulsión (%)": tabla["emulsion"], "Salida (kg/MT)": tabla["salida"], "Eficiencia (%)": tabla["eficiencia"]}),
            2, **{"Eficiencia (%)": 1}), "Esponja por cuartil")

    izquierda, derecha = st.columns(2)
    emulsion = por_eboe.assign(eboe_texto="eBOE " + por_eboe["eboe"].astype(str),
                               temporada=por_eboe["temporada"].astype(str))
    registro.grafico(columnas_agrupadas(emulsion, "eboe_texto", "temporada", "emulsion", "Aceite en emulsión (%)", "%",
                                        2), "Emulsión por eBOE", donde=izquierda)
    derecha.markdown("**Pendiente: salida vs emulsión** (kg/MT de salida por cada 1 % más de aceite en emulsión)")
    registro.tabla(con_coma(pd.DataFrame({
        "eBOE": pendientes["eboe"].map(lambda e: f"eBOE {e}"), "Temporada": pendientes["temporada"].astype(str),
        "N": pendientes["n"], "Pendiente": pendientes["pendiente"], "IC 95 % inferior": pendientes["ic_inf"],
        "IC 95 % superior": pendientes["ic_sup"], "R²": pendientes["r2"], "p": pendientes["p"].map(texto_p)}), 3),
        "Pendiente salida vs emulsión", donde=derecha)
    registro.tabla(con_coma(emulsion[["eboe_texto", "temporada", "n", "emulsion", "salida", "eficiencia"]].rename(
        columns={"eboe_texto": "eBOE", "temporada": "Temporada", "n": "N", "emulsion": "Emulsión (%)",
                 "salida": "Salida (kg/MT)", "eficiencia": "Eficiencia (%)"}), 2, **{"Eficiencia (%)": 1}),
        "Emulsión, salida y eficiencia por eBOE", donde=derecha)


@st.cache_data(ttl=600, show_spinner=False)
def justa_en_cache(pares: pd.DataFrame, cortes: list[float], cortes_emulsion: list[float]):
    datos = preparar_celdas(pares, cortes, cortes_emulsion)
    return datos, comparacion_justa(datos)


def bloque_justa(pares: pd.DataFrame, cortes: list[float], cortes_emulsion: list[float], registro: Descargas) -> None:
    st.subheader("5. Comparación justa entre eBOE")
    st.caption(f"Se compara cada eBOE contra el promedio de los otros dentro de celdas de fruta y operación parecidas: "
               f"calibre (quintil) × programa (N0–N3, N4–N5, N6–N9); después además × MTPH (≤ 10, 10–12, > 12) × "
               f"temporada; y después además × cuartil de aceite en emulsión. Solo entran las celdas con al menos "
               f"{N_MINIMO_JUSTA} pares de cada eBOE; las diferencias de las celdas se promedian ponderando por los pares "
               "del eBOE. Todas las versiones usan los mismos pares (los que tienen calibre, programa, MTPH y emulsión); "
               "promedio simple dentro de cada celda. Ignora fechas y proceso de la barra.")
    datos, resumen = justa_en_cache(pares, cortes, cortes_emulsion)
    if resumen.empty:
        st.info("Hace falta más de un eBOE para comparar.")
        return
    st.markdown(f"**{frase_justa(resumen)}**")
    st.info(NOTA_JUSTA)

    eboe = eboe_peor(resumen)
    izquierda, derecha = st.columns(2)
    for columna, indicador, titulo, unidad, decimales in (
            (izquierda, "dif_salida", "Salida (kg/MT)", "kg/MT", 2),
            (derecha, "dif_eficiencia", "Eficiencia (puntos)", "puntos", 1)):
        pasos = cascada_justa(resumen, eboe, indicador)
        if pasos is None:
            continue
        registro.grafico(cascada_pasos(
            ["Sin corregir", "Calibre y programa", "MTPH y temporada", "No explicada"],
            [pasos["total"], pasos["calibre_programa"], pasos["mtph_temporada"], pasos["no_explicada"]],
            f"{titulo}: eBOE {int(eboe)} − otros", unidad, decimales),
            f"Cascada eBOE {int(eboe)} - {indicador}", donde=columna)
    st.caption(f"Cascada del eBOE {int(eboe)} (el que más aceite deja sin corregir): diferencia contra el promedio de los "
               f"otros sin corregir, lo que cambia al comparar con el mismo calibre y programa, lo que cambia al sumar MTPH "
               f"y temporada, y lo que queda (no explicada).")

    st.markdown("**Resumen por nivel de corrección** (diferencia de cada eBOE contra el promedio de los otros)")
    registro.tabla(con_coma(pd.DataFrame({
        "Corrección": resumen["nivel"], "eBOE": resumen["eboe"].map(lambda e: f"eBOE {e}"), "Celdas": resumen["celdas"],
        "Pares": resumen["pares"], "% de los pares del eBOE": resumen["pct_pares"],
        "Diferencia de salida (kg/MT)": resumen["dif_salida"],
        "Diferencia de eficiencia (puntos)": resumen["dif_eficiencia"]}), 2,
        **{"% de los pares del eBOE": 0, "Diferencia de eficiencia (puntos)": 1}), "Comparación justa - resumen")
    poco = resumen[resumen["pct_pares"] < 50]
    if not poco.empty:
        st.caption("Con más correcciones, las celdas se achican y entran menos pares: " + ", ".join(
            f"{f['nivel']} eBOE {f['eboe']} ({numero(f['pct_pares'], 0)} %)" for f in poco.to_dict("records")) +
            ". Esas diferencias son menos firmes.")

    with st.expander("Tabla de celdas"):
        nivel = st.selectbox("Corrección", [n for n in NIVELES_JUSTA if NIVELES_JUSTA[n]], key="factores_justa_nivel",
                             index=list(n for n in NIVELES_JUSTA if NIVELES_JUSTA[n]).index(NIVEL_FRASE))
        celdas = celdas_justas(datos, NIVELES_JUSTA[nivel])
        nombres = {"quintil": "Calibre (quintil)", "grupo_programa": "Programa", "grupo_mtph": "MTPH",
                   "temporada": "Temporada", "cuartil_emulsion": "Cuartil de emulsión", "eboe": "eBOE", "n": "N",
                   "salida": "Salida (kg/MT)", "eficiencia": "Eficiencia (%)", "dif_salida": "Dif. salida (kg/MT)",
                   "dif_eficiencia": "Dif. eficiencia (puntos)"}
        registro.tabla(con_coma(celdas.assign(eboe=celdas["eboe"].map(lambda e: f"eBOE {e}")).rename(columns=nombres),
                                2, **{"Eficiencia (%)": 1, "Dif. eficiencia (puntos)": 1}),
                       f"Comparación justa - celdas {nivel}")


def mostrar_modelo(resultado: dict, respuesta: str, registro: Descargas) -> None:
    """R², N, "¿Qué pesa más?", VIF y tabla de efectos de un modelo."""
    nombre = NOMBRES_MODELO[resultado["con_eboe"]]
    letra = nombre[0]
    st.markdown(f"**{frase_modelo(resultado, respuesta)}**")
    if resultado["r2"] is None:
        return
    uno, dos, tres = st.columns(3)
    uno.metric(f"R² ({letra})", numero(resultado["r2"], 2))
    dos.metric(f"R² ajustado ({letra})", numero(resultado["r2_ajustado"], 2))
    tres.metric("N (pares)", numero(resultado["n"], 0))
    if resultado["excluidos"]:
        st.caption("Sin variación con estos filtros (no entran): " + ", ".join(resultado["excluidos"]) + ".")

    izquierda, derecha = st.columns([3, 2])
    registro.grafico(barras_importancia(resultado["importancia"], resultado["sin_explicar"], f"¿Qué pesa más? ({letra})"),
                     f"Qué pesa más {letra} - {RESPUESTAS[respuesta]['nombre']}", donde=izquierda)
    derecha.caption("% de la variación que explica cada factor por sí solo, una vez que están los demás (suma de "
                    "cuadrados tipo II sobre la total). No suman el R²: lo que comparten dos factores no se le asigna a "
                    "ninguno.")
    vif = resultado["vif"]
    altos = vif[vif["vif"] > VIF_MAXIMO]
    if not altos.empty:
        derecha.warning(f"VIF mayor que {VIF_MAXIMO} en: {', '.join(altos['nombre'])}. Esos factores se mueven juntos "
                        "con otros: sus efectos por separado no son confiables.")
    registro.tabla(con_coma(vif.drop(columns="factor").rename(columns={"nombre": "Factor", "vif": "VIF"}), 2),
                   f"VIF {letra} - {RESPUESTAS[respuesta]['nombre']}", donde=derecha)

    with st.expander(f"Efectos del modelo {letra} (cada uno, con los demás factores fijos)"):
        efectos = resultado["efectos"]
        registro.tabla(con_coma(pd.DataFrame({
            "Efecto": efectos["texto"], "IC 95 % inferior": efectos["ic_inf"], "IC 95 % superior": efectos["ic_sup"],
            "p": efectos["p"].map(texto_p)}), RESPUESTAS[respuesta]["decimales"]),
            f"Efectos {letra} - {RESPUESTAS[respuesta]['nombre']}")


def bloque_modelo(pares: pd.DataFrame, registro: Descargas) -> None:
    st.subheader("6. Modelos multivariables")
    st.caption("Regresión lineal con todos los factores a la vez (respeta todos los filtros de la barra): temporada, "
               "programa, MTPH, frutos en 10 kg, calidad, turno, caudal de fase pesada, aceite en emulsión y día del "
               "proceso; el modelo A suma el eBOE y el B no. Los dos usan los mismos pares (los que tienen todos los "
               "datos).")
    respuesta = st.radio("Variable de respuesta", list(RESPUESTAS), horizontal=True, key="factores_respuesta",
                         format_func=lambda r: f"{RESPUESTAS[r]['nombre']} ({RESPUESTAS[r]['unidad']})")
    con, sin = modelo_en_cache(pares, respuesta, True), modelo_en_cache(pares, respuesta, False)
    nombre = RESPUESTAS[respuesta]["nombre"]

    st.markdown(f"##### Modelo {NOMBRES_MODELO[True]}")
    st.caption("El efecto del eBOE es su diferencia propia: la que no explican las variables del modelo.")
    mostrar_modelo(con, respuesta, registro)
    st.markdown(f"##### Modelo {NOMBRES_MODELO[False]}")
    st.info(NOTA_MODELO_B)
    mostrar_modelo(sin, respuesta, registro)
    if con["r2"] is None or sin["r2"] is None:
        return

    st.markdown("##### A contra B")
    st.markdown(f"**{frase_r2_modelos(con, sin)}**")
    residuos = residuos_por_eboe(sin)
    st.markdown(f"**{frase_residuo_eboe(residuos, respuesta)}**")
    unidad, decimales = RESPUESTAS[respuesta]["unidad"], RESPUESTAS[respuesta]["decimales"]
    titulo_y = f"Residuo en B ({'puntos' if unidad == '%' else unidad})"
    izquierda, derecha = st.columns(2)
    figura = intervalos(residuos.assign(grupo="eBOE " + residuos["eboe"].astype(str), media=residuos["residuo"]),
                        titulo_y, "puntos" if unidad == "%" else unidad, decimales, titulo_x="eBOE",
                        titulo="Residuo promedio por eBOE en el modelo B (IC 95 %)")
    figura.add_hline(y=0, line_dash="dot", line_color=GRIS)          # 0 = rinde lo esperado
    registro.grafico(figura, f"Residuo por eBOE - {nombre}", donde=izquierda)
    derecha.caption("Residuo = valor real − lo que espera el modelo B con el calibre, programa, MTPH y demás variables "
                    "de cada par. Un eBOE con residuo distinto de 0 rinde distinto de lo esperado por razones que el "
                    "modelo no ve.")
    registro.tabla(con_coma(pd.DataFrame({
        "eBOE": residuos["eboe"].map(lambda e: f"eBOE {e}"), "N": residuos["n"], f"Real ({unidad})": residuos["observado"],
        f"Esperado ({unidad})": residuos["esperado"], "Residuo": residuos["residuo"], "IC 95 % inferior": residuos["ic_inf"],
        "IC 95 % superior": residuos["ic_sup"]}), decimales), f"Residuo por eBOE - {nombre}", donde=derecha)

    st.markdown("**Efecto de cada variable en A y en B**")
    comparacion = comparar_modelos(con, sin)
    registro.tabla(con_coma(pd.DataFrame({
        "Variable": comparacion["variable"], "Efecto en A": comparacion["coef_a"],
        "IC 95 % A": [f"{numero(i, decimales)} a {numero(s, decimales)}" for i, s in
                      zip(comparacion["ic_inf_a"], comparacion["ic_sup_a"])],
        "p A": comparacion["p_a"].map(texto_p), "Efecto en B": comparacion["coef_b"],
        "IC 95 % B": [f"{numero(i, decimales)} a {numero(s, decimales)}" if pd.notna(i) else "—" for i, s in
                      zip(comparacion["ic_inf_b"], comparacion["ic_sup_b"])],
        "p B": comparacion["p_b"].map(lambda p: texto_p(p) if pd.notna(p) else "—")}), decimales),
        f"Efectos A y B - {nombre}")
    st.caption(f"Efectos en {RESPUESTAS[respuesta]['efecto']}. Si un efecto cambia mucho de A a B, esa variable se "
               "mueve junto con el eBOE (por ejemplo, un eBOE recibe más fruta chica): sin el eBOE, absorbe parte de su "
               "diferencia.")
    st.info(NOTA_MODELO)


def bloque_aceite_perdido(pares_filtrados: pd.DataFrame, pares_temporadas: pd.DataFrame, toneladas: pd.DataFrame,
                          base, nueva, precios: Precios, registro: Descargas) -> tuple[pd.DataFrame, dict | None]:
    st.subheader("7. Aceite perdido")
    st.caption("Toneladas por eBOE (supuesto): la fruta procesada de cada turno (registro de producción) se reparte "
               "entre los eBOE que tienen muestras en ese turno, en proporción a su MTPH promedio del turno; si a "
               "alguno le falta el MTPH, partes iguales. Residual del eBOE ponderado por esas toneladas. Aceite perdido "
               "= (residual − el del eBOE con menor residual de la temporada) × toneladas del eBOE. Esta parte respeta "
               "todos los filtros.")
    reparto = toneladas_por_eboe(pares_filtrados, toneladas)
    perdido = aceite_perdido(reparto)
    precio = precios.aceite_usd_kg
    if perdido.empty:
        st.info("No hay turnos con toneladas y pares con estos filtros.")
        return perdido, None
    sin_mtph = reparto[reparto["reparto"] == "partes iguales"][["fecha_produccion", "turno"]].drop_duplicates()
    if not sin_mtph.empty:
        st.caption(f"{len(sin_mtph)} turnos repartidos en partes iguales por falta de MTPH.")
    if precio is None:
        st.caption(SIN_PRECIOS)
    else:
        perdido = perdido.assign(usd=usd(perdido["kg_perdidos"], precio))
    for temporada in sorted(perdido["temporada"].unique(), reverse=True):
        sub = perdido[perdido["temporada"] == temporada]
        st.markdown(f"**{frase_aceite_perdido(perdido, temporada, precio)}**")
        for columna, fila in zip(st.columns(len(sub)), sub.to_dict("records")):
            columna.metric(f"eBOE {fila['eboe']} · {temporada}", f"{numero(fila['kg_perdidos'], 0)} kg",
                           help="Aceite perdido contra el eBOE con menor residual de la temporada")
            if precio is not None:
                columna.markdown(f"**{texto_usd(fila['usd'])}**")
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
            "kg_perdidos": "Aceite perdido (kg)", "usd": "Aceite perdido (USD)"}), 2,
            **{"Toneladas (MT)": 0, "Aceite perdido (kg)": 0, **({"Aceite perdido (USD)": 0} if precio else {})}),
            "Aceite perdido por eBOE")

    st.markdown(f"**Toda la planta: {nueva} contra {base}** (ignora fechas y proceso de la barra)")
    planta = residual_planta(toneladas_por_eboe(pares_temporadas, toneladas))
    comparacion = comparar_planta(planta, base, nueva)
    st.markdown(f"**{frase_planta(comparacion, precio)}**")
    if comparacion is not None:
        uno, dos, tres, cuatro = st.columns(4)
        uno.metric(f"Residual {base}", f"{numero(comparacion['residual_base'], 2)} kg/MT")
        dos.metric(f"Residual {nueva}", f"{numero(comparacion['residual_nueva'], 2)} kg/MT",
                   delta=f"{numero(comparacion['diferencia'], 2)} kg/MT", delta_color="inverse")
        tres.metric(f"Aceite de diferencia en {nueva}", f"{numero(comparacion['kg'], 0)} kg")
        cuatro.metric("En USD", texto_usd(usd(comparacion["kg"], precio)), help=None if precio else SIN_PRECIOS)
    return perdido, comparacion


# ---------------------------------------------------------------- pestaña

def pestana_factores(registro: Descargas) -> None:
    filtros = filtros_elegidos()
    planta_id = planta_elegida()
    crudos = pares_planta(planta_id)
    todos, excluidos = separar_errores(crudos)
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
    por_eboe = por_quintil_eboe(pares_temporadas, cortes, toneladas)

    precios = bloque_precios()
    resumen = st.container()
    bloque_limpieza(excluidos, registro)
    bloque_calibre(quintiles, eboes, por_eboe, cortes, registro)
    if len(temporadas) >= 2:
        descomposicion = bloque_descomposicion(quintiles, filtrar(crudos, filtros_sin_periodo(filtros)), toneladas,
                                               base, nueva, registro)
    else:
        st.info("La descomposición necesita dos temporadas.")
        descomposicion = None
    bloque_categorias(pares_temporadas, registro)
    cortes_emulsion = cortes_cuartiles(todos["aceite_emulsion"])     # todo el histórico, sin filtros
    bloque_esponja(pares_temporadas, cortes_emulsion, toneladas, registro)
    bloque_justa(pares_temporadas, cortes, cortes_emulsion, registro)
    bloque_modelo(pares_filtrados, registro)
    perdido, comparacion = bloque_aceite_perdido(pares_filtrados, pares_temporadas, toneladas, base, nueva, precios,
                                                 registro)

    with resumen:
        st.subheader("Resumen")
        modelo = modelo_en_cache(pares_filtrados, "eficiencia_pct")
        for frase in resumen_factores(quintiles, eboes, descomposicion, modelo, perdido, comparacion, nueva,
                                       precios.aceite_usd_kg):
            st.markdown(f"- {frase}")
