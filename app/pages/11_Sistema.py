"""Página Sistema: la mirada del sistema completo de recuperación de aceite (informe Brown 2023).

Usa la barra de filtros global. Una pestaña por análisis; solo se dibuja la abierta, cada gráfico tiene botón PNG,
cada tabla Excel y cada pestaña "Descargar todo".
- Volatilización: eficiencia eBOE (por turno y por día) y eficiencia de proceso (por día), en gráficos separados,
  contra la fase pesada específica o el rango de fase pesada de referencia (sin circularidad con la eficiencia).
- Fase pesada: caudal de fase pesada real (laboratorio) contra el requerido por el balance de aceite del licor;
  rango de fase pesada (gráfico Brown) por temporada, eBOE, día y turno; coherencia del muestreo del licor (indicador
  interno: no se usa en otros análisis y no se muestra en el modo presentación).
Solo muestra: la lógica está en core/fase_pesada.py y core/volatilizacion.py.
"""

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba
from core.analisis import filtrar, pares_con_emulsion
from core.inicio import SIN_EMULSION
from core.estadistica import es_outlier_iqr
from core.fase_pesada import CLAVES_TURNO, mediana_por, tabla_fase_pesada
from core.formato import numero
from core.parametros import ParametrosIncompletos
from core.proceso import filtrar_produccion, turnos_sin_aprobar
from core.repetidas import sin_entradas_repetidas
from core.volatilizacion import (HIPOTESIS, RANGOS_FP, eficiencia_eboe_por_turno, eficiencias_por_dia,
                                 medias_por_rango)
from descargas import Descargas
from filtros import (barra_filtros, casilla_repetidas, filtros_elegidos, parametros_fase_pesada_planta,
                     planta_elegida, produccion_con_borradores, produccion_planta, tabla_emulsion_planta,
                     toneladas_planta)
from graficos import boxplot_temporadas, dispersion_por_temporada, intervalos_agrupados, lineas_en_el_tiempo
from presentacion import en_presentacion

st.set_page_config(page_title="Sistema", layout="wide")
cartel_modo_prueba()
st.title("Sistema de recuperación de aceite")
COHERENCIA = "Coherencia del muestreo (licor)"
AYUDA_COHERENCIA = ("Caudal de fase pesada que explicaría el aceite medido en el licor. Si queda por debajo del real, la "
                    "muestra de licor sale más rica que el licor promedio (punto o momento de muestreo, dilución hasta "
                    "el tanque). Indicador interno, no es una pérdida.")
CIRCULARIDAD = ("El rango de fase pesada usa la eficiencia del mismo análisis, así que compararlo con la eficiencia daría "
                "una relación matemática: se muestra solo contra el gráfico Brown. Para comparar con la eficiencia "
                "(Volatilización) se usan el rango de referencia (eficiencia fija de la planta) y la fase pesada "
                "específica (m³/t).")
CON_COHERENCIA = not en_presentacion()     # indicador interno: no en el modo presentación
st.caption("Mirada del sistema completo: eBOE, emulsión, fase pesada y centrífugas (informe Brown 2023).")

datos = barra_filtros()
if filtros_elegidos() is None:      # la planta no tiene análisis: la barra ya lo avisó
    st.stop()


def aviso_n(cantidad: int, que: str) -> None:
    if cantidad < 3:
        st.warning(f"N < 3 en {que}: los valores no son representativos.")


def tabla_medianas(tabla: pd.DataFrame, claves: dict) -> pd.DataFrame:
    """Medianas de mediana_por() con nombres para la pantalla. claves: {columna: título}."""
    vista = tabla.rename(columns=claves)
    for columna in claves.values():
        if columna in ("Temporada", "eBOE"):
            vista[columna] = vista[columna].astype(str)
    return pd.DataFrame({**{t: vista[t] for t in claves.values()},
                         "Rango de fase pesada (%)": vista["rango_fp_pct"].map(lambda v: numero(v, 1)),
                         "Real (m³/h)": vista["real"].map(lambda v: numero(v, 2)),
                         "Requerido (m³/h)": vista["requerido"].map(lambda v: numero(v, 2)),
                         **({f"{COHERENCIA} (m³/h)": vista["coherente"].map(lambda v: numero(v, 2))}
                            if CON_COHERENCIA else {}),
                         "N análisis": vista["n"].map(lambda v: numero(v, 0) + (" ⚠️" if v < 3 else ""))})


def pestana_fase_pesada(registro: Descargas) -> None:
    try:
        parametros = parametros_fase_pesada_planta(planta_elegida())
    except ParametrosIncompletos as error:
        st.warning(str(error))
        return
    st.caption(
        "**Requerido**: caudal de fase pesada que lleva el licor al aceite objetivo "
        f"({numero(parametros['omega_licor_objetivo_pct'], 2)} %), con el balance de aceite del licor: aceite removido "
        f"de la fruta (MTPH × entrada × eficiencia), agua de los aspersores "
        f"({numero(parametros['caudal_aspersores_lh'], 0)} L/h) y aceite en la fase pesada que vuelve "
        f"({numero(parametros['omega_fp_pct'], 2)} %). **Real**: caudal que carga el laboratorio del BOE. **Rango de "
        "fase pesada** = (real − requerido) / requerido × 100: negativo, entra menos fase pesada que la requerida "
        "(gráfico Brown). Por turno y por día, mediana de los análisis.")
    st.caption(CIRCULARIDAD)
    emulsion = filtrar(tabla_emulsion_planta(planta_elegida()), filtros_elegidos())
    tabla = tabla_fase_pesada(pares_con_emulsion(datos, emulsion), parametros)
    validos = tabla.dropna(subset=["rango_fp_pct"])
    if validos.empty:
        st.info("No hay pares con caudal de fase pesada y MTPH con estos filtros.")
        return
    validos = validos.assign(outlier=es_outlier_iqr(validos["rango_fp_pct"]))

    columnas = st.columns(4)
    columnas[0].metric("Rango de fase pesada", f"{numero(validos['rango_fp_pct'].median(), 1)} %",
                       border=True, help=f"Mediana de los análisis. N = {len(validos)} análisis.")
    tarjetas = [(columnas[1], "caudal_fase_pesada", "Real (laboratorio)", ""),
                (columnas[2], "requerido", "Requerido (balance)", "")]
    if CON_COHERENCIA:
        tarjetas.append((columnas[3], "coherente", COHERENCIA, AYUDA_COHERENCIA + " "))
    for columna, clave, titulo, ayuda in tarjetas:
        valores = validos[clave].dropna()
        columna.metric(titulo, f"{numero(valores.median(), 2)} m³/h" if len(valores) else "—", border=True,
                       help=f"{ayuda}Mediana de los análisis. N = {len(valores)}.")
    aviso_n(len(validos), "el período")
    st.caption(f"{numero(int(validos['outlier'].sum()), 0)} análisis con el rango fuera de los límites IQR: se marcan "
               "en la tabla de análisis, no se excluyen.")

    st.markdown("##### Por temporada")
    por_temporada = mediana_por(validos, ["temporada"])
    registro.tabla(tabla_medianas(por_temporada, {"temporada": "Temporada"}), "Fase pesada por temporada")
    figura = boxplot_temporadas(validos, "rango_fp_pct", "Rango de fase pesada (%)", "%", 1)
    abajo, arriba_eje = validos["rango_fp_pct"].quantile([0.01, 0.99])
    margen = (arriba_eje - abajo) * 0.05
    figura.update_yaxes(range=[abajo - margen, arriba_eje + margen])
    registro.grafico(figura, "Rango de fase pesada por temporada")
    afuera = int(((validos["rango_fp_pct"] < abajo) | (validos["rango_fp_pct"] > arriba_eje)).sum())
    st.caption(f"Eje recortado al percentil 1–99 para ver la caja: {numero(afuera, 0)} análisis quedan fuera del eje "
               "(siguen contados en las medianas; los valores muy altos aparecen cuando el requerido es muy chico).")

    st.markdown("##### Por temporada y eBOE")
    incluir = casilla_repetidas("sistema_fp_repetidas")
    por_eboe = sin_entradas_repetidas(validos, incluir)
    if not incluir:
        st.caption("Sin las entradas repetidas en varios eBOE (posible copia), como en el resto de las comparaciones "
                   "entre eBOE.")
    registro.tabla(tabla_medianas(mediana_por(por_eboe, ["temporada", "eboe"]),
                                  {"temporada": "Temporada", "eboe": "eBOE"}), "Fase pesada por temporada y eBOE")

    st.markdown("##### Por día de producción")
    dias = mediana_por(validos, ["fecha_produccion"])
    series = {"real": ("Real (laboratorio)", "n"), "requerido": ("Requerido (balance)", "n")}
    if CON_COHERENCIA:
        series["coherente"] = (COHERENCIA, "n")
    registro.grafico(lineas_en_el_tiempo(dias, "fecha_produccion", series, "Caudal de fase pesada (m³/h)", "m³/h"),
                     "Caudal de fase pesada por día")
    st.caption("Mediana de los análisis de cada día. Informativo: estos caudales no generan alertas.")

    with st.expander("Por turno"):
        turnos = mediana_por(validos, CLAVES_TURNO).assign(
            fecha_produccion=lambda d: d["fecha_produccion"].dt.strftime("%d/%m/%Y"))
        registro.tabla(tabla_medianas(turnos, {"fecha_produccion": "Día", "turno": "Turno"}), "Fase pesada por turno")
    with st.expander("Análisis"):
        registro.tabla(pd.DataFrame({
            "Fecha y hora": validos["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"), "Turno": validos["turno"],
            "eBOE": validos["eboe"], "MTPH": validos["tph"].map(lambda v: numero(v, 1)),
            "Entrada (kg/MT)": validos["aceite_fruta"].map(numero),
            "Eficiencia (%)": (validos["eficiencia_par"] * 100).map(lambda v: numero(v, 1)),
            "Licor (%)": validos["aceite_emulsion"].map(numero),
            "Real (m³/h)": validos["caudal_fase_pesada"].map(numero),
            "Requerido (m³/h)": validos["requerido"].map(numero),
            **({f"{COHERENCIA} (m³/h)": validos["coherente"].map(numero)} if CON_COHERENCIA else {}),
            "Rango (%)": validos["rango_fp_pct"].map(lambda v: numero(v, 1)),
            "Outlier (IQR)": validos["outlier"].map({True: "sí", False: ""}),
            "Entrada repetida": validos["entrada_repetida"].map({True: "sí", False: ""})}),
            "Fase pesada por análisis")


def tabla_por_rango(medias: pd.DataFrame, que: str, decimales: int = 1) -> pd.DataFrame:
    """Media, IC 95 % y N por temporada y rango (core.volatilizacion.medias_por_rango) para la pantalla."""
    return pd.DataFrame({
        "Temporada": medias["temporada"].astype(str), "Rango": medias["grupo"],
        f"N {que}": medias["n"].map(lambda v: numero(v, 0) + (" ⚠️" if v < 3 else "")),
        "Media (%)": medias["media"].map(lambda v: numero(v, decimales)),
        "IC 95 %": [f"{numero(a, decimales)} a {numero(b, decimales)}" if pd.notna(a) else "—"
                    for a, b in zip(medias["ic_inf"], medias["ic_sup"])]})


def sin_ic_chico(medias: pd.DataFrame) -> pd.DataFrame:
    """Para el gráfico: los rangos con N < 3 van como punto, sin intervalo (su IC es enorme y aplasta el resto)."""
    chicos = medias["n"] < 3
    return medias.assign(ic_inf=medias["ic_inf"].mask(chicos), ic_sup=medias["ic_sup"].mask(chicos))


def pestana_volatilizacion(registro: Descargas) -> None:
    try:
        parametros = parametros_fase_pesada_planta(planta_elegida())
    except ParametrosIncompletos as error:
        st.warning(str(error))
        return
    st.info(HIPOTESIS)
    st.caption("El rango de fase pesada de la pestaña Fase pesada usa la eficiencia del mismo análisis: compararlo con "
               "la eficiencia daría una relación matemática. Acá se usan dos variables sin esa circularidad: el rango "
               f"de referencia (con la eficiencia fija en {numero(parametros['eficiencia_ref_rango_pct'], 0)} %, "
               "parámetro de la planta) y la fase pesada específica (caudal real / MTPH).")
    variable = st.radio("Variable de fase pesada", list(RANGOS_FP), horizontal=True, key="sistema_vol_variable",
                        format_func=lambda v: f"{RANGOS_FP[v]['nombre']} ({RANGOS_FP[v]['unidad']})")
    rango = RANGOS_FP[variable]
    nombre, unidad, decimales = rango["nombre"], rango["unidad"], rango["decimales"]
    tabla = tabla_fase_pesada(pares_con_emulsion(datos, SIN_EMULSION), parametros)
    filtros, planta = filtros_elegidos(), planta_elegida()
    toneladas = toneladas_planta(planta)
    turnos = eficiencia_eboe_por_turno(tabla, variable)
    dias = eficiencias_por_dia(tabla, variable, toneladas, filtrar_produccion(produccion_planta(planta), filtros))
    if turnos.dropna(subset=["rango"]).empty:
        st.info("No hay pares con caudal de fase pesada y MTPH con estos filtros.")
        return
    st.caption(f"Rangos: {', '.join(rango['etiquetas'])} {unidad} (cada uno incluye su límite inferior). El valor de un "
               "turno o de un día es la mediana de sus análisis.")

    st.markdown("##### Eficiencia eBOE")
    izquierda, derecha = st.columns(2)
    registro.grafico(dispersion_por_temporada(dias, "valor", "eficiencia_eboe", nombre, "Eficiencia eBOE", unidad, "%",
                                              decimales, 1, n="n_pares"),
                     f"Eficiencia eBOE por día vs {nombre.lower()}", donde=izquierda)
    medias_eboe = medias_por_rango(turnos, "eficiencia", variable)
    if not medias_eboe.empty:
        registro.grafico(intervalos_agrupados(sin_ic_chico(medias_eboe), "Eficiencia eBOE (%)", "%", 1, f"{nombre} ({unidad})"),
                         f"Eficiencia eBOE por rango de {nombre.lower()}", donde=derecha)
    st.caption("Puntos: un día de producción (eficiencia de los eBOE juntos, ponderada por las toneladas aprobadas de "
               "cada turno; tenues con N < 3 pares). Intervalos: media e IC 95 % de los turnos de cada rango (cada "
               "turno, la media de sus pares). Los rangos con N < 3 van sin intervalo (⚠️ en la tabla).")
    registro.tabla(tabla_por_rango(medias_eboe, "turnos"), f"Eficiencia eBOE por rango de {nombre.lower()}")

    st.markdown("##### Eficiencia de proceso (rendimiento en tambor)")
    sin_aprobar = turnos_sin_aprobar(produccion_con_borradores(planta), filtros)
    if sin_aprobar:
        st.warning(f"{sin_aprobar} turno{'s' if sin_aprobar > 1 else ''} con producción en borrador: no entra"
                   f"{'n' if sin_aprobar > 1 else ''} hasta aprobarlo{'s' if sin_aprobar > 1 else ''}.")
    proceso = dias.dropna(subset=["eficiencia_proceso"])
    if proceso.empty:
        st.info("No hay días con producción aprobada (aceite envasado y al menos 60 t de fruta) con estos filtros.")
        return
    izquierda, derecha = st.columns(2)
    registro.grafico(dispersion_por_temporada(proceso, "valor", "eficiencia_proceso", nombre, "Eficiencia de proceso",
                                              unidad, "%", decimales, 1),
                     f"Eficiencia de proceso por día vs {nombre.lower()}", donde=izquierda)
    medias_proceso = medias_por_rango(proceso, "eficiencia_proceso", variable)
    if not medias_proceso.empty:
        registro.grafico(intervalos_agrupados(sin_ic_chico(medias_proceso), "Eficiencia de proceso (%)", "%", 1,
                                              f"{nombre} ({unidad})"),
                         f"Eficiencia de proceso por rango de {nombre.lower()}", donde=derecha)
    st.caption("Por día de producción, porque el envasado no coincide con el turno: Σ aceite envasado / Σ fruta de los "
               "turnos aprobados del día, sobre el aceite en fruta de entrada del día (como Análisis › Proceso). Los "
               "días con menos de 60 t no entran. Los rangos con N < 3 van sin intervalo (⚠️ en la tabla).")
    registro.tabla(tabla_por_rango(medias_proceso, "días"), f"Eficiencia de proceso por rango de {nombre.lower()}")
    with st.expander("Días"):
        marcados = proceso.assign(outlier=es_outlier_iqr(proceso["eficiencia_proceso"]))
        st.caption(f"{numero(int(marcados['outlier'].sum()), 0)} días fuera de los límites IQR: se marcan, no se "
                   "excluyen.")
        registro.tabla(pd.DataFrame({
            "Día": marcados["fecha_produccion"].dt.strftime("%d/%m/%Y"), "Temporada": marcados["temporada"].astype(str),
            f"{nombre} ({unidad})": marcados["valor"].map(lambda v: numero(v, decimales)), "Rango": marcados["rango"],
            "Fruta (t)": marcados["fruta_t"].map(lambda v: numero(v, 0)),
            "Eficiencia de proceso (%)": marcados["eficiencia_proceso"].map(lambda v: numero(v, 1)),
            "Outlier (IQR)": marcados["outlier"].map({True: "sí", False: ""})}), "Eficiencia de proceso por día")


PESTANAS = {"Volatilización": pestana_volatilizacion, "Fase pesada": pestana_fase_pesada}
# Con key y on_change, cada pestaña sabe si está abierta (.open): solo se dibuja la abierta.
# bind="query-params": la pestaña queda en la URL (?pestana_sistema=Fase+pesada) y se puede compartir el enlace.
for (nombre, dibujar), pestana in zip(PESTANAS.items(), st.tabs(list(PESTANAS), key="pestana_sistema",
                                                                    on_change="rerun", bind="query-params")):
    if pestana.open:
        with pestana:
            arriba = st.container()
            registro = Descargas(nombre)
            dibujar(registro)
            registro.boton_todo(arriba)
