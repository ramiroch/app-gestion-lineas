"""Estadística descriptiva para las páginas de análisis.

Cuartiles con el método de Minitab: posición (n + 1)·p, interpolando entre los dos valores
vecinos (en NumPy, method="weibull"). Desvío estándar muestral (n − 1). Los outliers se
marcan con la regla de Tukey (1,5·IQR) y nunca se borran.
"""

import math

import numpy as np
import pandas as pd
from scipy import stats

METODO_CUARTILES = "weibull"   # (n + 1)·p, como Minitab


def _limpios(valores) -> np.ndarray:
    """Valores como array de float, sin vacíos (None/NaN)."""
    serie = pd.Series(valores, dtype="float64")
    return serie.dropna().to_numpy()


def cuartiles(valores) -> tuple[float, float, float] | None:
    """(Q1, mediana, Q3) con el método de Minitab, o None si no hay valores."""
    datos = _limpios(valores)
    if datos.size == 0:
        return None
    q1, mediana, q3 = np.quantile(datos, [0.25, 0.5, 0.75], method=METODO_CUARTILES)
    return float(q1), float(mediana), float(q3)


def resumen_descriptivo(valores) -> dict:
    """N, media, error estándar, desvío, mín, Q1, mediana, Q3 y máx de una serie.

    Desvío muestral s (n − 1); error estándar de la media = s / √n. Con n < 2 el desvío y el
    error estándar quedan en None; sin valores, todo queda en None salvo n = 0.
    """
    datos = _limpios(valores)
    n = int(datos.size)
    resumen = {"n": n, "media": None, "error_estandar": None, "desvio": None, "minimo": None,
               "q1": None, "mediana": None, "q3": None, "maximo": None}
    if n == 0:
        return resumen
    q1, mediana, q3 = cuartiles(datos)
    resumen.update(media=float(datos.mean()), minimo=float(datos.min()), q1=q1, mediana=mediana,
                   q3=q3, maximo=float(datos.max()))
    if n >= 2:
        desvio = float(datos.std(ddof=1))
        resumen.update(desvio=desvio, error_estandar=desvio / math.sqrt(n))
    return resumen


def limites_iqr(valores, k: float = 1.5) -> dict:
    """Límites de outliers por rango intercuartil (regla de Tukey).

    IQR = Q3 − Q1; LI = Q1 − k·IQR; LS = Q3 + k·IQR (k = 1,5 por defecto).
    Devuelve n, q1, q3, iqr, li, ls, outliers (cantidad fuera de [LI, LS]) y los bigotes
    del boxplot: el menor y el mayor valor que quedan dentro de los límites.
    Sin valores, todo queda en None salvo n = 0 y outliers = 0.
    """
    if k <= 0:
        raise ValueError(f"El factor k del IQR tiene que ser mayor que 0 (dice {k}).")
    datos = _limpios(valores)
    resultado = {"n": int(datos.size), "q1": None, "q3": None, "iqr": None, "li": None, "ls": None,
                 "outliers": 0, "bigote_inferior": None, "bigote_superior": None}
    if datos.size == 0:
        return resultado
    q1, _, q3 = cuartiles(datos)
    iqr = q3 - q1
    li, ls = q1 - k * iqr, q3 + k * iqr
    dentro = datos[(datos >= li) & (datos <= ls)]
    resultado.update(q1=q1, q3=q3, iqr=iqr, li=li, ls=ls, outliers=int(datos.size - dentro.size),
                     bigote_inferior=float(dentro.min()), bigote_superior=float(dentro.max()))
    return resultado


def es_outlier_iqr(valores, k: float = 1.5) -> pd.Series:
    """True para cada valor fuera de [LI, LS] de limites_iqr() (los vacíos dan False).

    Sirve para marcar los outliers en tablas y gráficos sin sacarlos de los datos.
    """
    serie = pd.Series(valores, dtype="float64")
    limites = limites_iqr(serie, k)
    if limites["n"] == 0:
        return pd.Series(False, index=serie.index)
    return (serie < limites["li"]) | (serie > limites["ls"])


def ancho_de_clase(valores) -> float | None:
    """Ancho de clase para un histograma, regla de Freedman–Diaconis: 2 × IQR / n^(1/3).

    En las mismas unidades que los valores. None si hay menos de 2 valores o el IQR es 0.
    """
    datos = _limpios(valores)
    if datos.size < 2:
        return None
    q1, _, q3 = cuartiles(datos)
    if q3 == q1:
        return None
    return 2 * (q3 - q1) / datos.size ** (1 / 3)


def curva_normal(media: float, desvio: float, desde: float, hasta: float,
                 puntos: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """(x, densidad) de la normal con esa media y desvío, para dibujar sobre un histograma
    normalizado como densidad de probabilidad."""
    if desvio is None or desvio <= 0:
        raise ValueError(f"El desvío tiene que ser mayor que 0 para dibujar la curva normal (dice {desvio}).")
    if hasta <= desde:
        raise ValueError(f"El rango de la curva está invertido ({desde} → {hasta}).")
    x = np.linspace(desde, hasta, puntos)
    return x, stats.norm.pdf(x, loc=media, scale=desvio)


def media_ponderada_por_turno(datos: pd.DataFrame, columna: str, toneladas: pd.DataFrame) -> dict:
    """Media de una columna ponderada por las toneladas de fruta procesadas en cada turno.

    Fórmula: Σ (t del turno × media del turno) / Σ t, solo con los turnos que tienen
    toneladas (> 0). Es la ponderación de la hoja Resumen de LEDESMA.
    datos lleva fecha_produccion, turno y la columna; toneladas lleva fecha_produccion,
    turno y fruta_t (core.produccion.toneladas_por_turno).

    Devuelve valor (la ponderada, o la simple si ningún turno tiene toneladas),
    ponderada (True/False), media_simple, n (valores no vacíos) y n_ponderados (valores
    en turnos con toneladas, los que entran en la ponderación).
    """
    validos = datos[["fecha_produccion", "turno", columna]].dropna(subset=[columna])
    n = len(validos)
    simple = float(validos[columna].mean()) if n else None
    resultado = {"valor": simple, "ponderada": False, "media_simple": simple, "n": n, "n_ponderados": 0}
    if n == 0:
        return resultado

    por_turno = validos.groupby(["fecha_produccion", "turno"])[columna].agg(["mean", "size"]).reset_index()
    con_t = por_turno.merge(toneladas, on=["fecha_produccion", "turno"])
    con_t = con_t[con_t["fruta_t"] > 0]
    if con_t.empty:
        return resultado
    resultado.update(valor=float((con_t["mean"] * con_t["fruta_t"]).sum() / con_t["fruta_t"].sum()),
                     ponderada=True, n_ponderados=int(con_t["size"].sum()))
    return resultado
