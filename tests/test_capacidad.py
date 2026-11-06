"""Pruebas de core/capacidad.py: casos a mano, casos de libro y (si están los archivos) contra Minitab."""

import math
from datetime import datetime, time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.capacidad import (AVISO_NO_CALCULA, AVISO_POCOS, NO_APLICA, box_cox, capacidad, capacidad_desde_base,
                            datos_capacidad, indices, limites_capacidad, normalidad, ppm_esperado,
                            sigma_dentro_individuales, sigma_dentro_subgrupos)
from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.laboratorio import anderson_darling
from core.models import Cliente, Planta, RangoReferencia

MINITAB = Path(__file__).resolve().parent / "datos_minitab"


# ---------------------------------------------------------------- a mano

def test_datos_simetricos_centrados_dan_cp_igual_a_cpk():
    x = [84.0, 86.0] * 20                                   # media 85, centrada entre 80 y 90
    resultado = capacidad(x, 80, 90)
    sigma_dentro = 2 / 1.128                                # MR̄ = 2, d2(2) = 1,128
    assert resultado["media"] == pytest.approx(85)
    assert resultado["sigma_dentro"] == pytest.approx(sigma_dentro, rel=1e-3)
    assert resultado["cp"] == pytest.approx(10 / (6 * sigma_dentro), rel=1e-3)
    assert resultado["cp"] == pytest.approx(resultado["cpk"]) and resultado["cpl"] == pytest.approx(resultado["cpu"])
    sigma_total = np.std(x, ddof=1)
    assert resultado["pp"] == pytest.approx(10 / (6 * sigma_total)) and resultado["pp"] == pytest.approx(resultado["ppk"])
    assert resultado["ppm_observado"]["total"] == 0 and resultado["avisos"] == []


def test_proceso_corrido_hacia_un_limite():
    x = [88.0, 89.0] * 20                                   # media 88,5
    resultado = capacidad(x, 80, 90)
    sigma = 1 / 1.128
    assert resultado["cpu"] == pytest.approx(1.5 / (3 * sigma), rel=1e-3)
    assert resultado["cpl"] == pytest.approx(8.5 / (3 * sigma), rel=1e-3)
    assert resultado["cpk"] == resultado["cpu"] < resultado["cp"]


def test_un_solo_limite():
    resultado = capacidad([3.0, 4.0, 3.5, 4.2, 3.8] * 8, lse=5)           # °Brix ≤ 5
    assert resultado["cp"] == NO_APLICA and resultado["pp"] == NO_APLICA
    assert resultado["cpl"] is None and resultado["cpk"] == resultado["cpu"]
    assert resultado["ppm_esperado_total"]["lie"] == 0


def test_ppm_observados_y_avisos_de_n():
    x = [79.0, 85.0, 91.0, 85.0, 86.0, 84.0, 85.0, 85.0, 86.0, 84.0]
    resultado = capacidad(x, 80, 90)
    assert resultado["ppm_observado"] == {"lie": 100_000, "lse": 100_000, "total": 200_000}
    assert resultado["avisos"] == [AVISO_POCOS]
    pocos = capacidad([85, 86], 80, 90)
    assert pocos["avisos"] == [AVISO_NO_CALCULA] and pocos["cpk"] is None


def test_valida_los_limites():
    with pytest.raises(ValueError, match="al menos un límite"):
        capacidad([1, 2, 3])
    with pytest.raises(ValueError, match="menor que el LSE"):
        capacidad([1, 2, 3], 5, 4)


def test_sigma_dentro_con_subgrupos_y_c4():
    x = [1, 2, 3, 2, 3, 4]                                  # dos subgrupos de 3: Σ(x − x̄)² = 2 + 2, d = 4
    assert sigma_dentro_subgrupos(x, [0, 0, 0, 1, 1, 1], usar_c4=False) == pytest.approx(1)
    assert sigma_dentro_subgrupos(x, [0, 0, 0, 1, 1, 1]) == pytest.approx(1 / 0.9400, rel=1e-4)   # c4(5) = 0,9400
    por_tamano = capacidad(x * 5, 0, 6, subgrupos=3)
    por_etiqueta = capacidad(x * 5, 0, 6, subgrupos=np.arange(30) // 3)
    assert por_tamano["sigma_dentro"] == pytest.approx(por_etiqueta["sigma_dentro"])
    assert por_tamano["metodo_dentro"] == "desvío combinado / c4"


def test_sigma_total_con_c4_opcional():
    x = [1.0, 2.0, 4.0, 7.0]
    assert capacidad(x, 0, 10)["sigma_total"] == pytest.approx(np.std(x, ddof=1))
    con_c4 = capacidad(x, 0, 10, usar_c4_total=True)["sigma_total"]
    assert con_c4 == pytest.approx(np.std(x, ddof=1) / 0.9213, rel=1e-4)                     # c4(4) = 0,9213


def test_el_orden_importa_para_sigma_dentro():
    assert sigma_dentro_individuales([1, 2, 3, 4]) == pytest.approx(1 / 1.128, rel=1e-3)
    assert sigma_dentro_individuales([1, 4, 2, 3]) == pytest.approx((3 + 2 + 1) / 3 / 1.128, rel=1e-3)


# ---------------------------------------------------------------- de libro

def test_tres_sigma_a_cada_lado_son_2700_ppm():
    """Proceso centrado con Cp = 1 (±3σ): 0,27 % fuera, 2.700 ppm (Montgomery, cap. 8)."""
    assert ppm_esperado(0, 1, -3, 3)["total"] == pytest.approx(2699.8, abs=0.1)
    assert indices(0, 1, -3, 3) == {"cp": pytest.approx(1), "cpk": pytest.approx(1), "cpl": pytest.approx(1),
                                    "cpu": pytest.approx(1)}


def test_ejemplo_del_nist():
    """NIST/SEMATECH e-Handbook of Statistical Methods, 6.1.6: LSE = 20, LIE = 8, μ = 16, σ = 2 →
    Cp = 1,0; Cpu = 0,667; Cpl = 1,333; Cpk = 0,667."""
    resultado = indices(16, 2, 8, 20)
    assert resultado["cp"] == pytest.approx(1.0)
    assert resultado["cpu"] == pytest.approx(0.6667, abs=1e-4)
    assert resultado["cpl"] == pytest.approx(1.3333, abs=1e-4)
    assert resultado["cpk"] == pytest.approx(0.6667, abs=1e-4)


def test_cp_4_sigma_y_6_sigma():
    """Cp = 1,33 (±4σ) da 63 ppm y Cp = 2 (±6σ, "seis sigma" centrado) da 0,002 ppm (Montgomery, tabla 8.3)."""
    assert ppm_esperado(0, 1, -4, 4)["total"] == pytest.approx(63.3, abs=0.1)
    assert ppm_esperado(0, 1, -6, 6)["total"] == pytest.approx(0.002, abs=0.0005)


# ---------------------------------------------------------------- normalidad y Box-Cox

def test_normalidad_igual_que_la_version_tipo_minitab():
    rng = np.random.default_rng(7)
    for x in (rng.normal(85, 4, 30), rng.exponential(2, 40), rng.uniform(0, 1, 25)):
        propio, referencia = normalidad(x), anderson_darling(x)
        assert propio["ad"] == pytest.approx(referencia["ad"]) and propio["p"] == pytest.approx(referencia["p"])
    assert normalidad([1, 2])["p"] is None
    assert normalidad(rng.normal(0, 1, 200))["normal"]
    assert not normalidad(rng.exponential(1, 200))["normal"]


def test_box_cox_lognormal_da_lambda_0():
    rng = np.random.default_rng(3)
    x = np.exp(rng.normal(1, 0.5, 300))
    resultado = box_cox(x, lie=1, lse=10)
    assert resultado["lambda_redondeado"] == 0 and abs(resultado["lambda_estimado"]) < 0.25
    assert resultado["lie"] == pytest.approx(0) and resultado["lse"] == pytest.approx(math.log(10))
    assert np.allclose(resultado["datos"], np.log(x))
    assert resultado["normalidad"]["normal"] and resultado["capacidad"]["n"] == 300


def test_box_cox_con_lambda_negativo_da_vuelta_los_limites():
    rng = np.random.default_rng(5)
    y = rng.normal(1, 0.3, 300)
    x = 1 / y[y > 0.2]                                      # su inversa es normal: λ ≈ −1 (con poca dispersión no se distingue)
    resultado = box_cox(x, lie=0.8, lse=1.25)
    assert resultado["lambda_redondeado"] == -1
    assert resultado["lie"] == pytest.approx(1 / 1.25) and resultado["lse"] == pytest.approx(1 / 0.8)


def test_box_cox_no_transforma_valores_cero_o_negativos():
    resultado = box_cox([0.0, 1.0, 2.0, 3.0], 1, 3)
    assert resultado["lambda_redondeado"] is None and "≤ 0" in resultado["avisos"][0]
    limite = box_cox([1.0, 2.0, 3.0, 4.0, 5.0], lie=0, lse=6)
    assert limite["lie"] is None and "LIE (0)" in limite["avisos"][0]


# ---------------------------------------------------------------- desde la base

def _analisis():
    filas = []
    for k, (entrada, salida, eficiencia, eboe) in enumerate([(7.0, 1.0, 0.857, 1), (8.0, 1.6, 0.8, 1),
                                                              (7.5, 1.0, 0.867, 2), (6.0, 0.6, 0.9, 1)]):
        momento = datetime(2026, 7, 1 + k, 10)
        for lugar, aceite in (("Entrada", entrada), ("Salida", salida)):
            filas.append({"fecha_hora": momento, "fecha_produccion": pd.Timestamp(momento.date()), "eboe": eboe,
                          "lugar": lugar, "aceite_fruta": aceite, "eficiencia_par": eficiencia,
                          "fruta": "N Val S/R" if k < 3 else "L Eureka"})
    return pd.DataFrame(filas)


def test_datos_de_eficiencia_rendimiento_y_emulsion():
    analisis = _analisis()
    eficiencia = datos_capacidad(analisis, None, "eficiencia", equipo=1, fruta="naranja")
    assert eficiencia["valor"].tolist() == pytest.approx([85.7, 80.0])                  # el limón queda afuera
    rendimiento = datos_capacidad(analisis, None, "rendimiento", desde="2026-07-02")
    assert rendimiento["valor"].tolist() == pytest.approx([6.4, 6.5, 5.4])
    emulsion = pd.DataFrame({"fecha_hora": pd.to_datetime(["2026-07-02", "2026-07-01"]), "eboe": [1, 1],
                             "fecha_produccion": pd.to_datetime(["2026-07-02", "2026-07-01"]),
                             "aceite_emulsion": [1.0, 0.9], "brix": [4.0, None], "ph": [4.5, 4.6]})
    assert datos_capacidad(None, emulsion, "aceite_emulsion")["valor"].tolist() == [0.9, 1.0]   # en orden de tiempo
    assert len(datos_capacidad(None, emulsion, "brix")) == 1
    with pytest.raises(ValueError, match="no válida"):
        datos_capacidad(analisis, None, "temperatura")


def test_limites_de_la_base_y_capacidad_desde_base():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        planta = Planta(nombre="Norte", pais="Argentina", cliente=Cliente(nombre="X", pais="Argentina"))
        sesion.add_all([planta,
                        RangoReferencia(planta=planta, variable="Eficiencia eBOE", punto_o_equipo="BOE (todos)",
                                        minimo=75, maximo=95, fuente="general"),
                        RangoReferencia(planta=planta, variable="Eficiencia eBOE", punto_o_equipo="BOE (todos)",
                                        fruta="Naranja", minimo=80, maximo=90, unidad="%", fuente="planta"),
                        RangoReferencia(planta=planta, variable="°Brix emulsión", punto_o_equipo="Emulsión (licor)",
                                        maximo=5, fuente="planta")])
        sesion.commit()
        assert limites_capacidad(sesion, planta.id, "eficiencia", "naranja")["fuente"] == "planta"
        assert limites_capacidad(sesion, planta.id, "eficiencia", "limón")["fuente"] == "general"
        assert limites_capacidad(sesion, planta.id, "brix")["lie"] is None
        assert limites_capacidad(sesion, planta.id, "ph") is None
        resultado = capacidad_desde_base(sesion, planta.id, "eficiencia", equipo=1, analisis=_analisis())
        with pytest.raises(ValueError, match="pH emulsión"):
            capacidad_desde_base(sesion, planta.id, "ph", emulsion=pd.DataFrame())
    assert (resultado["limites"]["lie"], resultado["limites"]["lse"]) == (80, 90)
    assert resultado["capacidad"]["n"] == 2 and resultado["capacidad"]["avisos"] == [AVISO_NO_CALCULA]


# ---------------------------------------------------------------- contra Minitab

def _casos_minitab():
    archivo = MINITAB / "capacidad_resultados.csv"
    if not archivo.exists():
        return []
    return pd.read_csv(archivo, sep=";", decimal=",").to_dict("records")


@pytest.mark.parametrize("caso", _casos_minitab() or [None], ids=lambda c: c["caso"] if c else "sin_archivo")
def test_igual_que_minitab(caso):
    """Compara con los resultados de Minitab de tests/datos_minitab/ (formato en su README). Se saltea si no están."""
    if caso is None:
        pytest.skip("No está tests/datos_minitab/capacidad_resultados.csv.")
    datos = pd.read_csv(MINITAB / caso["archivo_datos"], sep=";", decimal=",")
    subgrupos = None if pd.isna(caso.get("subgrupo")) else int(caso["subgrupo"])
    lie = None if pd.isna(caso.get("lie")) else caso["lie"]
    lse = None if pd.isna(caso.get("lse")) else caso["lse"]
    resultado = capacidad(datos["valor"], lie, lse, subgrupos)
    prueba = normalidad(datos["valor"])
    calculado = resultado | {"ppm_esperado_total": resultado["ppm_esperado_total"]["total"], "ad": prueba["ad"],
                             "p": prueba["p"]}
    for columna in ("n", "media", "sigma_dentro", "sigma_total", "cp", "cpk", "pp", "ppk", "ppm_esperado_total",
                    "ad", "p"):
        esperado = caso.get(columna)
        if esperado is None or pd.isna(esperado):
            continue
        assert calculado[columna] == pytest.approx(esperado, rel=1e-3, abs=5e-3), columna
