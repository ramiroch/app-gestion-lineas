"""Gráficos de las páginas de análisis (Plotly).

Solo arma figuras: la estadística sale de core/estadistica.py y core/comparaciones.py, y los
números con coma de core/formato.py. No usa Streamlit, así los gráficos también se pueden
exportar a PNG desde un script (fig.write_image, con kaleido).
"""

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.estadistica import ancho_de_clase, cuartiles, curva_normal, es_outlier_iqr, limites_iqr, resumen_descriptivo
from core.formato import numero
from core.programa import numero_programa
from core.regresion import recta

LUGARES = ("Entrada", "Salida")
# Paleta categórica en orden fijo (azul, naranja, aqua, amarillo, magenta, verde, violeta, rojo):
# los 3 primeros se distinguen bien entre sí; con más grupos superpuestos el gráfico ya no se lee.
PALETA = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
COLORES = {"Entrada": PALETA[0], "Salida": PALETA[1]}
COLOR_SERIE = "#2a78d6"     # una sola serie (boxplot por temporada): color 1 de la paleta
COLOR_DATOS = "#2b2f36"     # medias e intervalos: oscuro, como pide CLAUDE.md
TITULO_ACEITE = "Aceite en fruta (kg/MT)"
ALTURA = 450
# Escala secuencial de un solo azul (claro → oscuro) para datos ordenados: programa N0 a N9 y eficiencia.
AZULES = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
ESCALA_AZUL = [[i / (len(AZULES) - 1), color] for i, color in enumerate(AZULES)]
GRIS = "#8a8f98"
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
    return histograma_grupos(datos, "aceite_fruta", "lugar", TITULO_ACEITE, "kg/MT", colores=COLORES)


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
               lugar, COLORES[lugar], "kg/MT", offsetgroup=lugar)
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


def dispersion_por_eboe(datos: pd.DataFrame, x: str, y: str, regresiones: pd.DataFrame, titulo_x: str,
                        titulo_y: str, unidad_x: str, unidad_y: str, dec_x: int = 2, dec_y: int = 1) -> go.Figure:
    """Dispersión de y vs x con un panel por eBOE (mismo eje Y), coloreada por temporada.

    regresiones: regresiones_por_grupo(datos, x, y, ["eboe", "temporada"]); cada temporada de cada
    panel lleva su recta (dentro de su rango de x) y su R² va en el título del panel. Las rectas se
    dibujan al final, con borde blanco, para que los puntos no las tapen.
    """
    datos = datos.dropna(subset=[x, y])
    eboes = sorted(datos["eboe"].unique())
    temporadas = sorted(datos["temporada"].unique())
    colores = {t: PALETA[i % len(PALETA)] for i, t in enumerate(temporadas)}
    regs = {(f["eboe"], f["temporada"]): f for f in regresiones.to_dict("records")}

    def r2(eboe, temporada):
        reg = regs.get((eboe, temporada))
        return None if reg is None or pd.isna(reg["r2"]) else reg["r2"]

    titulos = [f"eBOE {e} · R² " + " · ".join(f"{t}: {numero(r2(e, t), 2)}" for t in temporadas) for e in eboes]
    figura = make_subplots(rows=1, cols=max(len(eboes), 1), shared_yaxes=True, subplot_titles=titulos,
                           horizontal_spacing=0.03)
    for columna, eboe in enumerate(eboes, start=1):
        for temporada in temporadas:
            sub = datos[(datos["eboe"] == eboe) & (datos["temporada"] == temporada)]
            if sub.empty:
                continue
            figura.add_scatter(
                row=1, col=columna, x=sub[x], y=sub[y], mode="markers", name=str(temporada),
                legendgroup=str(temporada), showlegend=columna == 1,
                marker=dict(color=colores[temporada], size=6, opacity=0.35),
                customdata=list(zip(sub["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"), sub["turno"],
                                    sub["analista"].fillna("—"), sub[x].map(lambda v: numero(v, dec_x)),
                                    sub[y].map(lambda v: numero(v, dec_y)))),
                hovertemplate=f"<b>eBOE {eboe} · {temporada}</b><br>%{{customdata[0]}} · turno %{{customdata[1]}}"
                              f"<br>Analista: %{{customdata[2]}}<br>{titulo_x}: %{{customdata[3]}}"
                              f"<br>{titulo_y}: %{{customdata[4]}}<extra></extra>")
        figura.update_xaxes(title_text=titulo_x, row=1, col=columna)

    for columna, eboe in enumerate(eboes, start=1):          # rectas arriba de todos los puntos
        for temporada in temporadas:
            reg = regs.get((eboe, temporada))
            if reg is None or pd.isna(reg["pendiente"]):
                continue
            sub = datos[(datos["eboe"] == eboe) & (datos["temporada"] == temporada)]
            extremos = [sub[x].min(), sub[x].max()]
            valores = recta(reg, extremos)
            figura.add_scatter(row=1, col=columna, x=extremos, y=valores, mode="lines", showlegend=False,
                               line=dict(color="white", width=6), hoverinfo="skip")
            figura.add_scatter(
                row=1, col=columna, x=extremos, y=valores, mode="lines", showlegend=False,
                legendgroup=str(temporada), line=dict(color=colores[temporada], width=2.5),
                hovertemplate=f"<b>Recta {temporada} · eBOE {eboe}</b><br>"
                              f"Pendiente {numero(reg['pendiente'], dec_y)} {unidad_y} por {unidad_x}<br>"
                              f"R² {numero(reg['r2'], 2)} · N {numero(reg['n'], 0)}<extra></extra>")
    figura.update_yaxes(title_text=titulo_y, row=1, col=1)
    figura.update_xaxes(matches="x")                       # misma escala de x en todos los paneles
    figura.update_annotations(font_size=12)
    estilo(figura)
    figura.update_layout(margin=dict(t=80), legend=dict(y=1.14))   # leyenda arriba de los títulos de panel
    return figura


def burbujas(combos: pd.DataFrame) -> tuple[go.Figure, list[list[tuple]]]:
    """Burbujas de eficiencia media vs MTPH, un panel por eBOE (mismo eje Y), color por programa
    (azul claro N0 → oscuro N9) y tamaño por N de pares.

    combos es core.programa.combinaciones(). Las combinaciones con N < 3 van tenues y con aviso en
    el tooltip. Devuelve la figura y, por cada traza, la lista de (eboe, programa, mtph) de cada
    burbuja, para saber qué combinación eligió el usuario al hacer clic.
    """
    eboes = sorted(combos["eboe"].unique())
    programas = sorted(combos["programa"].unique(), key=lambda p: (numero_programa(p) is None, numero_programa(p), p))
    figura = make_subplots(rows=1, cols=max(len(eboes), 1), shared_yaxes=True, horizontal_spacing=0.03,
                           subplot_titles=[f"eBOE {e}" for e in eboes])
    escala = 2 * combos["n"].max() / 40 ** 2 if len(combos) else 1      # burbuja más grande ≈ 40 px
    claves, en_leyenda = [], set()
    for columna, eboe in enumerate(eboes, start=1):
        for programa in programas:
            sub = combos[(combos["eboe"] == eboe) & (combos["programa"] == programa)]
            if sub.empty:
                continue
            num = numero_programa(programa)
            avisos = ["" if r else "<br><b>⚠ N < 3: poco representativa</b>" for r in sub["representativa"]]
            ics = ["—" if pd.isna(a) else f"{numero(a, 1)} a {numero(b, 1)} %" for a, b in zip(sub["ic_inf"], sub["ic_sup"])]
            figura.add_scatter(
                row=1, col=columna, x=sub["mtph"], y=sub["media"], mode="markers", name=programa,
                legendgroup=programa, showlegend=programa not in en_leyenda,
                marker=dict(size=sub["n"], sizemode="area", sizeref=escala, sizemin=4,
                            color=AZULES[num] if num is not None else GRIS,
                            opacity=[0.85 if r else 0.2 for r in sub["representativa"]],
                            line=dict(color="white", width=1)),
                customdata=list(zip(sub["n"], sub["media"].map(lambda v: numero(v, 1)), ics, avisos)),
                hovertemplate=f"<b>eBOE {eboe} · {programa} a %{{x}} MTPH</b><br>Eficiencia %{{customdata[1]}} %"
                              "<br>IC 95 %: %{customdata[2]}<br>N %{customdata[0]} pares%{customdata[3]}<extra></extra>")
            en_leyenda.add(programa)
            claves.append([(eboe, programa, int(m)) for m in sub["mtph"]])
        figura.update_xaxes(title_text="MTPH", row=1, col=columna)
    figura.update_xaxes(matches="x")
    figura.update_yaxes(title_text="Eficiencia eBOE (%)", row=1, col=1)
    figura.update_annotations(font_size=12)
    estilo(figura)
    figura.update_layout(margin=dict(t=80), legend=dict(y=1.14, title_text="Programa", itemsizing="constant"),
                         dragmode="select")
    return figura, claves


def superficie(mtph: list, programas: list, eficiencia, n, eboe) -> go.Figure:
    """Superficie 3D de eficiencia media según programa (0 a 9) y MTPH; las celdas sin N ≥ 3 quedan vacías."""
    figura = go.Figure(go.Surface(
        x=programas, y=mtph, z=eficiencia, customdata=n, colorscale=ESCALA_AZUL,
        colorbar=dict(title=dict(text="%"), thickness=12),
        hovertemplate="<b>N%{x} a %{y} MTPH</b><br>Eficiencia %{z:.1f} %<br>N %{customdata} pares<extra></extra>"))
    figura.update_layout(title=dict(text=f"Superficie · eBOE {eboe}", font_size=14), scene=dict(
        xaxis=dict(title="Programa", tickvals=programas, ticktext=[f"N{p}" for p in programas]),
        yaxis=dict(title="MTPH"), zaxis=dict(title="Eficiencia (%)"),
        aspectratio=dict(x=1.2, y=1.0, z=0.8),
        camera=dict(eye=dict(x=1.2, y=-1.6, z=1.4), center=dict(x=0, y=0, z=-0.15))), margin=dict(l=0, r=0, t=40, b=0))
    return estilo(figura)


def contorno(mtph: list, programas: list, eficiencia, n, eboe) -> go.Figure:
    """Contorno de eficiencia media según programa y MTPH. Los puntos marcan las celdas con datos
    (N ≥ 3): el contorno interpola entre ellas."""
    figura = go.Figure(go.Contour(
        x=programas, y=mtph, z=eficiencia, customdata=n, colorscale=ESCALA_AZUL, connectgaps=False,
        contours=dict(showlabels=True, labelfont=dict(size=10, color="white")),
        colorbar=dict(title=dict(text="%"), thickness=12),
        hovertemplate="<b>N%{x} a %{y} MTPH</b><br>Eficiencia %{z:.1f} %<extra></extra>"))
    filas, columnas = np.where(~np.isnan(np.asarray(eficiencia, dtype=float)))
    figura.add_scatter(
        x=[programas[j] for j in columnas], y=[mtph[i] for i in filas], mode="markers", showlegend=False,
        marker=dict(size=6, color=COLOR_DATOS, line=dict(color="white", width=1)),
        customdata=[[numero(eficiencia[i][j], 1), int(n[i][j])] for i, j in zip(filas, columnas)],
        hovertemplate="<b>N%{x} a %{y} MTPH</b><br>Eficiencia %{customdata[0]} %<br>N %{customdata[1]} pares"
                      "<extra></extra>")
    figura.update_layout(title=dict(text=f"Contorno · eBOE {eboe}", font_size=14),
                         xaxis=dict(title="Programa", tickvals=programas, ticktext=[f"N{p}" for p in programas]),
                         yaxis=dict(title="MTPH"))
    return estilo(figura)
