"""Volatilización / agua (Sistema › Volatilización): eficiencia eBOE y eficiencia de proceso contra la fase pesada.

Hipótesis (informe Brown 2023 y estudio de aerosolización): con menos fase pesada que la requerida los rolos remueven
más, pero parte del aceite se pierde al aire; el resultado neto es menos rendimiento en tambor.

Variables de fase pesada (sin circularidad con la eficiencia; core.fase_pesada):
- rango_fp_ref (%): rango de fase pesada con la eficiencia fija en eficiencia_ref_rango_pct.
- fp_especifica (m³/t): caudal real de fase pesada / MTPH.
Cada una se agrupa en rangos fijos (RANGOS_FP). El valor de un turno o un día es la mediana de sus análisis.

- Eficiencia eBOE: por turno (media de los pares del turno, los eBOE juntos) y por día (media de cada turno del día
  ponderada por sus toneladas aprobadas, core.estadistica.media_ponderada_por_turno).
- Eficiencia de proceso (rendimiento en tambor): por DÍA, porque el envasado no coincide con el turno. Es la misma de
  Análisis › Proceso (core.proceso.resumen_proceso): Σ aceite envasado / Σ fruta de los turnos aprobados del día,
  sobre el aceite en fruta de entrada del día ponderado por toneladas, en %. Los días con menos de FRUTA_MINIMA_DIA t
  no entran (core.factores).
Las dos eficiencias se muestran por separado: nunca se comparan entre sí ni se restan.
- medias_por_rango(): media, IC 95 % (t de Student) y N por temporada y rango.
"""

import numpy as np
import pandas as pd
from scipy import stats

from core.estadistica import media_ponderada_por_turno
from core.factores import FRUTA_MINIMA_DIA
from core.fase_pesada import CLAVES_TURNO
from core.proceso import resumen_proceso

ALFA = 0.05
INFINITO = np.inf
RANGOS_FP = {
    "fp_especifica": {"nombre": "Fase pesada específica", "unidad": "m³/t", "decimales": 2,
                      "cortes": [-INFINITO, 0.4, 0.5, 0.6, 0.8, INFINITO],
                      "etiquetas": ["< 0,4", "0,4–0,5", "0,5–0,6", "0,6–0,8", "> 0,8"]},
    "rango_fp_ref": {"nombre": "Rango de fase pesada de referencia", "unidad": "%", "decimales": 1,
                     "cortes": [-INFINITO, -40, -25, -10, 0, 15, INFINITO],
                     "etiquetas": ["< −40", "−40 a −25", "−25 a −10", "−10 a 0", "0 a 15", "> 15"]},
}
HIPOTESIS = ("Hipótesis (informe Brown 2023 y estudio de aerosolización): con menos fase pesada que la requerida los "
             "rolos remueven más, pero parte del aceite se pierde al aire; el resultado neto es menos rendimiento en "
             "tambor.")


def asignar_rango(valores: pd.Series, variable: str) -> pd.Series:
    """Etiqueta del rango de cada valor (RANGOS_FP[variable]); cada rango incluye su límite inferior y no el superior
    (0,4 va en "0,4–0,5"). Vacío si el valor está vacío."""
    if variable not in RANGOS_FP:
        raise ValueError(f'Variable de fase pesada desconocida: "{variable}". Las válidas son: '
                         f'{", ".join(RANGOS_FP)}.')
    rango = RANGOS_FP[variable]
    return pd.cut(pd.to_numeric(valores, errors="coerce"), bins=rango["cortes"], labels=rango["etiquetas"],
                  right=False).astype(object).where(lambda s: s.notna(), None)


def eficiencia_eboe_por_turno(tabla: pd.DataFrame, variable: str) -> pd.DataFrame:
    """Eficiencia eBOE (%) por turno: media de los pares del turno (los eBOE juntos), con la mediana de la variable de
    fase pesada de sus análisis y su rango. tabla: core.fase_pesada.tabla_fase_pesada(). Columnas: temporada,
    fecha_produccion, turno, eficiencia, n (pares), valor (mediana de la variable) y rango."""
    datos = tabla.dropna(subset=["eficiencia_par"]).assign(eficiencia=lambda d: d["eficiencia_par"] * 100)
    columnas = ["temporada", "fecha_produccion", "turno", "eficiencia", "n", "valor", "rango"]
    if datos.empty:
        return pd.DataFrame(columns=columnas)
    turnos = datos.groupby(CLAVES_TURNO, sort=True).agg(temporada=("temporada", "first"),
                                                        eficiencia=("eficiencia", "mean"), n=("eficiencia", "size"),
                                                        valor=(variable, "median")).reset_index()
    return turnos.assign(rango=asignar_rango(turnos["valor"], variable))[columnas]


def eficiencias_por_dia(tabla: pd.DataFrame, variable: str | None, toneladas: pd.DataFrame,
                        produccion: pd.DataFrame) -> pd.DataFrame:
    """Por día de producción: eficiencia eBOE (%, media de cada turno ponderada por sus toneladas aprobadas) y
    eficiencia de proceso (%, la de Análisis › Proceso con los turnos aprobados del día; vacía si el día tiene menos de
    FRUTA_MINIMA_DIA t o no tiene aceite envasado), con la mediana de la variable de fase pesada y su rango.

    tabla: core.fase_pesada.tabla_fase_pesada(). toneladas: core.produccion.toneladas_por_turno(). produccion:
    registros aprobados del período (core.proceso.filtrar_produccion). Columnas: temporada, fecha_produccion,
    eficiencia_eboe, n_pares, ponderada, eficiencia_proceso, rendimiento (rendimiento en tambor = Σ aceite envasado /
    Σ fruta, kg/MT; vacío como la eficiencia de proceso), fruta_t, valor y rango (vacíos si variable es None).
    """
    columnas = ["temporada", "fecha_produccion", "eficiencia_eboe", "n_pares", "ponderada", "eficiencia_proceso",
                "rendimiento", "fruta_t", "valor", "rango"]
    pares = tabla.dropna(subset=["eficiencia_par"]).assign(eficiencia_pct=lambda d: d["eficiencia_par"] * 100)
    if pares.empty:
        return pd.DataFrame(columns=columnas)
    por_dia = dict(list(produccion.groupby("fecha_produccion"))) if len(produccion) else {}
    filas = []
    for dia, sub in pares.groupby("fecha_produccion", sort=True):
        eficiencia = media_ponderada_por_turno(sub, "eficiencia_pct", toneladas)
        del_dia = por_dia.get(dia, produccion.iloc[0:0])
        totales = resumen_proceso(sub, toneladas, del_dia)["totales"]
        fruta = totales["fruta_t"]
        alcanza = fruta is not None and fruta >= FRUTA_MINIMA_DIA
        filas.append({"temporada": sub["temporada"].iloc[0], "fecha_produccion": dia,
                      "eficiencia_eboe": eficiencia["valor"], "n_pares": eficiencia["n"],
                      "ponderada": eficiencia["ponderada"],
                      "eficiencia_proceso": totales["eficiencia_proceso"] if alcanza else None,
                      "rendimiento": totales["rendimiento"] if alcanza else None, "fruta_t": fruta,
                      "valor": sub[variable].median() if variable else np.nan})
    dias = pd.DataFrame(filas)
    rango = asignar_rango(dias["valor"], variable) if variable else None
    return dias.assign(rango=rango)[columnas]


def media_ic(valores, alfa: float = ALFA) -> dict:
    """Media, IC 1 − alfa con la t de Student y N. Con menos de 2 valores, sin IC."""
    limpios = pd.to_numeric(pd.Series(valores), errors="coerce").dropna()
    n = len(limpios)
    if n == 0:
        return {"n": 0, "media": None, "ic_inf": None, "ic_sup": None}
    media = float(limpios.mean())
    if n < 2:
        return {"n": n, "media": media, "ic_inf": None, "ic_sup": None}
    margen = stats.t.ppf(1 - alfa / 2, n - 1) * limpios.std(ddof=1) / np.sqrt(n)
    return {"n": n, "media": media, "ic_inf": media - margen, "ic_sup": media + margen}


def medias_por_rango(datos: pd.DataFrame, columna: str, variable: str, alfa: float = ALFA) -> pd.DataFrame:
    """Media, IC 1 − alfa y N de `columna` por temporada y rango de la variable de fase pesada, en el orden de los
    rangos. datos: eficiencia_eboe_por_turno() o eficiencias_por_dia(). Columnas: temporada, grupo (el rango), n,
    media, ic_inf, ic_sup (las de core.factores.por_categoria, para graficos.intervalos_agrupados)."""
    columnas = ["temporada", "grupo", "n", "media", "ic_inf", "ic_sup"]
    validos = datos.dropna(subset=[columna, "rango"])
    filas = []
    for (temporada, rango), sub in validos.groupby(["temporada", "rango"]):
        filas.append({"temporada": temporada, "grupo": rango, **media_ic(sub[columna], alfa)})
    if not filas:
        return pd.DataFrame(columns=columnas)
    orden = {etiqueta: k for k, etiqueta in enumerate(RANGOS_FP[variable]["etiquetas"])}
    tabla = pd.DataFrame(filas, columns=columnas).astype({"media": float, "ic_inf": float, "ic_sup": float})
    return tabla.sort_values(["temporada", "grupo"], key=lambda c: c.map(orden) if c.name == "grupo" else c,
                             ignore_index=True)
