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
RALLADOS = ["Ok", "Poco rallada", "Sobre rallada"]     # rallado de la fruta de salida
VENTANA_USO = 3                  # programas consecutivos que se buscan en la frase de uso
PROGRAMAS_ALTOS = (8, 9)         # contra los que se compara el % de rallado Ok
LIMITE_SOBRE_RALLADO = 1.0       # % de sobre rallado a partir del cual se menciona en la frase


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


def _rango(numeros) -> str:
    """[2, 3, 4] → "N2–N4"; [8] → "N8"."""
    numeros = sorted(numeros)
    return f"N{numeros[0]}" if len(numeros) == 1 else f"N{numeros[0]}–N{numeros[-1]}"


def uso_de_programas(pares: pd.DataFrame) -> pd.DataFrame:
    """Cuánto se usa cada programa: % de pares de cada eBOE en cada programa.

    Devuelve eboe, programa, programa_num, n, n_eboe (pares del eBOE con programa) y porcentaje
    (n / n_eboe × 100), ordenado por eBOE y número de programa.
    """
    datos = pares.dropna(subset=["eboe", "programa"])
    uso = datos.groupby(["eboe", "programa"]).size().rename("n").reset_index()
    uso["programa_num"] = uso["programa"].map(numero_programa)
    uso["n_eboe"] = uso.groupby("eboe")["n"].transform("sum")
    uso["porcentaje"] = uso["n"] / uso["n_eboe"] * 100
    return uso.sort_values(["eboe", "programa_num", "programa"], ignore_index=True)[
        ["eboe", "programa", "programa_num", "n", "n_eboe", "porcentaje"]]


def rallado_por_programa(pares: pd.DataFrame) -> pd.DataFrame:
    """% de rallado de la fruta de salida (Ok, Poco rallada, Sobre rallada) por eBOE y programa.

    Usa los pares con rallado_salida cargado; n es esa cantidad. Devuelve eboe, programa,
    programa_num, n, pct_ok, pct_poco y pct_sobre.
    """
    datos = pares.dropna(subset=["eboe", "programa", "rallado_salida"])
    filas = []
    for (eboe, programa), sub in datos.groupby(["eboe", "programa"], sort=True):
        conteo = sub["rallado_salida"].value_counts()
        n = len(sub)
        filas.append({"eboe": eboe, "programa": programa, "programa_num": numero_programa(programa), "n": n,
                      "pct_ok": conteo.get("Ok", 0) / n * 100,
                      "pct_poco": conteo.get("Poco rallada", 0) / n * 100,
                      "pct_sobre": conteo.get("Sobre rallada", 0) / n * 100})
    tabla = pd.DataFrame(filas, columns=["eboe", "programa", "programa_num", "n", "pct_ok", "pct_poco", "pct_sobre"])
    return tabla.sort_values(["eboe", "programa_num", "programa"], ignore_index=True)


def frase_uso(pares: pd.DataFrame, eboe) -> str:
    """Uso de programas y rallado de salida de un eBOE, en una frase.

    Busca los VENTANA_USO programas consecutivos más usados y compara su % de pares con rallado Ok
    con el de PROGRAMAS_ALTOS (si la ventana no los incluye). El sobre rallado se menciona solo en los
    programas donde supera LIMITE_SOBRE_RALLADO %. "Del tiempo" = % de pares (≈ una muestra por hora).
    Ejemplo: "El eBOE 2 trabaja el 59 % del tiempo en N2–N4; en N8–N9 el rallado es Ok en el 72,7 % de
    los pares (N 33), contra 12,0 % (N 472) en N2–N4."
    """
    del_eboe = pares[pares["eboe"] == eboe]
    uso = uso_de_programas(del_eboe).dropna(subset=["programa_num"])
    if uso.empty:
        return f"El eBOE {eboe} no tiene pares con programa."
    porcentajes = dict(zip(uso["programa_num"].astype(int), uso["porcentaje"]))
    ventanas = [list(range(inicio, inicio + VENTANA_USO)) for inicio in range(len(PROGRAMAS) - VENTANA_USO + 1)]
    ventana = max(ventanas, key=lambda v: (sum(porcentajes.get(p, 0) for p in v), -v[0]))
    frase = (f"El eBOE {eboe} trabaja el {numero(sum(porcentajes.get(p, 0) for p in ventana), 0)} % "
             f"del tiempo en {_rango(ventana)}")

    con_rallado = del_eboe.dropna(subset=["rallado_salida"]).assign(
        num=lambda d: d["programa"].map(numero_programa))

    def ok(numeros):
        sub = con_rallado[con_rallado["num"].isin(numeros)]
        return len(sub), ((sub["rallado_salida"] == "Ok").mean() * 100 if len(sub) else None)

    if not set(ventana) & set(PROGRAMAS_ALTOS):
        n_altos, ok_altos = ok(PROGRAMAS_ALTOS)
        n_ventana, ok_ventana = ok(ventana)
        if n_altos and n_ventana:
            frase += (f"; en {_rango(PROGRAMAS_ALTOS)} el rallado es Ok en el {numero(ok_altos, 1)} % de los pares "
                      f"(N {numero(n_altos, 0)}), contra {numero(ok_ventana, 1)} % (N {numero(n_ventana, 0)}) "
                      f"en {_rango(ventana)}")
        else:
            frase += f"; no hay pares con rallado cargado en {_rango(PROGRAMAS_ALTOS)} para comparar"

    rallado = rallado_por_programa(del_eboe)
    altos = rallado[rallado["pct_sobre"] > LIMITE_SOBRE_RALLADO]
    if altos.empty:
        return frase + "."
    detalles = [f"{f['programa']} ({numero(f['pct_sobre'], 1)} %, N {numero(f['n'], 0)})" for f in altos.to_dict("records")]
    lista = detalles[0] if len(detalles) == 1 else ", ".join(detalles[:-1]) + " y " + detalles[-1]
    return f"{frase}. El sobre rallado supera el {numero(LIMITE_SOBRE_RALLADO, 0)} % en {lista}."
