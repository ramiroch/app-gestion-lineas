"""Pruebas de core/comparaciones.py.

Caso de libro: Montgomery, Design and Analysis of Experiments, ejemplo 3.1 (tasa de grabado
de un plasma con 4 potencias de RF, 5 réplicas). Valores del libro:
SC tratamientos 66.870,55 (3 GL) · SC error 5.339,20 (16 GL) · CM 22.290,18 y 333,70 · F 66,80.
Medias 551,2 · 587,4 · 625,4 · 707,0. IC 95 % de la media de 220 W: 689,68 a 724,32.
Tukey: los 6 pares difieren (la menor diferencia, 36,2, supera T = 33,09).
"""

import pandas as pd
import pytest
from scipy import stats

from core.comparaciones import comparar_grupos, frase_resultado

MONTGOMERY = {
    "160": [575, 542, 530, 539, 570],
    "180": [565, 593, 590, 579, 610],
    "200": [600, 651, 610, 637, 629],
    "220": [725, 700, 715, 685, 710],
}


def _tabla(grupos: dict, variable="y", factor="g") -> pd.DataFrame:
    return pd.DataFrame([{factor: g, variable: v} for g, valores in grupos.items() for v in valores])


@pytest.fixture(scope="module")
def montgomery():
    return comparar_grupos(_tabla(MONTGOMERY, "tasa", "potencia"), "tasa", "potencia")


def test_tabla_anova_igual_que_el_libro(montgomery):
    anova = montgomery["anova"].set_index("Fuente")

    assert anova.loc["potencia", "GL"] == 3 and anova.loc["Error", "GL"] == 16 and anova.loc["Total", "GL"] == 19
    assert anova.loc["potencia", "SC"] == pytest.approx(66870.55, abs=0.01)
    assert anova.loc["Error", "SC"] == pytest.approx(5339.20, abs=0.01)
    assert anova.loc["Total", "SC"] == pytest.approx(72209.75, abs=0.01)
    assert anova.loc["potencia", "CM"] == pytest.approx(22290.18, abs=0.01)
    assert anova.loc["Error", "CM"] == pytest.approx(333.70, abs=0.01)
    assert anova.loc["potencia", "F"] == pytest.approx(66.80, abs=0.01)
    assert anova.loc["potencia", "p"] < 0.001


def test_medias_e_intervalo_combinado(montgomery):
    medias = montgomery["medias"].set_index("grupo")

    assert medias["media"].tolist() == pytest.approx([551.2, 587.4, 625.4, 707.0])
    assert montgomery["ic_metodo"] == "combinado"
    assert (medias.loc["220", "ic_inf"], medias.loc["220", "ic_sup"]) == pytest.approx((689.68, 724.32), abs=0.01)


def test_tukey_todos_los_pares_difieren(montgomery):
    tukey = montgomery["tukey"]

    assert len(tukey) == 6 and tukey["significativa"].all()
    primero = tukey.iloc[0]
    assert (primero["grupo_1"], primero["grupo_2"]) == ("160", "180")
    assert primero["diferencia"] == pytest.approx(36.2)                  # en las unidades de la variable
    assert primero["ic_inf"] > 0                                          # el IC no incluye el 0


def test_varianzas_iguales_no_calcula_welch(montgomery):
    assert montgomery["varianzas_distintas"] is False
    assert montgomery["welch"] is None
    assert montgomery["p"] == montgomery["anova"]["p"].iloc[0]


def test_varianzas_distintas_usa_welch():
    grupos = {"2025": [10.0, 10.1, 9.9, 10.2, 9.8, 10.0, 10.1, 9.9],
              "2026": [5.0, 15.0, 8.0, 12.0, 3.0, 17.0, 6.0, 16.0]}
    r = comparar_grupos(_tabla(grupos), "y", "g")

    assert r["varianzas_distintas"] is True and r["levene"]["p"] < 0.05
    # Con 2 grupos, el ANOVA de Welch es la prueba t de Welch: F = t² y el mismo p.
    t = stats.ttest_ind(grupos["2026"], grupos["2025"], equal_var=False)
    assert r["welch"]["F"] == pytest.approx(t.statistic ** 2)
    assert r["welch"]["p"] == pytest.approx(t.pvalue)
    assert r["p"] == r["welch"]["p"]
    assert r["ic_metodo"] == "individual"
    ic = r["medias"].set_index("grupo").loc["2026"]          # IC con el desvío y los GL del grupo
    margen = stats.t.ppf(0.975, 7) * ic["desvio"] / 8 ** 0.5
    assert (ic["ic_inf"], ic["ic_sup"]) == pytest.approx((ic["media"] - margen, ic["media"] + margen))


def test_valida_entradas():
    with pytest.raises(ValueError, match="al menos 2 grupos"):
        comparar_grupos(_tabla({"2025": [1, 2, 3]}), "y", "g")
    with pytest.raises(ValueError, match="no los tienen: 2026"):
        comparar_grupos(_tabla({"2025": [1, 2, 3], "2026": [4]}), "y", "g")
    with pytest.raises(ValueError, match='No existe la columna "z"'):
        comparar_grupos(_tabla({"2025": [1, 2], "2026": [3, 4]}), "z", "g")


def test_ignora_vacios_y_factor_numerico():
    df = pd.DataFrame({"temporada": [2025, 2025, 2025, 2026, 2026, 2026, None],
                       "y": [1.0, 2.0, None, 3.0, 4.0, 5.0, 9.0]})
    r = comparar_grupos(df, "y", "temporada")

    assert r["medias"]["grupo"].tolist() == ["2025", "2026"]
    assert r["medias"]["n"].tolist() == [2, 3]


def test_frase_dos_grupos_significativa():
    grupos = {"2025": [84, 85, 86, 85, 84, 86], "2026": [82, 83, 84, 83, 82, 84]}
    r = comparar_grupos(_tabla(grupos), "y", "g")

    frase = frase_resultado(r, "La eficiencia", "la", "puntos", decimales=1)

    assert frase.startswith("La eficiencia de 2026 es 2,0 puntos menor que la de 2025 (p = 0,00")


def test_frase_dos_grupos_sin_diferencia():
    grupos = {"2025": [1.0, 1.2, 0.9, 1.1], "2026": [1.05, 1.15, 0.95, 1.1]}
    r = comparar_grupos(_tabla(grupos), "y", "g")

    frase = frase_resultado(r, "El aceite en emulsión", "el", "puntos")

    assert frase.startswith("El aceite en emulsión de 2026 es 0,01 puntos mayor que el de 2025, "
                            "pero la diferencia no es significativa (p = ")


def test_frase_varios_grupos(montgomery):
    frase = frase_resultado(montgomery, "La tasa", "la", "Å/min", decimales=1)

    assert frase.startswith("Hay diferencias entre los grupos (p < 0,001).")
    assert "La tasa de 180 es 36,2 Å/min mayor que la de 160" in frase
    assert frase.count("mayor que la de") == 6
