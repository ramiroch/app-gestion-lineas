"""Pruebas de core/efectos.py: medias ajustadas (contra las predicciones del modelo en la grilla balanceada),
estimabilidad, VIF con factores confundidos, interacción, regresión segmentada, aceite captado por turno y
correlaciones."""

from itertools import product

import numpy as np
import pandas as pd
import pytest
import statsmodels.formula.api as smf

from core.efectos import (ETIQUETAS_MTPH, REGIMENES, _media_ajustada, captado_por_turno, correlaciones,
                          datos_por_turno, descomponer_por_mezcla, frase_quiebre, modelo_efectos, modelo_interaccion, quiebre, quiebres_por_temporada,
                          rango_mtph, regimen_agua)

RUIDO = np.random.default_rng(7)


def _diseno(repeticiones=4, quitar=0):
    """A (2 niveles) × B (3 niveles) con efectos aditivos: y = 10 + 2·[A = a2] + 1·[B = b2] + 3·[B = b3] + ruido.
    quitar: filas que se sacan del principio (diseño desbalanceado)."""
    filas = [{"A": a, "B": b} for a, b in product(["a1", "a2"], ["b1", "b2", "b3"]) for _ in range(repeticiones)]
    datos = pd.DataFrame(filas).iloc[quitar:].reset_index(drop=True)
    efecto = datos["A"].map({"a1": 0, "a2": 2}) + datos["B"].map({"b1": 0, "b2": 1, "b3": 3})
    return datos.assign(y=10 + efecto + RUIDO.normal(0, 0.5, len(datos)))


@pytest.mark.parametrize("quitar", [0, 7])
def test_media_ajustada_es_el_promedio_de_la_grilla_balanceada(quitar):
    datos = _diseno(quitar=quitar)
    modelo = smf.ols("y ~ C(A) + C(B)", data=datos).fit()
    niveles = {"A": ["a1", "a2"], "B": ["b1", "b2", "b3"]}
    for nivel in niveles["A"]:
        grilla = pd.DataFrame({"A": nivel, "B": niveles["B"]})        # el otro factor, en partes iguales
        esperado = modelo.predict(grilla).mean()
        resultado = _media_ajustada(modelo, niveles, {"A": nivel}, 0.05)
        assert resultado["estimable"] and resultado["media"] == pytest.approx(esperado)
        assert resultado["ic_inf"] < resultado["media"] < resultado["ic_sup"]
    if quitar:                                     # desbalanceado: la media ajustada no es la media de los datos
        crudo = datos.loc[datos["A"] == "a1", "y"].mean()
        assert _media_ajustada(modelo, niveles, {"A": "a1"}, 0.05)["media"] != pytest.approx(crudo, abs=1e-3)
    else:                                          # balanceado: coincide con la media de los datos
        assert _media_ajustada(modelo, niveles, {"A": "a1"}, 0.05)["media"] == \
            pytest.approx(datos.loc[datos["A"] == "a1", "y"].mean())


def test_celda_sin_datos_no_es_estimable():
    datos = _diseno()
    datos = datos[~((datos["A"] == "a2") & (datos["B"] == "b3"))]        # celda vacía
    modelo = smf.ols("y ~ C(A) + C(B) + C(A):C(B)", data=datos).fit()
    niveles = {"A": ["a1", "a2"], "B": ["b1", "b2", "b3"]}
    assert not _media_ajustada(modelo, niveles, {"A": "a2", "B": "b3"}, 0.05)["estimable"]
    assert _media_ajustada(modelo, niveles, {"A": "a2", "B": "b2"}, 0.05)["estimable"]


def test_rangos_de_mtph_y_regimen():
    assert rango_mtph(pd.Series([10.9, 11, 11.5, 12, 13.5, 14, 14.1, None])).tolist() == \
        ["≤ 11", "≤ 11", "11–12", "11–12", "13–14", "13–14", "> 14", None]
    assert regimen_agua(pd.Series([0.8, 0.79, None])).tolist() == [REGIMENES[0], REGIMENES[1], None]


def _datos_modelo(n_por_celda=6, confundir=False):
    """Pares sintéticos con todos los factores del modelo; la eficiencia baja con la MTPH solo en 2026."""
    azar = np.random.default_rng(11)                # los demás factores, al azar (casi ortogonales)
    filas = []
    for temporada, mtph, eboe, turno in product(["2025", "2026"], ETIQUETAS_MTPH, ["1", "2"], ["M", "T"]):
        for _ in range(n_por_celda):
            filas.append({"temporada": temporada, "mtph_rango": mtph, "eboe": eboe, "turno": turno,
                          "programa_grupo": azar.choice(["N0–N3", "N4–N5"]), "aceite_rango": azar.choice(["C1", "C2"]),
                          "calibre": azar.choice(["Q1", "Q2", "Q3"]), "mes": azar.choice(["Jul", "Ago"])})
    datos = pd.DataFrame(filas)
    caida = datos["mtph_rango"].map({e: i for i, e in enumerate(ETIQUETAS_MTPH)})
    datos["eficiencia_pct"] = (85 - 2 * caida * (datos["temporada"] == "2026") + 1.5 * (datos["eboe"] == "2")
                               + RUIDO.normal(0, 1, len(datos)))
    if confundir:                                  # el mes casi igual a la temporada: confundidos
        datos["mes"] = datos["temporada"].map({"2025": "Jul", "2026": "Ago"})
        datos.loc[datos.index[::40], "mes"] = "Sep"
    return datos


def test_modelo_efectos():
    datos = _datos_modelo()
    resultado = modelo_efectos(datos, "eficiencia_pct")
    assert resultado["n"] == len(datos) and 0 < resultado["r2"] < 1
    assert set(resultado["anova"]["factor"]) == set(resultado["factores"]) and "temporada" in resultado["factores"]
    eboe = resultado["medias"][resultado["medias"]["factor"] == "eboe"].set_index("nivel")
    assert eboe.loc["2", "media"] - eboe.loc["1", "media"] == pytest.approx(1.5, abs=0.4)
    assert eboe["n"].tolist() == [len(datos) // 2] * 2
    mtph = resultado["medias"][resultado["medias"]["factor"] == "mtph_rango"]
    assert mtph["nivel"].tolist() == ETIQUETAS_MTPH                                   # en su orden
    assert (resultado["vif"]["vif"] < 2).all() and not resultado["vif"]["confundido"].any()   # diseño ortogonal
    sola = modelo_efectos(datos[datos["temporada"] == "2026"], "eficiencia_pct")
    assert "temporada" not in sola["factores"]                                        # una sola temporada
    with pytest.raises(ValueError, match="desconocida"):
        modelo_efectos(datos, "rango_fp_pct")


def test_vif_avisa_factores_confundidos():
    vif = modelo_efectos(_datos_modelo(confundir=True), "eficiencia_pct")["vif"].set_index("factor")
    assert vif.loc["mes", "confundido"] and vif.loc["mes", "vif"] > 5
    assert vif.loc["mes", "confundido_con"] == "Temporada" and vif.loc["mes", "v_cramer"] > 0.9


def test_interaccion_mtph_por_temporada():
    resultado = modelo_interaccion(_datos_modelo(), "eficiencia_pct")
    assert resultado["p"] < 0.001                        # la MTPH solo baja la eficiencia en 2026
    celdas = resultado["celdas"].set_index(["b", "a"])
    caida_2026 = celdas.loc[("2026", "≤ 11"), "media"] - celdas.loc[("2026", "> 14"), "media"]
    caida_2025 = celdas.loc[("2025", "≤ 11"), "media"] - celdas.loc[("2025", "> 14"), "media"]
    assert caida_2026 == pytest.approx(8, abs=1.5) and abs(caida_2025) < 1.5
    assert (celdas["n"] == 24).all() and celdas["estimable"].all()
    with pytest.raises(ValueError, match="no se combina"):
        modelo_interaccion(_datos_modelo(), "eficiencia_pct", carga="fp", con="regimen")


def test_quiebre_conocido():
    x = np.repeat(np.arange(9, 19, 0.5), 15)
    y = 85 - 2 * np.clip(x - 13, 0, None) + RUIDO.normal(0, 0.3, len(x))
    resultado = quiebre(x, y)
    assert resultado["quiebre"] == 13
    assert resultado["pendiente_antes"] == pytest.approx(0, abs=0.2)
    assert resultado["pendiente_despues"] == pytest.approx(-2, abs=0.2)
    assert resultado["p_cambio"] < 0.001 and resultado["n_despues"] == int((x > 13).sum())
    assert quiebre([10, 11, 12], [1, 2, 3]) is None                        # sin datos de los dos lados
    tabla = quiebres_por_temporada(pd.DataFrame({"temporada": "2026", "tph": x, "eficiencia_pct": y}),
                                   "eficiencia_pct")
    assert tabla["quiebre"].tolist() == [13]


def test_quiebre_ajustado_por_los_demas_factores():
    """El programa alto se usa más con MTPH alta y sube la eficiencia 4 puntos: sin ajustar tapa la caída por encima
    de 13 MTPH; ajustado por el programa aparece la pendiente real (−2 puntos por MTPH)."""
    azar = np.random.default_rng(3)
    x = np.repeat(np.arange(9, 19, 0.5), 30)
    alto = azar.random(len(x)) < (x - 8) / 11                    # más programa alto a más MTPH
    y = 85 + 4 * alto - 2 * np.clip(x - 13, 0, None) + azar.normal(0, 0.3, len(x))
    datos = pd.DataFrame({"temporada": "2026", "tph": x, "eficiencia_pct": y,
                          "programa_grupo": np.where(alto, "N6–N9", "N0–N3")})
    ajustado = quiebres_por_temporada(datos, "eficiencia_pct", ajustado=True).iloc[0]
    crudo = quiebres_por_temporada(datos, "eficiencia_pct").iloc[0]
    assert ajustado["quiebre"] == 13 and ajustado["pendiente_despues"] == pytest.approx(-2, abs=0.2)
    assert ajustado["pendiente_antes"] == pytest.approx(0, abs=0.2)
    assert crudo["pendiente_antes"] > 0.2                         # sin ajustar, el programa la hace subir
    assert crudo["pendiente_despues"] > -1.8                      # y achica la caída


def test_aceite_captado_por_turno():
    dia = pd.Timestamp("2026-07-01")
    balance = pd.DataFrame({
        "fecha_produccion": [dia] * 4, "turno": ["M"] * 4, "tipo": ["Centrífuga primaria"] * 3 + ["Pulidora"],
        "equipo": ["ESE 230 (1)", "ESE 230 (1)", "ESE 230 (2)", "OSD 60"],
        "caudal_liviana": [200.0, 300.0, 250.0, 100.0], "aceite_liviana": [80.0, 80.0, 70.0, 99.0],
        "copiada": [False, False, True, False], "fuera_fisico": False})
    fuera = pd.DataFrame({"fecha_produccion": [dia] * 2, "turno": ["M"] * 2, "tipo": ["Centrífuga primaria"] * 2,
                          "equipo": ["ESE 230 (2)"] * 2, "caudal_liviana": [-50.0, 400.0], "aceite_liviana": [75.0, 75.0],
                          "copiada": False, "fuera_fisico": [False, True]})
    balance = pd.concat([balance, fuera], ignore_index=True)       # caudal negativo y fuera de rango: no entran
    captado = captado_por_turno(balance)
    densidad, densidad_2 = 0.852 * 0.8 + 0.997 * 0.2, 0.852 * 0.7 + 0.997 * 0.3
    total = 250 * 0.8 * densidad + 250 * 0.7 * densidad_2      # (1): mediana de 200 y 300; (2): la copiada SÍ entra
    assert captado["captado_kgh"].iloc[0] == pytest.approx(total)
    assert (captado["n_primarias"].iloc[0], captado["n_muestreos"].iloc[0]) == (2, 3)
    paralelo = pd.DataFrame({"fecha_produccion": [dia], "turno": ["M"], "modo": ["en paralelo"]})
    solo_una = balance[balance["equipo"] != "ESE 230 (2)"]
    assert captado_por_turno(solo_una, paralelo).empty            # en paralelo con una sola primaria: incompleto
    assert len(captado_por_turno(solo_una, paralelo.assign(modo="una sola"))) == 1
    pares = pd.DataFrame({"fecha_produccion": [dia] * 3, "turno": ["M"] * 3, "eboe": ["1", "1", "2"],
                          "temporada": ["2026"] * 3, "tph": [10.0, 12.0, 12.0], "fp_especifica": [0.5, 0.6, 0.7],
                          "aceite_fruta": [8.0, 8.0, 8.0], "frutos_10kg": [40.0, 45.0, 50.0],
                          "programa": ["N3", "N3", "N5"], "rango_fp_ref": [-30.0, -20.0, -10.0],
                          "removido": [6.8, 7.0, 6.0]})
    turnos = datos_por_turno(pares, captado)
    assert turnos["captado_kg_mt"].iloc[0] == pytest.approx(total / (11 + 12))   # MTPH total 23
    removido_kgh = 11 * 6.9 + 12 * 6.0                         # de cada eBOE: MTPH media × removido medio
    assert turnos["captado_pct"].iloc[0] == pytest.approx(total / removido_kgh * 100)
    assert turnos["tph"].iloc[0] == pytest.approx(34 / 3) and turnos["programa_grupo"].iloc[0] == "N0–N3"


def test_correlaciones():
    datos = pd.DataFrame({"temporada": ["2026"] * 5, "tph": [10.0, 11, 12, 13, 14],
                          "fp_especifica": [0.70, 0.64, 0.58, 0.54, 0.50], "rango_fp_ref": [None] * 5,
                          "aceite_fruta": [8.0, 8.2, 7.9, 8.1, 8.0], "frutos_10kg": [40.0, 42, 44, 46, 48]})
    tabla = correlaciones(datos).set_index(["x", "y"])
    assert tabla.loc[("MTPH", "FP específica (m³/t)"), "r"] < -0.95
    assert tabla.loc[("MTPH", "Calibre (frutos/10 kg)"), "r"] == pytest.approx(1.0)
    assert pd.isna(tabla.loc[("MTPH", "Rango FP ref. (%)"), "r"]) and tabla.loc[("MTPH", "Rango FP ref. (%)"), "n"] == 0


def test_frase_del_quiebre():
    fila = {"temporada": "2026", "quiebre": 10, "pendiente_antes": -0.09, "pendiente_despues": -0.88,
            "p_cambio": 0.0018, "p_despues": 1e-6, "n": 2655, "n_despues": 1928}
    frase = frase_quiebre(fila, "eficiencia_pct")
    assert "quiebre en 10 MTPH" in frase and "baja 0,88 puntos por MTPH (p < 0,001)" in frase and "borde" in frase
    plana = frase_quiebre(fila | {"quiebre": 12, "pendiente_despues": 0.11, "p_despues": 0.31}, "aceite_salida")
    assert "no cambia de forma significativa (p = 0,310)" in plana and "borde" not in plana
    assert "no hay datos suficientes" in frase_quiebre({"temporada": "2023", "quiebre": None, "n": 4}, "eficiencia_pct")


@pytest.mark.skip(reason="Pendiente: cargar las medias ajustadas de Minitab (Gráfica de efectos principales) con un "
                         "conjunto exportado de la app y compararlas acá.")
def test_medias_ajustadas_contra_minitab():
    pass


def test_descomponer_por_mezcla_de_mtph():
    """2025 opera casi todo a baja MTPH y 2026 a alta; a igual MTPH, 2026 rinde 1 punto menos."""
    celdas = pd.DataFrame({"a": ["≤ 11", "> 14"] * 2, "b": ["2025", "2025", "2026", "2026"],
                           "media": [86.0, 82.0, 85.0, 81.0], "estimable": True, "n": 1})
    datos = pd.DataFrame({"temporada": ["2025"] * 4 + ["2026"] * 4,
                          "mtph_rango": ["≤ 11", "≤ 11", "≤ 11", "> 14", "≤ 11", "> 14", "> 14", "> 14"]})
    resultado = descomponer_por_mezcla({"celdas": celdas, "a": "mtph_rango", "b": "temporada"}, datos, "2025", "2026")
    assert resultado["media_base"] == pytest.approx(0.75 * 86 + 0.25 * 82)               # 85
    assert resultado["media_nueva"] == pytest.approx(0.25 * 85 + 0.75 * 81)              # 82
    assert resultado["nueva_con_mezcla_base"] == pytest.approx(0.75 * 85 + 0.25 * 81)    # 84
    assert (resultado["por_mezcla"], resultado["a_igual_carga"]) == (pytest.approx(-2), pytest.approx(-1))
    assert resultado["por_mezcla"] + resultado["a_igual_carga"] == pytest.approx(resultado["diferencia"])
    assert resultado["rangos"]["n_nueva"].tolist() == [1, 3]
    assert descomponer_por_mezcla({"celdas": None}, datos, "2025", "2026") is None
