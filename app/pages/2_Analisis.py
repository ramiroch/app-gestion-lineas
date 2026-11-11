"""Página Análisis: aceite en fruta, eficiencia del eBOE y emulsión con los filtros globales.

Pestaña Período: análisis 1 a 4 de la sección 9 de docs/mapeo_ledesma.md (histograma, boxplot,
estadística de Salida y límites IQR), con todos los filtros.
Pestaña Temporadas: análisis 8 a 10 (ANOVA, Tukey, intervalos al 95 % y boxplot por temporada);
ignora fechas y procesos, y respeta eBOE, turno y programa.
Pestaña Programa y MTPH: análisis 6 y 7 (burbujas, superficie y contorno), con todos los filtros y
filtrado cruzado al hacer clic en una burbuja.
Pestaña Proceso: análisis 5 (flowsheet): KPI por eBOE, totales del período y comparación de dos períodos.
Pestaña Factores (app/pestana_factores.py): precios, calibre, descomposición entre temporadas, turno / calidad /
día del proceso, efecto esponja, comparación justa entre eBOE, modelos con y sin eBOE, aceite perdido y balance
aceite–jugo.
Solo se dibuja la pestaña abierta. Cada gráfico tiene un botón PNG, cada tabla un botón Excel, y cada
pestaña un "Descargar todo" (app/descargas.py).
Solo muestra: la estadística está en core/, los gráficos en app/graficos.py y los datos
filtrados vienen de app/filtros.py.
"""

from dataclasses import replace

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba

from core.analisis import (ATAJO_PERSONALIZADO, FUENTES, atajos, con_fuente, filtrar, filtros_sin_periodo, opciones, pares_con_emulsion,
                           rango_de_atajo)
from core.comparaciones import comparar_grupos, frase_resultado
from core.estadistica import limites_iqr, media_ponderada_por_turno, resumen_descriptivo
from core.formato import numero, texto_p
from core.mensual import (cajas_por_mes, eficiencia_eboe_planta_por_mes, eficiencia_proceso_por_mes, eje_de_meses,
                          por_mes_y_eboe)
from core.parametros import ParametrosIncompletos
from core.programa import (N_MINIMO, N_MINIMO_RANKING, combinaciones, de_combinaciones, frase_programa, frase_uso, numero_programa,
                           grilla, mejores_y_peores, rallado_por_programa, uso_de_programas)
from core.proceso import (INDICADORES_EBOE, INDICADORES_TOTALES, comparar_resumenes, filtrar_produccion,
                          turnos_sin_aprobar,
                          resumen_proceso)
from core.regresion import frase_pendiente, regresiones_por_grupo
from core.repetidas import sin_entradas_repetidas
from descargas import Descargas
from pestana_factores import pestana_factores
from filtros import (barra_filtros, casilla_repetidas, produccion_con_borradores, ultima_fecha_planta, filtros_elegidos, planta_elegida, produccion_planta, rango_planta,
                     tabla_emulsion_planta, tabla_planta, toneladas_planta)
from graficos import (PALETA, barras_uso, boxplot, boxplot_mensual, boxplot_temporadas, burbujas, columnas_mensuales,
                      columnas_mensuales_series, combinaciones_de_puntos,
                      contorno, dispersion_por_eboe, histograma,
                      histograma_grupos, intervalos, superficie)


def porcentaje(fraccion, decimales: int = 1) -> str:
    return "—" if fraccion is None else f"{numero(fraccion * 100, decimales)} %"


def kg_t(valor) -> str:
    return "—" if valor is None else f"{numero(valor)} kg/MT"


def tarjeta(columna, titulo: str, resultado: dict, formato) -> None:
    """Tarjeta con la media (ponderada por t si hay producción) y debajo cómo se calculó y el N."""
    columna.metric(titulo, formato(resultado["valor"]))
    if resultado["ponderada"]:
        columna.caption(f"Ponderado por t · {numero(resultado['n_ponderados'], 0)} de "
                        f"{numero(resultado['n'], 0)} muestras en turnos con producción · "
                        f"simple {formato(resultado['media_simple'])}")
    else:
        columna.caption(f"Promedio simple (sin datos de producción) · N {numero(resultado['n'], 0)}")


def tabla(filas: list[dict], decimales_ee: bool = False):
    """DataFrame con coma decimal (2 decimales; 3 en el error estándar)."""
    estilo = pd.DataFrame(filas).style.format(precision=2, decimal=",", thousands=".")
    if decimales_ee:
        estilo = estilo.format(precision=3, decimal=",", subset=["EE media"])
    return estilo


def avisar_n_chico(grupos: dict[str, int]) -> None:
    chicos = [nombre for nombre, n in grupos.items() if n < 3]
    if chicos:
        st.warning(f"Menos de 3 muestras en: {', '.join(chicos)}. Esos valores no son representativos.")


def pestana_periodo(datos: pd.DataFrame, registro: Descargas) -> None:
    if datos.empty:
        st.info("No hay muestras con estos filtros.")
        return
    entradas = datos[datos["lugar"] == "Entrada"]
    salidas = datos[datos["lugar"] == "Salida"]
    pares = entradas.dropna(subset=["eficiencia_par"])
    toneladas = toneladas_planta(planta_elegida())

    medias = {"entrada": media_ponderada_por_turno(entradas, "aceite_fruta", toneladas),
              "salida": media_ponderada_por_turno(salidas, "aceite_fruta", toneladas),
              "eficiencia": media_ponderada_por_turno(pares, "eficiencia_par", toneladas)}
    columnas = st.columns(4)
    tarjeta(columnas[0], "Aceite en fruta entrada", medias["entrada"], kg_t)
    tarjeta(columnas[1], "Aceite en fruta salida", medias["salida"], kg_t)
    tarjeta(columnas[2], "Eficiencia eBOE", medias["eficiencia"], porcentaje)
    columnas[3].metric("Pares entrada/salida", numero(len(pares), 0))
    columnas[3].caption(f"{numero(len(entradas), 0)} entradas · {numero(len(salidas), 0)} salidas")
    avisar_n_chico({"aceite de entrada": medias["entrada"]["n"], "aceite de salida": medias["salida"]["n"],
                    "pares": len(pares)})
    st.caption("Las tarjetas se ponderan por toneladas procesadas en el turno. Los gráficos y las tablas "
               "describen las muestras: cada muestra cuenta igual.")

    st.subheader("Distribución del aceite en fruta")
    registro.grafico(histograma(datos), "Distribución del aceite en fruta")

    st.subheader("Aceite en fruta por eBOE y lugar")
    incluir = casilla_repetidas("periodo_repetidas")
    datos = sin_entradas_repetidas(datos, incluir)
    salidas = datos[datos["lugar"] == "Salida"]
    if not incluir:
        st.caption("Sin las entradas repetidas en varios eBOE (posible copia) ni sus salidas.")
    registro.grafico(boxplot(datos), "Aceite en fruta por eBOE y lugar")
    st.caption("Caja: Q1 a Q3 con la mediana. Bigotes: hasta el último valor dentro de los límites IQR. "
               "Círculos abiertos: outliers (se marcan, no se borran). El N de cada caja está en su tooltip.")

    st.subheader("Estadística del aceite en fruta de Salida por eBOE (kg/MT)")
    filas, grupos = [], {}
    for eboe, valores in salidas.groupby("eboe")["aceite_fruta"]:
        r = resumen_descriptivo(valores)
        grupos[f"eBOE {eboe}"] = r["n"]
        filas.append({"eBOE": f"eBOE {eboe}", "N": r["n"], "Media": r["media"], "EE media": r["error_estandar"],
                      "Desvío": r["desvio"], "Mín": r["minimo"], "Q1": r["q1"], "Mediana": r["mediana"],
                      "Q3": r["q3"], "Máx": r["maximo"]})
    if filas:
        registro.tabla(tabla(filas, decimales_ee=True), "Estadística de Salida por eBOE")
        avisar_n_chico(grupos)
    else:
        st.info("No hay muestras de Salida con estos filtros.")

    st.subheader("Límites de outliers por IQR (kg/MT)")
    filas, grupos = [], {}
    for (eboe, lugar), valores in datos.groupby(["eboe", "lugar"])["aceite_fruta"]:
        lim = limites_iqr(valores)
        grupos[f"eBOE {eboe} {lugar.lower()}"] = lim["n"]
        filas.append({"eBOE": f"eBOE {eboe}", "Lugar": lugar, "N": lim["n"], "Q1": lim["q1"], "Q3": lim["q3"],
                      "IQR": lim["iqr"], "LI": lim["li"], "LS": lim["ls"], "Outliers": lim["outliers"]})
    registro.tabla(tabla(filas), "Límites IQR")
    avisar_n_chico(grupos)
    st.caption("LI = Q1 − 1,5·IQR · LS = Q3 + 1,5·IQR · cuartiles con el método de Minitab.")

    bloque_evolucion_mensual(datos, registro)


COLOR_EFICIENCIA_EBOE, COLOR_EFICIENCIA_PROCESO = PALETA[0], PALETA[2]   # azul y verde de la paleta


def tabla_mensual(mensual: pd.DataFrame, columna: str, decimales: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"Mes": mensual["etiqueta"], "eBOE": mensual["eboe"].map(lambda e: f"eBOE {e}"),
                         columna: mensual["valor"].map(lambda v: numero(v, decimales)),
                         "N": mensual["n"].map(lambda n: numero(n, 0)),
                         "Ponderado por t": mensual["ponderada"].map({True: "Sí", False: "No"})})


def bloque_evolucion_mensual(datos: pd.DataFrame, registro: Descargas) -> None:
    """Eficiencia eBOE, MTPH, eficiencia eBOE y del proceso de la planta, y aceite en fruta (entrada y salida) por
    mes de producción."""
    st.subheader("Evolución mensual")
    st.caption("Por mes de producción (una muestra del 1/08 a las 02:00 es del día de producción 31/07 y cuenta en "
               "julio). Eficiencia y aceite ponderados por toneladas; MTPH promedio simple. La eficiencia del proceso "
               "es de la planta: Σ aceite envasado / Σ fruta procesada del mes, dividido por el aceite en fruta de "
               "entrada ponderado del mes; la producción respeta fechas, turno y proceso, no eBOE ni programa. "
               "El aceite en fruta por mes es por muestra (no ponderado). Los meses con N < 3 se ven tenues.")
    pares = pares_con_emulsion(datos, SIN_EMULSION)
    if pares.empty:
        st.info("No hay pares entrada/salida con estos filtros.")
        return
    planta_id = planta_elegida()
    toneladas = toneladas_planta(planta_id)
    produccion = filtrar_produccion(produccion_planta(planta_id), filtros_elegidos())
    eficiencia = por_mes_y_eboe(pares, "eficiencia_pct", toneladas)
    mtph = por_mes_y_eboe(pares, "tph", None)
    planta_eboe = eficiencia_eboe_planta_por_mes(pares, toneladas)
    proceso = eficiencia_proceso_por_mes(pares, toneladas, produccion).rename(columns={"eficiencia_proceso": "valor"})
    cajas, outliers = cajas_por_mes(datos)
    eje = eje_de_meses(pd.concat([eficiencia["mes"], proceso["mes"], cajas["mes"]]))
    # Las eficiencias (por eBOE, de la planta y del proceso) comparten la escala del eje Y.
    tope = max([100.0, *eficiencia["valor"].dropna(), *proceso["valor"].dropna(), *planta_eboe["valor"].dropna()]) * 1.08
    objetivo = rango_planta(planta_id, "Eficiencia eBOE")

    st.markdown("##### Eficiencia eBOE por mes")
    registro.grafico(columnas_mensuales(eficiencia, eje, "Eficiencia eBOE (%)", "%", rango_y=[0, tope],
                                        objetivo=objetivo), "Eficiencia eBOE por mes")
    if objetivo is None:
        st.caption("La planta no tiene cargado el objetivo de eficiencia eBOE en RangoReferencia.")
    with st.expander("Tabla"):
        registro.tabla(tabla_mensual(eficiencia, "Eficiencia (%)"), "Eficiencia eBOE por mes")

    st.markdown("##### MTPH promedio por mes")
    registro.grafico(columnas_mensuales(mtph, eje, "MTPH promedio", "MTPH"), "MTPH por mes")
    with st.expander("Tabla"):
        registro.tabla(tabla_mensual(mtph, "MTPH"), "MTPH por mes")

    st.markdown("##### Eficiencia eBOE y del proceso por mes (planta)")
    registro.grafico(columnas_mensuales_series([("Eficiencia eBOE", planta_eboe, COLOR_EFICIENCIA_EBOE),
                                                ("Eficiencia del proceso", proceso, COLOR_EFICIENCIA_PROCESO)],
                                               eje, "Eficiencia (%)", rango_y=[0, tope]),
                     "Eficiencia eBOE y del proceso por mes")
    with st.expander("Tabla"):
        tabla_planta_mes = proceso.merge(planta_eboe[["mes", "valor", "n"]].rename(
            columns={"valor": "eboe", "n": "n_eboe"}), on="mes", how="left")
        registro.tabla(pd.DataFrame({
            "Mes": tabla_planta_mes["etiqueta"], "Rendimiento (kg/MT)": tabla_planta_mes["rendimiento"].map(numero),
            "Aceite en fruta entrada (kg/MT)": tabla_planta_mes["aceite_entrada"].map(numero),
            "Eficiencia del proceso (%)": tabla_planta_mes["valor"].map(lambda v: numero(v, 1)),
            "Eficiencia eBOE (%)": tabla_planta_mes["eboe"].map(lambda v: numero(v, 1)),
            "N turnos": tabla_planta_mes["n"].map(lambda n: numero(n, 0)),
            "N pares": tabla_planta_mes["n_pares"].map(lambda n: numero(n, 0)),
        }), "Eficiencia eBOE y del proceso por mes")
    st.caption("Eficiencia eBOE: los eBOE filtrados juntos, ponderada por toneladas (N = pares). Eficiencia del "
               "proceso: N turnos = turnos con fruta procesada y aceite envasado aprobados (los que entran en el "
               "rendimiento); un mes sin producción cargada muestra solo la eficiencia eBOE.")

    st.markdown("##### Aceite en fruta por mes (entrada y salida)")
    if cajas.empty:
        st.info("No hay muestras de aceite en fruta con estos filtros.")
        return
    registro.grafico(boxplot_mensual(cajas, outliers, eje), "Aceite en fruta por mes")
    st.caption("Por muestra (no ponderado). Caja: Q1 a Q3 con la mediana (cuartiles de Minitab). Bigotes: hasta el "
               "último valor dentro de los límites IQR. Círculos abiertos: outliers (se marcan, no se borran). N "
               "debajo de cada caja.")
    with st.expander("Tabla"):
        registro.tabla(pd.DataFrame({
            "Mes": cajas["etiqueta"], "Lugar": cajas["lugar"], "N": cajas["n"].map(lambda n: numero(n, 0)),
            "Q1 (kg/MT)": cajas["q1"].map(numero), "Mediana (kg/MT)": cajas["mediana"].map(numero),
            "Q3 (kg/MT)": cajas["q3"].map(numero), "Media (kg/MT)": cajas["media"].map(numero)}),
            "Aceite en fruta por mes")


# ---------------------------------------------------------------- pestaña Temporadas

def texto_filtros(filtros) -> str:
    """Filtros que sí se aplican en la pestaña Temporadas, para mostrarlos."""
    partes = [("eBOE", ", ".join(map(str, filtros.eboes))), ("turno", ", ".join(filtros.turnos)),
              ("programa", ", ".join(filtros.programas))]
    return " · ".join(f"{nombre} {valor or 'todos'}" for nombre, valor in partes)


def tabla_anova(resultado: dict, decimales: int) -> pd.DataFrame:
    """Tabla ANOVA como texto con coma decimal (GL, SC, CM, F, p)."""
    anova = resultado["anova"]

    def texto(columna, formato):        # celdas vacías en blanco, como Minitab
        return anova[columna].map(lambda v: "" if pd.isna(v) else formato(v))

    return pd.DataFrame({
        "Fuente": anova["Fuente"].replace({"temporada": "Temporada"}),
        "GL": texto("GL", lambda v: numero(v, 0)),
        "SC": texto("SC", lambda v: numero(v, decimales)),
        "CM": texto("CM", lambda v: numero(v, decimales)),
        "F": texto("F", lambda v: numero(v, 2)),
        "p": texto("p", lambda v: texto_p(v).removeprefix("p ").removeprefix("= ")),
    })


def tabla_tukey(resultado: dict, decimales: int, unidad: str) -> pd.DataFrame:
    tukey = resultado["tukey"]
    return pd.DataFrame({
        "Comparación": tukey["grupo_2"] + " − " + tukey["grupo_1"],
        f"Diferencia ({unidad})": tukey["diferencia"].map(lambda v: numero(v, decimales)),
        "IC 95 %": [f"{numero(a, decimales)} a {numero(b, decimales)}" for a, b in zip(tukey["ic_inf"], tukey["ic_sup"])],
        "p ajustado": tukey["p"].map(lambda v: texto_p(v).removeprefix("p ").removeprefix("= ")),
        "¿Difieren?": tukey["significativa"].map({True: "Sí", False: "No"}),
    })


def bloque_temporadas(registro: Descargas, titulo: str, datos: pd.DataFrame, variable: str, sujeto: str,
                      articulo: str, unidad: str, unidad_frase: str, titulo_y: str, decimales: int) -> None:
    """Un bloque de la pestaña: frase, intervalos + boxplot, tabla ANOVA, Levene/Welch y Tukey."""
    st.subheader(titulo)
    try:
        resultado = comparar_grupos(datos, variable, "temporada")
    except ValueError as error:
        st.warning(f"No se puede comparar: {error}")
        return

    st.markdown(f"**{frase_resultado(resultado, sujeto, articulo, unidad_frase, decimales)}**")
    izquierda, derecha = st.columns(2)
    registro.grafico(intervalos(resultado["medias"], titulo_y, unidad, decimales), f"{titulo} - intervalos",
                     donde=izquierda)
    registro.grafico(boxplot_temporadas(datos, variable, titulo_y, unidad, decimales), f"{titulo} - boxplot",
                     donde=derecha)
    medias = resultado["medias"]
    ic = ("desvío combinado (varianzas iguales)" if resultado["ic_metodo"] == "combinado"
          else "desvío de cada temporada (varianzas distintas)")
    st.caption("N: " + " · ".join(f"{g} = {numero(n, 0)}" for g, n in zip(medias["grupo"], medias["n"]))
               + f". IC de la media calculado con el {ic}.")
    avisar_n_chico({f"temporada {g}": n for g, n in zip(medias["grupo"], medias["n"])})

    registro.tabla(tabla_anova(resultado, decimales), f"ANOVA {titulo}")
    levene = f"Levene (¿varianzas iguales?): {texto_p(resultado['levene']['p'])}"
    if resultado["welch"]:
        w = resultado["welch"]
        st.caption(f"{levene} → las varianzas difieren. ANOVA de Welch: F = {numero(w['F'])}, "
                   f"GL = {numero(w['gl_num'], 0)} y {numero(w['gl_den'], 1)}, {texto_p(w['p'])}. "
                   "Ese es el p que vale para decir si las medias difieren.")
    else:
        st.caption(f"{levene} → no hay evidencia de que difieran; vale el p del ANOVA.")
    with st.expander("Comparaciones de Tukey"):
        registro.tabla(tabla_tukey(resultado, decimales, unidad), f"Tukey {titulo}")
        st.caption("Diferencia = temporada posterior − anterior, en las unidades de la variable. "
                   "Tukey supone varianzas iguales.")


def bloque_caudal(entradas: pd.DataFrame, registro: Descargas) -> None:
    """Caudal de fase pesada por temporada (¿bajó el agua?): frase, intervalos y medias."""
    st.markdown("##### Caudal de fase pesada")
    try:
        resultado = comparar_grupos(entradas, "caudal_fase_pesada", "temporada")
    except ValueError as error:
        st.warning(f"No se puede comparar el caudal: {error}")
        return
    st.markdown(f"**{frase_resultado(resultado, 'El caudal de fase pesada', 'el', 'm³/h', 2)}**")
    izquierda, derecha = st.columns(2)
    registro.grafico(intervalos(resultado["medias"], "Caudal fase pesada (m³/h)", "m³/h", 2),
                     "Caudal de fase pesada - intervalos", donde=izquierda)
    medias = resultado["medias"]
    registro.tabla(pd.DataFrame({
        "Temporada": medias["grupo"], "N": medias["n"].map(lambda n: numero(n, 0)),
        "Media (m³/h)": medias["media"].map(numero), "Desvío": medias["desvio"].map(numero),
        "IC 95 %": [f"{numero(a)} a {numero(b)}" for a, b in zip(medias["ic_inf"], medias["ic_sup"])],
    }), "Caudal de fase pesada", donde=derecha)
    derecha.caption(f"Levene {texto_p(resultado['levene']['p'])}"
                    + (f" · Welch {texto_p(resultado['welch']['p'])}" if resultado["welch"]
                       else f" · ANOVA {texto_p(resultado['p'])}")
                    + ". Una lectura por eBOE y hora (la que se carga con la fila de Entrada).")
    avisar_n_chico({f"temporada {g}": n for g, n in zip(medias["grupo"], medias["n"])})


RELACIONES = [  # (columna x, título del eje, unidad, paso de la frase, texto de x, decimales de la pendiente)
    ("caudal_fase_pesada", "Caudal fase pesada (m³/h)", "m³/h", "1 m³/h", "de caudal de fase pesada", 2),
    ("aceite_emulsion", "Aceite en emulsión (%)", "%", "1 %", "de aceite en emulsión", 1),
]


def tabla_rectas(regresiones: pd.DataFrame, unidad_x: str) -> pd.DataFrame:
    return pd.DataFrame({
        "eBOE": regresiones["eboe"].map(lambda e: f"eBOE {e}"), "Temporada": regresiones["temporada"].astype(str),
        "N": regresiones["n"].map(lambda n: numero(n, 0)),
        f"Pendiente (puntos por {unidad_x})": regresiones["pendiente"].map(lambda v: numero(v, 2)),
        "IC 95 % pendiente": [f"{numero(a, 2)} a {numero(b, 2)}" for a, b in
                              zip(regresiones["ic_inf"], regresiones["ic_sup"])],
        "R²": regresiones["r2"].map(lambda v: numero(v, 3)),
        "p": regresiones["p"].map(lambda v: "—" if pd.isna(v) else texto_p(v).removeprefix("p ").removeprefix("= ")),
    })


def bloque_agua_licor(fruta: pd.DataFrame, emulsion: pd.DataFrame, registro: Descargas) -> None:
    """Eficiencia eBOE vs caudal de fase pesada y vs aceite en emulsión, dentro de cada temporada."""
    st.subheader("Agua y licor")
    st.caption("Cómo se relaciona la eficiencia del eBOE con el agua (caudal de fase pesada) y con el licor "
               "(aceite en emulsión). Las rectas y el R² de los gráficos se calculan dentro de cada eBOE y "
               "temporada; las frases, dentro de cada temporada con los eBOE elegidos juntos. Nunca se juntan "
               "las temporadas. Una relación no prueba una causa.")
    datos = pares_con_emulsion(fruta, emulsion)
    for x, titulo_x, unidad_x, paso, x_texto, decimales in RELACIONES:
        st.markdown(f"##### Eficiencia eBOE vs {titulo_x.split(' (')[0].lower()}")
        for reg in regresiones_por_grupo(datos, x, "eficiencia_pct", ["temporada"]).to_dict("records"):
            st.markdown("- " + frase_pendiente(reg, str(reg["temporada"]), paso, x_texto, "puntos", "de eficiencia",
                                               decimales))
        por_eboe = regresiones_por_grupo(datos, x, "eficiencia_pct", ["eboe", "temporada"])
        nombre = f"Eficiencia vs {titulo_x.split(' (')[0].lower()}"
        registro.grafico(dispersion_por_eboe(datos, x, "eficiencia_pct", por_eboe, titulo_x, "Eficiencia eBOE (%)",
                                             unidad_x, "puntos", 2, 1), nombre)
        avisar_n_chico({f"eBOE {f['eboe']} {f['temporada']}": f["n"] for f in por_eboe.to_dict("records")})
        with st.expander("Rectas por eBOE y temporada"):
            registro.tabla(tabla_rectas(por_eboe, unidad_x), f"Rectas - {nombre}")


def pestana_temporadas(registro: Descargas) -> None:
    filtros = filtros_sin_periodo(filtros_elegidos())
    st.info("Esta pestaña compara temporadas: **no usa el filtro de fechas ni el de proceso** (toma todas "
            f"las fechas y procesos). Sí respeta eBOE, turno y programa. Filtros aplicados: {texto_filtros(filtros)}.")

    fruta = filtrar(tabla_planta(planta_elegida()), filtros)
    entradas = fruta[fruta["lugar"] == "Entrada"]
    pares = entradas.dropna(subset=["eficiencia_par"]).assign(eficiencia_pct=lambda d: d["eficiencia_par"] * 100)

    bloque_temporadas(registro, "Aceite en fruta de entrada", entradas, "aceite_fruta", "El aceite en fruta de entrada",
                      "el", "kg/MT", "kg/MT", "Aceite en fruta entrada (kg/MT)", 2)
    bloque_temporadas(registro, "Eficiencia eBOE", pares, "eficiencia_pct", "La eficiencia", "la", "%", "puntos",
                      "Eficiencia eBOE (%)", 1)
    try:
        emulsion = filtrar(tabla_emulsion_planta(planta_elegida()), filtros)
    except ParametrosIncompletos as error:
        st.subheader("Aceite en emulsión")
        st.warning(str(error))
        return
    bloque_temporadas(registro, "Aceite en emulsión", emulsion, "aceite_emulsion", "El aceite en emulsión", "el", "%",
                      "puntos", "Aceite en emulsión (%)", 2)
    if not emulsion.empty:
        st.markdown("##### Distribución del aceite en emulsión por temporada")
        registro.grafico(histograma_grupos(emulsion, "aceite_emulsion", "temporada", "Aceite en emulsión (%)", "%"),
                         "Distribución del aceite en emulsión")
    bloque_caudal(entradas, registro)
    bloque_agua_licor(fruta, emulsion, registro)
    st.caption("Promedios simples por muestra: el ANOVA compara muestras, no se pondera por toneladas.")


# ---------------------------------------------------------------- pestaña Programa y MTPH

CLAVE_VERSION_BURBUJAS = "_version_burbujas"   # cambiarla crea un gráfico nuevo, sin selección


def quitar_seleccion() -> None:
    st.session_state[CLAVE_VERSION_BURBUJAS] = st.session_state.get(CLAVE_VERSION_BURBUJAS, 0) + 1


def puntos_elegidos(evento, clave: str) -> list[dict]:
    """Puntos elegidos en el gráfico de burbujas.

    Se leen del valor que devuelve st.plotly_chart; si viene vacío, de st.session_state[clave].
    """
    seleccion = (evento or {}).get("selection") or {}
    if not seleccion.get("points") and clave in st.session_state:
        seleccion = (st.session_state[clave] or {}).get("selection") or {}
    return list(seleccion.get("points") or [])


def tabla_muestras(muestras: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Fecha": muestras["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"), "Turno": muestras["turno"],
        "eBOE": muestras["eboe"], "Programa": muestras["programa"],
        "MTPH": muestras["tph"].map(lambda v: numero(v, 0)),
        "Aceite entrada (kg/MT)": muestras["aceite_fruta"].map(numero),
        "Aceite salida (kg/MT)": muestras["aceite_salida"].map(numero),
        "Eficiencia (%)": muestras["eficiencia_pct"].map(lambda v: numero(v, 1)),
        "Aceite en emulsión (%)": muestras["aceite_emulsion"].map(numero),
    })


def tabla_ranking(ranking: pd.DataFrame) -> pd.DataFrame:
    singular = {"Mejores": "Mejor", "Peores": "Peor"}
    return pd.DataFrame({
        "Puesto": [f"{singular[g]} {p}" for g, p in zip(ranking["grupo"], ranking["puesto"])],
        "Programa": ranking["programa"], "MTPH": ranking["mtph"], "N": ranking["n"],
        "Eficiencia (%)": ranking["media"].map(lambda v: numero(v, 1)),
        "IC 95 %": [f"{numero(a, 1)} a {numero(b, 1)}" for a, b in zip(ranking["ic_inf"], ranking["ic_sup"])],
    })


def pestana_programa(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Respeta todos los filtros de la barra. Eficiencia = promedio simple de los pares de cada "
               f"combinación de eBOE, programa y MTPH. Las combinaciones con N < {N_MINIMO} se ven tenues y no "
               f"entran en la superficie; el ranking y las frases usan solo las que tienen N ≥ {N_MINIMO_RANKING}.")
    try:
        emulsion = filtrar(tabla_emulsion_planta(planta_elegida()), filtros_elegidos())
    except ParametrosIncompletos:
        emulsion = pd.DataFrame(columns=["eboe", "fecha_hora", "aceite_emulsion"])
    pares = pares_con_emulsion(datos, emulsion)
    combos = combinaciones(pares)
    if combos.empty:
        st.info("No hay pares con programa y MTPH con estos filtros.")
        return

    st.subheader("Eficiencia según MTPH y programa")
    figura, claves = burbujas(combos)
    clave = f"burbujas_{st.session_state.get(CLAVE_VERSION_BURBUJAS, 0)}"
    evento = registro.grafico(figura, "Burbujas programa y MTPH", on_select="rerun",
                              selection_mode=("points", "box", "lasso"), key=clave)
    st.caption(f"Tamaño = N de pares ({numero(combos['n'].sum(), 0)} en total). Hacé clic en una burbuja para ver "
               "sus muestras y filtrar el resto de la pestaña. Para elegir varias: Shift+clic, o las herramientas "
               "de recuadro y lazo de la barra del gráfico (arriba a la derecha).")

    elegidas = combinaciones_de_puntos(puntos_elegidos(evento, clave), claves)
    st.button("Quitar selección", on_click=quitar_seleccion, disabled=not elegidas)
    if elegidas:
        muestras = de_combinaciones(pares, elegidas)
        nombres = ", ".join(f"eBOE {e} · {p} a {m} MTPH" for e, p, m in elegidas)
        st.markdown(f"**Muestras de {nombres}** (N {numero(len(muestras), 0)})")
        registro.tabla(tabla_muestras(muestras), "Muestras elegidas")
        st.info("El resto de la pestaña muestra solo las combinaciones elegidas.")
        combos = combinaciones(muestras)

    st.subheader("Resumen por eBOE")
    for eboe in sorted(combos["eboe"].unique()):
        st.markdown("- " + frase_programa(combos, eboe))

    st.subheader("Superficie y contorno")
    opciones = [f"eBOE {e}" for e in sorted(combos["eboe"].unique())]
    eboe = int(st.selectbox("eBOE", opciones, key="programa_eboe").removeprefix("eBOE "))
    mtph, programas, eficiencia, n = grilla(combos, eboe)
    if len(mtph) < 2 or (~pd.isna(eficiencia)).any(axis=0).sum() < 2:
        st.info(f"Para la superficie hacen falta al menos 2 MTPH y 2 programas con N ≥ {N_MINIMO} en el eBOE {eboe}.")
    else:
        izquierda, derecha = st.columns(2)
        registro.grafico(superficie(mtph, programas, eficiencia, n, eboe), f"Superficie eBOE {eboe}", donde=izquierda)
        registro.grafico(contorno(mtph, programas, eficiencia, n, eboe), f"Contorno eBOE {eboe}", donde=derecha)
        st.caption(f"Solo celdas con N ≥ {N_MINIMO}; las vacías quedan sin color. En el contorno, los puntos "
                   "marcan las celdas con datos: entre ellas el color es interpolado.")

    st.subheader(f"Mejores y peores combinaciones (solo combinaciones con N ≥ {N_MINIMO_RANKING})")
    ranking = mejores_y_peores(combos)
    if ranking.empty:
        st.info(f"No hay combinaciones con N ≥ {N_MINIMO_RANKING}.")
        return
    eboes = sorted(ranking["eboe"].unique())
    for columna, eboe in zip(st.columns(len(eboes)), eboes):
        columna.markdown(f"**eBOE {eboe}**")
        registro.tabla(tabla_ranking(ranking[ranking["eboe"] == eboe]), f"Ranking eBOE {eboe}", donde=columna)


def bloque_uso_programas(datos: pd.DataFrame, registro: Descargas) -> None:
    """Cuánto se usa cada programa y cómo sale rallada la fruta en cada uno, por eBOE."""
    st.subheader("Uso de programas")
    st.caption("Con los filtros de la barra (no con la selección de burbujas). \"Del tiempo\" = % de pares "
               "entrada/salida, que son aproximadamente una muestra por hora. El rallado es el de la fruta de salida.")
    pares = pares_con_emulsion(datos, pd.DataFrame(columns=["eboe", "fecha_hora", "aceite_emulsion"]))
    uso = uso_de_programas(pares)
    if uso.empty:
        st.info("No hay pares con programa con estos filtros.")
        return
    for eboe in sorted(uso["eboe"].unique()):
        st.markdown("- " + frase_uso(pares, eboe))
    registro.grafico(barras_uso(uso), "Uso de programas")

    st.markdown("##### Rallado de la fruta de salida por programa")
    rallado = rallado_por_programa(pares)
    if rallado.empty:
        st.info("No hay pares con rallado cargado con estos filtros.")
        return
    eboes = sorted(rallado["eboe"].unique())
    for columna, eboe in zip(st.columns(len(eboes)), eboes):
        sub = rallado[rallado["eboe"] == eboe]
        columna.markdown(f"**eBOE {eboe}** · N {numero(sub['n'].sum(), 0)}")
        registro.tabla(pd.DataFrame({
            "Programa": sub["programa"], "N": sub["n"].map(lambda v: numero(v, 0)),
            "% Ok": sub["pct_ok"].map(lambda v: numero(v, 1)),
            "% Poco rallada": sub["pct_poco"].map(lambda v: numero(v, 1)),
            "% Sobre rallada": sub["pct_sobre"].map(lambda v: numero(v, 1)),
        }), f"Rallado eBOE {eboe}", donde=columna)
    avisar_n_chico({f"eBOE {f['eboe']} {f['programa']}": f["n"] for f in rallado.to_dict("records")})


# ---------------------------------------------------------------- pestaña Proceso

SIN_EMULSION = pd.DataFrame(columns=["eboe", "fecha_hora", "aceite_emulsion"])
COLOR_DELTA = {+1: "normal", -1: "inverse", 0: "off"}   # normal: verde si sube; inverse: verde si baja; off: gris


def valor_con_unidad(valor, unidad: str, decimales: int) -> str:
    if valor is None or pd.isna(valor):
        return "—"
    return f"{numero(valor, decimales)} {unidad}"


def texto_diferencia(diferencia, unidad: str, decimales: int) -> str | None:
    """"+0,20 kg/MT", "-2,8 puntos" (los % se comparan en puntos). Con signo ASCII para st.metric."""
    if diferencia is None or pd.isna(diferencia):
        return None
    signo = "+" if diferencia >= 0 else "-"
    return f"{signo}{numero(abs(diferencia), decimales)} {'puntos' if unidad == '%' else unidad}"


# Títulos cortos para las tarjetas por eBOE (van varias en poco ancho): la unidad va en el título y el
# valor queda solo con el número, así no se corta.
TITULOS_CORTOS = {"aceite_entrada": "Entrada", "aceite_salida": "Salida", "mtph": "MTPH", "eficiencia": "Eficiencia"}


def tarjetas(donde, valores: dict, indicadores, por_fila: int, comparacion: dict | None = None,
             cortas: bool = False) -> None:
    """Una st.metric por indicador, en filas de `por_fila`. Con comparacion ({clave: fila de
    comparar_resumenes}), cada tarjeta muestra la diferencia: verde si mejora, roja si empeora.
    cortas: título corto con la unidad ("Entrada (kg/MT)") y el valor sin unidad."""
    columnas = []
    for i, (clave, nombre, unidad, decimales, sentido) in enumerate(indicadores):
        if i % por_fila == 0:
            columnas = donde.columns(por_fila)
        fila = (comparacion or {}).get(clave)
        delta = texto_diferencia(fila["diferencia"], unidad, decimales) if fila is not None else None
        if cortas:
            corto = TITULOS_CORTOS.get(clave, nombre)
            titulo = corto if corto == unidad else f"{corto} ({unidad})"
            valor = valor_con_unidad(valores.get(clave), "", decimales).strip()
        else:
            titulo, valor = nombre, valor_con_unidad(valores.get(clave), unidad, decimales)
        columnas[i % por_fila].metric(titulo, valor, delta=delta, delta_color=COLOR_DELTA[sentido])


def mostrar_resumen(donde, resumen: dict, comparacion: pd.DataFrame | None = None, ancho_completo: bool = True) -> None:
    """Tarjetas por eBOE y totales de un resumen_proceso(); con comparacion, con las diferencias."""
    def de_seccion(seccion):
        if comparacion is None:
            return None
        sub = comparacion[comparacion["seccion"] == seccion]
        return {f["clave"]: f for f in sub.to_dict("records")}

    totales = resumen["totales"]
    donde.markdown("**Totales del período**")
    tarjetas(donde, totales, INDICADORES_TOTALES, 3, de_seccion("Totales"))
    donde.caption(f"Eficiencia: {numero(totales['n_pares'], 0)} pares, "
                  f"{'ponderada por toneladas' if totales['ponderada'] else 'promedio simple (sin producción)'}. "
                  f"Fruta y aceite: {numero(totales['n_turnos'], 0)} turnos con producción; rendimiento con los "
                  f"{numero(totales['n_turnos_rendimiento'], 0)} que tienen fruta y aceite envasado.")

    donde.markdown("**Por eBOE**")
    columnas = donde.columns(len(resumen["eboes"])) if ancho_completo else [donde] * len(resumen["eboes"])
    for columna, eboe in zip(columnas, resumen["eboes"]):
        caja = columna.container(border=True)
        caja.markdown(f"**eBOE {eboe['eboe']}** · N {numero(eboe['n'], 0)} pares")
        tarjetas(caja, eboe, INDICADORES_EBOE, 2 if ancho_completo else 4, de_seccion(f"eBOE {eboe['eboe']}"),
                 cortas=True)
        caja.caption("Aceite y eficiencia " + ("ponderados por toneladas" if eboe["ponderada"] else
                                              "con promedio simple (sin producción)") + "; MTPH promedio simple.")
    avisar_n_chico({f"eBOE {e['eboe']}": e["n"] for e in resumen["eboes"]})


def mostrar_comparacion(resumen_a: dict, resumen_b: dict, comparacion: pd.DataFrame) -> None:
    """Período A y B lado a lado, fila por fila: cada fila de tarjetas de A queda a la altura de la de B
    (si se dibujaran en dos columnas largas, las de B, con la diferencia, son más altas y los bloques de
    eBOE quedan desplazados en diagonal)."""
    def por_clave(seccion):
        sub = comparacion[comparacion["seccion"] == seccion]
        return {f["clave"]: f for f in sub.to_dict("records")}

    def fila(valores_a, valores_b, indicadores, seccion, cortas=False):
        lado_a, lado_b = st.columns(2, gap="large")
        tarjetas(lado_a, valores_a, indicadores, len(indicadores), cortas=cortas)
        tarjetas(lado_b, valores_b, indicadores, len(indicadores), por_clave(seccion), cortas=cortas)

    st.markdown("**Totales del período**")
    totales_a, totales_b = resumen_a["totales"], resumen_b["totales"]
    for inicio in range(0, len(INDICADORES_TOTALES), 3):
        fila(totales_a, totales_b, INDICADORES_TOTALES[inicio:inicio + 3], "Totales")

    eboes_a = {e["eboe"]: e for e in resumen_a["eboes"]}
    eboes_b = {e["eboe"]: e for e in resumen_b["eboes"]}
    for eboe in sorted(set(eboes_a) | set(eboes_b)):
        valores_a, valores_b = eboes_a.get(eboe, {"n": 0}), eboes_b.get(eboe, {"n": 0})
        titulo_a, titulo_b = st.columns(2, gap="large")
        titulo_a.markdown(f"**eBOE {eboe}** · N {numero(valores_a['n'], 0)} pares")
        titulo_b.markdown(f"**eBOE {eboe}** · N {numero(valores_b['n'], 0)} pares")
        fila(valores_a, valores_b, INDICADORES_EBOE, f"eBOE {eboe}", cortas=True)
    avisar_n_chico({f"eBOE {e} ({nombre})": r.get(e, {"n": 0})["n"]
                    for nombre, r in (("A", eboes_a), ("B", eboes_b)) for e in sorted(set(eboes_a) | set(eboes_b))})


def resumen_de(filtros) -> dict:
    """resumen_proceso() con los análisis y la producción de la planta elegida, con esos filtros."""
    planta_id = planta_elegida()
    pares = pares_con_emulsion(filtrar(tabla_planta(planta_id), filtros), SIN_EMULSION)
    produccion = filtrar_produccion(produccion_planta(planta_id), filtros)
    return resumen_proceso(pares, toneladas_planta(planta_id), produccion)


def tabla_resumen(resumen: dict) -> pd.DataFrame:
    filas = [{"Sección": "Totales", "Indicador": nombre, "Valor": valor_con_unidad(resumen["totales"].get(clave), unidad, dec)}
             for clave, nombre, unidad, dec, _ in INDICADORES_TOTALES]
    for eboe in resumen["eboes"]:
        filas += [{"Sección": f"eBOE {eboe['eboe']}", "Indicador": nombre,
                   "Valor": valor_con_unidad(eboe.get(clave), unidad, dec)} for clave, nombre, unidad, dec, _ in INDICADORES_EBOE]
        filas.append({"Sección": f"eBOE {eboe['eboe']}", "Indicador": "Pares", "Valor": numero(eboe["n"], 0)})
    return pd.DataFrame(filas)


def tabla_comparacion(comparacion: pd.DataFrame, nombre_a: str, nombre_b: str) -> pd.DataFrame:
    return pd.DataFrame({
        "Sección": comparacion["seccion"], "Indicador": comparacion["indicador"], "Unidad": comparacion["unidad"],
        nombre_a: [numero(v, d) for v, d in zip(comparacion["valor_a"], comparacion["decimales"])],
        nombre_b: [numero(v, d) for v, d in zip(comparacion["valor_b"], comparacion["decimales"])],
        "Diferencia": [numero(v, d) for v, d in zip(comparacion["diferencia"], comparacion["decimales"])],
        "Resultado": comparacion["evaluacion"].map({"mejora": "Mejora", "empeora": "Empeora", "igual": "Igual",
                                                    "neutro": "Neutro (depende de la fruta)"}).fillna("—"),
    })


def pestana_proceso(registro: Descargas) -> None:
    filtros = filtros_elegidos()
    st.caption("Los análisis respetan todos los filtros de la barra. La producción (fruta procesada y aceite "
               "envasado) es de la planta: respeta fechas, turno y proceso, pero no eBOE ni programa. Rendimiento "
               "y eficiencia del proceso se calculan con totales del período (Σ aceite envasado / Σ fruta "
               "procesada), no como promedio de turnos, porque por turno hay desfase de envasado. Solo cuentan los "
               "registros de producción aprobados.")
    sin_aprobar = turnos_sin_aprobar(produccion_con_borradores(planta_elegida()), filtros)
    if sin_aprobar:
        st.warning(f"{sin_aprobar} turno{'s' if sin_aprobar > 1 else ''} sin aprobar, no incluido"
                   f"{'s' if sin_aprobar > 1 else ''} (se aprueban en la página Producción).")
    resumen = resumen_de(filtros)
    if not resumen["eboes"]:
        st.info("No hay pares entrada/salida con estos filtros.")
    else:
        mostrar_resumen(st, resumen)
        with st.expander("Tabla del período"):
            registro.tabla(tabla_resumen(resumen), "Resumen del período")

    st.subheader("Comparación de dos períodos")
    op = opciones(tabla_planta(planta_elegida()), ultima_fecha_planta(planta_elegida()))
    periodos = [a for a in atajos(op) if a != ATAJO_PERSONALIZADO]
    if len(periodos) < 2:
        st.info("Hace falta más de un período con datos para comparar.")
        return
    por_defecto = [periodos.index(p) if p in periodos else i for i, p in enumerate(("Temporada 2025", "Temporada 2026"))]
    columna_a, columna_b = st.columns(2)
    nombre_a = columna_a.selectbox("Período A", periodos, index=por_defecto[0], key="proceso_periodo_a")
    nombre_b = columna_b.selectbox("Período B", periodos, index=por_defecto[1], key="proceso_periodo_b")
    base = replace(filtros_sin_periodo(filtros), procesos=[])
    resumenes = {}
    for nombre in (nombre_a, nombre_b):
        desde, hasta = rango_de_atajo(nombre, op)
        resumenes[nombre] = (resumen_de(replace(base, desde=desde, hasta=hasta)), desde, hasta)
    st.caption("Cada período usa los filtros de eBOE, turno y programa de la barra (no el de fechas ni el de "
               "proceso). La diferencia es B − A: verde si mejora, rojo si empeora. Mejor si sube: eficiencias, "
               "rendimiento, aceite en fruta de entrada, fruta procesada, aceite envasado y MTPH; mejor si baja: "
               "aceite en fruta de salida.")
    (resumen_a, desde_a, hasta_a), (resumen_b, desde_b, hasta_b) = resumenes[nombre_a], resumenes[nombre_b]
    if not resumen_a["eboes"] or not resumen_b["eboes"]:
        st.info("Uno de los dos períodos no tiene pares con estos filtros.")
        return
    comparacion = comparar_resumenes(resumen_a, resumen_b)
    columna_a.caption(f"Del {desde_a:%d/%m/%Y} al {hasta_a:%d/%m/%Y}")
    columna_b.caption(f"Del {desde_b:%d/%m/%Y} al {hasta_b:%d/%m/%Y}")
    mostrar_comparacion(resumen_a, resumen_b, comparacion)
    with st.expander("Tabla de la comparación"):
        registro.tabla(tabla_comparacion(comparacion, nombre_a, nombre_b), f"Comparación {nombre_a} vs {nombre_b}")


# ---------------------------------------------------------------- página

st.set_page_config(page_title="Análisis", layout="wide")
cartel_modo_prueba()
st.title("Análisis de aceite y eficiencia")

datos = barra_filtros()
if filtros_elegidos() is None:      # la planta no tiene análisis: la barra ya lo avisó
    st.stop()

def pestana_programa_completa(registro: Descargas) -> None:
    fuente = st.radio("Fuente del programa y la MTPH", FUENTES, horizontal=True, key="programa_fuente")
    con_datos = con_fuente(datos, fuente)
    if fuente == "PLC":
        sin_plc = con_datos["programa"].map(numero_programa).isna() & (con_datos["lugar"] == "Entrada")
        st.caption("PLC: programa y TON calculadas de la lectura del PLC más cercana del mismo eBOE (± 10 min). Las "
                   "muestras sin lectura del PLC o con un programa del PLC que no es N0–N9 (por ejemplo \"9\") no "
                   f"entran: {numero(sin_plc.sum(), 0)} de {numero((con_datos['lugar'] == 'Entrada').sum(), 0)} "
                   "muestras de entrada. El filtro de programa de la barra usa el programa del laboratorio.")
    else:
        st.caption("Laboratorio: programa y Ton/H anotados en la planilla (la Ton/H es el setpoint).")
    pestana_programa(con_datos, registro)
    bloque_uso_programas(con_datos, registro)


PESTANAS = {
    "Período": lambda registro: pestana_periodo(datos, registro),
    "Temporadas": pestana_temporadas,
    "Programa y MTPH": pestana_programa_completa,
    "Proceso": pestana_proceso,
    "Factores": pestana_factores,
}
# Con key y on_change, cada pestaña sabe si está abierta (.open): solo se dibuja la abierta.
# bind="query-params": la pestaña queda en la URL (?pestana_analisis=Proceso) y se puede compartir el enlace.
for (nombre, dibujar), pestana in zip(PESTANAS.items(), st.tabs(list(PESTANAS), key="pestana_analisis",
                                                                    on_change="rerun", bind="query-params")):
    if pestana.open:
        with pestana:
            arriba = st.container()             # "Descargar todo" va arriba, pero se arma al final
            registro = Descargas(nombre)
            dibujar(registro)
            registro.boton_todo(arriba)
