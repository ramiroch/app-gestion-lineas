"""Página Análisis: aceite en fruta y eficiencia del eBOE con los filtros globales.

Reproduce los análisis 1 a 4 de la sección 9 de docs/mapeo_ledesma.md (histograma, boxplot,
estadística de Salida y límites IQR). Solo muestra: la estadística está en core/estadistica.py,
los gráficos en app/graficos.py y los datos filtrados vienen de app/filtros.py.
"""

import pandas as pd
import streamlit as st

from core.estadistica import limites_iqr, media_ponderada_por_turno, resumen_descriptivo
from filtros import barra_filtros, planta_elegida, toneladas_planta
from graficos import boxplot, histograma, numero


def porcentaje(fraccion, decimales: int = 1) -> str:
    return "—" if fraccion is None else f"{numero(fraccion * 100, decimales)} %"


def kg_t(valor) -> str:
    return "—" if valor is None else f"{numero(valor)} kg/t"


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


# ---------------------------------------------------------------- página

st.set_page_config(page_title="Análisis", layout="wide")
st.title("Análisis de aceite en fruta")

datos = barra_filtros()
if datos.empty:
    st.info("No hay muestras con estos filtros.")
    st.stop()

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

st.subheader("Estadística del aceite en fruta de Salida por eBOE (kg/t)")
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

st.subheader("Límites de outliers por IQR (kg/t)")
filas, grupos = [], {}
for (eboe, lugar), valores in datos.groupby(["eboe", "lugar"])["aceite_fruta"]:
    lim = limites_iqr(valores)
    grupos[f"eBOE {eboe} {lugar.lower()}"] = lim["n"]
    filas.append({"eBOE": f"eBOE {eboe}", "Lugar": lugar, "N": lim["n"], "Q1": lim["q1"], "Q3": lim["q3"],
                  "IQR": lim["iqr"], "LI": lim["li"], "LS": lim["ls"], "Outliers": lim["outliers"]})
st.dataframe(tabla(filas), hide_index=True)
avisar_n_chico(grupos)
st.caption("LI = Q1 − 1,5·IQR · LS = Q3 + 1,5·IQR · cuartiles con el método de Minitab.")
