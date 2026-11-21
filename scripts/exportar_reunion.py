"""Exporta las figuras de la reunión de cierre de una planta: un PNG por figura, un Excel con sus datos (mismo nombre),
indice.md y resumen.json.

Uso:
    python scripts/exportar_reunion.py                     # LEDESMA, sin nombres de personas
    python scripts/exportar_reunion.py --con-nombres       # con nombres SOLO en las figuras 19d y 20b/20c
    python scripts/exportar_reunion.py --sin-png           # sin kaleido: guarda la figura como .plotly.json (pruebas)

Salida: data/capturas/reunion_<planta>_<año>/ (data/ no se sube a GitHub).

Reglas (CLAUDE.md):
- Corre como el modo presentación, salvo la sección 20 (equipos de trabajo): nunca la coherencia del muestreo del licor,
  ni valores en USD, ni texto o número que reste eficiencias de etapas distintas (eBOE, centrífugas, proceso): se
  muestran juntas, sin restarlas.
- Nombres de operadores y químicos: solo con --con-nombres y solo en las figuras de personas (19d, 20b, 20c). Sin el
  flag, anonimizados (Operador A, Químico A…). Nunca en indice.md ni en resumen.json.
- Filtros: cada temporada completa (los botones de temporada de la app), sin entradas repetidas en las comparaciones
  entre eBOE; los outliers se marcan, no se borran.
- Toda la estadística sale de core/ y los gráficos de app/graficos.py; acá solo se arman, se les pone el estilo de
  Brown (colores fijos por temporada y por eBOE) y se guardan.
"""

import argparse
import json
import sys
import traceback
import warnings
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "app"))      # graficos.py vive en app/ (no usa Streamlit)
sys.path.insert(0, str(RAIZ))

import graficos as g  # noqa: E402
from sqlalchemy import select  # noqa: E402

from core.analisis import Filtros, pares_con_emulsion, tabla_analisis, tabla_emulsion  # noqa: E402
from core.analisis_centrifugas import (ORDEN_EQUIPOS, PRIMARIA, SECUNDARIA, VARIABLES as VARIABLES_CEN,  # noqa: E402
                                       balance_por_muestreo, caudal_nominal_primaria, por_muestreo,
                                       primarias_por_turno, rangos_centrifugas, resumen_temporadas, tabla_muestras,
                                       valores_por_variable)
from core.cambios_operativos import listar_cambios, marcas  # noqa: E402
from core.capacidad import sigma_dentro_individuales  # noqa: E402
from core.cartas_periodo import carta_periodo, equipos_de_variable, serie_periodo  # noqa: E402
from core.comparaciones import comparar_grupos  # noqa: E402
from core.condiciones_primaria import cp_liviana_por_muestreo, datos_primarias, por_cuantiles  # noqa: E402
from core.consultas import rango_referencia  # noqa: E402
from core.db import crear_motor, crear_sesion  # noqa: E402
from core.efectos import (NOMBRES, captado_por_turno, datos_por_turno, descomponer_por_mezcla,  # noqa: E402
                          modelo_efectos, modelo_interaccion, orden_de_niveles, por_mtph, preparar_datos,
                          segmentada_por_temporada)
from core.emulsion import dilucion_por_turno, resumen_dilucion  # noqa: E402
from core.exportar import excel_de_tablas, nombre_de_archivo  # noqa: E402
from core.factores import (agregar_dia_proceso, cortes_cuartiles, esponja_por_cuartil, por_categoria,  # noqa: E402
                           preparar_pares, separar_errores)
from core.fase_pesada import mediana_por, parametros_fase_pesada, tabla_fase_pesada  # noqa: E402
from core.formato import con_signo, numero, texto_p  # noqa: E402
from core.gestion_alertas import listar_alertas  # noqa: E402
from core.indicadores import resumen_eboe_por_temporada  # noqa: E402
from core.inicio import TEMPORADAS_BRIX_SIN_VERIFICAR, cargar_flowsheet, datos_flowsheet, temporadas_con_datos  # noqa: E402
from core.mensual import (cajas_por_mes, eficiencia_eboe_planta_por_mes, eficiencia_proceso_por_mes,  # noqa: E402
                          eje_de_meses, etiqueta_mes, mes_de, por_mes_y_eboe)
from core.models import Planta  # noqa: E402
from core.muestreo import diferencia_por_eboe, eficiencia_por_calibre, pares_calibre, prueba_pareada  # noqa: E402
from core.parametros import validar_parametros  # noqa: E402
from core.personas import (ACLARACION, AJUSTE_EBOE, AJUSTE_PRIMARIA, anonimizar, brecha_turno_t,  # noqa: E402
                           datos_eboe, datos_primaria, lectura_por_quimico, medias_por_grupo, operadores_eboe,
                           personas_centrifugas, turno_por_temporada)
from core.proceso import filtrar_produccion, resumen_proceso  # noqa: E402
from core.produccion import tabla_produccion, toneladas_por_turno  # noqa: E402
from core.repetidas import sin_entradas_repetidas  # noqa: E402
from core.volatilizacion import RANGOS_FP, eficiencias_por_dia, media_ic  # noqa: E402

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------- estilo Brown

VERDE, NARANJA, NARANJA_SUAVE, OXIDO, TEXTO = "#1F4034", "#FF8710", "#FFB03E", "#DA4E00", "#333333"
GRIS_CLARO, GRIS_OSCURO = "#A6A6A6", "#5C5C5C"
FUENTE = "Barlow, Arial, sans-serif"          # Barlow si está instalada; si no, Arial
COLORES_TEMPORADA = {"2024": GRIS_CLARO, "2025": VERDE, "2026": NARANJA}
COLORES_EBOE = {"eBOE 1": OXIDO, "eBOE 2": NARANJA_SUAVE, "eBOE 3": GRIS_OSCURO}
COLORES = {**COLORES_TEMPORADA, **COLORES_EBOE}
COLORES_ETAPA = {"eBOE": VERDE, "Primaria": NARANJA, "Proceso (tambor)": OXIDO}
ANCHO, ALTO, ESCALA = 1600, 900, 2
PERSONAS = ("19d", "20b", "20c")               # las únicas figuras que pueden llevar nombres (con --con-nombres)
MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
SIN_REPETIDAS = "sin entradas repetidas"


def _con_alfa(color: str, alfa: float) -> str:
    color = color.lstrip("#")
    r, v, a = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{v},{a},{alfa})"


def recolorear(figura: go.Figure) -> go.Figure:
    """Colores fijos: cada traza que se llama como una temporada ("2025") o un eBOE ("eBOE 2") toma su color de
    marca, en todas las figuras igual. Los outliers de una caja (traza sin nombre justo después) van con su caja."""
    anterior = None
    for traza in figura.data:
        color = COLORES.get(str(traza.name)) if traza.name is not None else None
        if color is None and traza.name is None and traza.type == "scatter" and anterior is not None:
            traza.marker.color = anterior
            continue
        anterior = color if traza.type == "box" else None
        if color is None:
            continue
        if traza.type == "bar":
            traza.marker.color = color
        elif traza.type == "box":
            traza.marker.color, traza.line.color, traza.fillcolor = color, color, _con_alfa(color, 0.3)
        elif traza.type == "scatter":
            traza.marker.color = color
            traza.line.color = color
            if traza.error_y is not None and traza.error_y.array is not None:
                traza.error_y.color = color
    return figura


def estilizar(figura: go.Figure, titulo: str, subtitulo: str, alto: int = ALTO) -> go.Figure:
    """Estilo de Brown: fondo blanco, Barlow, título corto en verde y el N en el subtítulo."""
    recolorear(figura)
    figura.update_layout(
        template="plotly_white", paper_bgcolor="white", plot_bgcolor="white", separators=",.",
        font=dict(family=FUENTE, color=TEXTO, size=16), width=ANCHO, height=alto,
        title=dict(text=f"<b>{titulo}</b><br><span style='font-size:16px;color:#666666'>{subtitulo}</span>",
                   x=0.01, xanchor="left", y=0.97, yanchor="top", font=dict(size=26, color=VERDE)),
        margin=dict(t=130, l=80, r=40, b=70),
        legend=dict(orientation="h", x=1, xanchor="right", y=1.01, yanchor="bottom", font=dict(size=15)))
    return figura


def barras_ic(tabla: pd.DataFrame, x: str, titulo_y: str, decimales: int = 1, orden_x: list | None = None) -> go.Figure:
    """Barras agrupadas por temporada con su IC 95 % (tabla: x, temporada, n, media, ic_inf, ic_sup). El valor va
    arriba de cada barra y el N en el tooltip."""
    figura = go.Figure()
    for temporada, sub in tabla.groupby("temporada", sort=True):
        sub = sub.set_index(x).reindex(orden_x).dropna(subset=["media"]).reset_index() if orden_x else sub
        figura.add_bar(x=sub[x], y=sub["media"], name=str(temporada),
                       text=[numero(v, decimales) for v in sub["media"]], textposition="inside", insidetextanchor="end",
                       textfont=dict(color="white", size=17),
                       error_y=dict(type="data", symmetric=False, array=sub["ic_sup"] - sub["media"],
                                    arrayminus=sub["media"] - sub["ic_inf"], thickness=1.5, width=6, color=TEXTO),
                       customdata=sub["n"], hovertemplate="%{x}: %{y:.1f} · N %{customdata}<extra></extra>")
    figura.update_layout(barmode="group", yaxis_title=titulo_y)
    return figura


def lineas(tabla: pd.DataFrame, x: str, serie: str, y: str, titulo_y: str, orden_x: list, colores: dict,
           decimales: int = 1) -> go.Figure:
    """Una línea por serie (por ejemplo, por temporada o por etapa) sobre categorías ordenadas; N en el tooltip."""
    figura = go.Figure()
    for nombre, sub in tabla.groupby(serie, sort=True):
        sub = sub.set_index(x).reindex(orden_x).reset_index()
        figura.add_scatter(x=sub[x], y=sub[y], name=str(nombre), mode="lines+markers+text",
                           line=dict(color=colores.get(str(nombre)), width=3), marker=dict(size=10),
                           text=[numero(v, decimales) if pd.notna(v) else "" for v in sub[y]], textposition="top center",
                           customdata=sub["n"], hovertemplate="%{x}: %{y:.2f} · N %{customdata}<extra></extra>")
    figura.update_layout(yaxis_title=titulo_y, xaxis=dict(type="category", categoryorder="array", categoryarray=orden_x))
    return figura


def mes_corto(mes) -> str:
    """Período, fecha o etiqueta ("Ago 2024") → "Ago"."""
    primera = str(mes).split(" ")[0]
    return primera if primera in MESES else MESES[pd.Timestamp(str(mes)).month - 1]


def orden_meses(valores) -> list[str]:
    return sorted(set(valores), key=MESES.index)


def texto_n(n) -> str:
    return f"N = {numero(int(n), 0)}" + (" ⚠ N < 3" if int(n) < 3 else "")


# ---------------------------------------------------------------- datos

@dataclass
class Figura:
    codigo: str
    titulo: str
    filtro: str = ""
    n: str = ""
    frase: str = ""
    figura: go.Figure | None = None
    tabla: pd.DataFrame | None = None
    motivo: str | None = None          # no generada: por qué
    archivos: list = field(default_factory=list)


class Contexto:
    """Todo lo que leen las figuras, leído una sola vez con las funciones de core (las mismas de la app)."""

    def __init__(self, sesion, planta: Planta, con_nombres: bool, periodo_carta: tuple[date, date]):
        self.sesion, self.planta, self.con_nombres, self.periodo_carta = sesion, planta, con_nombres, periodo_carta
        pid = planta.id
        self.temporadas = temporadas_con_datos(sesion, planta)              # {2024: (desde, hasta)}, como los botones
        self.actual = max(self.temporadas)
        self.fruta = tabla_analisis(sesion, pid)
        self.emulsion = tabla_emulsion(sesion, pid)
        self.toneladas = toneladas_por_turno(sesion, pid)
        produccion = tabla_produccion(sesion, pid)
        self.produccion = {t: filtrar_produccion(produccion, Filtros(desde=d, hasta=h))
                           for t, (d, h) in self.temporadas.items()}
        self.pares = pares_con_emulsion(self.fruta, self.emulsion)
        self.pares_comp = sin_entradas_repetidas(self.pares)              # para comparar eBOE
        self.muestras = tabla_muestras(sesion, pid)
        self.balance = balance_por_muestreo(por_muestreo(self.muestras)) if not self.muestras.empty else self.muestras
        self.rangos_cen = rangos_centrifugas(sesion, pid)
        self.cambios = listar_cambios(sesion, pid)
        self.operadores, self.personas = operadores_eboe(sesion, pid), personas_centrifugas(sesion, pid)
        self.resumen: dict = {"planta": planta.nombre, "temporadas": {str(t): {} for t in self.temporadas}}
        self._cache: dict = {}

    def de(self, tabla: pd.DataFrame, temporada) -> pd.DataFrame:
        return tabla[tabla["temporada"].astype(str) == str(temporada)]

    def una(self, clave: str, funcion):
        if clave not in self._cache:
            self._cache[clave] = funcion()
        return self._cache[clave]

    def parametros_fp(self) -> dict:
        return parametros_fase_pesada(self.planta)

    def tabla_fp(self, comparar: bool) -> pd.DataFrame:
        return self.una(f"fp_{comparar}", lambda: tabla_fase_pesada(self.pares_comp if comparar else self.pares,
                                                                    self.parametros_fp()))

    def base(self) -> pd.DataFrame:
        """Pares para los modelos (core.efectos.preparar_datos), sin entradas repetidas: comparan eBOE."""
        return self.una("base", lambda: preparar_datos(self.tabla_fp(True)))

    def primarias(self) -> pd.DataFrame:
        quimicos = self.personas[["codigo", "fecha_hora", "analista"]] if not self.personas.empty else None
        return self.una("primarias", lambda: datos_primarias(self.balance, cp_liviana_por_muestreo(self.muestras),
                                                             quimicos))

    def valores_cen(self) -> pd.DataFrame:
        return self.una("valores", lambda: valores_por_variable(self.muestras, self.balance))

    def resumen_temporada(self, temporada) -> dict:
        return self.una(f"proceso_{temporada}", lambda: resumen_proceso(
            self.de(self.pares, temporada), self.toneladas, self.produccion[int(temporada)]))

    def eboe_personas(self) -> pd.DataFrame:
        """Pares con operador y químico; anonimizados salvo --con-nombres."""
        def armar():
            datos = datos_eboe(self.base(), self.operadores)
            return datos if self.con_nombres else anonimizar(datos, ["operador", "analista"])
        return self.una("eboe_personas", armar)

    def primaria_personas(self) -> pd.DataFrame:
        def armar():
            datos = datos_primaria(self.balance, self.personas)
            return datos if self.con_nombres else anonimizar(datos, ["analista", "operador_centrifugas"])
        return self.una("primaria_personas", armar)

    def cambios_de(self, temporada) -> pd.DataFrame:
        desde, hasta = self.temporadas[int(temporada)]
        return self.cambios[(self.cambios["fecha_produccion"] >= pd.Timestamp(desde))
                            & (self.cambios["fecha_produccion"] <= pd.Timestamp(hasta))]

    def poner(self, temporada, clave: str, valor) -> None:
        """Un valor de resumen.json (sin nombres ni USD)."""
        self.resumen["temporadas"].setdefault(str(temporada), {})[clave] = valor


def temporadas_texto(ctx: Contexto) -> str:
    return ", ".join(f"{t} ({d:%d/%m}–{h:%d/%m})" for t, (d, h) in ctx.temporadas.items())


def redondo(valor, decimales: int = 2):
    return None if valor is None or pd.isna(valor) else round(float(valor), decimales)


# ---------------------------------------------------------------- 03–05: la cadena y el flowsheet

def fig_03a(ctx: Contexto) -> list[Figura]:
    filas = []
    for t in ctx.temporadas:
        pares = ctx.de(ctx.pares, t)
        filas.append({"etapa": "eBOE", "temporada": str(t), **media_ic(pares["eficiencia_pct"])})
        filas.append({"etapa": "Primaria", "temporada": str(t),
                      **media_ic(ctx.de(ctx.primarias(), t)["eficiencia_pct"])})
        dias = eficiencias_por_dia(ctx.de(ctx.tabla_fp(False), t), None, ctx.toneladas, ctx.produccion[t])
        filas.append({"etapa": "Proceso (tambor)", "temporada": str(t), **media_ic(dias["eficiencia_proceso"])})
    tabla = pd.DataFrame(filas)
    figura = barras_ic(tabla, "etapa", "Eficiencia (%)", 1, list(COLORES_ETAPA))
    figura.update_yaxes(range=[0, 105])
    frase = " ".join(f"{t}: " + ", ".join(f"{e if e == 'eBOE' else e.lower()} {numero(m, 1)} %" for e, m in zip(sub["etapa"], sub["media"]))
                     + "." for t, sub in tabla.groupby("temporada"))
    n = "; ".join(f"{e}: " + ", ".join(numero(v, 0) for v in sub["n"]) for e, sub in tabla.groupby("etapa", sort=False))
    return [Figura("03a", "Eficiencia por etapa y temporada", temporadas_texto(ctx),
                   f"N por temporada (pares / balances / días) — {n}", frase,
                   estilizar(figura, "Eficiencia por etapa y temporada",
                             f"Media e IC 95 %. eBOE: pares; primaria: balances válidos; proceso: días ≥ 60 t. {n}"),
                   tabla)]


def fig_03b(ctx: Contexto) -> list[Figura]:
    t = ctx.actual
    pares, produccion = ctx.de(ctx.pares, t), ctx.produccion[t]
    eboe = eficiencia_eboe_planta_por_mes(pares, ctx.toneladas).assign(etapa="eBOE")
    proceso = eficiencia_proceso_por_mes(pares, ctx.toneladas, produccion).rename(
        columns={"eficiencia_proceso": "valor"}).assign(etapa="Proceso (tambor)")
    primarias = ctx.de(ctx.primarias(), t)
    primaria = pd.DataFrame([{"mes": m, **media_ic(s["eficiencia_pct"])} for m, s in primarias.groupby("mes")]).rename(
        columns={"media": "valor"}).assign(etapa="Primaria")
    tabla = pd.concat([eboe.assign(mes=eboe["mes"].map(mes_corto)), primaria,
                       proceso.assign(mes=proceso["mes"].map(mes_corto))], ignore_index=True)[["etapa", "mes", "valor", "n"]]
    orden = orden_meses(tabla["mes"])
    figura = lineas(tabla, "mes", "etapa", "valor", "Eficiencia (%)", orden, COLORES_ETAPA)
    figura.update_yaxes(range=[0, 105])
    return [Figura("03b", f"Eficiencia por etapa y mes {t}", f"Temporada {t} completa", f"N total = {numero(tabla['n'].sum(), 0)}",
                   "Las tres eficiencias mes a mes, cada una en su escala de etapa; no se restan.",
                   estilizar(figura, f"Eficiencia por etapa y mes · {t}",
                             "eBOE ponderada por t (N pares); primaria: media de balances (N); proceso: Σ aceite / Σ "
                             "fruta del mes sobre la entrada (N turnos)"), tabla)]


def fig_03c(ctx: Contexto) -> list[Figura]:
    filas = []
    for t in ctx.temporadas:
        mensual = eficiencia_proceso_por_mes(ctx.de(ctx.pares, t), ctx.toneladas, ctx.produccion[t])
        filas.append(mensual.assign(temporada=str(t), mes=mensual["mes"].map(mes_corto)))
    tabla = pd.concat(filas, ignore_index=True)[["temporada", "mes", "rendimiento", "n"]].dropna(subset=["rendimiento"])
    figura = lineas(tabla, "mes", "temporada", "rendimiento", "Rendimiento en tambor (kg/MT)", orden_meses(tabla["mes"]),
                    COLORES_TEMPORADA, 2)
    return [Figura("03c", "Rendimiento de aceite en tambor por mes", temporadas_texto(ctx),
                   f"N turnos con fruta y aceite = {numero(tabla['n'].sum(), 0)}",
                   "; ".join(f"{t}: {numero(s['rendimiento'].min(), 2)} a {numero(s['rendimiento'].max(), 2)} kg/MT"
                             for t, s in tabla.groupby("temporada")) + ".",
                   estilizar(figura, "Rendimiento de aceite en tambor por mes",
                             f"Σ aceite envasado / Σ fruta procesada (producción aprobada). N = "
                             f"{numero(tabla['n'].sum(), 0)} turnos"), tabla)]


def fig_04(ctx: Contexto) -> list[Figura]:
    cen = resumen_temporadas(ctx.muestras, ctx.balance, primarias_por_turno(ctx.muestras.iloc[0:0], 0),
                             pd.concat(ctx.produccion.values())).set_index("temporada")
    filas = []
    for t in ctx.temporadas:
        tot = ctx.resumen_temporada(t)["totales"]
        primaria = cen["eficiencia_mediana"].get(t)
        fila = {"Temporada": str(t), "Fruta procesada (t)": redondo(tot["fruta_t"], 0),
                "Aceite en fruta de entrada (kg/MT)": redondo(tot["aceite_entrada"]),
                "Eficiencia eBOE (%)": redondo(tot["eficiencia"], 1),
                "Eficiencia primaria (%, mediana)": redondo(primaria, 1),
                "Eficiencia de proceso (%)": redondo(tot["eficiencia_proceso"], 1),
                "Rendimiento de aceite (kg/MT)": redondo(tot["rendimiento"]),
                "Rendimiento jugo 65 °Bx (kg/MT)": redondo(tot["rendimiento_concentrado"], 1),
                "Nota": "°Bx sin verificar" if t in TEMPORADAS_BRIX_SIN_VERIFICAR else "",
                "N pares": tot["n_pares"], "N turnos con producción": tot["n_turnos"]}
        filas.append(fila)
        for clave, valor in fila.items():
            if clave not in ("Temporada", "Nota"):
                ctx.poner(t, clave, valor)
        ctx.poner(t, "eficiencia_eboe_por_eboe_pct", {f"eBOE {e['eboe']}": redondo(e["eficiencia"], 1)
                                                      for e in ctx.resumen_temporada(t)["eboes"]})
    tabla = pd.DataFrame(filas)
    return [Figura("04", "Tres temporadas en números", temporadas_texto(ctx),
                   " · ".join(f"{f['Temporada']}: {numero(f['N pares'], 0)} pares" for f in filas),
                   "Eficiencias y rendimientos ponderados por toneladas aprobadas (como Análisis › Proceso); la de la "
                   "primaria es la mediana de los balances (Centrífugas › Resumen).", None, tabla)]


def _flowsheet(ctx: Contexto, codigo: str, temporada: int) -> list[Figura]:
    config = cargar_flowsheet(ctx.planta.nombre)
    if config is None:
        raise ValueError("la planta no tiene flowsheet (data/plantas/<planta>/flowsheet.json)")
    desde, hasta = ctx.temporadas[temporada]
    marcadores = datos_flowsheet(ctx.sesion, ctx.planta, config, desde, hasta, None)
    figura = g.flowsheet(config, marcadores)
    alto = round(ANCHO * config["alto"] / config["ancho"]) + 130
    tabla = pd.DataFrame([{"Marcador": m["nombre"], "Etiqueta": m.get("etiqueta"), "Valor": m.get("valor"),
                           "Texto": m.get("texto"), "Semáforo": m.get("color"), "Alertas": m.get("alertas")}
                          for m in marcadores])
    return [Figura(codigo, f"Flowsheet temporada {temporada}", f"{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}, todos los turnos",
                   "N por marcador en el Excel (turnos y muestras en el tooltip de la app)",
                   "Mediana de los turnos; eficiencia y salida ponderadas por t. Verde/rojo contra el rango; azul sin "
                   "rango o en revisión; gris sin datos.",
                   estilizar(figura, f"Flowsheet · temporada {temporada}", "Valores del período; semáforo contra "
                             "RangoReferencia", alto=alto), tabla)]


def fig_05a(ctx: Contexto) -> list[Figura]:
    return _flowsheet(ctx, "05a", ctx.actual - 1)


def fig_05b(ctx: Contexto) -> list[Figura]:
    return _flowsheet(ctx, "05b", ctx.actual)


# ---------------------------------------------------------------- 06–08: aceite y eficiencia eBOE

def fig_06(ctx: Contexto) -> list[Figura]:
    t = ctx.actual
    fruta = ctx.de(ctx.fruta, t)
    figura_a = g.histograma(fruta)
    n = fruta.groupby("lugar")["aceite_fruta"].count()
    medias = []
    for lugar, sub in ctx.fruta.groupby("lugar"):
        m = comparar_grupos(sub, "aceite_fruta", "temporada")["medias"]
        medias.append(m.rename(columns={"grupo": "temporada"}).assign(grupo=lugar))
    tabla_b = pd.concat(medias, ignore_index=True)
    figura_b = g.intervalos_agrupados(tabla_b, "Aceite en fruta (kg/MT)", "kg/MT", 2, "")
    entrada = tabla_b[tabla_b["grupo"] == "Entrada"].set_index("temporada")["media"]
    salida = tabla_b[tabla_b["grupo"] == "Salida"].set_index("temporada")["media"]
    return [
        Figura("06a", f"Aceite en fruta entrada y salida {t}", f"Temporada {t} completa",
               f"Entrada N = {numero(n.get('Entrada', 0), 0)}; salida N = {numero(n.get('Salida', 0), 0)}",
               f"Distribución por muestra de {t}.",
               estilizar(figura_a, f"Aceite en fruta · entrada y salida {t}",
                         f"Por muestra. Entrada N = {numero(n.get('Entrada', 0), 0)} · salida N = "
                         f"{numero(n.get('Salida', 0), 0)}"),
               fruta[["fecha_hora", "turno", "eboe", "lugar", "aceite_fruta"]]),
        Figura("06b", "Aceite en fruta por temporada", temporadas_texto(ctx),
               ", ".join(f"{t} {l}: {numero(n_, 0)}" for t, l, n_ in zip(tabla_b["temporada"], tabla_b["grupo"],
                                                                         tabla_b["n"])),
               "Entrada: " + ", ".join(f"{t} {numero(v, 2)}" for t, v in entrada.items()) + " kg/MT. Salida: "
               + ", ".join(f"{t} {numero(v, 2)}" for t, v in salida.items()) + " kg/MT.",
               estilizar(figura_b, "Aceite en fruta por temporada", "Media e IC 95 % por muestra. N total = "
                         f"{numero(tabla_b['n'].sum(), 0)}"), tabla_b)]


def fig_07(ctx: Contexto) -> list[Figura]:
    pares = ctx.pares_comp
    tabla = por_categoria(pares, "eboe", "eficiencia_pct").assign(grupo=lambda d: "eBOE " + d["grupo"])
    figura_a = g.intervalos_agrupados(tabla, "Eficiencia eBOE (%)", "%", 1, "")
    valores = pd.DataFrame({"equipo": "eBOE " + pares["eboe"].astype(str), "temporada": pares["temporada"],
                            "valor": pares["eficiencia_pct"], "fecha_hora": pares["fecha_hora"], "turno": pares["turno"],
                            "analista": ""}).dropna(subset=["valor"])
    figura_b = g.boxplot_por_equipo(valores, "Eficiencia eBOE (%)", "%", list(COLORES_EBOE), 1)
    figura_b.update_yaxes(range=[40, 100])
    for t, sub in tabla.groupby("temporada"):
        ctx.poner(t, "eficiencia_eboe_simple_por_eboe_pct", {e: redondo(m, 1) for e, m in zip(sub["grupo"], sub["media"])})
    return [Figura("07a", "Eficiencia eBOE por temporada y eBOE", f"{temporadas_texto(ctx)}; {SIN_REPETIDAS}",
                   f"N total = {numero(tabla['n'].sum(), 0)} pares",
                   " · ".join(f"{t}: " + ", ".join(f"{e} {numero(m, 1)} %" for e, m in zip(s["grupo"], s["media"]))
                              for t, s in tabla.groupby("temporada")),
                   estilizar(figura_a, "Eficiencia eBOE por temporada y eBOE",
                             f"Media e IC 95 % por par, {SIN_REPETIDAS}. N = {numero(tabla['n'].sum(), 0)}"), tabla),
            Figura("07b", "Distribución de la eficiencia eBOE", f"{temporadas_texto(ctx)}; {SIN_REPETIDAS}",
                   f"N = {numero(len(valores), 0)} pares", "Cajas por eBOE y temporada; círculos abiertos: outliers "
                   "(marcados, no borrados). Eje recortado a 40–100 %.",
                   estilizar(figura_b, "Distribución de la eficiencia eBOE",
                             f"Q1–Q3 y mediana; outliers marcados. N = {numero(len(valores), 0)} pares"),
                   valores.drop(columns="analista"))]


def fig_08(ctx: Contexto) -> list[Figura]:
    t = ctx.actual
    pares, fruta = ctx.de(ctx.pares, t), sin_entradas_repetidas(ctx.de(ctx.fruta, t))
    eficiencia = por_mes_y_eboe(pares, "eficiencia_pct", ctx.toneladas)
    cajas, outliers = cajas_por_mes(fruta)
    eje = eje_de_meses(pd.concat([eficiencia["mes"], cajas["mes"]]))
    cambios = ctx.cambios_de(t)
    meses = [etiqueta_mes(m) for m in mes_de(cambios["fecha_produccion"])]
    del_mes = marcas(cambios, [m if m in eje else None for m in meses])
    objetivo = rango_referencia(ctx.sesion, ctx.planta.id, "Eficiencia eBOE")
    figura_a = g.marcar_cambios(g.columnas_mensuales(eficiencia, eje, "Eficiencia eBOE (%)", "%", rango_y=[0, 105],
                                                     objetivo=objetivo), del_mes)
    figura_b = g.marcar_cambios(g.boxplot_mensual(cajas, outliers, eje), del_mes)
    return [Figura("08a", f"Eficiencia eBOE por mes {t}", f"Temporada {t}; líneas: bitácora de cambios",
                   f"N = {numero(eficiencia['n'].sum(), 0)} pares; {len(cambios)} cambios",
                   "Ponderada por t; banda gris: objetivo de la planta. Las líneas violetas son los cambios de la "
                   "bitácora.", estilizar(figura_a, f"Eficiencia eBOE por mes · {t}",
                                          f"Ponderada por t. N = {numero(eficiencia['n'].sum(), 0)} pares · "
                                          f"{len(cambios)} cambios de la bitácora"), eficiencia),
            Figura("08b", f"Aceite en fruta por mes {t}", f"Temporada {t}; {SIN_REPETIDAS}",
                   f"N = {numero(cajas['n'].sum(), 0)} muestras",
                   "Entrada arriba y salida abajo; outliers marcados, no borrados.",
                   estilizar(figura_b, f"Aceite en fruta por mes · {t}",
                             f"Entrada (arriba) y salida (abajo), por muestra. N = {numero(cajas['n'].sum(), 0)}"),
                   cajas)]


# ---------------------------------------------------------------- 09–13: sistema

def fig_09(ctx: Contexto) -> list[Figura]:
    validos = ctx.tabla_fp(True).dropna(subset=["rango_fp_pct"])
    medianas = mediana_por(validos, ["temporada", "eboe"]).drop(columns="coherente")
    vista = medianas.assign(eboe="eBOE " + medianas["eboe"].astype(str), temporada=medianas["temporada"].astype(str))
    figura_a = g.columnas_agrupadas(vista, "eboe", "temporada", "rango_fp_pct", "Rango de fase pesada (%, mediana)", "%",
                                    1)
    for corte in RANGOS_FP["rango_fp_ref"]["cortes"][1:-1]:
        figura_a.add_hline(y=corte, line_dash="dot", line_color=GRIS_CLARO,
                           annotation_text=f"{numero(corte, 0)} %", annotation_position="right")
    for t, rango in mediana_por(validos, ["temporada"]).set_index("temporada")["rango_fp_pct"].items():
        ctx.poner(int(t), "rango_fp_mediana_pct", redondo(rango, 1))
    fp = ctx.tabla_fp(False).dropna(subset=["fp_especifica"])
    figura_b = g.boxplot_temporadas(fp, "fp_especifica", "Fase pesada específica (m³/t)", "m³/t", 2)
    for t, v in fp.groupby("temporada")["fp_especifica"].median().items():
        ctx.poner(t, "fp_especifica_mediana_m3_t", redondo(v, 3))
    emulsion = comparar_grupos(ctx.emulsion, "aceite_emulsion", "temporada")["medias"]
    figura_c = g.intervalos(emulsion, "Aceite en emulsión del BOE (%)", "%", 2)
    rango = rango_referencia(ctx.sesion, ctx.planta.id, "Aceite en emulsión")
    if rango and rango.get("minimo") is not None and rango.get("maximo") is not None:
        figura_c.add_hrect(y0=rango["minimo"], y1=rango["maximo"], fillcolor=VERDE, opacity=0.08, line_width=0)
    for t, m in zip(emulsion["grupo"], emulsion["media"]):
        ctx.poner(t, "aceite_emulsion_boe_media_pct", redondo(m, 3))
    return [Figura("09a", "Rango de fase pesada por temporada y eBOE", f"{temporadas_texto(ctx)}; {SIN_REPETIDAS}",
                   f"N = {numero(medianas['n'].sum(), 0)} análisis",
                   "Negativo: entra menos fase pesada que la requerida por el balance del licor. Líneas: cortes del "
                   "gráfico Brown.",
                   estilizar(figura_a, "Rango de fase pesada · gráfico Brown",
                             f"(real − requerido) / requerido, mediana por eBOE. N = {numero(medianas['n'].sum(), 0)}"),
                   medianas),
            Figura("09b", "Fase pesada específica por temporada", temporadas_texto(ctx), f"N = {numero(len(fp), 0)} pares",
                   ", ".join(f"{t}: {numero(v, 2)} m³/t" for t, v in fp.groupby("temporada")["fp_especifica"].median()
                             .items()) + " (medianas).",
                   estilizar(figura_b, "Fase pesada específica por temporada",
                             f"Caudal real del laboratorio / MTPH. N = {numero(len(fp), 0)} pares"),
                   fp.groupby("temporada")["fp_especifica"].describe().reset_index()),
            Figura("09c", "Aceite en emulsión del BOE por temporada", temporadas_texto(ctx),
                   f"N = {numero(emulsion['n'].sum(), 0)} muestras",
                   ", ".join(f"{t}: {numero(m, 2)} %" for t, m in zip(emulsion["grupo"], emulsion["media"])) + ".",
                   estilizar(figura_c, "Aceite en emulsión del BOE",
                             f"Media e IC 95 %; banda: rango de la planta. N = {numero(emulsion['n'].sum(), 0)}"),
                   emulsion)]


def fig_10(ctx: Contexto) -> list[Figura]:
    crudos = preparar_pares(pares_con_emulsion(agregar_dia_proceso(ctx.fruta), ctx.emulsion))
    todos, _ = separar_errores(crudos)
    cortes = cortes_cuartiles(todos["aceite_emulsion"])
    ultimas = [str(t) for t in sorted(ctx.temporadas)[-2:]]
    sub = todos[todos["temporada"].astype(str).isin(ultimas)]
    tabla = esponja_por_cuartil(sub, cortes, ctx.toneladas)
    tabla = tabla.assign(corta="C" + tabla["cuartil"].astype(str), eboe_texto="eBOE " + tabla["eboe"].astype(str),
                         temporada=tabla["temporada"].astype(str))
    figura = g.columnas_agrupadas(tabla, "corta", "eboe_texto", "salida", "Aceite en fruta de salida (kg/MT)", "kg/MT", 2,
                                  panel="temporada", titulo_x="Cuartil de aceite en emulsión")
    return [Figura("10", "Efecto esponja", f"Temporadas {' y '.join(ultimas)}; por par entrada/salida (la app no lo "
                   "tiene por turno)", f"N = {numero(tabla['n'].sum(), 0)} pares",
                   "¿Más aceite en el licor → más aceite en la fruta de salida? Salida por cuartil de aceite en emulsión "
                   "(C1 bajo a C4 alto), por eBOE.",
                   estilizar(figura, "Efecto esponja: salida por cuartil de emulsión",
                             "Cuartiles: " + ", ".join(f"{numero(c, 2)} %" for c in cortes)
                             + f". Ponderado por t. N = {numero(tabla['n'].sum(), 0)} pares"), tabla)]


def fig_11(ctx: Contexto) -> list[Figura]:
    base = ctx.base()
    interaccion = modelo_interaccion(base, "eficiencia_pct", "mtph", "temporada")
    celdas, a = interaccion["celdas"], interaccion["a"]
    figura_a = g.grafico_interaccion(celdas, NOMBRES[a], "Eficiencia eBOE ajustada (%)", "%", 1,
                                     orden_de_niveles(a, celdas["a"].unique()))
    figuras = [Figura("11a", "Eficiencia eBOE por MTPH y temporada", f"{temporadas_texto(ctx)}; {SIN_REPETIDAS}",
                      f"N = {numero(interaccion['n'], 0)} pares",
                      f"Interacción MTPH × temporada: {texto_p(interaccion['p'])}. Medias ajustadas por programa, "
                      "aceite en fruta, calibre, mes, turno y eBOE.",
                      estilizar(figura_a, "Eficiencia eBOE por MTPH y temporada",
                                f"Medias ajustadas e IC 95 %. N = {numero(interaccion['n'], 0)} pares"), celdas)]
    temporadas = orden_de_niveles("temporada", base["temporada"].dropna().unique())
    if len(temporadas) >= 2:
        anterior, nueva = temporadas[-2], temporadas[-1]
        r = descomponer_por_mezcla(interaccion, base.dropna(subset=["eficiencia_pct"]), anterior, nueva)
        if r is None:
            raise ValueError("no hay rangos de MTPH con datos en las dos temporadas")
        figura_b = g.cascada_pasos([anterior, "Por la mezcla de MTPH", "A igual MTPH", nueva],
                                   [r["media_base"], r["por_mezcla"], r["a_igual_carga"], r["media_nueva"]],
                                   "Eficiencia eBOE ajustada (%)", "%", 1)
        ctx.resumen["descomposicion"] = {"base": anterior, "nueva": nueva, **{k: redondo(r[k], 2) for k in (
            "media_base", "media_nueva", "diferencia", "por_mezcla", "a_igual_carga")}}
        tabla = pd.DataFrame([{"concepto": k, "valor": r[k]} for k in ("media_base", "media_nueva",
                                                                         "nueva_con_mezcla_base", "diferencia",
                                                                         "por_mezcla", "a_igual_carga")])
        figuras.append(Figura("11b", f"Descomposición {anterior} → {nueva}", f"Temporadas {anterior} y {nueva}",
                              f"N = {numero(r['n_base'], 0)} ({anterior}) y {numero(r['n_nueva'], 0)} ({nueva}) pares",
                              f"De {con_signo(r['diferencia'])} puntos: {con_signo(r['por_mezcla'])} por operar con otra "
                              f"mezcla de MTPH y {con_signo(r['a_igual_carga'])} a igual MTPH.",
                              estilizar(figura_b, f"Eficiencia eBOE {anterior} → {nueva}",
                                        "Por la mezcla de MTPH y a igual MTPH (medias ajustadas). N = "
                                        f"{numero(r['n_base'], 0)} y {numero(r['n_nueva'], 0)}"),
                              tabla.merge(r["rangos"].assign(concepto="rango"), how="outer")))
    umbral = validar_parametros(ctx.planta, ["umbral_paralelo_lh"])["umbral_paralelo_lh"]
    turnos = datos_por_turno(preparar_datos(ctx.tabla_fp(False)),
                             captado_por_turno(ctx.balance, primarias_por_turno(ctx.muestras, umbral)))
    medias = comparar_grupos(turnos, "captado_pct", "temporada")["medias"]
    figura_c = g.intervalos(medias, "Aceite captado por las primarias (%)", "%", 1)
    mas_100 = int((turnos["captado_pct"] > 100).sum())
    for t, m in zip(medias["grupo"], medias["media"]):
        ctx.poner(t, "captado_pct_media", redondo(m, 1))
    figuras.append(Figura("11c", "Aceite captado por las primarias", temporadas_texto(ctx),
                          f"N = {numero(medias['n'].sum(), 0)} turnos ({mas_100} con más de 100 %, marcados)",
                          "Fase liviana de las primarias / aceite removido por los eBOE, por turno: "
                          + ", ".join(f"{t} {numero(m, 1)} %" for t, m in zip(medias["grupo"], medias["media"])) + ".",
                          estilizar(figura_c, "Aceite captado por las primarias",
                                    f"Media e IC 95 % por turno. N = {numero(medias['n'].sum(), 0)} turnos; "
                                    f"{mas_100} con más de 100 % (error de medición, marcados)"),
                          turnos[["temporada", "fecha_produccion", "turno", "captado_pct", "captado_kg_mt"]]))
    return figuras


def _boxplot_centrifuga(ctx: Contexto, variable: str, tipos: tuple, temporada=None) -> tuple[go.Figure, pd.DataFrame]:
    valores = ctx.valores_cen()
    datos = valores[(valores["variable"] == variable) & ~valores["no_medible"] & valores["tipo"].isin(tipos)]
    if temporada is not None:
        datos = ctx.de(datos, temporada)
    if datos.empty:
        raise ValueError(f"sin datos de {VARIABLES_CEN[variable][0].lower()}")
    bandas, fuentes = {}, set()
    for equipo, tipo in datos.drop_duplicates("equipo")[["equipo", "tipo"]].itertuples(index=False):
        rango = ctx.rangos_cen.get((variable, tipo))
        if rango is not None:
            bandas[equipo] = (rango["minimo"], rango["maximo"])
            fuentes.add(rango["fuente"])
    nombre, unidad = VARIABLES_CEN[variable]
    figura = g.boxplot_por_equipo(datos, f"{nombre} ({unidad})", unidad, ORDEN_EQUIPOS, 2, bandas=bandas,
                                  nombre_banda=f"Referencia ({' y '.join(sorted(fuentes))})" if fuentes else "Referencia")
    return figura, datos


def fig_12(ctx: Contexto) -> list[Figura]:
    figura, datos = _boxplot_centrifuga(ctx, "aceite_pesada", (PRIMARIA,))
    medianas = datos.groupby(["temporada", "equipo"])["valor"].agg(["count", "median"]).reset_index()
    return [Figura("12", "Aceite en fase pesada de la primaria", f"{temporadas_texto(ctx)}; sin muestras copiadas",
                   f"N = {numero(len(datos), 0)} muestras",
                   "Medianas: " + ", ".join(f"{t} {e} {numero(m, 3)} %" for t, e, m in
                                           zip(medianas["temporada"], medianas["equipo"], medianas["median"])) + ".",
                   estilizar(figura, "Aceite en fase pesada de la primaria",
                             f"Por temporada y equipo, sin copiadas; outliers marcados. N = {numero(len(datos), 0)}"),
                   medianas)]


def fig_13(ctx: Contexto) -> list[Figura]:
    base = ctx.base()
    t = str(ctx.actual)
    resultado = modelo_efectos(base[base["temporada"] == t], "eficiencia_pct", "mtph")
    if resultado["medias"] is None:
        raise ValueError(f"datos insuficientes para el modelo de {t}")
    medias = resultado["medias"]
    medias = medias[medias["factor"].isin(["programa_grupo", "mtph_rango", "calibre", "mes"])]
    figura_a = g.efectos_principales(medias, "Eficiencia eBOE (%)", "%", 1)
    ajustados, ajustada = segmentada_por_temporada(base, "eficiencia_pct", ajustado=True)
    figura_b = g.curva_mtph(por_mtph(base.assign(_ajustada=ajustada), "_ajustada", por=None), ajustados,
                            "Eficiencia eBOE ajustada", "%", 1)
    ctx.resumen["quiebres_mtph"] = [{"temporada": str(f["temporada"]), "quiebre_mtph": redondo(f["quiebre"], 0),
                                     "pendiente_antes": redondo(f["pendiente_antes"], 3),
                                     "pendiente_despues": redondo(f["pendiente_despues"], 3),
                                     "p_cambio": redondo(f["p_cambio"], 4), "n": int(f["n"]) if pd.notna(f["n"]) else None}
                                    for f in ajustados.to_dict("records")]
    return [Figura("13a", f"Efectos principales {t}", f"Temporada {t}; {SIN_REPETIDAS}",
                   f"N = {numero(resultado['n'], 0)} pares; R² = {numero(resultado['r2'] * 100, 1)} %",
                   "Medias ajustadas (como Minitab) de programa, MTPH, calibre y mes; el modelo también tiene aceite en "
                   "fruta, turno y eBOE.",
                   estilizar(figura_a, f"Efectos principales sobre la eficiencia eBOE · {t}",
                             f"Medias ajustadas e IC 95 %. N = {numero(resultado['n'], 0)} pares · R² "
                             f"{numero(resultado['r2'] * 100, 1)} %"), medias),
            Figura("13b", "Umbral de MTPH por temporada", f"{temporadas_texto(ctx)}; {SIN_REPETIDAS}",
                   f"N = {numero(ajustados['n'].sum(), 0)} pares",
                   "; ".join(f"{f['temporada']}: quiebre en {numero(f['quiebre'], 0)} MTPH" for f in
                             ajustados.dropna(subset=["quiebre"]).to_dict("records")) + ".",
                   estilizar(figura_b, "Umbral de MTPH por temporada",
                             "Eficiencia ajustada por programa, aceite, calibre, mes, turno y eBOE; línea punteada: "
                             f"quiebre. N = {numero(ajustados['n'].sum(), 0)}"), ajustados)]


# ---------------------------------------------------------------- 14–18: eBOE, centrífugas y alertas

def fig_14(ctx: Contexto) -> list[Figura]:
    pares = sin_entradas_repetidas(pares_calibre(ctx.fruta))
    tabla = por_categoria(pares[~pares["error_carga"]], "eboe", "peso_entrada_g").assign(
        grupo=lambda d: "eBOE " + d["grupo"])
    figura = g.intervalos_agrupados(tabla, "Peso medio del fruto de entrada (g)", "g", 0, "")
    return [Figura("14", "Peso medio del fruto de entrada", f"{temporadas_texto(ctx)}; {SIN_REPETIDAS}; sin errores de "
                   "carga", f"N = {numero(tabla['n'].sum(), 0)} rondas",
                   " · ".join(f"{t}: " + ", ".join(f"{e} {numero(m, 0)} g" for e, m in zip(s["grupo"], s["media"]))
                              for t, s in tabla.groupby("temporada")),
                   estilizar(figura, "Peso medio del fruto de entrada por eBOE",
                             f"Media e IC 95 %. N = {numero(tabla['n'].sum(), 0)} rondas"), tabla)]


def fig_15(ctx: Contexto) -> list[Figura]:
    filas = []
    for t in ctx.temporadas:
        for e in ctx.resumen_temporada(t)["eboes"]:
            if int(e["eboe"]) == 2:
                filas.append({"Temporada": str(t), "N pares": e["n"], "Aceite entrada (kg/MT)": redondo(e["aceite_entrada"]),
                              "Aceite salida (kg/MT)": redondo(e["aceite_salida"]), "MTPH": redondo(e["mtph"], 1),
                              "Eficiencia (%)": redondo(e["eficiencia"], 1)})
    figuras = [Figura("15a", "eBOE 2 en tres temporadas", temporadas_texto(ctx),
                      " · ".join(f"{f['Temporada']}: {f['N pares']}" for f in filas),
                      "Ponderado por t (como Análisis › Proceso).", None, pd.DataFrame(filas))]
    desde, hasta = ctx.periodo_carta
    for k, (codigo, nombre) in enumerate(equipos_de_variable(ctx.sesion, ctx.planta.id, "aceite_salida")):
        serie = serie_periodo(ctx.sesion, ctx.planta, "aceite_salida", codigo, desde, hasta)
        carta = carta_periodo(ctx.sesion, serie, "aceite_salida")
        fuera = int(carta["tabla"]["reglas"].map(bool).sum())
        origen = "congelados" if carta["origen"] == "congelados" else "del período"
        figuras.append(Figura(f"15b{k + 1}", f"Carta de aceite en salida {nombre}",
                              f"{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}, por turno",
                              f"N = {carta['n_muestras']} muestras en {carta['n_turnos']} turnos",
                              f"{fuera} de {len(carta['tabla'])} turnos violan alguna regla de Nelson (límites {origen}).",
                              estilizar(g.carta_periodo(carta), f"Aceite en fruta de salida · {nombre}",
                                        f"{desde:%d/%m} al {hasta:%d/%m/%Y}; carta por turno, límites {origen}. N = "
                                        f"{carta['n_muestras']} muestras, {carta['n_turnos']} turnos"),
                              carta["tabla"].drop(columns=[c for c in ("datos",) if c in carta["tabla"]])))
    salidas = ctx.fruta[ctx.fruta["lugar"] == "Salida"].sort_values("fecha_hora")
    sigma = [{"temporada": str(t), "eboe": f"eBOE {e}", "n": len(s),
              "sigma": sigma_dentro_individuales(s["aceite_fruta"].dropna().to_numpy())}
             for (t, e), s in salidas.groupby(["temporada", "eboe"])]
    tabla = pd.DataFrame(sigma)
    figura = g.columnas_agrupadas(tabla, "eboe", "temporada", "sigma", "Desvío de corto plazo (kg/MT)", "kg/MT", 2)
    figuras.append(Figura("15c", "Desvío de corto plazo de la salida", temporadas_texto(ctx),
                          f"N = {numero(tabla['n'].sum(), 0)} muestras",
                          "σ dentro = MR̄ / d2 de las muestras de salida en orden de tiempo, por eBOE y temporada.",
                          estilizar(figura, "Desvío de corto plazo del aceite en salida",
                                    f"σ dentro (MR̄/d2) por eBOE y temporada. N = {numero(tabla['n'].sum(), 0)}"), tabla))
    return figuras


def fig_16(ctx: Contexto) -> list[Figura]:
    figuras = []
    nominal = caudal_nominal_primaria(ctx.rangos_cen)
    figura, datos = _boxplot_centrifuga(ctx, "caudal_emulsion", (PRIMARIA,))
    figura.add_hline(y=nominal, line_dash="dash", line_color=OXIDO, annotation_text=f"{numero(nominal, 0)} L/h nominal")
    hasta = (datos["valor"] <= nominal).groupby(datos["temporada"]).mean() * 100
    figuras.append(Figura("16a", "Caudal de alimentación por primaria", temporadas_texto(ctx),
                          f"N = {numero(len(datos), 0)} muestras",
                          "% de muestras hasta el nominal: " + ", ".join(f"{t} {numero(v, 0)} %" for t, v in hasta.items())
                          + ".", estilizar(figura, f"Caudal por primaria contra {numero(nominal, 0)} L/h",
                                           f"Por temporada y equipo; línea: nominal (RangoReferencia). N = "
                                           f"{numero(len(datos), 0)}"),
                          datos.groupby(["temporada", "equipo"])["valor"].describe().reset_index()))
    for sufijo, variable in (("16b1", "solidos"), ("16b2", "cp_pesada")):
        figura, datos = _boxplot_centrifuga(ctx, variable, (PRIMARIA,))
        nombre, unidad = VARIABLES_CEN[variable]
        figuras.append(Figura(sufijo, f"{nombre} de la primaria", temporadas_texto(ctx),
                              f"N = {numero(len(datos), 0)} muestras", f"{nombre} contra la referencia GEA (banda).",
                              estilizar(figura, f"{nombre} de la primaria", f"Banda: referencia. N = "
                                        f"{numero(len(datos), 0)}"),
                              datos.groupby(["temporada", "equipo"])["valor"].describe().reset_index()))
    cuantiles = por_cuantiles(ctx.primarias(), "cp_liviana").sort_values(["orden", "temporada"])
    figura = g.intervalos_agrupados(cuantiles, "Eficiencia de la primaria (%)", "%", 1, "CP fase liviana (bar), quintiles")
    figuras.append(Figura("16c", "Eficiencia de la primaria por CP liviana", temporadas_texto(ctx),
                          f"N = {numero(cuantiles['n'].sum(), 0)} balances",
                          "La CP liviana se mide aparte del balance; quintiles con los mismos cortes en las tres "
                          "temporadas.", estilizar(figura, "Eficiencia de la primaria por CP de fase liviana",
                                                   f"Media e IC 95 % (IC con N ≥ 3). N = "
                                                   f"{numero(cuantiles['n'].sum(), 0)}"), cuantiles))
    t = ctx.actual
    _, datos = _boxplot_centrifuga(ctx, "aceite_descarga", (SECUNDARIA,), t)
    datos = datos.assign(mes=datos["fecha_hora"].map(lambda f: MESES[f.month - 1]))
    mensual = datos.groupby(["mes", "equipo"])["valor"].agg(n="count", mediana="median").reset_index()
    mensual = mensual.sort_values("mes", key=lambda c: c.map(MESES.index))
    figura = g.columnas_agrupadas(mensual, "mes", "equipo", "mediana", "Aceite en descarga (%, mediana)", "%", 2,
                                  colores={e: g.color_equipo(e, ORDEN_EQUIPOS) for e in ORDEN_EQUIPOS})
    figuras.append(Figura("16d", f"Aceite en descarga de la secundaria por mes {t}", f"Temporada {t}; sin copiadas",
                          f"N = {numero(len(datos), 0)} muestras", "Mediana por mes y equipo (marca de valor extremo: "
                          "se cuenta, no se excluye).",
                          estilizar(figura, f"Aceite en descarga de la secundaria · {t}",
                                    f"Mediana por mes. N = {numero(len(datos), 0)} muestras"), mensual))
    primarias = ctx.primarias()
    tabla = pd.DataFrame([{"temporada": t_, "grupo": m, **media_ic(s["eficiencia_pct"])}
                          for (t_, m), s in primarias.groupby(["temporada", "mes"])])
    tabla = tabla.sort_values("grupo", key=lambda c: c.map(MESES.index))
    figura = g.intervalos_agrupados(tabla, "Eficiencia de la primaria (%)", "%", 1, "Mes")
    figuras.append(Figura("16e", "Eficiencia de la primaria por mes", temporadas_texto(ctx),
                          f"N = {numero(tabla['n'].sum(), 0)} balances", "Balances sin copiados ni fuera de rango físico.",
                          estilizar(figura, "Eficiencia de la primaria por mes",
                                    f"Media e IC 95 %. N = {numero(tabla['n'].sum(), 0)} balances"), tabla))
    valores = ctx.valores_cen()
    for (t_, tipo, variable), v in valores[~valores["no_medible"]].groupby(["temporada", "tipo", "variable"])["valor"]:
        if variable in ("caudal_emulsion", "solidos", "cp_pesada", "cp_liviana") and tipo == PRIMARIA:
            ctx.poner(t_, f"primaria_{variable}_mediana", redondo(v.median(), 2))
        if variable == "aceite_descarga" and tipo == SECUNDARIA:
            ctx.poner(t_, "secundaria_aceite_descarga_mediana_pct", redondo(v.median(), 2))
    return figuras


def fig_17(ctx: Contexto) -> list[Figura]:
    objetivo = rango_referencia(ctx.sesion, ctx.planta.id, "Eficiencia eBOE")
    if not objetivo or objetivo.get("minimo") is None:
        raise ValueError("la planta no tiene el mínimo de eficiencia eBOE en RangoReferencia")
    minimo = objetivo["minimo"]
    filas = resumen_eboe_por_temporada(ctx.sesion, ctx.planta.id, minimo / 100)
    tabla = pd.DataFrame([{"temporada": str(f["temporada"]), "eboe": f"eBOE {f['eboe']}", "n": f["pares"],
                           "pct": f["fraccion_bajo_minimo"] * 100} for f in filas])
    figura = g.columnas_agrupadas(tabla, "eboe", "temporada", "pct", f"Pares con eficiencia < {numero(minimo, 0)} % (%)",
                                  "%", 1)
    return [Figura("17", f"Pares por debajo de {numero(minimo, 0)} %", temporadas_texto(ctx) + "; todos los pares",
                   f"N = {numero(tabla['n'].sum(), 0)} pares",
                   " · ".join(f"{t}: " + ", ".join(f"{e} {numero(p, 0)} %" for e, p in zip(s["eboe"], s["pct"]))
                              for t, s in tabla.groupby("temporada")),
                   estilizar(figura, f"% de pares con eficiencia < {numero(minimo, 0)} %",
                             f"Por eBOE y temporada (mínimo del objetivo de la planta). N = "
                             f"{numero(tabla['n'].sum(), 0)} pares"), tabla)]


def fig_18(ctx: Contexto) -> list[Figura]:
    t = ctx.actual
    desde, hasta = ctx.temporadas[t]
    alertas = listar_alertas(ctx.sesion, ctx.planta.id, estados=None, desde=desde, hasta=hasta)
    if alertas.empty:
        raise ValueError(f"no hay alertas en {t}")
    figura = go.Figure()
    for estado, sub in alertas.groupby("estado"):
        figura.add_scatter(x=sub["inicio"], y=sub["nombre"] + " · " + sub["ambito"], mode="markers", name=estado,
                           marker=dict(size=(sub["turnos"].fillna(1).clip(1, 10) * 3 + 6), opacity=0.8,
                                       color={"histórico": GRIS_OSCURO, "cerrada": VERDE}.get(estado, NARANJA)),
                           customdata=sub[["regla", "turnos"]],
                           hovertemplate="%{x|%d/%m %H:%M} · %{y}<br>Regla %{customdata[0]} · %{customdata[1]} turnos"
                                         "<extra></extra>")
    cambios = ctx.cambios_de(t)
    g.marcar_cambios(figura, marcas(cambios, list(cambios["fecha_hora"])))
    figura.update_layout(yaxis=dict(categoryorder="category descending"), xaxis_title="")
    conteo = alertas.groupby(["nombre", "ambito", "estado"]).size().reset_index(name="episodios")
    ctx.resumen["alertas"] = {"temporada": str(t), "episodios": int(len(alertas)),
                              "por_estado": {k: int(v) for k, v in alertas["estado"].value_counts().items()},
                              "por_variable": {k: int(v) for k, v in alertas["nombre"].value_counts().items()}}
    principales = alertas["nombre"].value_counts().head(3)
    return [Figura("18", f"Alertas y cambios {t}", f"Temporada {t}, todos los estados",
                   f"N = {len(alertas)} episodios; {len(cambios)} cambios",
                   "Más episodios: " + ", ".join(f"{k} ({v})" for k, v in principales.items()) + ".",
                   estilizar(figura, f"Línea de tiempo {t}: alertas y cambios",
                             f"Un punto por episodio (tamaño = turnos); líneas: bitácora. N = {len(alertas)} episodios",
                             alto=1000), conteo)]


# ---------------------------------------------------------------- 19–20: muestreo y equipos de trabajo

def fig_19(ctx: Contexto) -> list[Figura]:
    tolerancia = validar_parametros(ctx.planta, ["tolerancia_calibre_pct"])["tolerancia_calibre_pct"]
    pares = sin_entradas_repetidas(pares_calibre(ctx.fruta))
    diferencias = diferencia_por_eboe(pares)
    figura_a = g.intervalos_agrupados(diferencias.assign(grupo="eBOE " + diferencias["grupo"]),
                                      "Salida − entrada (% del peso de entrada)", "%", 1, "")
    figura_a.add_hline(y=0, line_color=GRIS_OSCURO)
    figura_a.add_hrect(y0=-tolerancia, y1=tolerancia, fillcolor=VERDE, opacity=0.07, line_width=0)
    prueba = prueba_pareada(pares, "peso", tolerancia)
    for t, s in prueba.groupby("temporada"):
        ctx.poner(t, "calibre_salida_vs_entrada_pct", {f"eBOE {e}": redondo(d, 1) for e, d in zip(s["eboe"], s["dif_pct"])})
    eficiencias = eficiencia_por_calibre(pares, tolerancia)
    figura_b = g.intervalos_agrupados(eficiencias, "Eficiencia eBOE (%)", "%", 1, "Fruta de la salida contra la de entrada")
    turnos = dilucion_por_turno(ctx.muestras, ctx.emulsion, ctx.fruta)
    for t, fila in resumen_dilucion(turnos, ["temporada"]).set_index("temporada").iterrows():
        ctx.poner(t, "dilucion_mediana", redondo(fila["mediana"], 3))
    mensual = resumen_dilucion(turnos, ["temporada", "mes"])
    mensual = mensual.assign(temporada=mensual["temporada"].astype(str), mes=mensual["mes"].map(mes_corto))
    figura_c = lineas(mensual, "mes", "temporada", "mediana", "Alimentación / BOE (mediana)", orden_meses(mensual["mes"]),
                      COLORES_TEMPORADA, 2)
    figura_c.add_hline(y=1, line_dash="dot", line_color=GRIS_OSCURO)
    figuras = [
        Figura("19a", "Peso del fruto salida contra entrada", f"{temporadas_texto(ctx)}; {SIN_REPETIDAS}",
               f"N = {numero(diferencias['n'].sum(), 0)} rondas",
               f"La salida trae fruta más pesada en todas las temporadas y eBOE; banda: ±{numero(tolerancia, 0)} %.",
               estilizar(figura_a, "Peso medio del fruto: salida contra entrada",
                         f"Media e IC 95 % por ronda; banda ±{numero(tolerancia, 0)} % (tolerancia de la planta). N = "
                         f"{numero(diferencias['n'].sum(), 0)}"), prueba),
        Figura("19b", "Eficiencia según el calibre de la salida", f"{temporadas_texto(ctx)}; {SIN_REPETIDAS}",
               f"N = {numero(eficiencias['n'].sum(), 0)} rondas",
               "Comparación entre rondas, no prueba la causa: parte de la eficiencia puede venir de la muestra.",
               estilizar(figura_b, "Eficiencia eBOE según el calibre de la salida",
                         f"Media e IC 95 %. N = {numero(eficiencias['n'].sum(), 0)} rondas"), eficiencias),
        Figura("19c", "Dilución de la emulsión por mes", temporadas_texto(ctx), f"N = {numero(mensual['n'].sum(), 0)} turnos",
               "Menos de 1: la emulsión llega más diluida a las centrífugas que como sale del BOE.",
               estilizar(figura_c, "Dilución de la emulsión entre el BOE y las centrífugas",
                         f"Mediana de los turnos por mes. N = {numero(mensual['n'].sum(), 0)} turnos"), mensual)]
    return figuras


def fig_19d(ctx: Contexto) -> list[Figura]:
    lectura = lectura_por_quimico(ctx.eboe_personas())
    if lectura.empty:
        raise ValueError("hacen falta al menos dos químicos con N ≥ 20")
    larga = pd.concat([pd.DataFrame({"quimico": lectura["quimico"], "lugar": lugar, "n": lectura["n"],
                                     "pct": lectura[f"pct_{clave}"], "p": lectura[f"p_{clave}"]})
                       for lugar, clave in (("Entrada", "entrada"), ("Salida", "salida"))], ignore_index=True)
    figura_d = g.columnas_agrupadas(larga, "quimico", "lugar", "pct", "Diferencia contra la mediana de los químicos (%)",
                                    "%", 1)
    sesgos = int(lectura["lectura"].str.startswith("posible sesgo").sum())
    return [Figura("19d", "Lectura de entrada y salida por químico",
                   f"{temporadas_texto(ctx)}; ajustado por eBOE, programa, MTPH y semana",
                   f"N = {numero(lectura['n'].sum(), 0)} pares; {len(lectura)} químicos",
                   f"{sesgos} químico(s) con posible sesgo de medición (p < 0,05 y ≥ 2 %). Es lectura del "
                   "laboratorio, no desempeño.",
                   estilizar(figura_d, "Lectura del aceite en fruta por químico",
                             f"Diferencia contra la lectura mediana, con la misma fruta. N = "
                             f"{numero(lectura['n'].sum(), 0)} pares. {ACLARACION}"),
                   larga.merge(lectura[["quimico", "lectura"]], on="quimico"))]


def _por_turno(datos: pd.DataFrame, ajuste: list[str]) -> pd.DataFrame:
    tabla = turno_por_temporada(datos, "eficiencia_pct", ajuste)
    tabla = tabla[tabla["estimable"]].rename(columns={"turno": "grupo"})
    return tabla.sort_values(["temporada", "grupo"], key=lambda c: c.map({"M": 0, "T": 1, "N": 2}) if c.name == "grupo"
                             else c)


def _turno(ctx: Contexto, codigo: str, datos: pd.DataFrame, ajuste: list[str], que: str) -> Figura:
    tabla = _por_turno(datos, ajuste)
    figura = g.intervalos_agrupados(tabla, f"Eficiencia {que} (%)", "%", 1, "Turno")
    brechas = brecha_turno_t(datos, "eficiencia_pct", ajuste)
    ctx.resumen.setdefault("brecha_turno_t", {})[que] = [
        {"temporada": str(f["temporada"]), "sin_quimico": redondo(f["brecha_sin"], 2),
         "con_quimico": redondo(f["brecha_con"], 2), "p_con": redondo(f["p_con"], 4)} for f in brechas.to_dict("records")]
    return Figura(codigo, f"Eficiencia {que} por turno y temporada", temporadas_texto(ctx),
                  f"N = {numero(tabla['n'].sum(), 0)}",
                  "Medias ajustadas por las condiciones de trabajo; el turno T sale abajo en las tres temporadas.",
                  estilizar(figura, f"Eficiencia {que} por turno",
                            f"Medias ajustadas e IC 95 %. N = {numero(tabla['n'].sum(), 0)}. {ACLARACION}"), tabla)


def _operador(ctx: Contexto, codigo: str, datos: pd.DataFrame, grupo: str, ajuste: list[str], titulo: str) -> Figura:
    resultado = medias_por_grupo(datos, "eficiencia_pct", [grupo], ajuste)
    medias = resultado["medias"]
    medias = medias[medias["estimable"]] if not medias.empty else medias
    if medias.empty:
        raise ValueError("no hay dos grupos estimables con N ≥ 20")
    figura = g.intervalos(medias, "Eficiencia (%)", "%", 1, titulo_x="")
    return Figura(codigo, titulo, f"{temporadas_texto(ctx)}; grupos con N ≥ 20",
                  f"N = {numero(resultado['n'], 0)}; R² = {numero(resultado['r2'] * 100, 1)} %",
                  f"{len(medias)} grupos; medias ajustadas por las condiciones de trabajo. {ACLARACION}",
                  estilizar(figura, titulo, f"Medias ajustadas e IC 95 %. N = {numero(resultado['n'], 0)} · R² "
                            f"{numero(resultado['r2'] * 100, 1)} %. {ACLARACION}"), medias)


def fig_20(ctx: Contexto) -> list[Figura]:
    """Cada figura por separado: si una no tiene datos (por ejemplo, sin balances de las primarias), las otras salen."""
    partes = [("20a1", "Eficiencia eBOE por turno y temporada",
               lambda: _turno(ctx, "20a1", ctx.eboe_personas(), AJUSTE_EBOE, "eBOE")),
              ("20a2", "Eficiencia primaria por turno y temporada",
               lambda: _turno(ctx, "20a2", ctx.primaria_personas(), AJUSTE_PRIMARIA, "primaria")),
              ("20b", "Eficiencia eBOE por operador de eBOE",
               lambda: _operador(ctx, "20b", ctx.eboe_personas(), "operador", AJUSTE_EBOE,
                                 "Eficiencia eBOE por operador de eBOE")),
              ("20c", "Eficiencia de la primaria por operador",
               lambda: _operador(ctx, "20c", ctx.primaria_personas(), "operador_centrifugas", AJUSTE_PRIMARIA,
                                 "Eficiencia de la primaria por operador"))]
    figuras = []
    for codigo, titulo, armar in partes:
        try:
            figuras.append(armar())
        except (ValueError, KeyError) as error:
            figuras.append(Figura(codigo, titulo, motivo=f"{type(error).__name__}: {error}"))
    return figuras


FIGURAS = [fig_03a, fig_03b, fig_03c, fig_04, fig_05a, fig_05b, fig_06, fig_07, fig_08, fig_09, fig_10, fig_11, fig_12,
           fig_13, fig_14, fig_15, fig_16, fig_17, fig_18, fig_19, fig_19d, fig_20]


# ---------------------------------------------------------------- salida

def _guardar(figura: Figura, carpeta: Path, png: bool) -> None:
    nombre = f"{figura.codigo}_{nombre_de_archivo(figura.titulo)}"
    if figura.figura is not None:
        if png:
            destino = carpeta / f"{nombre}.png"
            figura.figura.write_image(destino, width=figura.figura.layout.width or ANCHO,
                                      height=figura.figura.layout.height or ALTO, scale=ESCALA)
        else:
            destino = carpeta / f"{nombre}.plotly.json"
            destino.write_text(figura.figura.to_json(), encoding="utf-8")
        figura.archivos.append(destino.name)
    if figura.tabla is not None and not figura.tabla.empty:
        tabla = figura.tabla.copy()
        for columna in tabla.columns:          # Excel no acepta fechas con zona ni listas
            if tabla[columna].map(lambda v: isinstance(v, (list, dict, tuple))).any():
                tabla[columna] = tabla[columna].map(str)
        destino = carpeta / f"{nombre}.xlsx"
        destino.write_bytes(excel_de_tablas({figura.titulo: tabla}))
        figura.archivos.append(destino.name)


def _celda(texto: str) -> str:
    return str(texto).replace("|", "/").replace("\n", " ")


def indice_md(ctx: Contexto, figuras: list[Figura]) -> str:
    """indice.md: número, título, filtro, N y la frase de lectura de cada figura (sin nombres de personas)."""
    hechas = [f for f in figuras if f.motivo is None]
    lineas_ = [f"# Reunión de cierre · {ctx.planta.nombre} · {ctx.actual}", "",
               f"Generado el {date.today():%d/%m/%Y} con `scripts/exportar_reunion.py`"
               + (" (con nombres en 19d, 20b y 20c)." if ctx.con_nombres else " (anonimizado)."), "",
               f"Temporadas: {temporadas_texto(ctx)}. Modo presentación: sin coherencia del muestreo, sin USD y sin "
               "restar eficiencias de etapas distintas.", "",
               "| N.º | Título | Filtro | N | Lectura | Archivos |", "|---|---|---|---|---|---|"]
    for f in hechas:
        lineas_.append("| " + " | ".join(_celda(x) for x in (f.codigo, f.titulo, f.filtro, f.n, f.frase,
                                                               ", ".join(f.archivos))) + " |")
    no_hechas = [f for f in figuras if f.motivo is not None]
    if no_hechas:
        lineas_ += ["", "## No generadas", "", "| N.º | Título | Motivo |", "|---|---|---|"]
        lineas_ += [f"| {f.codigo} | {_celda(f.titulo)} | {_celda(f.motivo)} |" for f in no_hechas]
    return "\n".join(lineas_) + "\n"


def exportar(sesion, planta: Planta, carpeta: Path, con_nombres: bool = False, png: bool = True,
             periodo_carta: tuple[date, date] = (date(2026, 9, 21), date(2026, 10, 1)), avisar=print) -> list[Figura]:
    """Arma todas las figuras y escribe PNG (o .plotly.json con png=False), Excel, indice.md y resumen.json."""
    carpeta.mkdir(parents=True, exist_ok=True)
    ctx = Contexto(sesion, planta, con_nombres, periodo_carta)
    if png:
        import kaleido
        kaleido.start_sync_server(silence_warnings=True)       # Chrome abierto una vez: cada PNG tarda menos
    figuras = []
    for funcion in FIGURAS:
        codigo = funcion.__name__.removeprefix("fig_")
        try:
            nuevas = funcion(ctx)
        except Exception as error:                              # una figura que falla no corta las demás
            motivo = f"{type(error).__name__}: {error}"
            avisar(f"  {codigo}: no generada ({motivo})")
            if not isinstance(error, (ValueError, KeyError)):
                avisar(traceback.format_exc(limit=3))
            figuras.append(Figura(codigo, (funcion.__doc__ or codigo).strip(), motivo=motivo))
            continue
        for figura in nuevas:
            if figura.motivo is not None:
                avisar(f"  {figura.codigo}: no generada ({figura.motivo})")
                figuras.append(figura)
                continue
            _guardar(figura, carpeta, png)
            avisar(f"  {figura.codigo}: {figura.titulo}")
            figuras.append(figura)
    (carpeta / "indice.md").write_text(indice_md(ctx, figuras), encoding="utf-8")
    (carpeta / "resumen.json").write_text(json.dumps(ctx.resumen, ensure_ascii=False, indent=2, default=str),
                                          encoding="utf-8")
    return figuras


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Exporta las figuras de la reunión de cierre de una planta.")
    parser.add_argument("--planta", default="LEDESMA")
    parser.add_argument("--con-nombres", action="store_true",
                        help="nombres de operadores y químicos en 19d, 20b y 20c (por defecto, anonimizados)")
    parser.add_argument("--sin-png", action="store_true", help="sin kaleido: guarda cada figura como .plotly.json")
    parser.add_argument("--carta-desde", default="2026-09-21", help="inicio de la carta 15b (AAAA-MM-DD)")
    parser.add_argument("--carta-hasta", default="2026-10-01", help="fin de la carta 15b (AAAA-MM-DD)")
    parser.add_argument("--salida", help="carpeta de salida (por defecto data/capturas/reunion_<planta>_<año>)")
    args = parser.parse_args()
    with crear_sesion(crear_motor()) as sesion:
        planta = sesion.scalars(select(Planta).where(Planta.nombre == args.planta)).one_or_none()
        if planta is None:
            sys.exit(f'No existe la planta "{args.planta}".')
        anio = max(temporadas_con_datos(sesion, planta))
        carpeta = Path(args.salida) if args.salida else RAIZ / "data" / "capturas" / f"reunion_{args.planta.lower()}_{anio}"
        print(f"Exportando a {carpeta}")
        figuras = exportar(sesion, planta, carpeta, args.con_nombres, not args.sin_png,
                           (date.fromisoformat(args.carta_desde), date.fromisoformat(args.carta_hasta)))
    hechas = sum(f.motivo is None for f in figuras)
    print(f"Listo: {hechas} figuras generadas, {len(figuras) - hechas} no generadas. Ver {carpeta / 'indice.md'}")


if __name__ == "__main__":
    main()
