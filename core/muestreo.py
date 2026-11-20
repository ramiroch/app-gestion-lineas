"""Representatividad del muestreo: ¿la fruta de la muestra de salida es comparable con la de entrada?

La eficiencia del eBOE compara el aceite de la fruta que entra con el de la que sale en la misma ronda. Si la muestra
de salida trae fruta de otro tamaño (más grande o más chica), la comparación no es justa: la fruta más grande tiene
menos cáscara por kilo y da menos aceite en kg/MT, aunque el eBOE haya trabajado igual. El eBOE no cambia el tamaño
de la fruta, así que entrada y salida deberían tener el mismo peso medio.
- pares_calibre(): une la entrada y la salida de la misma ronda y eBOE, con el peso medio del fruto (g) = peso de
  la muestra (kg) × 1000 / n.º de frutos y los frutos en 10 kg = n.º de frutos × 10 / peso (kg), de cada lado.
  Los pares con menos de FRUTOS_MINIMO o más de FRUTOS_MAXIMO frutos en 10 kg de algún lado se marcan como error de
  carga (no se borran) y no entran en la prueba.
- prueba_pareada(): por temporada y eBOE, media de entrada y de salida, diferencia (salida − entrada) y su %, prueba
  pareada (t pareada si las diferencias son normales por Anderson-Darling; si no, Wilcoxon de rangos con signo), % de
  rondas con |diferencia| mayor que la tolerancia y el aviso "pueden no ser comparables" si la diferencia media pasa
  el parámetro de la planta tolerancia_calibre_pct.
- diferencia_por_eboe(): media e IC de la diferencia % de peso medio por eBOE y temporada (para el gráfico).
- eficiencia_por_calibre(): eficiencia eBOE de las rondas con la salida más chica, comparable o más grande que la
  entrada (según la tolerancia), por temporada.
- frase_muestreo(): resumen en una oración.
"""

import numpy as np
import pandas as pd
from scipy import stats

from core.capacidad import normalidad
from core.comparaciones import comparar_grupos
from core.factores import FRUTOS_MAXIMO, por_categoria
from core.formato import con_signo, numero

ALFA = 0.05
FRUTOS_MINIMO = 20          # frutos en 10 kg: menos que esto (frutos de más de 500 g) es un error de carga
ERROR_CARGA = f"error de carga (menos de {FRUTOS_MINIMO} o más de {FRUTOS_MAXIMO} frutos en 10 kg)"
AVISO = "Las muestras de entrada y salida pueden no ser comparables"
# variable → nombre, unidad, columna de entrada, columna de salida, decimales
VARIABLES = {"peso": ("Peso medio del fruto", "g", "peso_entrada_g", "peso_salida_g", 0),
             "frutos": ("Frutos en 10 kg", "frutos", "frutos_entrada", "frutos_salida", 1)}
GRUPOS = ["Salida más chica", "Comparable", "Salida más grande"]


def _fruta(tabla: pd.DataFrame) -> pd.DataFrame:
    """Peso medio del fruto (g) y frutos en 10 kg de cada análisis; vacíos si falta el peso o el n.º de frutos."""
    validos = (tabla["n_frutos"] > 0) & (tabla["peso_fruta_kg"] > 0)
    return pd.DataFrame({"peso_g": (tabla["peso_fruta_kg"] * 1000 / tabla["n_frutos"]).where(validos),
                         "frutos": (tabla["n_frutos"] * 10 / tabla["peso_fruta_kg"]).where(validos)})


def pares_calibre(tabla: pd.DataFrame) -> pd.DataFrame:
    """Un renglón por ronda y eBOE con la fruta de la entrada y de la salida.

    tabla: core.analisis.tabla_analisis() (una fila por análisis de fruta, con lugar "Entrada" o "Salida").
    Peso medio del fruto (g) = peso_fruta_kg × 1000 / n_frutos; frutos en 10 kg = n_frutos × 10 / peso_fruta_kg.
    dif_peso_g = salida − entrada; dif_peso_pct = dif_peso_g / entrada × 100 (positivo: la fruta de la salida es más
    grande). error_carga: algún lado fuera de FRUTOS_MINIMO–FRUTOS_MAXIMO frutos en 10 kg (se marca, no se borra).
    eficiencia_pct: la del par (%). Solo las rondas con los dos lados medidos.
    """
    columnas = ["temporada", "eboe", "fecha_hora", "fecha_produccion", "turno", "entrada_repetida", "eficiencia_pct",
                "peso_entrada_g", "peso_salida_g", "frutos_entrada", "frutos_salida", "dif_peso_g", "dif_peso_pct",
                "dif_frutos", "error_carga"]
    fruta = pd.concat([tabla, _fruta(tabla)], axis=1).dropna(subset=["peso_g"])
    claves = ["eboe", "fecha_hora"]
    entrada = fruta[fruta["lugar"] == "Entrada"]
    salida = fruta[fruta["lugar"] == "Salida"]
    if entrada.empty or salida.empty:
        return pd.DataFrame(columns=columnas)
    extras = [c for c in ("entrada_repetida", "eficiencia_par") if c in entrada]
    pares = (entrada[[*claves, "temporada", "fecha_produccion", "turno", *extras, "peso_g", "frutos"]]
             .rename(columns={"peso_g": "peso_entrada_g", "frutos": "frutos_entrada"})
             .merge(salida[[*claves, "peso_g", "frutos"]].rename(columns={"peso_g": "peso_salida_g",
                                                                         "frutos": "frutos_salida"}),
                    on=claves, how="inner"))
    pares["entrada_repetida"] = pares.get("entrada_repetida", pd.Series(False, index=pares.index)).fillna(False)
    pares["eficiencia_pct"] = pares["eficiencia_par"] * 100 if "eficiencia_par" in pares else np.nan
    pares["dif_peso_g"] = pares["peso_salida_g"] - pares["peso_entrada_g"]
    pares["dif_peso_pct"] = pares["dif_peso_g"] / pares["peso_entrada_g"] * 100
    pares["dif_frutos"] = pares["frutos_salida"] - pares["frutos_entrada"]
    fuera = lambda f: (f < FRUTOS_MINIMO) | (f > FRUTOS_MAXIMO)        # noqa: E731
    pares["error_carga"] = fuera(pares["frutos_entrada"]) | fuera(pares["frutos_salida"])
    return pares[columnas].sort_values(["temporada", "eboe", "fecha_hora"], ignore_index=True)


def _prueba(diferencias: np.ndarray, alfa: float) -> tuple[str | None, float | None]:
    """t pareada si las diferencias son normales (Anderson-Darling, p ≥ alfa); si no, Wilcoxon. Sin prueba con N < 3
    o si todas las diferencias son 0."""
    if len(diferencias) < 3 or not np.any(diferencias):
        return None, None
    if normalidad(diferencias)["normal"]:
        return "t pareada", float(stats.ttest_1samp(diferencias, 0).pvalue)
    return "Wilcoxon", float(stats.wilcoxon(diferencias).pvalue)


def prueba_pareada(pares: pd.DataFrame, variable: str = "peso", tolerancia_pct: float | None = None,
                   alfa: float = ALFA) -> pd.DataFrame:
    """Entrada contra salida de la misma ronda, por temporada y eBOE (sin los pares con error de carga).

    variable: "peso" (peso medio del fruto, g) o "frutos" (frutos en 10 kg). Columnas: temporada, eboe, n,
    media_entrada, media_salida, dif (media de salida − entrada por ronda), dif_pct (dif / media_entrada × 100), prueba
    ("t pareada" o "Wilcoxon"), p, pct_fuera (% de rondas con |diferencia %| > tolerancia_pct) y no_comparable
    (|dif_pct| > tolerancia_pct). Sin tolerancia (parámetro "a definir"), pct_fuera y no_comparable quedan vacíos.
    """
    if variable not in VARIABLES:
        raise ValueError(f"Variable desconocida: {variable}. Las válidas son: {', '.join(VARIABLES)}.")
    _, _, col_entrada, col_salida, _ = VARIABLES[variable]
    filas = []
    for (temporada, eboe), sub in pares[~pares["error_carga"].astype(bool)].groupby(["temporada", "eboe"], sort=True):
        entrada, salida = sub[col_entrada].to_numpy(float), sub[col_salida].to_numpy(float)
        diferencias = salida - entrada
        prueba, p = _prueba(diferencias, alfa)
        media_entrada = float(entrada.mean())
        dif_pct = float(diferencias.mean()) / media_entrada * 100
        fila = {"temporada": temporada, "eboe": eboe, "n": len(sub), "media_entrada": media_entrada,
                "media_salida": float(salida.mean()), "dif": float(diferencias.mean()), "dif_pct": dif_pct,
                "prueba": prueba, "p": p, "pct_fuera": None, "no_comparable": None}
        if tolerancia_pct is not None:
            por_ronda = diferencias / entrada * 100
            fila.update(pct_fuera=float((np.abs(por_ronda) > tolerancia_pct).mean() * 100),
                        no_comparable=bool(abs(dif_pct) > tolerancia_pct))
        filas.append(fila)
    return pd.DataFrame(filas, columns=["temporada", "eboe", "n", "media_entrada", "media_salida", "dif", "dif_pct",
                                        "prueba", "p", "pct_fuera", "no_comparable"])


def diferencia_por_eboe(pares: pd.DataFrame, alfa: float = ALFA) -> pd.DataFrame:
    """Diferencia de peso medio del fruto por ronda (salida − entrada, % de la entrada): media e IC 1 − alfa por eBOE
    y temporada, sin los pares con error de carga (core.factores.por_categoria). Columnas: temporada, grupo (el eBOE),
    n, media, ic_inf, ic_sup y p (ANOVA entre eBOE de la temporada)."""
    return por_categoria(pares[~pares["error_carga"].astype(bool)], "eboe", "dif_peso_pct", alfa)


def grupo_calibre(dif_peso_pct: pd.Series, tolerancia_pct: float) -> pd.Series:
    """"Salida más chica" (diferencia de peso medio < −tolerancia), "Comparable" o "Salida más grande" (> tolerancia)."""
    grupos = np.select([dif_peso_pct < -tolerancia_pct, dif_peso_pct > tolerancia_pct], [GRUPOS[0], GRUPOS[2]],
                       GRUPOS[1])
    return pd.Series(grupos, index=dif_peso_pct.index).where(dif_peso_pct.notna())


def eficiencia_por_calibre(pares: pd.DataFrame, tolerancia_pct: float, alfa: float = ALFA) -> pd.DataFrame:
    """Eficiencia eBOE (%, promedio simple con IC 1 − alfa) de las rondas según la fruta de la salida comparada con la
    de entrada (grupo_calibre), por temporada; sin los pares con error de carga. Si la salida trae fruta más grande,
    su aceite en kg/MT sale más bajo y la eficiencia, más alta, sin que el eBOE haya trabajado distinto.
    Columnas: temporada, grupo, n, media, ic_inf, ic_sup y p (ANOVA de la temporada)."""
    datos = pares[~pares["error_carga"].astype(bool)].dropna(subset=["eficiencia_pct", "dif_peso_pct"])
    datos = datos.assign(grupo_calibre=grupo_calibre(datos["dif_peso_pct"], tolerancia_pct))
    filas = []
    for temporada, sub in datos.groupby("temporada", sort=True):
        sub = sub[sub.groupby("grupo_calibre")["eficiencia_pct"].transform("size") >= 2]
        if sub["grupo_calibre"].nunique() < 2:
            continue
        resultado = comparar_grupos(sub, "eficiencia_pct", "grupo_calibre", alfa)
        filas.append(resultado["medias"].assign(temporada=temporada, p=resultado["p"]))
    columnas = ["temporada", "grupo", "n", "media", "ic_inf", "ic_sup", "p"]
    if not filas:
        return pd.DataFrame(columns=columnas)
    tabla = pd.concat(filas, ignore_index=True)[columnas]
    tabla["grupo"] = tabla["grupo"].astype(str)
    return tabla.sort_values(["temporada", "grupo"], key=lambda c: c.map(GRUPOS.index) if c.name == "grupo" else c,
                             ignore_index=True)


def frase_muestreo(tabla: pd.DataFrame, tolerancia_pct: float | None, alfa: float = ALFA) -> str:
    """Resumen de prueba_pareada("peso"): en cuántas combinaciones temporada × eBOE la fruta de la salida es
    distinta (p < alfa) y en cuántas la diferencia pasa la tolerancia."""
    if tabla.empty:
        return "No hay rondas con el peso y el n.º de frutos de la entrada y de la salida."
    distintas = tabla[tabla["p"].notna() & (tabla["p"] < alfa)]
    grande = (distintas["dif"] > 0).sum()
    texto = (f"En {len(distintas)} de {len(tabla)} combinaciones de temporada y eBOE el peso medio del fruto de la "
             f"salida es distinto del de la entrada (p < {numero(alfa, 2)})")
    texto += (f"; en {grande} de ellas la salida trae fruta más grande" if len(distintas) else "") + "."
    if tolerancia_pct is None:
        return texto + " La tolerancia de calibre de la planta está a definir: no se avisa."
    fuera = tabla[tabla["no_comparable"].fillna(False).astype(bool)]
    if fuera.empty:
        return texto + f" Ninguna pasa la tolerancia de ±{numero(tolerancia_pct, 1)} %."
    lista = ", ".join(f"{t} eBOE {e} ({con_signo(d, 1)} %)"
                      for t, e, d in zip(fuera["temporada"], fuera["eboe"], fuera["dif_pct"]))
    return texto + f" Pasan la tolerancia de ±{numero(tolerancia_pct, 1)} %: {lista}. {AVISO}."
