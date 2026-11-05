"""Pruebas de core/factores.py con datos inventados."""

import numpy as np
import pandas as pd
import pytest

from core.factores import (aceite_perdido, agregar_dia_proceso, asignar_quintil, comparar_planta, cortes_quintiles,
                           descomponer, etiquetas_quintiles, frase_aceite_perdido, frase_calibre, frase_categoria,
                           frase_descomposicion, frase_eboe_calibre, frase_modelo, frase_planta, frutos_en_10kg,
                           frase_quintil_eboe, frutos_por_eboe, modelo_multivariable, nota_descomposicion,
                           por_quintil_eboe, por_categoria, por_quintil,
                           preparar_pares, promedio_por_temporada,
                           residual_planta, resumen_factores, separar_errores, toneladas_por_eboe)

SIN_TONELADAS = pd.DataFrame(columns=["fecha_produccion", "turno", "fruta_t"])


# ---------------------------------------------------------------- preparación

def test_dia_proceso_y_frutos():
    tabla = pd.DataFrame({"proceso_id": [1, 1, 1, 2],
                          "fecha_produccion": pd.to_datetime(["2025-07-01", "2025-07-03", "2025-07-02", "2025-07-03"])})
    assert agregar_dia_proceso(tabla)["dia_proceso"].tolist() == [1, 3, 2, 1]

    fruta = pd.DataFrame({"n_frutos": [50, 0, 40, None], "peso_fruta_kg": [10.0, 10.0, 0.0, 10.0]})
    assert frutos_en_10kg(fruta).iloc[0] == pytest.approx(50.0)
    assert frutos_en_10kg(fruta).iloc[1:].isna().all()              # 0 frutos, peso 0 y vacío: no medido


def _par(**cambios):
    base = {"eboe": 1, "temporada": 2025, "fecha_produccion": pd.Timestamp("2025-07-01"), "turno": "M",
            "fecha_hora": pd.Timestamp("2025-07-01 06:00"), "aceite_fruta": 8.0, "aceite_salida": 1.2,
            "eficiencia_pct": 85.0, "n_frutos": 50, "peso_fruta_kg": 10.0, "programa": "N5", "dia_proceso": 1,
            "tph": 12.0, "calidad": "Firme", "caudal_fase_pesada": 8.0, "aceite_emulsion": 1.1}
    return base | cambios


def test_preparar_y_separar_errores():
    pares = preparar_pares(pd.DataFrame([_par(), _par(n_frutos=700, dia_proceso=6), _par(programa=None)]))
    assert pares["residual"].tolist() == [1.2] * 3 and pares["removido"].iloc[0] == pytest.approx(6.8)
    assert pares["programa_num"].iloc[0] == 5 and pd.isna(pares["programa_num"].iloc[2])
    assert pares["dia_categoria"].tolist() == ["día 1", "día 5 o más", "día 1"]

    limpios, excluidos = separar_errores(pares)
    assert len(limpios) == 2 and excluidos["frutos_10kg"].tolist() == [700.0]


# ---------------------------------------------------------------- calibre

def test_quintiles():
    frutos = pd.Series(range(1, 101), dtype=float)
    cortes = cortes_quintiles(frutos)
    assert cortes == pytest.approx([20.8, 40.6, 60.4, 80.2])
    assert asignar_quintil(pd.Series([1.0, 20.8, 21.0, 100.0]), cortes).tolist() == [1, 1, 2, 5]
    assert etiquetas_quintiles([42.8, 48.7, 53.8, 59.8]) == {
        1: "Q1 grande (≤ 43)", 2: "Q2 (43–49)", 3: "Q3 (49–54)", 4: "Q4 (54–60)", 5: "Q5 chica (> 60)"}
    with pytest.raises(ValueError, match="al menos 5"):
        cortes_quintiles(pd.Series([1.0, 2.0]))


def _pares_calibre():
    """Dos temporadas; la fruta chica (Q2) tiene peor eficiencia. En 2026 hay más fruta chica (3 de 4)."""
    filas = []
    for temporada, chicas in ((2025, 1), (2026, 3)):
        for k in range(4):
            chica = k < chicas
            filas.append(_par(temporada=temporada, n_frutos=70 if chica else 40,
                              eficiencia_pct=(80.0 if chica else 90.0) - (2.0 if temporada == 2026 else 0.0),
                              aceite_fruta=8.4 if chica else 8.0, aceite_salida=1.8 if chica else 1.1,
                              eboe=2 if chica else 3))
    return preparar_pares(pd.DataFrame(filas))


def test_por_quintil_y_frases():
    pares = _pares_calibre()
    cortes = [50.0, 55.0, 60.0, 65.0]          # 40 frutos → Q1; 70 → Q5
    tabla = por_quintil(pares, cortes, SIN_TONELADAS)

    q5 = tabla.set_index(["quintil", "temporada"]).loc[(5, 2026)]
    assert (q5["n"], q5["eficiencia"], q5["residual"]) == (3, 78.0, 1.8)
    assert q5["removido"] == pytest.approx(6.6) and not q5["ponderada"]
    assert frase_calibre(tabla, 2026) == (
        "En 2026, la fruta chica trae 0,40 kg/MT más de aceite que la grande, y el eBOE remueve 0,30 kg/MT menos "
        "(6,60 contra 6,90 kg/MT); el residual sube de 1,10 a 1,80 kg/MT y la eficiencia baja de 88,0 % a 78,0 %.")
    assert "no hay datos de fruta grande y chica" in frase_calibre(tabla, 2024)

    eboes = frutos_por_eboe(pares)
    assert frase_eboe_calibre(eboes, 2026) == ("En 2026, el eBOE 2 recibe la fruta más chica: 70 frutos en 10 kg "
                                                "contra 40 del eBOE 3.")


def test_eboe_dentro_de_cada_quintil():
    filas = []
    for frutos in (40, 70):                                      # Q1 y Q5 con los cortes de abajo
        for eboe, salida, programa in ((1, 1.0, "N3"), (2, 1.5, "N5")):
            filas += [_par(n_frutos=frutos, eboe=eboe, aceite_salida=salida, programa=programa)] * 30
    filas += [_par(n_frutos=55, eboe=1)] * 4                     # 55 frutos → Q2 (el corte entra en el quintil de abajo)
    tabla = por_quintil_eboe(preparar_pares(pd.DataFrame(filas)), [50.0, 55.0, 60.0, 65.0], SIN_TONELADAS)

    fila = tabla.set_index(["quintil", "eboe"]).loc[(5, 2)]
    assert (fila["n"], fila["residual"], fila["programa"], fila["chico"]) == (30, 1.5, 5.0, False)
    assert tabla.set_index(["quintil", "eboe"]).loc[(2, 1), "chico"]          # 4 pares: N < 30
    assert frase_quintil_eboe(tabla, 2025) == ("En 2025, con fruta del mismo tamaño, el eBOE 2 deja el mayor residual "
                                                "en 2 de 2 quintiles (programa promedio 5,0 contra 3,0 de los otros).")
    assert "no hay quintiles" in frase_quintil_eboe(tabla, 2026)


def test_descomposicion_suma_y_separa_el_calibre():
    tabla = por_quintil(_pares_calibre(), [50.0, 55.0, 60.0, 65.0], SIN_TONELADAS)
    resultado = descomponer(tabla, "eficiencia", 2025, 2026)

    # 2025: 1/4 chica (80) + 3/4 grande (90) = 87,5. 2026: 3/4 × 78 + 1/4 × 88 = 80,5.
    assert (resultado["valor_base"], resultado["valor_nueva"]) == pytest.approx((87.5, 80.5))
    # 2026 con la mezcla de 2025: 1/4 × 78 + 3/4 × 88 = 85,5 → calibre −5, resto −2 (lo que bajó cada quintil)
    assert resultado["estandarizado"] == pytest.approx(85.5)
    assert (resultado["calibre"], resultado["resto"]) == pytest.approx((-5.0, -2.0))
    assert resultado["calibre"] + resultado["resto"] == pytest.approx(resultado["total"])
    assert frase_descomposicion(resultado, "eficiencia") == (
        "De los 7,0 puntos de caída de la eficiencia entre 2025 y 2026, 5,0 se explican por fruta más chica y 2,0 "
        "por el resto (programa, MTPH, operación…).")
    residual = descomponer(tabla, "residual", 2025, 2026)
    assert frase_descomposicion(residual, "residual").startswith("De los 0,35 kg/MT de aumento del aceite residual")
    assert descomponer(tabla, "eficiencia", 2025, 2024) is None
    assert "Hacen falta las dos temporadas" in frase_descomposicion(None, "eficiencia")
    assert nota_descomposicion(resultado, "eficiencia") is None      # sin totales de Proceso: coincide


def test_descomposicion_con_el_total_de_proceso():
    pares = _pares_calibre()
    tabla = por_quintil(pares, [50.0, 55.0, 60.0, 65.0], SIN_TONELADAS)
    totales = promedio_por_temporada(pares, "eficiencia_pct", SIN_TONELADAS)
    assert totales == pytest.approx({2025: 87.5, 2026: 80.5})

    # Proceso dice 86,0 → 80,0 (−6): el calibre sigue en −5 y el resto pasa a −1 (= total − calibre)
    resultado = descomponer(tabla, "eficiencia", 2025, 2026, {2025: 86.0, 2026: 80.0})
    assert (resultado["valor_base"], resultado["valor_nueva"], resultado["total"]) == pytest.approx((86.0, 80.0, -6.0))
    assert (resultado["calibre"], resultado["resto"], resultado["total_metodo"]) == pytest.approx((-5.0, -1.0, -7.0))
    assert frase_descomposicion(resultado, "eficiencia").startswith("De los 6,0 puntos de caída")
    assert "la diferencia da -7,0 puntos en vez de -6,0" in nota_descomposicion(resultado, "eficiencia")


def test_descomposicion_con_calibre_en_contra():
    resultado = {"base": 2025, "nueva": 2026, "total": -1.0, "calibre": 0.5, "resto": -1.5, "frutos_base": 50,
                 "frutos_nueva": 45}
    assert "ninguno se explica por el calibre: la fruta más grande lo habría movido 0,5 puntos en sentido contrario" \
        in frase_descomposicion(resultado, "eficiencia")


# ---------------------------------------------------------------- categorías

def test_por_categoria_y_frase():
    filas = [_par(turno=t, eficiencia_pct=v) for t, vs in (("M", [84, 86, 85]), ("T", [80, 82, 81]), ("N", [83, 83.5]))
             for v in vs]
    filas.append(_par(turno="X", eficiencia_pct=50))            # una sola muestra: no entra
    tabla = por_categoria(pd.DataFrame(filas), "turno", "eficiencia_pct")

    assert tabla["grupo"].tolist() == ["M", "T", "N"]
    m = tabla.set_index("grupo").loc["M"]
    assert m["media"] == pytest.approx(85.0) and m["ic_inf"] < 85 < m["ic_sup"] and m["n"] == 3
    frase = frase_categoria(tabla, "turno", "la eficiencia", "%")
    assert frase.startswith("Por turno, en 2025 la eficiencia va de 81,0 % (T) a 85,0 % (M) (p = ")
    assert "No hay datos suficientes" in frase_categoria(tabla.iloc[0:0], "calidad", "la eficiencia", "%")

    calidades = [_par(calidad=c, eficiencia_pct=v) for c, vs in (("Podrido", (76, 81)), ("Blanda", (80, 83)),
                                                                  ("Firme", (84, 85))) for v in vs]
    assert por_categoria(pd.DataFrame(calidades), "calidad", "eficiencia_pct")["grupo"].tolist() == [
        "Firme", "Blanda", "Podrido"]


# ---------------------------------------------------------------- modelo

def _datos_modelo(n=400):
    """Eficiencia = 90 + 0,8·programa − 1·MTPH − 0,1·frutos − 3·(eBOE 2) − 2·(Blanda) + ruido."""
    g = np.random.default_rng(4)
    datos = pd.DataFrame({
        "eboe": g.choice([1, 2, 3], n), "temporada": g.choice([2025, 2026], n), "programa_num": g.integers(1, 10, n),
        "tph": g.uniform(8, 14, n), "frutos_10kg": g.uniform(35, 70, n),
        "calidad": g.choice(["Firme", "Blanda"], n, p=[0.8, 0.2]), "turno": g.choice(["M", "T", "N"], n),
        "caudal_fase_pesada": g.uniform(5, 11, n), "aceite_emulsion": g.uniform(0.8, 2.0, n),
        "dia_proceso": g.integers(1, 6, n)})
    datos["eficiencia_pct"] = (90 + 0.8 * datos["programa_num"] - datos["tph"] - 0.1 * datos["frutos_10kg"]
                               - 3 * (datos["eboe"] == 2) - 2 * (datos["calidad"] == "Blanda") + g.normal(0, 0.5, n))
    datos["residual"] = 2 - 0.1 * datos["programa_num"] + g.normal(0, 0.05, n)
    return datos


def test_modelo_recupera_los_efectos():
    resultado = modelo_multivariable(_datos_modelo(), "eficiencia_pct")

    assert resultado["n"] == 400 and resultado["r2"] > 0.9 and resultado["excluidos"] == []
    efectos = resultado["efectos"].set_index("texto")
    programa = efectos.filter(like="+1 nivel de programa", axis=0).iloc[0]
    assert programa["coeficiente"] == pytest.approx(0.8, abs=0.05) and programa["ic_inf"] < 0.8 < programa["ic_sup"]
    assert efectos.index.str.startswith("+1 nivel de programa → +0,").any()
    frutos = efectos.filter(like="+10 frutos", axis=0).iloc[0]
    assert frutos["coeficiente"] == pytest.approx(-1.0, abs=0.1)             # −0,1 por fruto × 10
    eboe2 = efectos.filter(like="eBOE 2 en vez de eBOE 1", axis=0).iloc[0]
    assert eboe2["coeficiente"] == pytest.approx(-3.0, abs=0.2)
    assert efectos.filter(like="Calidad Blanda en vez de Firme", axis=0)["coeficiente"].iloc[0] == pytest.approx(
        -2.0, abs=0.2)

    importancia = resultado["importancia"]
    assert importancia["pct"].is_monotonic_decreasing and importancia["nombre"].iloc[0] in ("Programa", "MTPH")
    assert (resultado["vif"]["vif"] < 5).all()
    assert frase_modelo(resultado, "eficiencia_pct").startswith("El modelo explica el 9")


def test_modelo_saca_factores_constantes_y_valida():
    datos = _datos_modelo().assign(temporada=2026)
    resultado = modelo_multivariable(datos, "residual")
    assert resultado["excluidos"] == ["temporada"] and "temporada" not in resultado["importancia"]["factor"].tolist()
    assert resultado["efectos"]["texto"].str.contains("kg/MT de residual").all()
    with pytest.raises(ValueError, match="Respuesta desconocida"):
        modelo_multivariable(datos, "brix")
    poco = modelo_multivariable(datos.head(5), "residual")
    assert poco["r2"] is None and "No hay datos suficientes" in frase_modelo(poco, "residual")


def test_vif_detecta_colinealidad():
    datos = _datos_modelo()
    datos["caudal_fase_pesada"] = datos["tph"] * 0.7 + np.random.default_rng(1).normal(0, 0.05, len(datos))
    vif = modelo_multivariable(datos, "eficiencia_pct")["vif"].set_index("factor")["vif"]
    assert vif["tph"] > 5 and vif["caudal_fase_pesada"] > 5


# ---------------------------------------------------------------- aceite perdido

def test_toneladas_por_eboe_y_aceite_perdido():
    dia = pd.Timestamp("2026-07-01")
    pares = pd.DataFrame([
        _par(temporada=2026, fecha_produccion=dia, turno="M", eboe=1, tph=10.0, aceite_salida=1.0),
        _par(temporada=2026, fecha_produccion=dia, turno="M", eboe=2, tph=30.0, aceite_salida=1.5),
        _par(temporada=2026, fecha_produccion=dia, turno="T", eboe=1, tph=10.0, aceite_salida=1.0),
        _par(temporada=2026, fecha_produccion=dia, turno="T", eboe=2, tph=None, aceite_salida=2.0),
        _par(temporada=2026, fecha_produccion=dia, turno="N", eboe=1, tph=10.0, aceite_salida=9.9),   # sin t
    ])
    pares = preparar_pares(pares)
    toneladas = pd.DataFrame({"fecha_produccion": [dia, dia], "turno": ["M", "T"], "fruta_t": [400.0, 100.0]})

    reparto = toneladas_por_eboe(pares, toneladas).set_index(["turno", "eboe"])
    assert reparto.loc[("M", 1), "fruta_t"] == pytest.approx(100) and reparto.loc[("M", 2), "fruta_t"] == pytest.approx(300)
    assert reparto.loc[("T", 1), "fruta_t"] == pytest.approx(50) and reparto.loc[("T", 2), "reparto"] == "partes iguales"
    assert ("N", 1) not in reparto.index

    tabla = aceite_perdido(reparto.reset_index()).set_index("eboe")
    # eBOE 2: (300 × 1,5 + 50 × 2,0) / 350; eBOE 1: 1,0 → diferencia × 350 t
    residual_2 = (300 * 1.5 + 50 * 2.0) / 350
    assert tabla.loc[2, "residual"] == pytest.approx(residual_2) and tabla.loc[2, "referencia"] == 1
    assert tabla.loc[2, "kg_perdidos"] == pytest.approx((residual_2 - 1.0) * 350)
    assert tabla.loc[1, "kg_perdidos"] == 0
    assert frase_aceite_perdido(tabla.reset_index(), 2026).startswith(
        "Si el eBOE 2 hubiera tenido el residual del eBOE 1 (1,00 kg/MT), en 2026 se habrían recuperado 200 kg")
    assert "un solo eBOE" in frase_aceite_perdido(tabla.reset_index().iloc[:1], 2026)
    con_precio = frase_aceite_perdido(tabla.reset_index(), 2026, precio_usd_kg=10.0)
    assert "recuperado 200 kg de aceite (USD 2.000) (" in con_precio and con_precio.endswith("200 kg (USD 2.000).")


def test_planta_entre_temporadas():
    reparto = pd.DataFrame({"temporada": [2025, 2025, 2026], "fruta_t": [100.0, 300.0, 1000.0],
                            "residual": [1.0, 1.4, 1.56]})
    planta = residual_planta(reparto)
    assert planta.set_index("temporada").loc[2025, "residual"] == pytest.approx(1.3)
    resultado = comparar_planta(planta, 2025, 2026)
    assert resultado["diferencia"] == pytest.approx(0.26) and resultado["kg"] == pytest.approx(260)
    assert frase_planta(resultado) == ("En 2026 quedaron 0,26 kg/MT más en la cáscara que en 2025 (1,56 contra "
                                       "1,30 kg/MT), equivalente a 260 kg de aceite sobre 1.000 MT.")
    assert "260 kg de aceite (USD 2.600) sobre" in frase_planta(resultado, 10.0)
    assert comparar_planta(planta, 2024, 2026) is None and "Hacen falta" in frase_planta(None)


def test_resumen_factores():
    pares = _pares_calibre()
    tabla = por_quintil(pares, [50.0, 55.0, 60.0, 65.0], SIN_TONELADAS)
    perdido = pd.DataFrame({"temporada": [2026, 2026], "eboe": [2, 3], "toneladas": [100.0, 100.0],
                            "residual": [1.8, 1.1], "referencia": [3, 3], "diferencia": [0.7, 0.0],
                            "kg_perdidos": [70.0, 0.0]})
    frases = resumen_factores(tabla, frutos_por_eboe(pares), descomponer(tabla, "eficiencia", 2025, 2026),
                              modelo_multivariable(_datos_modelo(), "eficiencia_pct"), perdido,
                              {"base": 2025, "nueva": 2026, "residual_base": 1.3, "residual_nueva": 1.56,
                               "diferencia": 0.26, "toneladas": 1000.0, "kg": 260.0}, 2026)
    assert len(frases) == 5
    assert frases[3].endswith("Ese eBOE es también el que recibe la fruta más chica (70 frutos en 10 kg contra 40 "
                              "del eBOE 3).")
