"""Gráficos de las páginas de análisis (Plotly).

Solo arma figuras: la estadística sale de core/estadistica.py y core/comparaciones.py, y los
números con coma de core/formato.py. No usa Streamlit, así los gráficos también se pueden
exportar a PNG desde un script (fig.write_image, con kaleido).
"""

import math
import warnings

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.estadistica import ancho_de_clase, cuartiles, curva_normal, es_outlier_iqr, limites_iqr, resumen_descriptivo
from core.formato import numero, texto_p
from core.laboratorio import DIAS_MISMA_NORMALIDAD, texto_p_ad
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
ANCHO_PNG = 1600            # ancho de los PNG descargables (px)
# Con el servidor de kaleido abierto (app/descargas.py), plotly avisa que ignora "kopts" en cada imagen;
# el ancho y la escala igual se respetan (verificado: el PNG sale de 1600 px).
warnings.filterwarnings("ignore", message="The kopts argument is ignored", category=UserWarning)
ANCHO_DIBUJO_PNG = 1100     # se dibuja a este ancho y se escala: el texto queda del tamaño justo
ANCHO_DIBUJO_PNG_MEDIO = 700   # gráficos que en la página ocupan media pantalla (en una columna)


def estilo(figura: go.Figure, altura: int = ALTURA) -> go.Figure:
    """Estilo común: coma decimal, alto fijo (450 px por defecto), leyenda arriba, márgenes chicos."""
    figura.update_layout(separators=",.", height=altura, margin=dict(t=40, b=10, l=10, r=10),
                         legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return figura


def png(figura: go.Figure, ancho_dibujo: int = ANCHO_DIBUJO_PNG) -> bytes:
    """PNG de la figura para pegar en un informe: 1600 px de ancho, fondo blanco, alto proporcional.

    Se dibuja a ancho_dibujo (el ancho que tiene en la página: ANCHO_DIBUJO_PNG o, si ocupa media
    pantalla, ANCHO_DIBUJO_PNG_MEDIO) y se escala a ANCHO_PNG, así conserva sus proporciones y el texto
    su tamaño relativo. No cambia la figura original. Usa kaleido.
    """
    copia = go.Figure(figura)
    alto = copia.layout.height or ALTURA
    copia.update_layout(template="plotly_white", paper_bgcolor="white", plot_bgcolor="white",
                        width=ancho_dibujo, height=alto)
    return copia.to_image(format="png", width=ancho_dibujo, height=alto, scale=ANCHO_PNG / ancho_dibujo)


def histograma_grupos(datos: pd.DataFrame, variable: str, grupo: str, titulo_x: str, unidad: str,
                      decimales: int = 2, colores: dict | None = None, curva: bool = True) -> go.Figure:
    """Histograma de una variable con un grupo por color (superpuestos) y su curva normal.

    Densidad de probabilidad en Y (así la curva normal y las barras están en la misma escala);
    media, desvío y N de cada grupo en la leyenda. Los grupos comparten el ancho de clase: el
    menor de los de Freedman–Diaconis de cada grupo, para que el más angosto no quede con pocas
    barras. colores: {grupo: color}; si falta, la paleta categórica en orden fijo (grupos ordenados).
    curva=False: sin curva normal (para variables que no tienen por qué ser normales).
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
        if curva and r["desvio"]:
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


def intervalos(medias: pd.DataFrame, titulo_y: str, unidad: str, decimales: int = 2,
               titulo_x: str = "Temporada", titulo: str = "Media e IC del 95 %") -> go.Figure:
    """Gráfico de intervalos como el de Minitab: la media de cada grupo con su IC del 95 %.

    medias es la tabla "medias" de core.comparaciones.comparar_grupos (grupo, n, media,
    ic_inf, ic_sup), o una con esas columnas. Las medias no se unen con líneas: cada grupo es
    independiente. titulo_x: qué son los grupos (temporada, analista…).
    """
    figura = go.Figure(go.Scatter(
        x=medias["grupo"], y=medias["media"], mode="markers",
        marker=dict(symbol="circle", size=10, color=COLOR_DATOS),
        error_y=dict(type="data", symmetric=False, array=medias["ic_sup"] - medias["media"],
                     arrayminus=medias["media"] - medias["ic_inf"], color=COLOR_DATOS, thickness=2, width=14),
        customdata=list(zip(*(medias[c].map(lambda v: numero(v, decimales)) for c in ("media", "ic_inf", "ic_sup")),
                            medias["n"].map(lambda n: numero(n, 0)))),
        hovertemplate=f"<b>{titulo_x} %{{x}}</b><br>Media %{{customdata[0]}} {unidad}<br>"
                      f"IC 95 %: %{{customdata[1]}} a %{{customdata[2]}} {unidad}<br>N %{{customdata[3]}}<extra></extra>",
        showlegend=False))
    figura.update_layout(yaxis_title=titulo_y, xaxis=dict(type="category", title=titulo_x),
                         title=dict(text=titulo, font_size=14))
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
                # customdata[4:7] = (eboe, programa, mtph): identifica la burbuja al hacer clic.
                customdata=list(zip(sub["n"], sub["media"].map(lambda v: numero(v, 1)), ics, avisos,
                                    [int(eboe)] * len(sub), [programa] * len(sub), sub["mtph"].astype(int))),
                hovertemplate=f"<b>eBOE {eboe} · {programa} a %{{x}} MTPH</b><br>Eficiencia %{{customdata[1]}} %"
                              "<br>IC 95 %: %{customdata[2]}<br>N %{customdata[0]} pares%{customdata[3]}<extra></extra>")
            en_leyenda.add(programa)
            claves.append([(eboe, programa, int(m)) for m in sub["mtph"]])
        figura.update_xaxes(title_text="MTPH", row=1, col=columna)
    figura.update_xaxes(matches="x")
    figura.update_yaxes(title_text="Eficiencia eBOE (%)", row=1, col=1)
    figura.update_annotations(font_size=12)
    estilo(figura)
    # Sin dragmode "select" ni "lasso": con esos, Streamlit pone clickmode="event" y el clic no selecciona.
    figura.update_layout(margin=dict(t=80), legend=dict(y=1.14, title_text="Programa", itemsizing="constant"))
    return figura, claves


def combinaciones_de_puntos(puntos: list[dict], claves: list[list[tuple]]) -> list[tuple]:
    """(eboe, programa, mtph) de los puntos elegidos en burbujas() (selection.points de Streamlit).

    Usa el customdata de cada punto (posiciones 4 a 6); si no viene, curve_number + point_index
    con las claves que devolvió burbujas().
    """
    elegidas = set()
    for punto in puntos or []:
        datos = punto.get("customdata")
        if isinstance(datos, (list, tuple)) and len(datos) >= 7:
            elegidas.add((int(datos[4]), str(datos[5]), int(datos[6])))
            continue
        traza, indice = punto.get("curve_number"), punto.get("point_index", punto.get("point_number"))
        if traza is not None and indice is not None and traza < len(claves) and indice < len(claves[traza]):
            eboe, programa, mtph = claves[traza][indice]
            elegidas.add((int(eboe), programa, int(mtph)))
    return sorted(elegidas)


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


def barras_uso(uso: pd.DataFrame) -> go.Figure:
    """% de pares en cada programa (N0 a N9), un panel por eBOE con el mismo eje Y.

    uso es core.programa.uso_de_programas(). Mismos azules por programa que las burbujas.
    """
    eboes = sorted(uso["eboe"].unique())
    categorias = [f"N{p}" for p in range(10)]
    figura = make_subplots(rows=1, cols=max(len(eboes), 1), shared_yaxes=True, horizontal_spacing=0.03,
                           subplot_titles=[f"eBOE {e} · N {numero(uso.loc[uso['eboe'] == e, 'n'].sum(), 0)}"
                                           for e in eboes])
    for columna, eboe in enumerate(eboes, start=1):
        sub = uso[uso["eboe"] == eboe]
        figura.add_bar(
            row=1, col=columna, x=sub["programa"], y=sub["porcentaje"], showlegend=False,
            marker=dict(color=[AZULES[int(n)] if pd.notna(n) else GRIS for n in sub["programa_num"]],
                        line=dict(color="white", width=2)),
            customdata=list(zip(sub["porcentaje"].map(lambda v: numero(v, 1)), sub["n"].map(lambda v: numero(v, 0)),
                                sub["n_eboe"].map(lambda v: numero(v, 0)))),
            hovertemplate=f"<b>eBOE {eboe} · %{{x}}</b><br>%{{customdata[0]}} % de los pares"
                          "<br>N %{customdata[1]} de %{customdata[2]}<extra></extra>")
        figura.update_xaxes(categoryorder="array", categoryarray=categorias, title_text="Programa", row=1, col=columna)
    figura.update_yaxes(title_text="% de pares", row=1, col=1)
    figura.update_annotations(font_size=12)
    figura.update_layout(bargap=0.15)
    return estilo(figura, ALTURA_CHICA)


def columnas_mensuales(tabla: pd.DataFrame, eje: list[str], titulo_y: str, unidad: str, por_eboe: bool = True,
                       rango_y: list | None = None, objetivo: dict | None = None) -> go.Figure:
    """Columnas por mes de producción: agrupadas por eBOE (un color por máquina) o una por mes (planta).

    tabla: core.mensual.por_mes_y_eboe() o eficiencia_proceso_por_mes() con la columna "valor".
    eje: core.mensual.eje_de_meses() (incluye los huecos entre temporadas). Valor sobre cada columna
    con 1 decimal; N en el tooltip; los meses con N < 3 van tenues y con aviso. rango_y fija el eje Y
    (para que paneles comparables tengan la misma escala). objetivo: {"minimo", "maximo"} → banda gris.
    """
    figura = go.Figure()
    grupos = sorted(tabla["eboe"].unique()) if por_eboe else [None]
    for grupo in grupos:
        sub = (tabla[tabla["eboe"] == grupo] if grupo is not None else tabla).dropna(subset=["valor"])
        if sub.empty:
            continue
        nombre = f"eBOE {grupo}" if grupo is not None else "Planta"
        color = PALETA[(int(grupo) - 1) % len(PALETA)] if grupo is not None else PALETA[0]
        chicos = (sub["n"] < 3).tolist()
        figura.add_bar(
            x=sub["etiqueta"], y=sub["valor"], name=nombre, showlegend=por_eboe,
            marker=dict(color=color, opacity=[0.3 if c else 1 for c in chicos], line=dict(color="white", width=1)),
            text=sub["valor"].map(lambda v: numero(v, 1)), textposition="outside", cliponaxis=False,
            textfont=dict(size=10),
            customdata=list(zip(sub["n"].map(lambda n: numero(n, 0)),
                                ["<br><b>⚠ N < 3: poco representativo</b>" if c else "" for c in chicos],
                                sub["valor"].map(lambda v: numero(v, 1)))),
            hovertemplate=f"<b>{nombre} · %{{x}}</b><br>{titulo_y}: %{{customdata[2]}}<br>"
                          "N %{customdata[0]}%{customdata[1]}<extra></extra>")
    huecos = [e for e in eje if not e.strip()]
    if huecos:      # Plotly solo dibuja categorías con algún dato: una traza invisible reserva el hueco
        figura.add_scatter(x=huecos, y=[None] * len(huecos), mode="markers", showlegend=False, hoverinfo="skip")
    if objetivo and objetivo.get("minimo") is not None and objetivo.get("maximo") is not None:
        figura.add_hrect(y0=objetivo["minimo"], y1=objetivo["maximo"], fillcolor=GRIS, opacity=0.12, line_width=0,
                         layer="below")
        for limite in (objetivo["minimo"], objetivo["maximo"]):
            figura.add_hline(y=limite, line=dict(color=GRIS, width=1, dash="dot"), layer="below")
        figura.add_annotation(xref="paper", x=1, xanchor="right", y=objetivo["maximo"], yanchor="bottom",
                              text=f"Objetivo {numero(objetivo['minimo'], 0)}–{numero(objetivo['maximo'], 0)} {unidad}",
                              showarrow=False, font=dict(size=11, color=GRIS))
    figura.update_layout(barmode="group", bargap=0.25, bargroupgap=0.05, yaxis_title=titulo_y,
                         xaxis=dict(type="category", categoryorder="array", categoryarray=eje))
    if rango_y:
        figura.update_yaxes(range=rango_y)
    return estilo(figura)


COLOR_REGLA_1 = "#e34948"      # rojo: punto fuera de los límites (regla 1)
COLOR_OTRAS_REGLAS = "#f29b30"  # naranja claro (el de la paleta se confunde con el rojo); además cambia la forma
ALTURA_CARTA = 620


def _texto_punto(fila, tipo: str, dispersion: str, unidad: str, decimales: int, con_fases: bool) -> str:
    """Tooltip de un punto de la carta de control."""
    por_muestra = tipo == "I-MR"
    titulo = f"Muestra {fila.punto}" if por_muestra else f"Turno {fila.punto}"
    if con_fases:
        titulo += f" · fase {fila.fase} ({fila.temporada})"
    fecha = f"{fila.fecha_produccion:%d/%m/%Y}" + (f" · hora {fila.fecha_hora:%H:%M}" if por_muestra else "")
    reglas = list(fila.reglas) + ([f"{dispersion} fuera de límites"] if fila.fuera_disp else [])
    simbolo = "Valor" if por_muestra else "X̄"
    lineas = [
        f"<b>{titulo}</b>",
        f"Fecha de producción: {fecha} · turno {fila.turno}",
        f"Proceso {fila.proceso or '—'} · programa {fila.programa or '—'} · MTPH {numero(fila.mtph, 1)}",
        f"{simbolo}: <b>{numero(fila.valor, decimales)} {unidad}</b>" + ("" if por_muestra else f" (n = {fila.n})"),
        f"{dispersion}: {numero(fila.disp, decimales)}",
        "Reglas violadas: " + (", ".join(str(r) for r in reglas) if reglas else "ninguna"),
    ]
    if fila.n < 2 and not por_muestra:
        lineas.append("⚠ Turno con 1 sola muestra: sin desvío")
    return "<br>".join(lineas)


def _limites(figura, x, tabla, prefijo: str, fila: int, mostrar_leyenda: bool) -> None:
    """LC continua y LCS/LCI punteadas en gris (en escalera si cambian con el tamaño del subgrupo)."""
    for columna, nombre, trazo in ((f"{prefijo}lcs", "LCS / LCI", "dot"), (f"{prefijo}lci", "LCS / LCI", "dot"),
                                   (f"{prefijo}lc", "Línea central", "solid")):
        figura.add_scatter(x=x, y=tabla[columna], mode="lines", line=dict(color=GRIS, width=1.2, dash=trazo, shape="hvh"),
                           name=nombre, legendgroup=nombre, hoverinfo="skip",
                           showlegend=mostrar_leyenda and columna != f"{prefijo}lci", row=fila, col=1)


def _etiquetas_limites(figura, tabla, prefijo: str, eje_y: str, central: str, decimales: int) -> None:
    """Valor de LCS, LC y LCI a la derecha de la carta, como Minitab.

    Si los límites cambian con el tamaño del subgrupo (X̄-S), se muestran los del tamaño más frecuente
    y se aclara el n.
    """
    con_limites = tabla.dropna(subset=[f"{prefijo}lc"])
    n_frecuente = int(con_limites["n"].mode().iloc[0])
    fila = con_limites[con_limites["n"] == n_frecuente].iloc[-1]
    variables = con_limites[f"{prefijo}lcs"].round(9).nunique() > 1
    for columna, nombre in ((f"{prefijo}lcs", "LCS"), (f"{prefijo}lc", central), (f"{prefijo}lci", "LCI")):
        texto = f"{nombre} = {numero(fila[columna], decimales)}" + (f" (n = {n_frecuente})" if variables else "")
        figura.add_annotation(x=1.005, xref="paper", xanchor="left", y=fila[columna], yref=eje_y, text=texto,
                              showarrow=False, font=dict(size=10, color=GRIS))


def carta_control(tabla: pd.DataFrame, tipo: str, dispersion: str, titulo_y: str, unidad: str, decimales: int,
                  fases: tuple | None = None) -> go.Figure:
    """Carta de control en dos paneles: X̄ o I arriba; R, S o MR abajo (eje X: número de punto).

    tabla: core.control.carta_de_control()["tabla"]. Línea de datos oscura, LC continua, LCS y LCI
    punteadas en gris; puntos fuera de control: cuadrado rojo (regla 1 o, abajo, fuera de límites) y
    rombo naranja (otras reglas de Nelson). fases: (temporada base, nueva) → línea vertical entre fases.
    """
    figura = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07, row_heights=[0.63, 0.37])
    x = tabla["punto"]
    textos = [_texto_punto(f, tipo, dispersion, unidad, decimales, fases is not None) for f in tabla.itertuples()]
    simbolo = "I" if tipo == "I-MR" else "X̄"

    # Panel de arriba: datos, límites y puntos que violan reglas
    figura.add_scatter(x=x, y=tabla["valor"], mode="lines+markers", name=f"{simbolo} {titulo_y}",
                       line=dict(color=COLOR_DATOS, width=1), marker=dict(color=COLOR_DATOS, size=4),
                       text=textos, hovertemplate="%{text}<extra></extra>", row=1, col=1)
    _limites(figura, x, tabla, "", 1, True)
    regla_1 = tabla["reglas"].map(lambda r: 1 in r)
    otras = tabla["reglas"].map(bool) & ~regla_1
    for seleccion, nombre, color, forma in ((regla_1, "Regla 1 (fuera de límites)", COLOR_REGLA_1, "square"),
                                            (otras, "Otras reglas de Nelson", COLOR_OTRAS_REGLAS, "diamond")):
        figura.add_scatter(x=x[seleccion], y=tabla.loc[seleccion, "valor"], mode="markers", name=nombre,
                           legendgroup=nombre, showlegend=bool(seleccion.any()),
                           marker=dict(color=color, symbol=forma, size=9,
                                                           line=dict(color="white", width=1)),
                           text=[t for t, s in zip(textos, seleccion) if s], hovertemplate="%{text}<extra></extra>",
                           row=1, col=1)

    # Panel de abajo: dispersión (R, S o MR) y sus límites
    figura.add_scatter(x=x, y=tabla["disp"], mode="lines+markers", name=dispersion, showlegend=False,
                       line=dict(color=COLOR_DATOS, width=1), marker=dict(color=COLOR_DATOS, size=4),
                       text=textos, hovertemplate="%{text}<extra></extra>", connectgaps=False, row=2, col=1)
    _limites(figura, x, tabla, "disp_", 2, False)
    fuera = tabla["fuera_disp"]
    figura.add_scatter(x=x[fuera], y=tabla.loc[fuera, "disp"], mode="markers", name="Regla 1 (fuera de límites)",
                       legendgroup="Regla 1 (fuera de límites)", showlegend=bool(fuera.any() and not regla_1.any()),
                       marker=dict(color=COLOR_REGLA_1, symbol="square", size=9, line=dict(color="white", width=1)),
                       text=[t for t, s in zip(textos, fuera) if s], hovertemplate="%{text}<extra></extra>",
                       row=2, col=1)

    _etiquetas_limites(figura, tabla, "", "y", simbolo if simbolo == "I" else "X̿", decimales)
    _etiquetas_limites(figura, tabla, "disp_", "y2", f"{dispersion}̄", decimales)

    if fases is not None and (tabla["fase"] == "II").any():
        corte = tabla.loc[tabla["fase"] == "II", "punto"].iloc[0] - 0.5
        figura.add_vline(x=corte, line=dict(color=COLOR_DATOS, width=1.5), row="all", col=1)
        for fase, temporada, ancla, desplazamiento in (("I", fases[0], "right", -6), ("II", fases[1], "left", 6)):
            texto = f"Fase {fase} · {temporada}" + (" (límites)" if fase == "I" else " (límites de fase I)")
            figura.add_annotation(x=corte, y=1, xref="x", yref="paper", yanchor="bottom", xanchor=ancla,
                                  xshift=desplazamiento, text=texto, showarrow=False,
                                  font=dict(size=11, color=COLOR_DATOS))

    eje_x = "Muestra (número)" if tipo == "I-MR" else "Turno (número de subgrupo)"
    figura.update_xaxes(title_text=eje_x, row=2, col=1)
    figura.update_yaxes(title_text=f"{simbolo} ({unidad})", row=1, col=1)
    figura.update_yaxes(title_text=f"{dispersion} ({unidad})", rangemode="tozero", row=2, col=1)
    estilo(figura, ALTURA_CARTA)
    figura.update_layout(margin=dict(t=60, b=10, l=10, r=125), legend=dict(y=1.06), hovermode="closest")
    return figura


# ---------------------------------------------------------------- confiabilidad del laboratorio

# Divergente para "más o menos de lo esperado": azul (menos) – gris claro (lo esperado) – rojo (más).
ESCALA_DIVERGENTE = [[0, "#2a78d6"], [0.5, "#f2f2f2"], [1, "#e34948"]]


def boxplot_por_analista(datos: pd.DataFrame, variable: str, titulo_y: str, unidad: str,
                         decimales: int = 2) -> go.Figure:
    """Boxplot de una variable por analista, con Entrada y Salida lado a lado."""
    figura = go.Figure()
    for lugar in LUGARES:
        _cajas(figura, datos[datos["lugar"] == lugar], variable, "analista", str, lugar, COLORES[lugar], unidad,
               decimales, offsetgroup=lugar)
    figura.update_layout(boxmode="group", scattermode="group", yaxis_title=titulo_y,
                         xaxis=dict(type="category", title="Analista", categoryorder="category ascending"))
    return estilo(figura)


def histograma_forma(valores: pd.Series, titulo_x: str, unidad: str, color: str, nombre: str, referencia: dict,
                     kde: tuple, forma: dict, decimales: int = 2) -> go.Figure:
    """Histograma de un lugar con su curva normal, la densidad real (KDE, punteada) y la referencia de la planta.

    referencia: una fila de core.laboratorio.referencia_planta() (q1, mediana, q3): la franja gris es el
    rango intercuartil y la línea, la mediana. kde: core.laboratorio.curva_kde(). forma:
    core.laboratorio.forma_distribucion() (media y desvío de la curva normal; AD y p en el título).
    """
    valores = valores.dropna()
    figura = go.Figure()
    ancho = ancho_de_clase(valores)
    clases = None
    if ancho:
        clases = dict(start=math.floor(valores.min() / ancho) * ancho, size=ancho)
    if referencia.get("q1") is not None:
        figura.add_vrect(x0=referencia["q1"], x1=referencia["q3"], fillcolor=GRIS, opacity=0.15, line_width=0,
                         layer="below")
        figura.add_vline(x=referencia["mediana"], line=dict(color=GRIS, width=1.5))
        figura.add_scatter(x=[None], y=[None], mode="markers", name=(
            f"Planta: mediana {numero(referencia['mediana'], decimales)} · IQR {numero(referencia['q1'], decimales)}–"
            f"{numero(referencia['q3'], decimales)} {unidad}"),
            marker=dict(symbol="square", size=12, color=GRIS, opacity=0.4))
    figura.add_histogram(x=valores, histnorm="probability density", xbins=clases, name=f"{nombre} · N {numero(len(valores), 0)}",
                         marker=dict(color=color, line=dict(color="white", width=0.5)), opacity=0.6,
                         hovertemplate=f"%{{x}} {unidad}<br>densidad %{{y:.3f}}<extra></extra>")
    if forma.get("desvio"):
        media, s = forma["media"], forma["desvio"]
        x, y = curva_normal(media, s, max(valores.min() - s / 2, media - 4 * s), min(valores.max() + s / 2, media + 4 * s))
        figura.add_scatter(x=x, y=y, mode="lines", name="Curva normal", line=dict(color=color, width=2),
                           hoverinfo="skip")
    if len(kde[0]):
        figura.add_scatter(x=kde[0], y=kde[1], mode="lines", name="Densidad real (KDE)",
                           line=dict(color=COLOR_DATOS, width=2, dash="dot"), hoverinfo="skip")
    titulo = nombre if forma.get("ad") is None else (
        f"{nombre} · AD = {numero(forma['ad'], 1)} · {texto_p_ad(forma['p'])}")
    figura.update_layout(xaxis_title=titulo_x, yaxis_title="Densidad", title=dict(text=titulo, font_size=13))
    estilo(figura, ALTURA_CHICA)
    figura.update_layout(margin=dict(t=90), legend=dict(y=1.0, yanchor="bottom", font_size=10),
                         title=dict(y=0.98, yanchor="top"))
    return figura


def grafico_probabilidad(tabla: pd.DataFrame, recta: dict, titulo_y: str, color: str, nombre: str,
                         decimales: int = 2) -> go.Figure:
    """Gráfico de probabilidad normal: datos ordenados contra su cuantil teórico, con la recta x̄ + s·z.

    tabla y recta: core.laboratorio.probabilidad_normal(). Si los datos son normales, quedan sobre la recta.
    """
    figura = go.Figure()
    figura.add_scatter(x=tabla["z"], y=tabla["valor"], mode="markers", name="Datos ordenados",
                       marker=dict(color=color, size=4, opacity=0.5),
                       customdata=list(zip(tabla["porcentaje"].map(lambda v: numero(v, 1)),
                                           tabla["valor"].map(lambda v: numero(v, decimales)))),
                       hovertemplate=f"{titulo_y}: %{{customdata[1]}}<br>Percentil %{{customdata[0]}} %<extra></extra>")
    if recta["desvio"]:
        extremos = np.array([tabla["z"].min(), tabla["z"].max()])
        figura.add_scatter(x=extremos, y=recta["media"] + recta["desvio"] * extremos, mode="lines",
                           name="Normal de referencia", line=dict(color=COLOR_DATOS, width=2), hoverinfo="skip")
    figura.update_layout(xaxis_title="Cuantil teórico de la normal (z)", yaxis_title=titulo_y,
                         title=dict(text=f"Probabilidad normal · {nombre}", font_size=13))
    estilo(figura, ALTURA_CHICA)
    figura.update_layout(margin=dict(t=90), legend=dict(y=1.0, yanchor="bottom", font_size=10),
                         title=dict(y=0.98, yanchor="top"))
    return figura


def barras_por_lugar(tabla: pd.DataFrame, x: str, titulo_x: str, eje: list[str] | None = None) -> go.Figure:
    """% de muestras a 5 g fijo (pct_fijo) por categoría, con Entrada y Salida lado a lado.

    tabla: core.laboratorio.porcentaje_fijo(). eje: orden de las categorías (para meses, con huecos:
    core.mensual.eje_de_meses()). Valor sobre cada barra; N en el tooltip; N < 3 tenue y con aviso.
    """
    figura = go.Figure()
    for lugar in LUGARES:
        sub = tabla[tabla["lugar"] == lugar]
        if sub.empty:
            continue
        chicos = (sub["n"] < 3).tolist()
        figura.add_bar(
            x=sub[x].astype(str), y=sub["pct_fijo"], name=lugar,
            marker=dict(color=COLORES[lugar], opacity=[0.3 if c else 1 for c in chicos], line=dict(color="white", width=1)),
            text=sub["pct_fijo"].map(lambda v: numero(v, 0)), textposition="outside", cliponaxis=False,
            textfont=dict(size=10),
            customdata=list(zip(sub["n_fijo"].map(lambda v: numero(v, 0)), sub["n"].map(lambda v: numero(v, 0)),
                                ["<br><b>⚠ N < 3</b>" if c else "" for c in chicos])),
            hovertemplate=f"<b>{lugar} · %{{x}}</b><br>5 g fijo: %{{y:.1f}} %<br>%{{customdata[0]}} de "
                          "%{customdata[1]} muestras%{customdata[2]}<extra></extra>")
    if eje:
        huecos = [e for e in eje if not e.strip()]
        if huecos:
            figura.add_scatter(x=huecos, y=[None] * len(huecos), mode="markers", showlegend=False, hoverinfo="skip")
        figura.update_xaxes(type="category", categoryorder="array", categoryarray=eje)
    else:
        figura.update_xaxes(type="category")
    figura.update_layout(barmode="group", bargap=0.25, xaxis_title=titulo_x, yaxis_title="% de muestras a 5 g fijo",
                         yaxis=dict(range=[0, max(5.0, float(tabla["pct_fijo"].max() or 0)) * 1.15]))
    return estilo(figura, ALTURA_CHICA)


def mapa_digitos(tabla: pd.DataFrame, titulo: str) -> go.Figure:
    """Mapa de calor analista × dígito con el % de veces que se anotó cada dígito (esperado: 10 %).

    tabla: core.laboratorio.preferencia_digitos(). Rojo = más de lo esperado, azul = menos; el N y
    el valor p del chi² de cada analista van en la etiqueta de la fila.
    """
    porcentajes = tabla[[f"pct_{d}" for d in range(10)]].to_numpy(dtype=float)
    filas = [f"{g} (N {numero(n, 0)}, {'sin prueba' if pd.isna(p) else texto_p(p)})"
             for g, n, p in zip(tabla["grupo"], tabla["n"], tabla["p"])]
    desvio = max(5.0, float(np.nanmax(np.abs(porcentajes - 10)))) if porcentajes.size else 5.0
    figura = go.Figure(go.Heatmap(
        z=porcentajes, x=[str(d) for d in range(10)], y=filas, zmid=10, zmin=10 - desvio, zmax=10 + desvio,
        colorscale=ESCALA_DIVERGENTE, xgap=2, ygap=2,
        text=[[numero(v, 1) for v in fila] for fila in porcentajes], texttemplate="%{text}",
        textfont=dict(size=11), colorbar=dict(title="%", thickness=12),
        hovertemplate="<b>%{y}</b><br>Dígito %{x}: %{text} % (esperado 10 %)<extra></extra>"))
    figura.update_layout(title=dict(text=titulo, font_size=14), xaxis_title="Dígito",
                         yaxis=dict(autorange="reversed"))
    return estilo(figura, 120 + 40 * max(len(filas), 1))


def grafico_normalidad(tabla: pd.DataFrame, periodos: pd.DataFrame, titulo_y: str, decimales: int = 4) -> go.Figure:
    """Carta I-MR de la normalidad por día de producción, con los períodos largos sombreados.

    tabla: core.laboratorio.carta_normalidad() (un renglón por día, con fecha_produccion y n); periodos:
    core.laboratorio.periodos_misma_normalidad() (se sombrean los que tienen largo = True).
    """
    figura = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07, row_heights=[0.63, 0.37])
    x = np.arange(1, len(tabla) + 1)
    textos = [f"<b>{f:%d/%m/%Y}</b><br>Normalidad {numero(v, decimales)} N<br>MR {numero(mr, decimales)}<br>"
              f"{n} muestras" for f, v, mr, n in zip(tabla["fecha_produccion"], tabla["valor"], tabla["disp"], tabla["n"])]
    for panel, columna, prefijo in ((1, "valor", ""), (2, "disp", "disp_")):
        figura.add_scatter(x=x, y=tabla[columna], mode="lines+markers" if panel == 1 else "markers",
                           name="Normalidad del día", showlegend=panel == 1, line=dict(color=COLOR_DATOS, width=1),
                           marker=dict(color=COLOR_DATOS, size=4), text=textos, hovertemplate="%{text}<extra></extra>",
                           row=panel, col=1)
        _limites(figura, x, tabla, prefijo, panel, panel == 1)
        fuera = (tabla["reglas"].map(bool) if panel == 1 else tabla["fuera_disp"]).to_numpy(dtype=bool)
        figura.add_scatter(x=x[fuera], y=tabla[columna].to_numpy()[fuera], mode="markers",
                           name="Fuera de límites", legendgroup="fuera", showlegend=panel == 1 and bool(fuera.any()),
                           marker=dict(color=COLOR_REGLA_1, symbol="square", size=8, line=dict(color="white", width=1)),
                           text=[t for t, s in zip(textos, fuera) if s], hovertemplate="%{text}<extra></extra>",
                           row=panel, col=1)
    _etiquetas_limites(figura, tabla, "", "y", "I", decimales)
    _etiquetas_limites(figura, tabla, "disp_", "y2", "MR̄", decimales)
    largos = periodos[periodos["largo"]]
    for periodo in largos.to_dict("records"):
        figura.add_vrect(x0=periodo["primero"] + 0.5, x1=periodo["ultimo"] + 1.5, fillcolor=COLOR_OTRAS_REGLAS,
                         opacity=0.18, line_width=0, layer="below", row="all", col=1)
        figura.add_annotation(x=(periodo["primero"] + periodo["ultimo"]) / 2 + 1, y=1, xref="x", yref="paper",
                              yanchor="bottom", text=f"{periodo['dias']} días", showarrow=False,
                              font=dict(size=10, color="#a35f00"))
    if not largos.empty:    # entrada en la leyenda para la franja
        figura.add_scatter(x=[None], y=[None], mode="markers", name=f"Misma normalidad más de {DIAS_MISMA_NORMALIDAD} días",
                           marker=dict(symbol="square", size=12, color=COLOR_OTRAS_REGLAS, opacity=0.4), row=1, col=1)
    if not tabla.empty:     # fechas en el eje X (unas 12 marcas)
        paso = max(1, len(tabla) // 12)
        figura.update_xaxes(tickvals=x[::paso], ticktext=[f"{f:%d/%m/%y}" for f in tabla["fecha_produccion"].iloc[::paso]],
                            row=2, col=1)
    figura.update_xaxes(title_text="Día de producción (solo días con datos)", row=2, col=1)
    figura.update_yaxes(title_text=titulo_y, tickformat=f".{decimales}f", row=1, col=1)
    figura.update_yaxes(title_text="MR", tickformat=f".{decimales}f", rangemode="tozero", row=2, col=1)
    estilo(figura, 520)
    figura.update_layout(margin=dict(t=70, b=10, l=10, r=110), legend=dict(y=1.1), hovermode="closest")
    return figura


def dispersion_por_lugar(datos: pd.DataFrame, x: str, y: str, regresiones: pd.DataFrame, titulo_x: str,
                         titulo_y: str, unidad_y: str, dec_x: int = 2, dec_y: int = 2) -> go.Figure:
    """Dispersión de y vs x con un panel por lugar (Entrada, Salida), cada uno con su eje Y y su recta.

    regresiones: core.laboratorio.gramaje_vs_resultado() (lugar y las columnas de regresion_lineal()).
    """
    datos = datos.dropna(subset=[x, y])
    regs = {f["lugar"]: f for f in regresiones.to_dict("records")}

    def titulo(lugar):
        reg = regs.get(lugar)
        if reg is None or pd.isna(reg["r2"]):
            return lugar
        return f"{lugar} · R² {numero(reg['r2'], 2)} · {texto_p(reg['p'])} · N {numero(reg['n'], 0)}"

    figura = make_subplots(rows=1, cols=2, subplot_titles=[titulo(l) for l in LUGARES], horizontal_spacing=0.08)
    for columna, lugar in enumerate(LUGARES, start=1):
        sub = datos[datos["lugar"] == lugar]
        if sub.empty:
            continue
        figura.add_scatter(
            row=1, col=columna, x=sub[x], y=sub[y], mode="markers", name=lugar, showlegend=False,
            marker=dict(color=COLORES[lugar], size=5, opacity=0.3),
            customdata=list(zip(sub["fecha_hora"].dt.strftime("%d/%m/%Y %H:%M"), sub["eboe"],
                                sub["analista"].fillna("—"), sub[x].map(lambda v: numero(v, dec_x)),
                                sub[y].map(lambda v: numero(v, dec_y)))),
            hovertemplate=f"<b>{lugar} · eBOE %{{customdata[1]}}</b><br>%{{customdata[0]}} · %{{customdata[2]}}"
                          f"<br>{titulo_x}: %{{customdata[3]}}<br>{titulo_y}: %{{customdata[4]}}<extra></extra>")
        reg = regs.get(lugar)
        if reg is not None and not pd.isna(reg["pendiente"]):
            extremos = [sub[x].min(), sub[x].max()]
            valores = recta(reg, extremos)
            figura.add_scatter(row=1, col=columna, x=extremos, y=valores, mode="lines", showlegend=False,
                               line=dict(color="white", width=6), hoverinfo="skip")
            figura.add_scatter(row=1, col=columna, x=extremos, y=valores, mode="lines", showlegend=False,
                               line=dict(color=COLOR_DATOS, width=2.5),
                               hovertemplate=f"<b>Recta {lugar.lower()}</b><br>Pendiente "
                                             f"{numero(reg['pendiente'], 3)} {unidad_y} por g<extra></extra>")
        figura.update_xaxes(title_text=titulo_x, row=1, col=columna)
        figura.update_yaxes(title_text=titulo_y, row=1, col=columna)
    figura.update_annotations(font_size=12)
    estilo(figura)
    figura.update_layout(margin=dict(t=60))
    return figura


# ---------------------------------------------------------------- factores

COLOR_EMPEORA = "#e34948"
COLOR_MEJORA = "#1baf7a"


def columnas_agrupadas(tabla: pd.DataFrame, x: str, grupo: str, y: str, titulo_y: str, unidad: str,
                       decimales: int = 1, panel: str | None = None, nombres_grupo: dict | None = None,
                       titulo_x: str = "", rango_y: list | None = None) -> go.Figure:
    """Columnas agrupadas: una categoría por x y un color por grupo (orden fijo de la paleta).

    tabla tiene las columnas x, grupo, y y n (N en el tooltip; N < 3 tenue y con aviso). panel: columna
    para separar en paneles lado a lado (mismo eje Y). nombres_grupo: {valor: nombre en la leyenda}.
    """
    paneles = sorted(tabla[panel].unique()) if panel else [None]
    figura = make_subplots(rows=1, cols=len(paneles), shared_yaxes=True, horizontal_spacing=0.04,
                           subplot_titles=[str(p) for p in paneles] if panel else None)
    grupos = list(dict.fromkeys(tabla[grupo]))
    nombres_grupo = nombres_grupo or {}
    for columna, valor_panel in enumerate(paneles, start=1):
        datos = tabla if panel is None else tabla[tabla[panel] == valor_panel]
        for k, g in enumerate(grupos):
            sub = datos[datos[grupo] == g].dropna(subset=[y])
            if sub.empty:
                continue
            chicos = (sub["n"] < 3).tolist()
            nombre = nombres_grupo.get(g, str(g))
            figura.add_bar(
                row=1, col=columna, x=sub[x].astype(str), y=sub[y], name=nombre, legendgroup=nombre,
                showlegend=columna == 1, offsetgroup=str(k),
                marker=dict(color=PALETA[k % len(PALETA)], opacity=[0.3 if c else 1 for c in chicos],
                            line=dict(color="white", width=1)),
                text=sub[y].map(lambda v: numero(v, decimales)), textposition="outside", cliponaxis=False,
                textfont=dict(size=10),
                customdata=list(zip(sub["n"].map(lambda v: numero(v, 0)),
                                    ["<br><b>⚠ N < 3: poco representativo</b>" if c else "" for c in chicos],
                                    sub[y].map(lambda v: numero(v, decimales)))),
                hovertemplate=f"<b>{nombre} · %{{x}}</b><br>{titulo_y}: %{{customdata[2]}} {unidad}<br>"
                              "N %{customdata[0]}%{customdata[1]}<extra></extra>")
        figura.update_xaxes(type="category", title_text=titulo_x, row=1, col=columna)
    figura.update_layout(barmode="group", bargap=0.2, bargroupgap=0.05)
    figura.update_yaxes(title_text=titulo_y, row=1, col=1)
    if rango_y:
        figura.update_yaxes(range=rango_y)
    estilo(figura)
    if panel:
        figura.update_layout(margin=dict(t=70), legend=dict(y=1.12))
    return figura


def cascada(resultado: dict, titulo_y: str, unidad: str, decimales: int, mejor_si_sube: bool) -> go.Figure:
    """Cascada de la descomposición entre temporadas: base → calibre → resto → nueva.

    resultado: core.factores.descomponer(). Rojo lo que empeora y verde lo que mejora (según
    mejor_si_sube); los totales en gris.
    """
    pasos = [resultado["calibre"], resultado["resto"]]
    colores = {"increasing": COLOR_MEJORA if mejor_si_sube else COLOR_EMPEORA,
               "decreasing": COLOR_EMPEORA if mejor_si_sube else COLOR_MEJORA}
    figura = go.Figure(go.Waterfall(
        x=[str(resultado["base"]), "Cambio de calibre", "Resto", str(resultado["nueva"])],
        measure=["absolute", "relative", "relative", "total"],
        y=[resultado["valor_base"], *pasos, resultado["valor_nueva"]],
        text=[numero(resultado["valor_base"], decimales), *(("+" if p >= 0 else "−") + numero(abs(p), decimales)
                                                             for p in pasos), numero(resultado["valor_nueva"], decimales)],
        textposition="outside", cliponaxis=False,
        increasing=dict(marker=dict(color=colores["increasing"])), decreasing=dict(marker=dict(color=colores["decreasing"])),
        totals=dict(marker=dict(color=GRIS)), connector=dict(line=dict(color=GRIS, width=1, dash="dot")),
        hovertemplate=f"%{{x}}: %{{text}} {unidad}<extra></extra>", showlegend=False))
    valores = [resultado["valor_base"], resultado["estandarizado"], resultado["valor_nueva"]]
    margen = max(abs(resultado["total"]), abs(resultado["calibre"]), abs(resultado["resto"])) * 0.8 or 1
    figura.update_layout(yaxis=dict(title=titulo_y, range=[min(valores) - margen, max(valores) + margen]))
    return estilo(figura, ALTURA_CHICA)


def intervalos_agrupados(tabla: pd.DataFrame, titulo_y: str, unidad: str, decimales: int, titulo_x: str) -> go.Figure:
    """Media e IC del 95 % por categoría, con un color por temporada (lado a lado).

    tabla: core.factores.por_categoria() (temporada, grupo, n, media, ic_inf, ic_sup).
    """
    figura = go.Figure()
    orden = list(dict.fromkeys(tabla["grupo"]))
    for k, (temporada, sub) in enumerate(tabla.groupby("temporada", sort=True)):
        color = PALETA[k % len(PALETA)]
        figura.add_scatter(
            x=sub["grupo"], y=sub["media"], mode="markers", name=str(temporada), offsetgroup=str(temporada),
            marker=dict(size=9, color=color),
            error_y=dict(type="data", symmetric=False, array=sub["ic_sup"] - sub["media"],
                         arrayminus=sub["media"] - sub["ic_inf"], color=color, thickness=2, width=8),
            customdata=list(zip(*(sub[c].map(lambda v: numero(v, decimales)) for c in ("media", "ic_inf", "ic_sup")),
                                sub["n"].map(lambda v: numero(v, 0)))),
            hovertemplate=f"<b>{temporada} · %{{x}}</b><br>Media %{{customdata[0]}} {unidad}<br>IC 95 %: "
                          f"%{{customdata[1]}} a %{{customdata[2]}} {unidad}<br>N %{{customdata[3]}}<extra></extra>")
    margen = (tabla["ic_sup"].max() - tabla["ic_inf"].min()) * 0.15 or 1
    figura.update_layout(scattermode="group", yaxis=dict(title=titulo_y, range=[tabla["ic_inf"].min() - margen,
                                                                               tabla["ic_sup"].max() + margen]),
                         xaxis=dict(type="category", title=titulo_x, categoryorder="array", categoryarray=orden))
    return estilo(figura, ALTURA_CHICA)


def barras_importancia(importancia: pd.DataFrame, sin_explicar: float, titulo: str) -> go.Figure:
    """"¿Qué pesa más?": % de la variación que explica cada factor por sí solo, en barras horizontales.

    importancia: core.factores.modelo_multivariable()["importancia"] (nombre, pct, p), ya ordenada.
    """
    datos = importancia.iloc[::-1]          # el más importante arriba
    figura = go.Figure(go.Bar(
        x=datos["pct"], y=datos["nombre"], orientation="h", marker=dict(color=COLOR_SERIE),
        text=datos["pct"].map(lambda v: f"{numero(v, 1)} %"), textposition="outside", cliponaxis=False,
        customdata=datos["p"].map(texto_p),
        hovertemplate="<b>%{y}</b><br>%{x:.1f} % de la variación<br>%{customdata}<extra></extra>", showlegend=False))
    figura.update_layout(title=dict(text=titulo, font_size=14),
                         xaxis=dict(title=f"% de la variación que explica por sí solo (sin explicar: "
                                          f"{numero(sin_explicar, 0)} %)", range=[0, datos["pct"].max() * 1.2]),
                         yaxis=dict(title=""))
    return estilo(figura, 120 + 32 * len(datos))
