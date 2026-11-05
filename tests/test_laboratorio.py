"""Pruebas de core/laboratorio.py con datos inventados."""

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
import statsmodels.formula.api as smf

from scipy import stats

from core.laboratorio import (agregar_ml_por_g, anderson_darling, carta_normalidad, comparar_analistas,
                              comparar_formas, compartidas_por_mes, curva_kde, digitos, error_de_lectura,
                              forma_de_pesar, forma_distribucion, frase_analistas, frase_digitos, frase_forma,
                              frase_formas, frase_gramaje, frase_periodos, frase_precision, gramaje_vs_resultado,
                              muestras_compartidas, muestras_vol_alto, normalidad_diaria, periodos_misma_normalidad,
                              porcentaje_fijo, precision_titulacion, preferencia_digitos, probabilidad_normal,
                              referencia_planta, resumen_hallazgos, semana_de, texto_p_ad)


def _muestra(eboe, lugar, fecha_hora, peso, vol, analista="Ana", normalidad=0.1, aceite=8.0):
    fecha_hora = pd.Timestamp(fecha_hora)
    return {"eboe": eboe, "lugar": lugar, "fecha_hora": fecha_hora, "fecha_produccion": fecha_hora.normalize(),
            "turno": "M", "peso_muestra_g": peso, "vol_ml": vol, "analista": analista, "normalidad": normalidad,
            "aceite_fruta": aceite}


# ---------------------------------------------------------------- gramaje y volumen

def test_ml_por_g_y_referencia():
    datos = agregar_ml_por_g(pd.DataFrame({"lugar": ["Entrada"] * 4 + ["Salida"],
                                           "vol_ml": [10.0, 12.0, 9.0, 20.0, 5.0],
                                           "peso_muestra_g": [5.0, 6.0, 9.0, 10.0, 5.0]}))
    assert datos["ml_por_g"].tolist() == pytest.approx([2.0, 2.0, 1.0, 2.0, 1.0])

    referencia = referencia_planta(datos, ["peso_muestra_g"]).set_index("lugar")
    entrada = referencia.loc["Entrada"]
    # Cuartiles de Minitab de 5, 6, 9, 10: Q1 = 5,25, mediana 7,5, Q3 = 9,75
    assert (entrada["q1"], entrada["mediana"], entrada["q3"]) == pytest.approx((5.25, 7.5, 9.75))
    assert entrada["iqr"] == pytest.approx(4.5) and entrada["n"] == 4


def test_muestras_vol_alto():
    datos = pd.DataFrame([_muestra(1, "Salida", "2025-07-01 08:00", 5.0, 58.2),
                          _muestra(1, "Salida", "2025-07-01 06:00", 5.0, 50.0)])
    assert muestras_vol_alto(datos)["vol_ml"].tolist() == [58.2]          # 50 justo no cuenta


# ---------------------------------------------------------------- forma de la distribución

def test_anderson_darling_igual_que_scipy_y_p_de_minitab():
    generador = np.random.default_rng(3)
    normales = generador.normal(10, 2, 200)
    resultado = anderson_darling(normales)
    assert resultado["ad"] == pytest.approx(stats.anderson(normales, "norm", method="interpolate").statistic)
    assert resultado["p"] > 0.05

    # p de Minitab con A*² = A²·(1 + 0,75/n + 2,25/n²): con A*² = 0,752 (el crítico del 5 %), p ≈ 0,05
    x = np.array([1.0, 2.0, 3.0, 4.0, 10.0])
    ad = anderson_darling(x)
    ajustado = ad["ad"] * (1 + 0.75 / 5 + 2.25 / 25)
    assert ad["p"] == pytest.approx(np.exp(0.9177 - 4.279 * ajustado - 1.38 * ajustado ** 2) if ajustado < 0.6
                                    else np.exp(1.2937 - 5.709 * ajustado + 0.0186 * ajustado ** 2))
    assert anderson_darling(generador.exponential(1, 500))["p"] < 0.005
    assert anderson_darling([1.0, 2.0])["ad"] is None
    assert texto_p_ad(0.001) == "p < 0,005" and texto_p_ad(0.2346) == "p = 0,235"


def test_probabilidad_normal_con_rangos_de_benard():
    tabla, recta = probabilidad_normal([3.0, 1.0, 2.0])
    assert tabla["valor"].tolist() == [1.0, 2.0, 3.0]
    assert tabla["porcentaje"].tolist() == pytest.approx([100 * 0.7 / 3.4, 50.0, 100 * 2.7 / 3.4])
    assert tabla["z"].iloc[1] == pytest.approx(0.0)
    assert recta == pytest.approx({"media": 2.0, "desvio": 1.0})


def test_forma_distribucion_detecta_cada_desvio():
    generador = np.random.default_rng(11)
    dos_grupos = np.concatenate([generador.normal(5.0, 0.05, 1000), generador.normal(9.0, 1.0, 2000)])
    forma = forma_distribucion(dos_grupos)
    assert forma["tipo"] == "dos_grupos" and 5.2 < forma["corte"] < 7.5
    assert frase_forma(forma, "El gramaje de entrada", "g", 0).startswith(
        "El gramaje de entrada no es normal (AD = ")
    assert "tiene dos grupos, uno en 5 g y otro entre 8 y 10 g. En el gráfico de probabilidad se ve como un escalón." \
        in frase_forma(forma, "El gramaje de entrada", "g", 0)

    derecha = forma_distribucion(generador.lognormal(0, 0.6, 3000))
    assert derecha["tipo"] == "cola_derecha"
    assert "una cola larga hacia valores altos por eventos raros (asimetría" in frase_forma(
        derecha, "Los mL de salida", "mL", plural=True, causa_cola_alta="por eventos raros")
    assert forma_distribucion(-generador.lognormal(0, 0.6, 3000))["tipo"] == "cola_izquierda"
    assert forma_distribucion(generador.standard_t(3, 5000))["tipo"] == "colas_pesadas"

    normal = forma_distribucion(generador.normal(0, 1, 300))
    assert normal["tipo"] == "normal"
    assert frase_forma(normal, "Los mL/g de entrada", "mL/g", plural=True).startswith(
        "Los mL/g de entrada son compatibles con una normal (AD = ")
    assert "no hay datos suficientes" in frase_forma(forma_distribucion([1.0]), "El gramaje", "g")


def test_curva_kde():
    eje, y = curva_kde(np.random.default_rng(1).normal(0, 1, 500), puntos=100)
    assert len(eje) == 100 and np.trapezoid(y, eje) == pytest.approx(1, abs=0.05)
    assert len(curva_kde([1.0, 1.0, 1.0])[0]) == 0


# ---------------------------------------------------------------- dos formas de pesar

def _datos_formas():
    """eBOE 1, salida, 4 días: cada día dos muestras de 5 g (aceite 1,6 + 0,002·día) y dos variables de
    8 g (1,4); el día 5 solo tiene variables (no entra en la comparación)."""
    filas = []
    for dia in range(1, 6):
        fecha = f"2025-07-0{dia}"
        pesos = [8.0, 8.2] if dia == 5 else [5.0, 5.05, 8.0, 8.2]
        for k, peso in enumerate(pesos):
            aceite = (1.6 + 0.002 * dia if peso < 6 else 1.4) + 0.01 * dia * (k % 2)
            filas.append(_muestra(1, "Salida", f"{fecha} {6 + k:02d}:00", peso, 10.0,
                                  analista="Ana" if peso < 6 else "Beto", aceite=aceite))
    filas.append(_muestra(1, "Entrada", "2025-07-01 06:00", 4.95, 10.0))
    datos = pd.DataFrame(filas)
    return datos.assign(forma=forma_de_pesar(datos))


def test_forma_de_pesar_y_porcentajes():
    datos = _datos_formas()
    assert forma_de_pesar(pd.DataFrame({"peso_muestra_g": [4.9, 5.1, 5.11, 4.89, None]})).tolist()[:4] == [
        "5 g fijo", "5 g fijo", "variable", "variable"]
    with pytest.raises(ValueError, match="mayor que el máximo"):
        forma_de_pesar(datos, 5.1, 4.9)

    analistas = porcentaje_fijo(datos, "analista").set_index(["lugar", "analista"])
    assert analistas.loc[("Salida", "Ana"), "pct_fijo"] == 100 and analistas.loc[("Salida", "Beto"), "n_fijo"] == 0
    meses = porcentaje_fijo(datos, "mes")
    assert meses["etiqueta"].tolist() == ["Jul 2025", "Jul 2025"]
    assert meses.set_index("lugar").loc["Salida", "pct_fijo"] == pytest.approx(100 * 8 / 18)


def test_comparar_formas_emparejado_por_dia():
    datos = _datos_formas()
    tabla = comparar_formas(datos).set_index("lugar")

    salida = tabla.loc["Salida"]
    assert salida["n_dias"] == 4 and (salida["n_fijo"], salida["n_variable"]) == (8, 8)
    # Cada día la diferencia es 0,2 + 0,002·día: en promedio (días 1 a 4), 0,205
    assert salida["diferencia"] == pytest.approx(0.205)
    assert salida["ic_inf"] < 0.205 < salida["ic_sup"] and salida["p"] < 0.001
    assert tabla.loc["Entrada", "n_dias"] == 0 and pd.isna(tabla.loc["Entrada", "diferencia"])

    assert frase_formas(tabla.reset_index().to_dict("records")[0]).startswith("En la entrada hay 0 días con muestras")
    fila = {"lugar": "Salida", "n_dias": 85, "diferencia": 0.08, "p": 0.02, "variable_p10": 7.0, "variable_p90": 11.0}
    assert frase_formas(fila) == ("En la salida, las muestras de 5 g dan 0,08 kg/MT más que las de 7–11 g del mismo día "
                                  "(p = 0,020; 85 días).")
    assert "no hay diferencia significativa" in frase_formas(fila | {"p": 0.4})


def test_comparar_formas_con_ruido():
    generador = np.random.default_rng(5)
    datos = _datos_formas()
    datos["aceite_fruta"] += generador.normal(0, 0.02, len(datos))
    salida = comparar_formas(datos).set_index("lugar").loc["Salida"]
    assert salida["ic_inf"] < salida["diferencia"] < salida["ic_sup"] and salida["p"] < 0.05


# ---------------------------------------------------------------- precisión de la titulación

def test_precision_titulacion():
    datos = pd.DataFrame({"lugar": ["Entrada", "Entrada", "Salida", "Salida"], "vol_ml": [10.2, 4.2, 25.2, 0.2],
                          "blanco_ml": [0.2] * 4})
    error = error_de_lectura(datos)
    assert error.iloc[0] == pytest.approx(0.5) and error.iloc[1] == pytest.approx(1.25) and pd.isna(error.iloc[3])

    tabla = precision_titulacion(datos).set_index("lugar")
    assert (tabla.loc["Entrada", "n_mayor"], tabla.loc["Entrada", "pct_mayor"]) == (1, 50.0)
    assert tabla.loc["Salida", "n"] == 1 and tabla.loc["Salida", "mediana"] == pytest.approx(0.2)
    assert frase_precision(tabla.reset_index()) == (
        "El error de lectura de la bureta supera el 1 % (menos de 5 mL netos) en el 50,0 % de las muestras de "
        "entrada y en el 0,0 % de las muestras de salida. Mediana del error: 0,88 % en entrada y 0,20 % en salida.")


# ---------------------------------------------------------------- dígitos

def test_digitos():
    resultado = digitos(pd.Series([12.35, 12.3499999, 7.0, 9.07]))
    assert resultado["decimo"].tolist() == [3, 3, 0, 0]
    assert resultado["centesimo"].tolist() == [5, 5, 0, 7]


def test_preferencia_y_frase():
    # Ana: 100 volúmenes con el centésimo parejo (10 de cada dígito); Beto: 60 de 100 terminan en 0 o 5.
    ana = [10 + d / 100 for d in range(10)] * 10
    beto = [10.00, 10.05] * 30 + [10 + d / 100 for d in (1, 2, 3, 4, 6, 7, 8, 9)] * 5
    datos = pd.DataFrame({"vol_ml": ana + beto + [10.0] * 20,
                          "analista": ["Ana"] * 100 + ["Beto"] * 100 + ["Ceci"] * 20})

    tabla = preferencia_digitos(datos, "centesimo").set_index("grupo")

    assert tabla.loc["Ana", "p"] == pytest.approx(1.0) and tabla.loc["Ana", "pct_3"] == pytest.approx(10.0)
    assert tabla.loc["Beto", "exceso_0_5"] == pytest.approx(60 / 20 - 1)          # 60 % contra 20 %: +200 %
    assert tabla.loc["Beto", "p"] < 0.001
    assert pd.isna(tabla.loc["Ceci", "p"])                                        # menos de 50 muestras
    frase = frase_digitos(tabla.reset_index(), "centesimo")
    assert frase.startswith("En el último dígito (centésimo) de los mL, Beto anota 0 y 5 un 200 % más de lo esperado")
    assert "Sin preferencia: Ana." in frase and "(no se prueba): Ceci." in frase
    with pytest.raises(ValueError, match="Posición desconocida"):
        preferencia_digitos(datos, "unidad")


def test_decimales_pares():
    # Beto lee con una bureta graduada cada 0,2 mL: 80 de 100 primeros decimales son pares.
    pares = [10 + d / 10 for d in (0, 2, 4, 6, 8)] * 16
    impares = [10 + d / 10 for d in (1, 3, 5, 7, 9)] * 4
    datos = pd.DataFrame({"vol_ml": pares + impares, "analista": "Beto"})

    tabla = preferencia_digitos(datos, "decimo")
    assert tabla["exceso_pares"].iloc[0] == pytest.approx(80 / 20 - 1)          # 4 veces más pares: +300 %
    assert tabla["p_pares"].iloc[0] < 0.001
    assert ("En Beto, los primeros decimales pares aparecen un 300 % más que los impares (p < 0,001; posible bureta "
            "graduada cada 0,2 mL).") in frase_digitos(tabla, "decimo")
    assert "pares" not in frase_digitos(preferencia_digitos(datos, "centesimo"), "centesimo")


def test_frase_sin_preferencia_y_otro_digito():
    parejo = pd.DataFrame({"vol_ml": [10 + d / 100 for d in range(10)] * 6, "analista": "Ana"})
    assert "ningún analista muestra preferencia" in frase_digitos(preferencia_digitos(parejo, "centesimo"), "centesimo")

    sietes = pd.DataFrame({"vol_ml": [10.07] * 40 + [10 + d / 100 for d in range(10)] * 6, "analista": "Ana"})
    assert "Ana anota el 7 un " in frase_digitos(preferencia_digitos(sietes, "centesimo"), "centesimo")


# ---------------------------------------------------------------- muestras compartidas

def test_muestras_compartidas_y_por_mes():
    datos = pd.DataFrame([
        _muestra(1, "Entrada", "2025-07-10 06:00", 8.123, 10.5, "Ana"),     # compartida por los 3 eBOE
        _muestra(2, "Entrada", "2025-07-10 06:00", 8.123, 10.5, "Ana"),
        _muestra(3, "Entrada", "2025-07-10 06:00", 8.123, 10.5, "Ana"),
        _muestra(1, "Entrada", "2025-07-10 08:00", 8.0, 10.5),              # mismo vol, otro peso: no
        _muestra(2, "Entrada", "2025-07-10 08:00", 8.1, 10.5),
        _muestra(1, "Salida", "2025-07-10 06:00", 8.123, 10.5),              # salida: no se mira
        _muestra(1, "Entrada", "2025-08-01 02:00", 9.0, 11.0),               # día de producción 31/07
        _muestra(2, "Entrada", "2025-08-01 02:00", 9.0, 11.0),
        _muestra(1, "Entrada", "2025-08-05 06:00", 9.0, 11.0),
    ])
    datos.loc[datos["fecha_hora"] == pd.Timestamp("2025-08-01 02:00"), "fecha_produccion"] = pd.Timestamp("2025-07-31")

    grupos = muestras_compartidas(datos)
    assert grupos["eboes"].tolist() == ["1, 2, 3", "1, 2"] and grupos["n_eboes"].tolist() == [3, 2]

    por_mes = compartidas_por_mes(datos).set_index("etiqueta")
    julio = por_mes.loc["Jul 2025"]
    assert (julio["entradas"], julio["grupos"], julio["compartidas"]) == (7, 2, 5)
    assert julio["pct"] == pytest.approx(100 * 5 / 7)
    assert por_mes.loc["Ago 2025", "compartidas"] == 0


# ---------------------------------------------------------------- analistas

def _datos_analistas():
    """3 analistas, 2 eBOE y 3 semanas; Beto da +0,3 y Ceci −0,2 respecto de Ana, más ruido.
    La semana 3 del eBOE 2 solo la midió Ana: ese bloque no entra."""
    generador = np.random.default_rng(7)
    efecto = {"Ana": 0.0, "Beto": 0.3, "Ceci": -0.2}
    filas = []
    for eboe in (1, 2):
        for semana, lunes in enumerate(pd.date_range("2025-07-07", periods=3, freq="7D"), start=1):
            analistas = ["Ana"] if (eboe, semana) == (2, 3) else ["Ana", "Beto", "Ceci"]
            for k, analista in enumerate(analistas * 4):
                fecha = lunes + pd.Timedelta(days=k % 7, hours=6)
                valor = 1.5 + 0.2 * eboe + 0.1 * semana + efecto[analista] + generador.normal(0, 0.05)
                filas.append(_muestra(eboe, "Salida", fecha, 5.0, 10.0, analista, aceite=valor))
    return pd.DataFrame(filas)


def test_semana_de():
    assert semana_de(pd.Series(pd.to_datetime(["2025-07-13", "2025-07-14"]))).tolist() == [
        pd.Timestamp("2025-07-07"), pd.Timestamp("2025-07-14")]       # domingo y lunes


def test_comparar_analistas_igual_que_statsmodels():
    datos = _datos_analistas()
    resultado = comparar_analistas(datos, "aceite_fruta")

    assert resultado["n_bloques"] == 5 and resultado["n"] == len(datos) - 4

    # Referencia: modelo lineal de statsmodels con codificación de suma y ANOVA tipo II.
    d = datos.assign(bloque=datos["eboe"].astype(str) + semana_de(datos["fecha_produccion"]).astype(str))
    d = d[d.groupby("bloque")["analista"].transform("nunique") >= 2]
    modelo = smf.ols("aceite_fruta ~ C(analista, Sum) + C(bloque, Sum)", data=d).fit()
    referencia = sm.stats.anova_lm(modelo, typ=2)
    anova = resultado["anova"].set_index("fuente")
    assert anova.loc["Analista", "sc"] == pytest.approx(referencia["sum_sq"].iloc[0])
    assert anova.loc["eBOE × semana", "f"] == pytest.approx(referencia["F"].iloc[1])
    assert anova.loc["Error", "gl"] == pytest.approx(referencia["df"].iloc[2])
    assert resultado["p"] == pytest.approx(referencia["PR(>F)"].iloc[0]) and resultado["p"] < 0.001

    medias = resultado["medias"].set_index("grupo")
    ana = modelo.t_test("Intercept + C(analista, Sum)[S.Ana] = 0")
    assert medias.loc["Ana", "media"] == pytest.approx(float(np.ravel(ana.effect)[0]))
    assert medias.loc["Ana", "ic_inf"] == pytest.approx(ana.conf_int()[0][0])
    # Las diferencias entre medias ajustadas recuperan los efectos inventados.
    assert medias.loc["Beto", "media"] - medias.loc["Ana", "media"] == pytest.approx(0.3, abs=0.05)
    assert medias.loc["Ceci", "media"] - medias.loc["Ana", "media"] == pytest.approx(-0.2, abs=0.05)

    frase = frase_analistas(resultado, "el aceite de salida", "kg/MT")
    assert frase.startswith("Comparando en el mismo eBOE y la misma semana, el aceite de salida cambia según el "
                            "analista (p < 0,001): Beto da en promedio")
    assert "Ceci" in frase


def test_comparar_analistas_sin_bloques_compartidos():
    datos = _datos_analistas()
    solo_ana = comparar_analistas(datos[datos["analista"] == "Ana"], "aceite_fruta")
    assert solo_ana["p"] is None and solo_ana["n_bloques"] == 0
    assert frase_analistas(solo_ana, "la eficiencia", "%").startswith("No hay semanas en que dos o más")


def test_frase_analistas_sin_diferencia_y_en_puntos():
    resultado = {"p": 0.4, "alfa": 0.05, "medias": pd.DataFrame(
        {"grupo": ["Ana", "Beto"], "media": [84.0, 85.5], "n": [10, 10], "ic_inf": [0, 0], "ic_sup": [0, 0]})}
    frase = frase_analistas(resultado, "la eficiencia", "%", 1, "puntos")
    assert "no cambia de forma significativa según el analista (p = 0,400)" in frase
    assert frase.endswith("Beto da en promedio 85,5 % y Ana 84,0 % (1,5 puntos de diferencia).")


# ---------------------------------------------------------------- normalidad

def test_normalidad_diaria_y_periodos():
    filas = []
    for dia, normalidad in [(1, 0.0999), (2, 0.0999), (20, 0.0999), (21, 0.1003), (22, 0.0999)]:
        fecha = pd.Timestamp(2025, 7, dia)
        filas += [_muestra(e, "Entrada", fecha + pd.Timedelta(hours=8), 9.0, 10.0, normalidad=normalidad)
                  for e in (1, 2, 3)]
    filas.append(_muestra(1, "Entrada", "2025-07-02 10:00", 9.0, 10.0, normalidad=0.1001))   # un valor distinto
    filas.append(_muestra(1, "Salida", "2025-07-02 10:00", 9.0, 10.0, normalidad=0.0098))

    diaria = normalidad_diaria(pd.DataFrame(filas), "Entrada")
    assert diaria["normalidad"].tolist() == [0.0999, 0.0999, 0.0999, 0.1003, 0.0999]   # la más frecuente
    assert diaria["distintas"].tolist() == [1, 2, 1, 1, 1]

    periodos = periodos_misma_normalidad(diaria)
    assert periodos["dias"].tolist() == [20, 1, 1]            # del 1 al 20/07 (los días sin datos cuentan)
    assert periodos["largo"].tolist() == [True, False, False]
    assert (periodos["primero"].iloc[0], periodos["ultimo"].iloc[0]) == (0, 2)
    assert periodos_misma_normalidad(diaria.iloc[0:0]).empty

    carta = carta_normalidad(diaria, periodos)
    # Límites con los 3 períodos (0,0999; 0,1003; 0,0999): MR̄ = 0,0004 y σ = MR̄ / d2(2)
    assert carta["lc"].iloc[0] == pytest.approx((0.0999 + 0.1003 + 0.0999) / 3)
    assert carta["sigma"].iloc[0] == pytest.approx(0.0004 / 1.128379, rel=1e-5)
    assert carta["disp"].isna().tolist() == [True, True, True, False, False]      # MR solo cuando cambia
    assert carta["disp"].iloc[3] == pytest.approx(0.0004)
    assert list(carta["fecha_produccion"]) == list(diaria["fecha_produccion"])
    with pytest.raises(ValueError, match="no cambió"):
        carta_normalidad(diaria.iloc[:3], periodos_misma_normalidad(diaria.iloc[:3]))

    assert frase_periodos(periodos, "Entrada") == (
        "En la entrada hubo 1 período de más de 15 días con la misma normalidad: 0,0999 N del 01/07/2025 al "
        "20/07/2025 (20 días). Conviene revisar que la solución se haya vuelto a valorar en esos períodos.")
    assert frase_periodos(periodos.iloc[1:], "Salida") == (
        "En la salida ninguna normalidad se repitió más de 15 días seguidos.")


# ---------------------------------------------------------------- gramaje vs resultado

def test_gramaje_vs_resultado_y_frases():
    pesos = np.linspace(5, 15, 30)
    datos = pd.DataFrame({"lugar": ["Salida"] * 30 + ["Entrada"] * 30,
                          "peso_muestra_g": np.concatenate([pesos, pesos]),
                          "aceite_fruta": np.concatenate([2.0 - 0.1 * pesos, 8.0 + np.tile([0.5, -0.5], 15)])})

    rectas = gramaje_vs_resultado(datos).set_index("lugar")
    assert rectas.loc["Salida", "pendiente"] == pytest.approx(-0.1) and rectas.loc["Salida", "r2"] == pytest.approx(1)

    salida = rectas.loc["Salida"].to_dict() | {"lugar": "Salida"}
    assert frase_gramaje(salida) == ("En la salida, cada 1 g más de muestra se asocia con 0,100 kg/MT menos de "
                                     "aceite (R² = 1,00; p < 0,001): el resultado cambia con el gramaje.")
    entrada = rectas.loc["Entrada"].to_dict() | {"lugar": "Entrada"}
    assert frase_gramaje(entrada).endswith("el resultado no cambia con el gramaje.")
    debil = salida | {"r2": 0.01, "p": 0.001}
    assert frase_gramaje(debil).endswith("pero el gramaje explica muy poco del resultado.")
    assert "no hay datos suficientes" in frase_gramaje({"lugar": "Entrada", "n": 2, "pendiente": None})



# ---------------------------------------------------------------- resumen

def test_resumen_hallazgos():
    lugar = pd.DataFrame({"lugar": ["Entrada", "Salida"], "n": [100, 100], "n_fijo": [1, 20], "pct_fijo": [1.0, 20.0]})
    analista = pd.DataFrame({"lugar": ["Salida", "Salida"], "analista": ["Ana", "Beto"], "n": [60, 60],
                             "n_fijo": [45, 3], "pct_fijo": [75.0, 5.0]})
    formas = pd.DataFrame([{"lugar": "Salida", "n_dias": 85, "diferencia": 0.08, "p": 0.02, "variable_p10": 7.0,
                            "variable_p90": 11.0}])
    precision = pd.DataFrame({"lugar": ["Entrada"], "n": [200], "mediana": [0.4], "n_mayor": [2], "pct_mayor": [1.0]})
    digitos_tabla = pd.DataFrame({"grupo": ["Ana", "Beto"], "p": [0.001, 0.5], "exceso_0_5": [0.6, 0.0]})
    compartidas = pd.DataFrame({"entradas": [100, 100], "compartidas": [5, 15], "pct": [5.0, 15.0]})
    analistas = {"p": 0.001, "alfa": 0.05, "medias": pd.DataFrame({"grupo": ["Ana", "Beto"], "media": [1.5, 1.2]})}

    frases = resumen_hallazgos(lugar, analista, formas, precision, digitos_tabla, compartidas, analistas)

    assert len(frases) == 5
    assert frases[0] == ("Conviven dos formas de pesar: se pesan a 5 g fijo el 1 % de las de entrada y el 20 % de las "
                         "de salida; el resto, con gramaje variable. Quien más pesa a 5 g es Ana (75 % de sus muestras "
                         "de salida).")
    assert frases[1].startswith("En la salida, las muestras de 5 g dan 0,08 kg/MT más")
    assert frases[2] == ("El 1,0 % de las muestras tiene un error de lectura mayor al 1 % (menos de 5 mL netos), y Ana "
                         "redondea el último dígito de los mL a 0 o 5.")
    assert frases[3].startswith("El 10,0 % de las muestras de entrada está anotado igual en más de un eBOE "
                                "(máximo mensual 15,0 %)")
    assert frases[4].endswith("Ojo: Ana pesa a 5 g el 75 % de sus salidas, y esas muestras dan 0,08 kg/MT más: parte "
                              "de la diferencia es por el gramaje y no por el analista.")
