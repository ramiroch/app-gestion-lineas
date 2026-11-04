"""Comparación de grupos (por ejemplo, temporadas): ANOVA de un factor, Tukey, Levene y Welch.

Criterios de Minitab (Estadísticas > ANOVA > Un solo factor):
- Levene con la mediana como centro (Brown-Forsythe), como la prueba de varianzas iguales de Minitab.
- Si Levene da p < alfa, las varianzas difieren: se calcula también el ANOVA de Welch, y su p es el
  que vale para decir si las medias difieren.
- IC de cada media: con varianzas iguales, desvío combinado (√CM error) y t con los GL del error;
  si difieren, desvío y GL de cada grupo (lo que hace Minitab sin "suponer varianzas iguales").
- Tukey (diferencias de a pares con IC y p ajustado) supone varianzas iguales.
Fuente de las pruebas: Montgomery, Design and Analysis of Experiments, ejemplo 3.1.
"""

from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.formula.api import ols
from statsmodels.stats.anova import anova_lm
from statsmodels.stats.multicomp import pairwise_tukeyhsd
from statsmodels.stats.oneway import anova_oneway

from core.formato import numero, texto_p

ALFA = 0.05


def comparar_grupos(df: pd.DataFrame, variable: str, factor: str, alfa: float = ALFA) -> dict:
    """Compara las medias de `variable` entre los niveles de `factor` (columnas de df).

    Las filas con la variable o el factor vacíos no se usan. Hacen falta al menos 2 grupos
    y cada uno con al menos 2 valores. Devuelve un diccionario con:
    - anova: DataFrame con Fuente (factor, Error, Total), GL, SC, CM, F y p.
    - medias: DataFrame con grupo, n, media, desvio, ic_inf, ic_sup (IC 1 − alfa).
    - tukey: DataFrame con grupo_1, grupo_2, diferencia (media 2 − media 1, en las unidades
      de la variable), ic_inf, ic_sup, p y significativa.
    - levene: {"estadistico", "p"}; varianzas_distintas: True si p < alfa.
    - welch: {"F", "gl_num", "gl_den", "p"} si las varianzas difieren; si no, None.
    - p: el p que decide si hay diferencia entre medias (Welch si las varianzas difieren,
      si no el del ANOVA); ic_metodo: "combinado" o "individual".
    """
    if not 0 < alfa < 1:
        raise ValueError(f"alfa tiene que estar entre 0 y 1 (dice {alfa}).")
    for columna in (variable, factor):
        if columna not in df.columns:
            raise ValueError(f'No existe la columna "{columna}".')
    datos = pd.DataFrame({"y": pd.to_numeric(df[variable]), "g": df[factor]}).dropna()
    if pd.api.types.is_float_dtype(datos["g"]) and (datos["g"] % 1 == 0).all():
        datos["g"] = datos["g"].astype(int)   # 2025.0 → 2025 (pasa si el factor tenía vacíos)
    datos["g"] = datos["g"].astype(str)
    grupos = {nombre: valores["y"].to_numpy() for nombre, valores in datos.groupby("g", sort=True)}
    if len(grupos) < 2:
        raise ValueError(f'Para comparar hacen falta al menos 2 grupos de "{factor}" (hay {len(grupos)}).')
    chicos = [nombre for nombre, valores in grupos.items() if len(valores) < 2]
    if chicos:
        raise ValueError(f"Cada grupo necesita al menos 2 valores; no los tienen: {', '.join(chicos)}.")

    # ANOVA de un factor (statsmodels) + fila Total, como la tabla de Minitab.
    tabla = anova_lm(ols("y ~ C(g)", data=datos).fit(), typ=1)
    gl_f, gl_e = tabla["df"].iloc[0], tabla["df"].iloc[1]
    sc_f, sc_e = tabla["sum_sq"].iloc[0], tabla["sum_sq"].iloc[1]
    cm_e = sc_e / gl_e
    anova = pd.DataFrame({
        "Fuente": [factor, "Error", "Total"],
        "GL": [int(gl_f), int(gl_e), int(gl_f + gl_e)],
        "SC": [sc_f, sc_e, sc_f + sc_e],
        "CM": [sc_f / gl_f, cm_e, None],
        "F": [tabla["F"].iloc[0], None, None],
        "p": [tabla["PR(>F)"].iloc[0], None, None],
    })
    p_anova = float(tabla["PR(>F)"].iloc[0])

    levene = stats.levene(*grupos.values(), center="median")
    varianzas_distintas = bool(levene.pvalue < alfa)
    welch = None
    if varianzas_distintas:
        resultado = anova_oneway(list(grupos.values()), use_var="unequal")
        welch = {"F": float(resultado.statistic), "gl_num": float(resultado.df[0]),
                 "gl_den": float(resultado.df[1]), "p": float(resultado.pvalue)}

    filas = []
    for nombre, valores in grupos.items():
        n, media, desvio = len(valores), float(valores.mean()), float(valores.std(ddof=1))
        if varianzas_distintas:
            margen = stats.t.ppf(1 - alfa / 2, n - 1) * desvio / np.sqrt(n)
        else:
            margen = stats.t.ppf(1 - alfa / 2, gl_e) * np.sqrt(cm_e) / np.sqrt(n)
        filas.append({"grupo": nombre, "n": n, "media": media, "desvio": desvio,
                      "ic_inf": media - margen, "ic_sup": media + margen})
    medias = pd.DataFrame(filas)

    tukey_res = pairwise_tukeyhsd(datos["y"], datos["g"], alpha=alfa)
    tukey = pd.DataFrame([
        {"grupo_1": g1, "grupo_2": g2, "diferencia": float(dif), "ic_inf": float(ic[0]), "ic_sup": float(ic[1]),
         "p": float(p), "significativa": bool(rechaza)}
        for (g1, g2), dif, ic, p, rechaza in zip(combinations(tukey_res.groupsunique, 2), tukey_res.meandiffs,
                                                  tukey_res.confint, tukey_res.pvalues, tukey_res.reject)])

    return {"anova": anova, "medias": medias, "tukey": tukey,
            "levene": {"estadistico": float(levene.statistic), "p": float(levene.pvalue)},
            "varianzas_distintas": varianzas_distintas, "welch": welch,
            "p": welch["p"] if welch else p_anova,
            "ic_metodo": "individual" if varianzas_distintas else "combinado", "alfa": alfa}


def frase_resultado(resultado: dict, sujeto: str, articulo: str, unidad: str, decimales: int = 2) -> str:
    """Resultado de comparar_grupos() en una frase simple, en castellano.

    sujeto: con artículo y en mayúscula ("La eficiencia"); articulo: "la" o "el", para
    "que la de 2025". Con 2 grupos usa el p de resultado["p"] (Welch si las varianzas
    difieren); con más grupos, una frase por cada par de Tukey con diferencia significativa.
    Ejemplo: "La eficiencia de 2026 es 2,1 puntos menor que la de 2025 (p < 0,001)."
    """
    def comparar(g1, g2, diferencia, p, significativa):
        sentido = "mayor" if diferencia > 0 else "menor"
        frase = f"{sujeto} de {g2} es {numero(abs(diferencia), decimales)} {unidad} {sentido} que {articulo} de {g1}"
        if significativa:
            return f"{frase} ({texto_p(p)})."
        return f"{frase}, pero la diferencia no es significativa ({texto_p(p)})."

    alfa = resultado["alfa"]
    tukey = resultado["tukey"]
    if len(tukey) == 1:
        par = tukey.iloc[0]
        frases = [comparar(par["grupo_1"], par["grupo_2"], par["diferencia"], resultado["p"], resultado["p"] < alfa)]
    elif resultado["p"] >= alfa:
        frases = [f"No hay diferencias significativas entre los grupos ({texto_p(resultado['p'])})."]
    else:
        significativos = tukey[tukey["significativa"]]
        frases = [f"Hay diferencias entre los grupos ({texto_p(resultado['p'])})."]
        frases += [comparar(f["grupo_1"], f["grupo_2"], f["diferencia"], f["p"], True)
                   for _, f in significativos.iterrows()]
        if resultado["varianzas_distintas"]:
            frases.append("Ojo: las varianzas difieren y Tukey supone que son iguales; "
                          "tomá las comparaciones de a pares con cuidado.")
    if resultado["varianzas_distintas"]:
        frases.append(f"Las varianzas difieren (Levene {texto_p(resultado['levene']['p'])}): "
                      "el p de la comparación es el de Welch.")
    return " ".join(frases)
