"""Página Sistema: la mirada del sistema completo de recuperación de aceite (informe Brown 2023).

Usa la barra de filtros global. Una pestaña por análisis; solo se dibuja la abierta, cada gráfico tiene botón PNG,
cada tabla Excel y cada pestaña "Descargar todo".
- Fase pesada: caudal de fase pesada real (laboratorio) contra el requerido por el balance de aceite del licor y el
  coherente con el licor medido; rango de fase pesada (gráfico Brown) por temporada, eBOE, día y turno.
Solo muestra: la lógica está en core/fase_pesada.py.
"""

import pandas as pd
import streamlit as st

from cartel import cartel_modo_prueba
from core.analisis import filtrar, pares_con_emulsion
from core.estadistica import es_outlier_iqr
from core.fase_pesada import CLAVES_TURNO, mediana_por, tabla_fase_pesada
from core.formato import numero
from core.parametros import ParametrosIncompletos
from core.repetidas import sin_entradas_repetidas
from descargas import Descargas
from filtros import (barra_filtros, casilla_repetidas, filtros_elegidos, parametros_fase_pesada_planta,
                     planta_elegida, tabla_emulsion_planta)
from graficos import boxplot_temporadas, lineas_en_el_tiempo

st.set_page_config(page_title="Sistema", layout="wide")
cartel_modo_prueba()
st.title("Sistema de recuperación de aceite")
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
                         "Coherente (m³/h)": vista["coherente"].map(lambda v: numero(v, 2)),
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
        f"({numero(parametros['omega_fp_pct'], 2)} %). **Coherente**: el mismo balance con el aceite en emulsión que "
        "midió el laboratorio en ese análisis (lo que el balance dice que entró). **Real**: caudal que carga el "
        "laboratorio del BOE. **Rango de fase pesada** = (real − requerido) / requerido × 100: negativo, entra menos "
        "fase pesada que la requerida (gráfico Brown). Por turno y por día, mediana de los análisis.")
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
    for columna, clave, titulo in ((columnas[1], "caudal_fase_pesada", "Real (laboratorio)"),
                                   (columnas[2], "requerido", "Requerido (balance)"),
                                   (columnas[3], "coherente", "Coherente con el licor")):
        valores = validos[clave].dropna()
        columna.metric(titulo, f"{numero(valores.median(), 2)} m³/h" if len(valores) else "—", border=True,
                       help=f"Mediana de los análisis. N = {len(valores)}.")
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
    registro.grafico(lineas_en_el_tiempo(dias, "fecha_produccion", {
        "real": ("Real (laboratorio)", "n"), "requerido": ("Requerido (balance)", "n"),
        "coherente": ("Coherente con el licor medido", "n")}, "Caudal de fase pesada (m³/h)", "m³/h"),
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
            "Coherente (m³/h)": validos["coherente"].map(numero),
            "Rango (%)": validos["rango_fp_pct"].map(lambda v: numero(v, 1)),
            "Outlier (IQR)": validos["outlier"].map({True: "sí", False: ""}),
            "Entrada repetida": validos["entrada_repetida"].map({True: "sí", False: ""})}),
            "Fase pesada por análisis")


PESTANAS = {"Fase pesada": pestana_fase_pesada}
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
