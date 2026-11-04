"""Página Análisis: aceite en fruta, eficiencia del eBOE y emulsión con los filtros globales.

Pestaña Período: análisis 1 a 4 de la sección 9 de docs/mapeo_ledesma.md (histograma, boxplot,
estadística de Salida y límites IQR), con todos los filtros.
Pestaña Temporadas: análisis 8 a 10 (ANOVA, Tukey, intervalos al 95 % y boxplot por temporada);
ignora fechas y procesos, y respeta eBOE, turno y programa.
Pestaña Programa y MTPH: análisis 6 y 7 (burbujas, superficie y contorno), con todos los filtros y
filtrado cruzado al hacer clic en una burbuja.
Solo muestra: la estadística está en core/, los gráficos en app/graficos.py y los datos
filtrados vienen de app/filtros.py.
"""

import pandas as pd
import streamlit as st

from core.analisis import filtrar, filtros_sin_periodo, pares_con_emulsion
from core.comparaciones import comparar_grupos, frase_resultado
from core.estadistica import limites_iqr, media_ponderada_por_turno, resumen_descriptivo
from core.formato import numero, texto_p
from core.parametros import ParametrosIncompletos
from core.programa import (N_MINIMO, combinaciones, de_combinaciones, frase_programa, grilla,
                           mejores_y_peores)
from core.regresion import frase_pendiente, regresiones_por_grupo
from filtros import (barra_filtros, filtros_elegidos, planta_elegida, tabla_emulsion_planta, tabla_planta,
                     toneladas_planta)
from graficos import (boxplot, boxplot_temporadas, burbujas, contorno, dispersion_por_eboe, histograma,
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


def pestana_periodo(datos: pd.DataFrame) -> None:
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
    st.plotly_chart(histograma(datos), width="stretch")

    st.subheader("Aceite en fruta por eBOE y lugar")
    st.plotly_chart(boxplot(datos), width="stretch")
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
        st.dataframe(tabla(filas, decimales_ee=True), hide_index=True)
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
    st.dataframe(tabla(filas), hide_index=True)
    avisar_n_chico(grupos)
    st.caption("LI = Q1 − 1,5·IQR · LS = Q3 + 1,5·IQR · cuartiles con el método de Minitab.")


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


def bloque_temporadas(titulo: str, datos: pd.DataFrame, variable: str, sujeto: str, articulo: str,
                      unidad: str, unidad_frase: str, titulo_y: str, decimales: int) -> None:
    """Un bloque de la pestaña: frase, intervalos + boxplot, tabla ANOVA, Levene/Welch y Tukey."""
    st.subheader(titulo)
    try:
        resultado = comparar_grupos(datos, variable, "temporada")
    except ValueError as error:
        st.warning(f"No se puede comparar: {error}")
        return

    st.markdown(f"**{frase_resultado(resultado, sujeto, articulo, unidad_frase, decimales)}**")
    izquierda, derecha = st.columns(2)
    izquierda.plotly_chart(intervalos(resultado["medias"], titulo_y, unidad, decimales), width="stretch")
    derecha.plotly_chart(boxplot_temporadas(datos, variable, titulo_y, unidad, decimales), width="stretch")
    medias = resultado["medias"]
    ic = ("desvío combinado (varianzas iguales)" if resultado["ic_metodo"] == "combinado"
          else "desvío de cada temporada (varianzas distintas)")
    st.caption("N: " + " · ".join(f"{g} = {numero(n, 0)}" for g, n in zip(medias["grupo"], medias["n"]))
               + f". IC de la media calculado con el {ic}.")
    avisar_n_chico({f"temporada {g}": n for g, n in zip(medias["grupo"], medias["n"])})

    st.dataframe(tabla_anova(resultado, decimales), hide_index=True)
    levene = f"Levene (¿varianzas iguales?): {texto_p(resultado['levene']['p'])}"
    if resultado["welch"]:
        w = resultado["welch"]
        st.caption(f"{levene} → las varianzas difieren. ANOVA de Welch: F = {numero(w['F'])}, "
                   f"GL = {numero(w['gl_num'], 0)} y {numero(w['gl_den'], 1)}, {texto_p(w['p'])}. "
                   "Ese es el p que vale para decir si las medias difieren.")
    else:
        st.caption(f"{levene} → no hay evidencia de que difieran; vale el p del ANOVA.")
    with st.expander("Comparaciones de Tukey"):
        st.dataframe(tabla_tukey(resultado, decimales, unidad), hide_index=True)
        st.caption("Diferencia = temporada posterior − anterior, en las unidades de la variable. "
                   "Tukey supone varianzas iguales.")


def bloque_caudal(entradas: pd.DataFrame) -> None:
    """Caudal de fase pesada por temporada (¿bajó el agua?): frase, intervalos y medias."""
    st.markdown("##### Caudal de fase pesada")
    try:
        resultado = comparar_grupos(entradas, "caudal_fase_pesada", "temporada")
    except ValueError as error:
        st.warning(f"No se puede comparar el caudal: {error}")
        return
    st.markdown(f"**{frase_resultado(resultado, 'El caudal de fase pesada', 'el', 'm³/h', 2)}**")
    izquierda, derecha = st.columns(2)
    izquierda.plotly_chart(intervalos(resultado["medias"], "Caudal fase pesada (m³/h)", "m³/h", 2), width="stretch")
    medias = resultado["medias"]
    derecha.dataframe(pd.DataFrame({
        "Temporada": medias["grupo"], "N": medias["n"].map(lambda n: numero(n, 0)),
        "Media (m³/h)": medias["media"].map(numero), "Desvío": medias["desvio"].map(numero),
        "IC 95 %": [f"{numero(a)} a {numero(b)}" for a, b in zip(medias["ic_inf"], medias["ic_sup"])],
    }), hide_index=True)
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


def bloque_agua_licor(fruta: pd.DataFrame, emulsion: pd.DataFrame) -> None:
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
        st.plotly_chart(dispersion_por_eboe(datos, x, "eficiencia_pct", por_eboe, titulo_x, "Eficiencia eBOE (%)",
                                            unidad_x, "puntos", 2, 1), width="stretch")
        avisar_n_chico({f"eBOE {f['eboe']} {f['temporada']}": f["n"] for f in por_eboe.to_dict("records")})
        with st.expander("Rectas por eBOE y temporada"):
            st.dataframe(tabla_rectas(por_eboe, unidad_x), hide_index=True)


def pestana_temporadas() -> None:
    filtros = filtros_sin_periodo(filtros_elegidos())
    st.info("Esta pestaña compara temporadas: **no usa el filtro de fechas ni el de proceso** (toma todas "
            f"las fechas y procesos). Sí respeta eBOE, turno y programa. Filtros aplicados: {texto_filtros(filtros)}.")

    fruta = filtrar(tabla_planta(planta_elegida()), filtros)
    entradas = fruta[fruta["lugar"] == "Entrada"]
    pares = entradas.dropna(subset=["eficiencia_par"]).assign(eficiencia_pct=lambda d: d["eficiencia_par"] * 100)

    bloque_temporadas("Aceite en fruta de entrada", entradas, "aceite_fruta", "El aceite en fruta de entrada",
                      "el", "kg/MT", "kg/MT", "Aceite en fruta entrada (kg/MT)", 2)
    bloque_temporadas("Eficiencia eBOE", pares, "eficiencia_pct", "La eficiencia", "la", "%", "puntos",
                      "Eficiencia eBOE (%)", 1)
    try:
        emulsion = filtrar(tabla_emulsion_planta(planta_elegida()), filtros)
    except ParametrosIncompletos as error:
        st.subheader("Aceite en emulsión")
        st.warning(str(error))
        return
    bloque_temporadas("Aceite en emulsión", emulsion, "aceite_emulsion", "El aceite en emulsión", "el", "%",
                      "puntos", "Aceite en emulsión (%)", 2)
    if not emulsion.empty:
        st.markdown("##### Distribución del aceite en emulsión por temporada")
        st.plotly_chart(histograma_grupos(emulsion, "aceite_emulsion", "temporada", "Aceite en emulsión (%)", "%"),
                        width="stretch")
    bloque_caudal(entradas)
    bloque_agua_licor(fruta, emulsion)
    st.caption("Promedios simples por muestra: el ANOVA compara muestras, no se pondera por toneladas.")


# ---------------------------------------------------------------- pestaña Programa y MTPH

CLAVE_VERSION_BURBUJAS = "_version_burbujas"   # cambiarla crea un gráfico nuevo, sin selección


def quitar_seleccion() -> None:
    st.session_state[CLAVE_VERSION_BURBUJAS] = st.session_state.get(CLAVE_VERSION_BURBUJAS, 0) + 1


def combinaciones_elegidas(evento, claves: list[list[tuple]]) -> list[tuple]:
    """(eboe, programa, mtph) de las burbujas elegidas con clic, recuadro o lazo."""
    elegidas = []
    for punto in evento.selection.points if evento else []:
        traza, indice = punto.get("curve_number"), punto.get("point_index", punto.get("point_number"))
        if traza is not None and indice is not None and traza < len(claves) and indice < len(claves[traza]):
            elegidas.append(claves[traza][indice])
    return sorted(set(elegidas))


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


def pestana_programa(datos: pd.DataFrame) -> None:
    st.caption("Respeta todos los filtros de la barra. Eficiencia = promedio simple de los pares de cada "
               f"combinación de eBOE, programa y MTPH. Las combinaciones con N < {N_MINIMO} se ven tenues y no "
               "entran en la superficie, el ranking ni las frases.")
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
    evento = st.plotly_chart(figura, width="stretch", on_select="rerun", selection_mode=("points", "box", "lasso"),
                             key=f"burbujas_{st.session_state.get(CLAVE_VERSION_BURBUJAS, 0)}")
    st.caption(f"Tamaño = N de pares ({numero(combos['n'].sum(), 0)} en total). Hacé clic en una burbuja "
               "(Shift+clic, recuadro o lazo para varias) para ver sus muestras y filtrar el resto de la pestaña.")

    elegidas = combinaciones_elegidas(evento, claves)
    if elegidas:
        muestras = de_combinaciones(pares, elegidas)
        nombres = ", ".join(f"eBOE {e} · {p} a {m} MTPH" for e, p, m in elegidas)
        st.markdown(f"**Muestras de {nombres}** (N {numero(len(muestras), 0)})")
        st.dataframe(tabla_muestras(muestras), hide_index=True)
        st.button("Quitar selección", on_click=quitar_seleccion)
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
        izquierda.plotly_chart(superficie(mtph, programas, eficiencia, n, eboe), width="stretch")
        derecha.plotly_chart(contorno(mtph, programas, eficiencia, n, eboe), width="stretch")
        st.caption(f"Solo celdas con N ≥ {N_MINIMO}; las vacías quedan sin color. En el contorno, los puntos "
                   "marcan las celdas con datos: entre ellas el color es interpolado.")

    st.subheader(f"Mejores y peores combinaciones (N ≥ {N_MINIMO})")
    ranking = mejores_y_peores(combos)
    if ranking.empty:
        st.info(f"No hay combinaciones con N ≥ {N_MINIMO}.")
        return
    eboes = sorted(ranking["eboe"].unique())
    for columna, eboe in zip(st.columns(len(eboes)), eboes):
        columna.markdown(f"**eBOE {eboe}**")
        columna.dataframe(tabla_ranking(ranking[ranking["eboe"] == eboe]), hide_index=True)


# ---------------------------------------------------------------- página

st.set_page_config(page_title="Análisis", layout="wide")
st.title("Análisis de aceite y eficiencia")

datos = barra_filtros()
if filtros_elegidos() is None:      # la planta no tiene análisis: la barra ya lo avisó
    st.stop()

periodo, temporadas, programa = st.tabs(["Período", "Temporadas", "Programa y MTPH"])
with periodo:
    pestana_periodo(datos)
with temporadas:
    pestana_temporadas()
with programa:
    pestana_programa(datos)
