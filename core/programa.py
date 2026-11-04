"""Eficiencia del eBOE según programa (N0 a N9) y MTPH: análisis 6 y 7 de la sección 9 del mapeo.

Una combinación es eBOE + programa + MTPH (redondeada a 1 MTPH). Se trabaja con los pares
entrada/salida (core.analisis.pares_con_emulsion), con la eficiencia en puntos de %.
Promedio simple por par: cada par cuenta igual (no es un promedio por día, turno, proceso ni
temporada, que son los que se ponderan por toneladas).
Dos mínimos de N:
- N_MINIMO (3): debajo, la combinación se muestra tenue (poco representativa) y no entra en la superficie.
- N_MINIMO_RANKING (10): solo las combinaciones con al menos ese N entran en el ranking de mejores y
  peores y en la frase; con N chico los extremos salen de combinaciones con IC muy anchos.
"""

import numpy as np
import pandas as pd
from scipy import stats

from core.formato import numero

N_MINIMO = 3
N_MINIMO_RANKING = 10
PROGRAMAS = list(range(10))      # N0 a N9


def numero_programa(programa) -> int | None:
    """"N4" → 4; vacío o con otro formato → None."""
    if not isinstance(programa, str) or not programa.startswith("N") or not programa[1:].isdigit():
        return None
    return int(programa[1:])


def combinaciones(pares: pd.DataFrame, alfa: float = 0.05) -> pd.DataFrame:
    """Eficiencia de cada combinación eBOE + programa + MTPH.

    pares lleva eboe, programa, tph y eficiencia_pct. Se descartan los pares sin alguno de esos
    datos. Devuelve eboe, programa, programa_num, mtph, n, media, desvio, ic_inf, ic_sup (IC
    1 − alfa de la media con t de n − 1 GL; vacíos con n < 2) y representativa (n ≥ N_MINIMO),
    ordenado por eBOE, programa y MTPH.
    """
    datos = pares.dropna(subset=["eboe", "programa", "tph", "eficiencia_pct"]).assign(
        mtph=lambda d: d["tph"].round().astype(int))
    filas = []
    for (eboe, programa, mtph), sub in datos.groupby(["eboe", "programa", "mtph"], sort=True):
        valores = sub["eficiencia_pct"].to_numpy()
        n, media = len(valores), float(valores.mean())
        desvio = float(valores.std(ddof=1)) if n >= 2 else None
        margen = stats.t.ppf(1 - alfa / 2, n - 1) * desvio / np.sqrt(n) if n >= 2 else None
        filas.append({"eboe": eboe, "programa": programa, "programa_num": numero_programa(programa),
                      "mtph": int(mtph), "n": n, "media": media, "desvio": desvio,
                      "ic_inf": media - margen if margen is not None else None,
                      "ic_sup": media + margen if margen is not None else None,
                      "representativa": n >= N_MINIMO})
    columnas = ["eboe", "programa", "programa_num", "mtph", "n", "media", "desvio", "ic_inf", "ic_sup",
                "representativa"]
    return pd.DataFrame(filas, columns=columnas)


def _representativas(combos: pd.DataFrame) -> pd.DataFrame:
    """Combinaciones con N ≥ N_MINIMO (con .loc y bool explícito: así anda también con la tabla vacía)."""
    return combos.loc[combos["representativa"].astype(bool)]


def _con_n_minimo(combos: pd.DataFrame, n_minimo: int) -> pd.DataFrame:
    return combos.loc[(combos["n"] >= n_minimo).astype(bool)]


def mejores_y_peores(combos: pd.DataFrame, k: int = 5, n_minimo: int = N_MINIMO_RANKING) -> pd.DataFrame:
    """Las k mejores y las k peores combinaciones de cada eBOE con N ≥ n_minimo.

    Ordena por eficiencia media (a igual media, la de más N primero). Si un eBOE tiene menos de
    2k combinaciones, las peores no repiten las que ya están entre las mejores. Agrega las
    columnas grupo ("Mejores" o "Peores") y puesto (1 = la mejor, o la peor).
    """
    partes = []
    for _, sub in _con_n_minimo(combos, n_minimo).groupby("eboe", sort=True):
        orden = sub.sort_values(["media", "n"], ascending=[False, False])
        mejores = orden.head(k).assign(grupo="Mejores")
        peores = orden.drop(mejores.index).sort_values(["media", "n"], ascending=[True, False]).head(k)
        partes += [mejores.assign(puesto=range(1, len(mejores) + 1)),
                   peores.assign(grupo="Peores", puesto=range(1, len(peores) + 1))]
    if not partes:
        return pd.DataFrame(columns=[*combos.columns, "grupo", "puesto"])
    return pd.concat(partes, ignore_index=True)


def grilla(combos: pd.DataFrame, eboe) -> tuple[list[int], list[int], np.ndarray, np.ndarray]:
    """Matriz de eficiencia media para la superficie y el contorno de un eBOE.

    Devuelve (mtph, programas, eficiencia, n): filas = MTPH con al menos una celda representativa,
    columnas = programas 0 a 9; las celdas sin datos o con N < N_MINIMO quedan en NaN.
    """
    sub = _representativas(combos)
    sub = sub[(sub["eboe"] == eboe) & sub["programa_num"].notna()]
    mtph = sorted(sub["mtph"].unique().tolist())
    eficiencia = np.full((len(mtph), len(PROGRAMAS)), np.nan)
    n = np.full((len(mtph), len(PROGRAMAS)), np.nan)
    for fila in sub.itertuples():
        i, j = mtph.index(fila.mtph), PROGRAMAS.index(int(fila.programa_num))
        eficiencia[i, j], n[i, j] = fila.media, fila.n
    return mtph, PROGRAMAS, eficiencia, n


def frase_programa(combos: pd.DataFrame, eboe, n_minimo: int = N_MINIMO_RANKING) -> str:
    """Mejor y peor combinación de un eBOE entre las que tienen N ≥ n_minimo, en una frase.

    Ejemplo: "El eBOE 2 rinde mejor con N4 a 10 MTPH (84,5 %, N 37) y peor con N7 a 13 MTPH
    (74,1 %, N 22)."
    """
    sub = _con_n_minimo(combos, n_minimo)
    sub = sub[sub["eboe"] == eboe]
    if sub.empty:
        return f"El eBOE {eboe} no tiene combinaciones de programa y MTPH con N ≥ {n_minimo}."

    def texto(fila):
        return f"{fila['programa']} a {fila['mtph']} MTPH ({numero(fila['media'], 1)} %, N {fila['n']})"

    orden = sub.sort_values(["media", "n"], ascending=[False, False])
    mejor, peor = orden.iloc[0], orden.iloc[-1]
    if len(orden) == 1:
        return f"El eBOE {eboe} tiene una sola combinación con N ≥ {n_minimo}: {texto(mejor)}."
    return f"El eBOE {eboe} rinde mejor con {texto(mejor)} y peor con {texto(peor)}."


def de_combinaciones(pares: pd.DataFrame, claves) -> pd.DataFrame:
    """Pares que pertenecen a las combinaciones dadas, como (eboe, programa, mtph)."""
    elegidas = {(int(e), p, int(m)) for e, p, m in claves}
    mtph = pares["tph"].round()
    marca = [(e, p, int(m)) in elegidas if pd.notna(m) else False
             for e, p, m in zip(pares["eboe"], pares["programa"], mtph)]
    return pares[marca].reset_index(drop=True)
