"""Pruebas de core/laboratorio.py con datos inventados."""

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
import statsmodels.formula.api as smf

from core.laboratorio import (carta_normalidad, comparar_analistas, compartidas_por_mes, digitos, frase_analistas,
                              frase_digitos, frase_gramaje, frase_periodos, fuera_de_protocolo, gramaje_vs_resultado,
                              muestras_compartidas, muestras_vol_alto, normalidad_diaria, periodos_misma_normalidad,
                              preferencia_digitos, rangos_protocolo, semana_de)


def _muestra(eboe, lugar, fecha_hora, peso, vol, analista="Ana", normalidad=0.1, aceite=8.0):
    fecha_hora = pd.Timestamp(fecha_hora)
    return {"eboe": eboe, "lugar": lugar, "fecha_hora": fecha_hora, "fecha_produccion": fecha_hora.normalize(),
            "turno": "M", "peso_muestra_g": peso, "vol_ml": vol, "analista": analista, "normalidad": normalidad,
            "aceite_fruta": aceite}


# ---------------------------------------------------------------- gramaje y volumen

def test_rangos_y_fuera_de_protocolo():
    rangos = rangos_protocolo(0.10)
    assert rangos["Entrada"] == pytest.approx((13.5, 16.5)) and rangos["Salida"] == pytest.approx((22.5, 27.5))
    with pytest.raises(ValueError, match="entre 0 y 1"):
        rangos_protocolo(1.5)

    datos = pd.DataFrame([_muestra(1, "Entrada", "2025-07-01 06:00", g, 10.0, analista=a)
                          for g, a in ((15.0, "Ana"), (10.0, "Ana"), (17.0, "Beto"), (14.0, "Beto"))]
                         + [_muestra(1, "Salida", "2025-07-01 06:00", 25.0, 10.0)])
    rangos = {"Entrada": (14.0, 16.0), "Salida": (24.0, 26.0)}

    por_lugar = fuera_de_protocolo(datos, rangos).set_index("lugar")
    entrada = por_lugar.loc["Entrada"]
    assert (entrada["n"], entrada["debajo"], entrada["encima"], entrada["fuera"]) == (4, 1, 1, 2)
    assert entrada["pct_fuera"] == pytest.approx(50.0) and por_lugar.loc["Salida", "fuera"] == 0   # bordes adentro
    por_analista = fuera_de_protocolo(datos, rangos, ["analista"]).set_index(["lugar", "analista"])
    assert por_analista.loc[("Entrada", "Beto"), "encima"] == 1


def test_muestras_vol_alto():
    datos = pd.DataFrame([_muestra(1, "Salida", "2025-07-01 08:00", 5.0, 58.2),
                          _muestra(1, "Salida", "2025-07-01 06:00", 5.0, 50.0)])
    assert muestras_vol_alto(datos)["vol_ml"].tolist() == [58.2]          # 50 justo no cuenta


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
