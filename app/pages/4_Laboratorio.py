"""Página Confiabilidad del laboratorio: controles sobre los datos crudos de la titulación Scott.

Usa la barra de filtros global. Arriba, un resumen con los hallazgos principales (para el informe);
debajo, una pestaña por bloque: gramaje y mL, formas de pesar, gramaje y resultado, precisión,
dígitos, entradas repetidas, analistas y normalidad del titulante. Solo se dibuja la pestaña abierta;
cada gráfico tiene botón PNG, cada tabla Excel y cada pestaña "Descargar todo".
Solo muestra: la lógica está en core/laboratorio.py.
"""

from dataclasses import replace

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba

from core.analisis import filtrar
from core.formato import numero
from core.laboratorio import (DIAS_MISMA_NORMALIDAD, ERROR_MAXIMO_PCT, N_MINIMO_DIGITOS, PESO_FIJO, POSICIONES,
                              VOL_MAXIMO_ML, agregar_ml_por_g, carta_normalidad, comparar_analistas, comparar_formas,
                              repetidas_por_mes, curva_kde, error_de_lectura, forma_de_pesar, forma_distribucion,
                              frase_analistas, frase_digitos, frase_forma, frase_formas, frase_gramaje,
                              frase_periodos, frase_precision, gramaje_vs_resultado, entradas_repetidas_grupos,
                              muestras_vol_alto, normalidad_diaria, periodos_misma_normalidad, porcentaje_fijo,
                              precision_titulacion, preferencia_digitos, probabilidad_normal, referencia_planta,
                              resumen_hallazgos)
from core.mensual import eje_de_meses
from descargas import Descargas
from filtros import barra_filtros, filtros_elegidos, planta_elegida, tabla_planta
from graficos import (COLOR_DATOS, COLORES, GRIS, barras_por_lugar, boxplot_por_analista, columnas_mensuales,
                      dispersion_por_lugar, grafico_normalidad, grafico_probabilidad, histograma_forma,
                      histograma_grupos, intervalos, mapa_digitos)

LUGARES = ("Entrada", "Salida")
# clave, título, sujeto de la frase, unidad, decimales (tablas), decimales de la frase, plural, título del eje
VARIABLES = [
    ("peso_muestra_g", "Gramaje", "El gramaje", "g", 2, 1, False, "Peso de muestra (g)"),
    ("vol_ml", "mL gastados", "Los mL", "mL", 2, 0, True, "Volumen de titulante (mL)"),
    ("ml_por_g", "mL/g", "Los mL/g", "mL/g", 3, 2, True, "mL / g de muestra"),
]
CAUSA_COLA_SALIDA = "probablemente por los eventos de baja eficiencia (salidas con mucho aceite)"
NOTA_AD = ("Con N grande, Anderson-Darling detecta diferencias muy chicas; el gráfico de probabilidad muestra cuánto "
           "se aparta de la normal y dónde.")


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


def solo_lugar(tabla: pd.DataFrame, lugar: str) -> pd.DataFrame:
    return tabla[tabla["lugar"] == lugar]


# ---------------------------------------------------------------- resumen

@st.cache_data(ttl=600, show_spinner="Calculando el resumen…")
def calcular_resumen(datos: pd.DataFrame, datos_sin_eboe: pd.DataFrame) -> list[str]:
    """Las frases del resumen (en caché: la ANOVA de analistas tarda un poco)."""
    return resumen_hallazgos(
        porcentaje_fijo(datos.assign(todas="todas"), "todas"), porcentaje_fijo(datos, "analista"),
        comparar_formas(datos), precision_titulacion(datos), preferencia_digitos(datos, "centesimo"),
        repetidas_por_mes(datos_sin_eboe), comparar_analistas(solo_lugar(datos, "Salida"), "aceite_fruta"))


# ---------------------------------------------------------------- 1. gramaje y mL

def pestana_gramaje(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Referencia de la propia planta: la franja gris es el rango intercuartil (del 25 % al 75 % de las "
               "muestras) y la línea, la mediana. mL/g = mL gastados / gramaje: saca el efecto del gramaje y deja la "
               "variación real del aceite. En cada histograma, la curva continua es la normal y la punteada, la "
               "densidad real de los datos (KDE).")
    referencia = referencia_planta(datos, [v[0] for v in VARIABLES])
    nombres = {v[0]: f"{v[1]} ({v[3]})" for v in VARIABLES}
    with st.expander("Referencia de la planta (mediana e IQR)"):
        registro.tabla(con_coma(referencia.assign(variable=referencia["variable"].map(nombres)).rename(columns={
            "lugar": "Lugar", "variable": "Variable", "n": "N", "q1": "Q1", "mediana": "Mediana", "q3": "Q3",
            "iqr": "IQR"}), 3), "Referencia de la planta")
    avisar_n(len(datos))

    for clave, titulo, sujeto, unidad, decimales, dec_frase, plural, titulo_x in VARIABLES:
        st.subheader(titulo)
        for lugar in LUGARES:
            valores = solo_lugar(datos, lugar)[clave].dropna()
            if valores.empty:
                continue
            forma = forma_distribucion(valores)
            fila = referencia[(referencia["lugar"] == lugar) & (referencia["variable"] == clave)].iloc[0].to_dict()
            nombre = f"{titulo} de {lugar.lower()}"
            izquierda, derecha = st.columns(2)
            registro.grafico(histograma_forma(valores, titulo_x, unidad, COLORES[lugar], nombre, fila,
                                              curva_kde(valores), forma, decimales), f"Histograma {nombre}",
                             donde=izquierda)
            tabla, recta = probabilidad_normal(valores)
            registro.grafico(grafico_probabilidad(tabla, recta, titulo_x, COLORES[lugar], nombre, decimales),
                             f"Probabilidad normal {nombre}", donde=derecha)
            causa = CAUSA_COLA_SALIDA if lugar == "Salida" and clave != "peso_muestra_g" else None
            st.markdown(f"**{frase_forma(forma, f'{sujeto} de {lugar.lower()}', unidad, dec_frase, plural, causa)}**")
        st.caption(NOTA_AD)

    st.subheader("Por analista")
    izquierda, derecha = st.columns(2)
    registro.grafico(boxplot_por_analista(datos, "peso_muestra_g", "Peso de muestra (g)", "g"),
                     "Gramaje por analista", donde=izquierda)
    registro.grafico(boxplot_por_analista(datos, "vol_ml", "Volumen de titulante (mL)", "mL"),
                     "mL por analista", donde=derecha)

    st.subheader(f"Muestras con más de {numero(VOL_MAXIMO_ML, 0)} mL")
    altos = muestras_vol_alto(datos)
    if altos.empty:
        st.info(f"Ninguna muestra pasa de {numero(VOL_MAXIMO_ML, 0)} mL.")
    else:
        st.caption(f"{numero(len(altos), 0)} muestras. Más de 50 mL suele indicar que se volvió a llenar la bureta "
                   "en medio de la titulación o un error de carga: conviene revisar el dato.")
        registro.tabla(con_coma(columnas_muestras(altos), 2, **{"Normalidad (N)": 4}),
                       f"Muestras con más de {numero(VOL_MAXIMO_ML, 0)} mL")


# ---------------------------------------------------------------- 2. formas de pesar

def pestana_formas(datos: pd.DataFrame, banda: tuple, registro: Descargas) -> None:
    st.caption(f"Muestras de {numero(banda[0], 2)} a {numero(banda[1], 2)} g = \"5 g fijo\"; el resto, \"variable\" "
               "(se pesa lo que da la muestra). La banda se cambia en «Parámetros», arriba de la página.")
    tarjetas = st.columns(len(LUGARES))
    por_lugar = porcentaje_fijo(datos.assign(todas="todas"), "todas").set_index("lugar")
    for columna, lugar in zip(tarjetas, LUGARES):
        if lugar in por_lugar.index:
            fila = por_lugar.loc[lugar]
            columna.metric(f"{lugar}: a 5 g fijo", f"{numero(fila['pct_fijo'], 1)} %")
            columna.caption(f"{numero(fila['n_fijo'], 0)} de {numero(fila['n'], 0)} muestras")
    avisar_n(len(datos))

    for por, titulo in (("analista", "Analista"), ("turno", "Turno")):
        tabla = porcentaje_fijo(datos, por)
        st.subheader(f"Por {titulo.lower()}")
        registro.grafico(barras_por_lugar(tabla, por, titulo), f"5 g fijo por {titulo.lower()}")
        with st.expander("Tabla"):
            registro.tabla(con_coma(tabla.rename(columns={"lugar": "Lugar", por: titulo, "n": "N",
                                                          "n_fijo": "A 5 g fijo", "pct_fijo": "%"}), 1),
                           f"5 g fijo por {titulo.lower()}")
    st.subheader("Por mes")
    meses = porcentaje_fijo(datos, "mes")
    registro.grafico(barras_por_lugar(meses, "etiqueta", "Mes", eje_de_meses(meses["mes"])), "5 g fijo por mes")
    with st.expander("Tabla"):
        registro.tabla(con_coma(meses.drop(columns="mes").rename(columns={
            "lugar": "Lugar", "etiqueta": "Mes", "n": "N", "n_fijo": "A 5 g fijo", "pct_fijo": "%"}), 1),
            "5 g fijo por mes")


# ---------------------------------------------------------------- 3. ¿el gramaje cambia el resultado?

def pestana_gramaje_resultado(datos: pd.DataFrame, registro: Descargas) -> None:
    st.subheader("5 g fijo contra gramaje variable, el mismo día")
    st.caption("Para cada eBOE, lugar y día de producción con muestras de las dos formas: aceite promedio de las de 5 g "
               "menos el de las variables. Comparar dentro del mismo día y eBOE saca el efecto de la fruta y del "
               "proceso. Diferencia media de esos días con su IC del 95 % (prueba t emparejada).")
    formas = comparar_formas(datos)
    for fila in formas.to_dict("records"):
        st.markdown(f"**{frase_formas(fila)}**")
    con_dato = formas.dropna(subset=["diferencia"])
    if not con_dato.empty:
        medias = con_dato.rename(columns={"lugar": "grupo", "n_dias": "n", "diferencia": "media"})
        figura = intervalos(medias, "Diferencia 5 g − variable (kg/MT)", "kg/MT", 2, titulo_x="Lugar",
                            titulo="Diferencia media e IC del 95 %")
        figura.add_hline(y=0, line=dict(color=GRIS, width=1, dash="dot"))
        izquierda, derecha = st.columns([2, 3])
        registro.grafico(figura, "Diferencia 5 g contra variable", donde=izquierda)
        registro.tabla(con_coma(formas.rename(columns={
            "lugar": "Lugar", "n_dias": "Días", "n_fijo": "Muestras 5 g", "n_variable": "Muestras variables",
            "media_fijo": "Aceite 5 g (kg/MT)", "media_variable": "Aceite variable (kg/MT)",
            "diferencia": "Diferencia (kg/MT)", "ic_inf": "IC inferior", "ic_sup": "IC superior", "p": "p",
            "variable_p10": "Gramaje variable P10 (g)", "variable_p90": "Gramaje variable P90 (g)"}), 3, p=4),
            "Diferencia 5 g contra variable", donde=derecha)

    st.subheader("Aceite en fruta vs gramaje")
    st.caption("Si la titulación funciona bien, el aceite en fruta no debería depender de cuánta muestra se pesó.")
    rectas = gramaje_vs_resultado(datos)
    for fila in rectas.to_dict("records"):
        st.markdown(f"**{frase_gramaje(fila)}**")
    registro.grafico(dispersion_por_lugar(datos, "peso_muestra_g", "aceite_fruta", rectas, "Peso de muestra (g)",
                                          "Aceite en fruta (kg/MT)", "kg/MT"), "Gramaje vs aceite en fruta")
    with st.expander("Rectas"):
        registro.tabla(con_coma(rectas.rename(columns={
            "lugar": "Lugar", "n": "N", "pendiente": "Pendiente (kg/MT por g)", "ordenada": "Ordenada (kg/MT)",
            "r2": "R²", "p": "p", "ic_inf": "IC inferior", "ic_sup": "IC superior"}), 4), "Rectas gramaje")


# ---------------------------------------------------------------- 4. precisión

def pestana_precision(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Error relativo de lectura = 0,05 mL / (mL gastados − blanco): la mitad de la menor división de una "
               f"bureta de 0,1 mL sobre lo que se tituló. Con menos de 5 mL netos, el error pasa del "
               f"{numero(ERROR_MAXIMO_PCT, 0)} %.")
    tabla = precision_titulacion(datos)
    st.markdown(f"**{frase_precision(tabla)}**")
    tarjetas = st.columns(len(LUGARES))
    for columna, fila in zip(tarjetas, tabla.to_dict("records")):
        columna.metric(f"{fila['lugar']}: error mayor al {numero(ERROR_MAXIMO_PCT, 0)} %",
                       f"{numero(fila['pct_mayor'], 1)} %")
        columna.caption(f"{numero(fila['n_mayor'], 0)} de {numero(fila['n'], 0)} muestras · mediana "
                        f"{numero(fila['mediana'], 2)} %")
    avisar_n(len(datos))
    con_error = datos.assign(error_pct=error_de_lectura(datos))
    figura = histograma_grupos(con_error, "error_pct", "lugar", "Error relativo de lectura (%)", "%", 2,
                               colores=COLORES, curva=False)
    figura.add_vline(x=ERROR_MAXIMO_PCT, line=dict(color=COLOR_DATOS, width=1.5, dash="dot"),
                     annotation_text=f"{numero(ERROR_MAXIMO_PCT, 0)} %", annotation_position="top right")
    registro.grafico(figura, "Error de lectura")
    mayores = con_error[con_error["error_pct"] > ERROR_MAXIMO_PCT]
    if not mayores.empty:
        with st.expander(f"Muestras con error mayor al {numero(ERROR_MAXIMO_PCT, 0)} %"):
            lista = columnas_muestras(mayores).assign(**{"Error (%)": mayores["error_pct"].to_numpy()})
            registro.tabla(con_coma(lista, 2, **{"Normalidad (N)": 4}), "Muestras con error de lectura alto")


# ---------------------------------------------------------------- 5. dígitos

def pestana_digitos(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Si los mL se leen bien, cada dígito (0 a 9) aparece cerca del 10 % de las veces. Anotar de más el 0 "
               "y el 5 indica que se redondea la lectura; muchos más decimales pares que impares, una bureta "
               "graduada cada 0,2 mL. Prueba chi² contra la distribución uniforme, solo para analistas con al menos "
               f"{N_MINIMO_DIGITOS} muestras.")
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
                                             "exceso_0_5": "Exceso de 0 y 5 (%)", "digito_max": "Dígito más anotado",
                                             "exceso_max": "Exceso del más anotado (%)",
                                             "exceso_pares": "Exceso de pares (%)", "p_pares": "p pares",
                                             **{f"pct_{d}": f"% {d}" for d in range(10)}})
            for columna in ("Exceso de 0 y 5 (%)", "Exceso del más anotado (%)", "Exceso de pares (%)"):
                mostrada[columna] = mostrada[columna] * 100
            registro.tabla(con_coma(mostrada, 1, p=4, **{"p pares": 4}), f"Dígitos - {nombre}")


# ---------------------------------------------------------------- 6. entradas repetidas

def pestana_repetidas(datos_sin_eboe: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Muestras de Entrada con la misma fecha y hora, el mismo peso y los mismos mL en más de un eBOE. Cada "
               "eBOE tiene su propia muestra de entrada y es muy poco probable que frutas distintas den exactamente lo "
               "mismo: lo más probable es que se haya copiado el dato. Se miran todos los eBOE (no se aplica el "
               "filtro de eBOE).")
    st.info("Las entradas repetidas quedan marcadas para revisar: siguen en los cálculos, pero no en las comparaciones "
            "entre eBOE (salvo que se marque «Incluir entradas repetidas»).")
    grupos = entradas_repetidas_grupos(datos_sin_eboe)
    por_mes = repetidas_por_mes(datos_sin_eboe)
    entradas = int(por_mes["entradas"].sum())
    repetidas = int(por_mes["repetidas"].sum())
    uno, dos, tres = st.columns(3)
    uno.metric("Grupos repetidos", numero(len(grupos), 0))
    dos.metric("Muestras de entrada en esos grupos", numero(repetidas, 0))
    tres.metric("% de las muestras de entrada", f"{numero(100 * repetidas / entradas, 1) if entradas else '—'} %")
    tres.caption(f"N = {numero(entradas, 0)} muestras de entrada")
    avisar_n(entradas, "muestras de entrada")
    if por_mes.empty:
        return
    tabla = por_mes.assign(valor=por_mes["pct"], n=por_mes["entradas"])
    registro.grafico(columnas_mensuales(tabla, eje_de_meses(por_mes["mes"]), "% de muestras de entrada repetidas", "%",
                                        por_eboe=False), "Entradas repetidas por mes")
    with st.expander("Tabla por mes"):
        registro.tabla(con_coma(por_mes[["etiqueta", "entradas", "grupos", "repetidas", "pct"]].rename(columns={
            "etiqueta": "Mes", "entradas": "Muestras de entrada", "grupos": "Grupos", "repetidas": "Repetidas",
            "pct": "%"}), 1), "Entradas repetidas por mes")
    st.subheader("Grupos repetidos")
    if grupos.empty:
        st.info("No hay entradas repetidas con estos filtros.")
        return
    lista = grupos.assign(fecha_hora=grupos["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M")).drop(
        columns="fecha_produccion").rename(columns={
            "fecha_hora": "Fecha y hora", "peso_muestra_g": "Peso (g)", "vol_ml": "Volumen (mL)", "eboes": "eBOE",
            "n_eboes": "Cantidad de eBOE", "analistas": "Analista"})
    registro.tabla(con_coma(lista, 2, **{"Peso (g)": 3}), "Grupos repetidos")


# ---------------------------------------------------------------- 7. analistas

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
    anova = resultado["anova"].rename(columns={"fuente": "Fuente", "gl": "GL", "sc": "SC", "f": "F", "p": "p"})
    registro.tabla(con_coma(anova, 2, p=4, GL=0), f"ANOVA analistas - {titulo}", donde=derecha)
    registro.tabla(con_coma(medias.rename(columns={"grupo": "Analista", "n": "N", "media": "Media ajustada",
                                                   "ic_inf": "IC inferior", "ic_sup": "IC superior"}), decimales),
                   f"Medias analistas - {titulo}", donde=derecha)


def pestana_analistas(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Para no confundir al analista con la fruta o la temporada, se compara a cada analista contra los "
               "otros que midieron el mismo eBOE en la misma semana: ANOVA de dos factores (analista + eBOE × "
               "semana), solo con las semanas en que trabajaron 2 o más analistas. Ojo con el gramaje: si un "
               "analista pesa casi siempre 5 g, parte de su diferencia puede venir de ahí (pestaña «Gramaje y "
               "resultado»).")
    salidas = solo_lugar(datos, "Salida")
    pares = datos[(datos["lugar"] == "Entrada") & datos["eficiencia_par"].notna()]
    pares = pares.assign(eficiencia_pct=pares["eficiencia_par"] * 100)
    bloque_analistas(registro, "Aceite en fruta de salida", salidas, "aceite_fruta", "el aceite de salida", "kg/MT", 2)
    bloque_analistas(registro, "Eficiencia eBOE", pares, "eficiencia_pct", "la eficiencia", "%", 1, "puntos")


# ---------------------------------------------------------------- 8. normalidad

def pestana_normalidad(datos: pd.DataFrame, registro: Descargas) -> None:
    st.caption("Toda la planta usa la misma solución de bromato en cada momento: un punto por día de producción "
               f"(la normalidad más anotada ese día). En naranja, los períodos de más de {DIAS_MISMA_NORMALIDAD} días "
               "con la misma normalidad (posible falta de re-estandarización). Los límites de la carta I-MR salen de "
               "un valor por período (cuánto cambia la normalidad de una valoración a la siguiente); en rojo, los "
               "días fuera de esos límites.")
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


# ---------------------------------------------------------------- página

st.set_page_config(page_title="Confiabilidad del laboratorio", layout="wide")
cartel_modo_prueba()
st.title("Confiabilidad del laboratorio")

datos = barra_filtros()
filtros = filtros_elegidos()
if filtros is None:      # la planta no tiene análisis: la barra ya lo avisó
    st.stop()
if datos.empty:
    st.info("No hay muestras con estos filtros.")
    st.stop()

with st.expander("Parámetros"):
    desde_col, hasta_col, _ = st.columns([1, 1, 3])
    banda = (desde_col.number_input("5 g fijo: desde (g)", value=PESO_FIJO[0], step=0.05, format="%.2f",
                                    key="lab_fijo_desde"),
             hasta_col.number_input("5 g fijo: hasta (g)", value=PESO_FIJO[1], step=0.05, format="%.2f",
                                    key="lab_fijo_hasta"))
if banda[0] > banda[1]:
    st.warning("La banda de 5 g fijo tiene el mínimo mayor que el máximo: se usa la de siempre.")
    banda = PESO_FIJO

datos = agregar_ml_por_g(datos)
datos = datos.assign(forma=forma_de_pesar(datos, *banda))
datos_sin_eboe = filtrar(tabla_planta(planta_elegida()), replace(filtros, eboes=[]))

st.subheader("Resumen")
for frase in calcular_resumen(datos, datos_sin_eboe):
    st.markdown(f"- {frase}")

PESTANAS = {
    "Gramaje y mL": lambda registro: pestana_gramaje(datos, registro),
    "Formas de pesar": lambda registro: pestana_formas(datos, banda, registro),
    "Gramaje y resultado": lambda registro: pestana_gramaje_resultado(datos, registro),
    "Precisión": lambda registro: pestana_precision(datos, registro),
    "Dígitos": lambda registro: pestana_digitos(datos, registro),
    "Entradas repetidas": lambda registro: pestana_repetidas(datos_sin_eboe, registro),
    "Analistas": lambda registro: pestana_analistas(datos, registro),
    "Normalidad": lambda registro: pestana_normalidad(datos, registro),
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
