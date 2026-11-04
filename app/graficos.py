"""Gráficos de las páginas de análisis (Plotly) y formato de números con coma decimal.

Solo arma figuras: la estadística sale de core/estadistica.py. No usa Streamlit, así los
gráficos también se pueden exportar a PNG desde un script (fig.write_image, con kaleido).
"""

import math

import pandas as pd
import plotly.graph_objects as go

from core.estadistica import ancho_de_clase, cuartiles, curva_normal, es_outlier_iqr, limites_iqr, resumen_descriptivo

LUGARES = ("Entrada", "Salida")
COLORES = {"Entrada": "#2a78d6", "Salida": "#eb6834"}   # paleta categórica: azul y naranja
TITULO_ACEITE = "Aceite en fruta (kg/t)"
ALTURA = 450


def numero(valor, decimales: int = 2) -> str:
    """Número con coma decimal y punto de miles: 1234.5 → "1.234,50"; vacío → "—"."""
    if valor is None or pd.isna(valor):
        return "—"
    return f"{valor:,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def estilo(figura: go.Figure) -> go.Figure:
    """Estilo común: coma decimal, 450 px de alto, leyenda arriba, márgenes chicos."""
    figura.update_layout(separators=",.", height=ALTURA, margin=dict(t=40, b=10, l=10, r=10),
                         legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return figura


def histograma(datos: pd.DataFrame) -> go.Figure:
    """Histograma de aceite en fruta, Entrada y Salida superpuestos, con su curva normal.

    Densidad de probabilidad en Y (así la curva normal y las barras están en la misma
    escala); media, desvío y N de cada lugar en la leyenda. Las dos series comparten el ancho
    de clase: el menor de los de Freedman–Diaconis de cada lugar, para que la más angosta
    (Salida) no quede con pocas barras.
    """
    series = {lugar: datos.loc[datos["lugar"] == lugar, "aceite_fruta"].dropna() for lugar in LUGARES}
    series = {lugar: valores for lugar, valores in series.items() if not valores.empty}
    anchos = [a for a in (ancho_de_clase(v) for v in series.values()) if a]
    clases = None
    if anchos:
        ancho = round(min(anchos), 2) or min(anchos)
        inicio = math.floor(min(v.min() for v in series.values()) / ancho) * ancho
        clases = dict(start=inicio, size=ancho)

    figura = go.Figure()
    for lugar, valores in series.items():
        r = resumen_descriptivo(valores)
        nombre = f"{lugar} · media {numero(r['media'])} · s {numero(r['desvio'])} · N {numero(r['n'], 0)}"
        figura.add_histogram(x=valores, histnorm="probability density", bingroup="aceite", xbins=clases, name=nombre,
                             legendgroup=lugar, marker=dict(color=COLORES[lugar], line=dict(color="white", width=0.5)),
                             opacity=0.55, hovertemplate=f"{lugar}<br>%{{x}} kg/t<br>densidad %{{y:.3f}}<extra></extra>")
        if r["desvio"]:
            # Media ± 4 s, sin pasar medio desvío más allá de los datos (la curva no cruza el otro lugar).
            media, s = r["media"], r["desvio"]
            x, y = curva_normal(media, s, max(valores.min() - s / 2, media - 4 * s),
                                min(valores.max() + s / 2, media + 4 * s))
            figura.add_scatter(x=x, y=y, mode="lines", line=dict(color=COLORES[lugar], width=2),
                               legendgroup=lugar, showlegend=False, hoverinfo="skip")
    figura.update_layout(barmode="overlay", xaxis_title=TITULO_ACEITE, yaxis_title="Densidad")
    return estilo(figura)


def _texto_caja(eboe: int, lugar: str, lim: dict, mediana: float) -> str:
    return (f"<b>eBOE {eboe} · {lugar}</b><br>N {numero(lim['n'], 0)}<br>"
            f"Bigote sup. {numero(lim['bigote_superior'])}<br>Q3 {numero(lim['q3'])}<br>"
            f"Mediana {numero(mediana)}<br>Q1 {numero(lim['q1'])}<br>"
            f"Bigote inf. {numero(lim['bigote_inferior'])}<extra></extra>")


def boxplot(datos: pd.DataFrame) -> go.Figure:
    """Boxplot de aceite en fruta por eBOE, con Entrada y Salida agrupadas lado a lado.

    Un go.Box por lugar con los cuartiles de limites_iqr() (mismo método que la tabla IQR);
    los bigotes llegan al último valor dentro de [LI, LS]. Los outliers van en un go.Scatter
    con el mismo offsetgroup que su caja, con tooltip de valor, fecha, turno y analista.
    """
    figura = go.Figure()
    for lugar in LUGARES:
        sub = datos[datos["lugar"] == lugar].dropna(subset=["aceite_fruta"])
        cajas = {"x": [], "q1": [], "median": [], "q3": [], "lowerfence": [], "upperfence": [], "hovertemplate": []}
        for eboe, valores in sub.groupby("eboe")["aceite_fruta"]:
            lim = limites_iqr(valores)
            mediana = cuartiles(valores)[1]
            for clave, valor in (("x", f"eBOE {eboe}"), ("q1", lim["q1"]), ("median", mediana), ("q3", lim["q3"]),
                                 ("lowerfence", lim["bigote_inferior"]), ("upperfence", lim["bigote_superior"]),
                                 ("hovertemplate", _texto_caja(eboe, lugar, lim, mediana))):
                cajas[clave].append(valor)
        if not cajas["x"]:
            continue
        figura.add_box(**cajas, name=lugar, legendgroup=lugar, offsetgroup=lugar,
                       marker_color=COLORES[lugar], line_width=1.5)

        outliers = sub[sub.groupby("eboe")["aceite_fruta"].transform(es_outlier_iqr).astype(bool)]
        if outliers.empty:
            continue
        figura.add_scatter(
            x=[f"eBOE {e}" for e in outliers["eboe"]], y=outliers["aceite_fruta"], mode="markers",
            name=f"Outliers {lugar.lower()}", legendgroup=lugar, offsetgroup=lugar,
            marker=dict(symbol="circle-open", size=8, color=COLORES[lugar], line_width=2),
            customdata=list(zip(outliers["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"), outliers["turno"],
                                outliers["analista"].fillna("—"), outliers["aceite_fruta"].map(numero))),
            hovertemplate="<b>%{customdata[3]} kg/t</b> (outlier)<br>%{customdata[0]} · turno %{customdata[1]}"
                          "<br>Analista: %{customdata[2]}<extra></extra>")
    figura.update_layout(boxmode="group", scattermode="group", yaxis_title=TITULO_ACEITE,
                         xaxis=dict(type="category", categoryorder="category ascending"))
    return estilo(figura)
