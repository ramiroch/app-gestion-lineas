"""Regresión lineal simple (mínimos cuadrados) para relacionar dos variables de proceso.

y = ordenada + pendiente · x. R² = proporción de la variación de y que explica la recta.
El p es el de la prueba de pendiente = 0 (t con n − 2 GL); el IC de la pendiente usa la misma t.
Fuente: Montgomery y Runger, Applied Statistics and Probability for Engineers, cap. 11.
La regresión se calcula dentro de cada grupo (por ejemplo, cada temporada): juntar grupos con
niveles distintos puede inventar o tapar una relación.
"""

import numpy as np
import pandas as pd
from scipy import stats

from core.formato import numero, texto_p

ALFA = 0.05
N_MINIMO = 3


def regresion_lineal(x, y, alfa: float = ALFA) -> dict:
    """Recta de mínimos cuadrados de y en función de x (se ignoran los pares con algún vacío).

    Devuelve n, pendiente, ordenada, r2, p (de la pendiente), ic_inf e ic_sup (IC 1 − alfa de
    la pendiente). Con menos de N_MINIMO pares o x sin variación, todo queda en None salvo n.
    """
    pares = pd.DataFrame({"x": pd.to_numeric(pd.Series(x), errors="coerce").to_numpy(),
                          "y": pd.to_numeric(pd.Series(y), errors="coerce").to_numpy()}).dropna()
    n = len(pares)
    resultado = {"n": n, "pendiente": None, "ordenada": None, "r2": None, "p": None, "ic_inf": None, "ic_sup": None}
    if n < N_MINIMO or pares["x"].nunique() < 2:
        return resultado
    r = stats.linregress(pares["x"], pares["y"])
    margen = stats.t.ppf(1 - alfa / 2, n - 2) * r.stderr
    resultado.update(pendiente=float(r.slope), ordenada=float(r.intercept), r2=float(r.rvalue ** 2),
                     p=float(r.pvalue), ic_inf=float(r.slope - margen), ic_sup=float(r.slope + margen))
    return resultado


def regresiones_por_grupo(df: pd.DataFrame, x: str, y: str, grupos: list[str], alfa: float = ALFA) -> pd.DataFrame:
    """Una regresión lineal de y en función de x por cada combinación de las columnas `grupos`.

    Devuelve un DataFrame con las columnas de grupo y las de regresion_lineal(), ordenado por grupo.
    """
    for columna in [x, y, *grupos]:
        if columna not in df.columns:
            raise ValueError(f'No existe la columna "{columna}".')
    filas = []
    for clave, sub in df.groupby(grupos, sort=True):
        clave = clave if isinstance(clave, tuple) else (clave,)
        filas.append(dict(zip(grupos, clave)) | regresion_lineal(sub[x], sub[y], alfa))
    return pd.DataFrame(filas, columns=[*grupos, "n", "pendiente", "ordenada", "r2", "p", "ic_inf", "ic_sup"])


def frase_pendiente(resultado: dict, grupo: str, paso_x: str, x_texto: str, y_unidad: str, y_texto: str,
                    decimales: int = 1, alfa: float = ALFA) -> str:
    """La pendiente en una frase simple, en castellano.

    Ejemplo: "Dentro de 2026, cada 1 % más de aceite en emulsión se asocia con 3,2 puntos menos
    de eficiencia (R² = 0,12: explica el 12 % de la variación; p < 0,001)."
    paso_x: el aumento de x con su unidad ("1 %", "1 m³/h"); x_texto: "de aceite en emulsión";
    y_unidad: "puntos"; y_texto: "de eficiencia".
    """
    if pd.isna(resultado["pendiente"]):     # None, o NaN si viene de una fila de regresiones_por_grupo()
        return f"Dentro de {grupo} no hay datos suficientes para la recta (N = {resultado['n']})."
    sentido = "más" if resultado["pendiente"] > 0 else "menos"
    r2 = resultado["r2"]
    frase = (f"Dentro de {grupo}, cada {paso_x} más {x_texto} se asocia con "
             f"{numero(abs(resultado['pendiente']), decimales)} {y_unidad} {sentido} {y_texto} "
             f"(R² = {numero(r2, 2)}: explica el {numero(r2 * 100, 0)} % de la variación; {texto_p(resultado['p'])}")
    if resultado["p"] >= alfa:
        return frase + "), pero la relación no es significativa."
    return frase + ")."


def recta(resultado: dict, x) -> np.ndarray:
    """Valores de la recta en los x dados (para dibujarla)."""
    return resultado["ordenada"] + resultado["pendiente"] * np.asarray(x, dtype=float)
