"""Gráficos de las páginas de análisis (Plotly).

Solo arma figuras: la estadística sale de core/estadistica.py y core/comparaciones.py, y los
números con coma de core/formato.py. No usa Streamlit, así los gráficos también se pueden
exportar a PNG desde un script (fig.write_image, con kaleido).
"""

import math

import pandas as pd
import plotly.graph_objects as go

from core.estadistica import ancho_de_clase, cuartiles, curva_normal, es_outlier_iqr, limites_iqr, resumen_descriptivo
from core.formato import numero

LUGARES = ("Entrada", "Salida")
# Paleta categórica en orden fijo (azul, naranja, aqua, amarillo, magenta, verde, violeta, rojo):
# los 3 primeros se distinguen bien entre sí; con más grupos superpuestos el gráfico ya no se lee.
PALETA = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
COLORES = {"Entrada": PALETA[0], "Salida": PALETA[1]}
COLOR_SERIE = "#2a78d6"     # una sola serie (boxplot por temporada): color 1 de la paleta
COLOR_DATOS = "#2b2f36"     # medias e intervalos: oscuro, como pide CLAUDE.md
TITULO_ACEITE = "Aceite en fruta (kg/t)"
ALTURA = 450
ALTURA_CHICA = 380          # dos gráficos lado a lado


def estilo(figura: go.Figure, altura: int = ALTURA) -> go.Figure:
    """Estilo común: coma decimal, alto fijo (450 px por defecto), leyenda arriba, márgenes chicos."""
    figura.update_layout(separators=",.", height=altura, margin=dict(t=40, b=10, l=10, r=10),
                         legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return figura


def histograma_grupos(datos: pd.DataFrame, variable: str, grupo: str, titulo_x: str, unidad: str,
                      decimales: int = 2, colores: dict | None = None) -> go.Figure:
    """Histograma de una variable con un grupo por color (superpuestos) y su curva normal.

    Densidad de probabilidad en Y (así la curva normal y las barras están en la misma escala);
    media, desvío y N de cada grupo en la leyenda. Los grupos comparten el ancho de clase: el
    menor de los de Freedman–Diaconis de cada grupo, para que el más angosto no quede con pocas
    barras. colores: {grupo: color}; si falta, la paleta categórica en orden fijo (grupos ordenados).
    """
    series = {g: valores.dropna() for g, valores in datos.groupby(grupo, sort=True)[variable]}
    series = {g: valores for g, valores in series.items() if not valores.empty}
    if colores is None:
        colores = {g: PALETA[i % len(PALETA)] for i, g in enumerate(series)}
    anchos = [a for a in (ancho_de_clase(v) for v in series.values()) if a]
    clases = None
    if anchos:
        ancho = round(min(anchos), 2) or min(anchos)
        inicio = math.floor(min(v.min() for v in series.values()) / ancho) * ancho
        clases = dict(start=inicio, size=ancho)

    figura = go.Figure()
    for g, valores in series.items():
        r = resumen_descriptivo(valores)
        nombre = (f"{g} · media {numero(r['media'], decimales)} · s {numero(r['desvio'], decimales)} · "
                  f"N {numero(r['n'], 0)}")
        figura.add_histogram(x=valores, histnorm="probability density", bingroup="grupos", xbins=clases, name=nombre,
                             legendgroup=str(g), marker=dict(color=colores[g], line=dict(color="white", width=0.5)),
                             opacity=0.55, hovertemplate=f"{g}<br>%{{x}} {unidad}<br>densidad %{{y:.3f}}<extra></extra>")
        if r["desvio"]:
            # Media ± 4 s, sin pasar medio desvío más allá de los datos (la curva no cruza el otro grupo).
            media, s = r["media"], r["desvio"]
            x, y = curva_normal(media, s, max(valores.min() - s / 2, media - 4 * s),
                                min(valores.max() + s / 2, media + 4 * s))
            figura.add_scatter(x=x, y=y, mode="lines", line=dict(color=colores[g], width=2),
                               legendgroup=str(g), showlegend=False, hoverinfo="skip")
    figura.update_layout(barmode="overlay", xaxis_title=titulo_x, yaxis_title="Densidad")
    return estilo(figura)


def histograma(datos: pd.DataFrame) -> go.Figure:
    """Histograma de aceite en fruta, Entrada y Salida superpuestos, con su curva normal."""
    return histograma_grupos(datos, "aceite_fruta", "lugar", TITULO_ACEITE, "kg/t", colores=COLORES)


def _texto_caja(titulo: str, lim: dict, mediana: float, decimales: int) -> str:
    return (f"<b>{titulo}</b><br>N {numero(lim['n'], 0)}<br>"
            f"Bigote sup. {numero(lim['bigote_superior'], decimales)}<br>Q3 {numero(lim['q3'], decimales)}<br>"
            f"Mediana {numero(mediana, decimales)}<br>Q1 {numero(lim['q1'], decimales)}<br>"
            f"Bigote inf. {numero(lim['bigote_inferior'], decimales)}<extra></extra>")


def _cajas(figura: go.Figure, datos: pd.DataFrame, variable: str, columna_x: str, etiqueta_x, nombre: str,
           color: str, unidad: str, decimales: int = 2, offsetgroup: str | None = None, leyenda: bool = True) -> None:
    """Agrega a la figura un go.Box (una caja por valor de columna_x) y sus outliers.

    Cajas y bigotes salen de limites_iqr() (cuartiles de Minitab, mismos que la tabla IQR);
    los bigotes llegan al último valor dentro de [LI, LS]. Los outliers van en un go.Scatter
    con el mismo offsetgroup que su caja, con tooltip de valor, eBOE, fecha, turno y analista.
    """
    sub = datos.dropna(subset=[variable])
    cajas = {"x": [], "q1": [], "median": [], "q3": [], "lowerfence": [], "upperfence": [], "hovertemplate": []}
    for valor_x, valores in sub.groupby(columna_x)[variable]:
        lim = limites_iqr(valores)
        mediana = cuartiles(valores)[1]
        x = etiqueta_x(valor_x)
        titulo = f"{x} · {nombre}" if offsetgroup else x
        for clave, valor in (("x", x), ("q1", lim["q1"]), ("median", mediana), ("q3", lim["q3"]),
                             ("lowerfence", lim["bigote_inferior"]), ("upperfence", lim["bigote_superior"]),
                             ("hovertemplate", _texto_caja(titulo, lim, mediana, decimales))):
            cajas[clave].append(valor)
    if not cajas["x"]:
        return
    figura.add_box(**cajas, name=nombre, legendgroup=nombre, offsetgroup=offsetgroup, showlegend=leyenda,
                   marker_color=color, line_width=1.5)

    outliers = sub[sub.groupby(columna_x)[variable].transform(es_outlier_iqr).astype(bool)]
    if outliers.empty:
        return
    figura.add_scatter(
        x=[etiqueta_x(v) for v in outliers[columna_x]], y=outliers[variable], mode="markers",
        name=f"Outliers {nombre.lower()}", legendgroup=nombre, offsetgroup=offsetgroup, showlegend=leyenda,
        marker=dict(symbol="circle-open", size=8, color=color, line_width=2),
        customdata=list(zip(outliers["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"), outliers["turno"],
                            outliers["analista"].fillna("—"), outliers[variable].map(lambda v: numero(v, decimales)),
                            outliers["eboe"])),
        hovertemplate=f"<b>%{{customdata[3]}} {unidad}</b> (outlier)<br>eBOE %{{customdata[4]}} · "
                      "%{customdata[0]} · turno %{customdata[1]}<br>Analista: %{customdata[2]}<extra></extra>")


def boxplot(datos: pd.DataFrame) -> go.Figure:
    """Boxplot de aceite en fruta por eBOE, con Entrada y Salida agrupadas lado a lado."""
    figura = go.Figure()
    for lugar in LUGARES:
        _cajas(figura, datos[datos["lugar"] == lugar], "aceite_fruta", "eboe", lambda e: f"eBOE {e}",
               lugar, COLORES[lugar], "kg/t", offsetgroup=lugar)
    figura.update_layout(boxmode="group", scattermode="group", yaxis_title=TITULO_ACEITE,
                         xaxis=dict(type="category", categoryorder="category ascending"))
    return estilo(figura)


def boxplot_temporadas(datos: pd.DataFrame, variable: str, titulo_y: str, unidad: str,
                       decimales: int = 2) -> go.Figure:
    """Boxplot de una variable por temporada (una sola serie, sin leyenda)."""
    figura = go.Figure()
    _cajas(figura, datos, variable, "temporada", str, titulo_y, COLOR_SERIE, unidad, decimales, leyenda=False)
    figura.update_layout(yaxis_title=titulo_y, xaxis=dict(type="category", title="Temporada",
                                                          categoryorder="category ascending"),
                         title=dict(text="Boxplot", font_size=14))
    return estilo(figura, ALTURA_CHICA)


def intervalos(medias: pd.DataFrame, titulo_y: str, unidad: str, decimales: int = 2) -> go.Figure:
    """Gráfico de intervalos como el de Minitab: la media de cada grupo con su IC del 95 %.

    medias es la tabla "medias" de core.comparaciones.comparar_grupos (grupo, n, media,
    ic_inf, ic_sup). Las medias no se unen con líneas: cada grupo es independiente.
    """
    figura = go.Figure(go.Scatter(
        x=medias["grupo"], y=medias["media"], mode="markers",
        marker=dict(symbol="circle", size=10, color=COLOR_DATOS),
        error_y=dict(type="data", symmetric=False, array=medias["ic_sup"] - medias["media"],
                     arrayminus=medias["media"] - medias["ic_inf"], color=COLOR_DATOS, thickness=2, width=14),
        customdata=list(zip(*(medias[c].map(lambda v: numero(v, decimales)) for c in ("media", "ic_inf", "ic_sup")),
                            medias["n"].map(lambda n: numero(n, 0)))),
        hovertemplate=f"<b>Temporada %{{x}}</b><br>Media %{{customdata[0]}} {unidad}<br>"
                      f"IC 95 %: %{{customdata[1]}} a %{{customdata[2]}} {unidad}<br>N %{{customdata[3]}}<extra></extra>",
        showlegend=False))
    figura.update_layout(yaxis_title=titulo_y, xaxis=dict(type="category", title="Temporada"),
                         title=dict(text="Media e IC del 95 %", font_size=14))
    return estilo(figura, ALTURA_CHICA)
