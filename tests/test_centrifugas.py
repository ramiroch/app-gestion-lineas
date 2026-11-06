"""Pruebas de core/centrifugas.py.

1. Con números inventados: los balances de masa y de aceite cierran, validaciones y "a definir".
2. Contra la planilla real (data/historico, no se sube a GitHub; si no está, se saltea): en 5 muestreos
   las fórmulas dan igual que las columnas P, U y V. Para eso se reproducen acá las versiones de la
   planilla (16 L fijos, descarga = x · ρ · t, masa total con el factor (x6 − x5)).
"""

from pathlib import Path

import pytest

from core.centrifugas import (A_DEFINIR, Eficiencia, aceite_pct, aceite_perdido_kgh, alimentacion_secundaria,
                              balance_centrifuga, caudal_descarga_lh, caudal_fase_liviana, caudal_fase_pesada,
                              densidad, eficiencia_primaria, texto_volumen)

PLANILLA = Path(__file__).resolve().parent.parent / "data" / "historico" / "CENTRIGUGAS Naranja 25 2.xlsx"
FILAS_VERIFICACION = [3, 2251, 4539, 5619, 8831]      # fila de la Emulsion de cada muestreo
BLANCO, FACTOR = 0.1, 3.45                            # los de la planilla (fijos en sus fórmulas)
VOLUMEN_PLANILLA = 16


# ---------------------------------------------------------------- con números inventados

def test_aceite_pct_y_validaciones():
    assert aceite_pct(ml=5.1, blanco=0.1, normalidad=0.1, gramos=5, factor=3.5) == pytest.approx(0.35)
    with pytest.raises(ValueError, match="mayor que 0 g"):
        aceite_pct(5.1, 0.1, 0.1, 0, 3.5)
    with pytest.raises(ValueError, match="normalidad"):
        aceite_pct(5.1, 0.1, None, 5, 3.5)
    with pytest.raises(ValueError, match="factor"):
        aceite_pct(5.1, 0.1, 0.1, 5, None)            # el factor no tiene valor por defecto


def test_densidad():
    assert densidad(0) == pytest.approx(0.997) and densidad(1) == pytest.approx(0.852)
    assert densidad(0.5) == pytest.approx((0.852 + 0.997) / 2)
    with pytest.raises(ValueError, match="fracción"):
        densidad(72)                                   # 72 % hay que pasarlo como 0,72


def test_caudal_de_descarga():
    assert caudal_descarga_lh(10, 300) == pytest.approx(120)
    assert caudal_descarga_lh(None, 300) is None                         # equipo sin volumen: a definir
    with pytest.raises(ValueError, match="tiempo entre descargas"):
        caudal_descarga_lh(10, 0)


def test_los_balances_de_masa_y_de_aceite_cierran():
    m3, x3, x4, x5, x6 = 15000, 0.008, 0.75, 0.0005, 0.002
    qd = caudal_descarga_lh(10, 300)
    m4 = caudal_fase_liviana(m3, x3, x4, x5, x6, qd)
    m5 = caudal_fase_pesada(m3, x3, m4, x4, x5, x6, qd)
    r3, r4, r5, r6 = map(densidad, (x3, x4, x5, x6))
    assert m3 * r3 == pytest.approx(m4 * r4 + m5 * r5 + qd * r6)                          # masa
    assert m3 * r3 * x3 == pytest.approx(m4 * r4 * x4 + m5 * r5 * x5 + qd * r6 * x6)      # aceite

    eficiencia = eficiencia_primaria(m3, x3, m4, x4)
    perdidas = aceite_perdido_kgh(m5, x5) + aceite_perdido_kgh(qd, x6)
    assert eficiencia.pct == pytest.approx(100 * (1 - perdidas / (m3 * r3 * x3)))
    assert not eficiencia.fuera_de_rango_fisico


def test_balance_completo_y_sin_volumen():
    datos = {"caudal_emulsion": 15000, "aceite_emulsion_pct": 0.8, "aceite_liviana_pct": 75,
             "aceite_pesada_pct": 0.05, "aceite_descarga_pct": 0.2, "t_descarga_s": 300}
    balance = balance_centrifuga(**datos, volumen_descarga_l=10)
    assert balance["caudal_descarga"] == pytest.approx(120)
    assert balance["caudal_liviana"] == pytest.approx(caudal_fase_liviana(15000, 0.008, 0.75, 0.0005, 0.002, 120))
    assert balance["perdida_descarga_kgh"] == pytest.approx(0.002 * densidad(0.002) * 120)

    sin_volumen = balance_centrifuga(**datos, volumen_descarga_l=None)
    assert all(valor is None for valor in sin_volumen.values())          # nada con un volumen inventado
    with pytest.raises(TypeError):
        balance_centrifuga(15000, 0.8, 75, 0.05, 0.2, 10, 300)           # por posición no se puede


def test_eficiencia_fuera_de_rango_fisico_se_devuelve_marcada():
    # la fase pesada con más aceite que la liviana da un caudal de liviana negativo
    m4 = caudal_fase_liviana(15000, 0.008, 0.005, 0.006, 0.002, 120)
    eficiencia = eficiencia_primaria(15000, 0.008, m4, 0.005)
    assert eficiencia.pct < 0 and eficiencia.fuera_de_rango_fisico
    assert eficiencia_primaria(1000, 0.01, 2000, 0.01) == Eficiencia(pytest.approx(200), True)
    with pytest.raises(ValueError, match="no se puede resolver"):
        caudal_fase_liviana(15000, 0.008, 0.005, 0.005, 0.002, 120)


def test_alimentacion_secundaria_y_texto_del_volumen():
    assert alimentacion_secundaria([100, 300], [80, 60]) == (400, pytest.approx(65))
    with pytest.raises(ValueError, match="un aceite"):
        alimentacion_secundaria([100], [80, 60])
    assert texto_volumen("Centrífuga primaria", 16.0) == "16 L"
    assert texto_volumen("Pulidora", 1.5) == "1,5 L"
    assert texto_volumen("Centrífuga secundaria", None) == A_DEFINIR
    assert texto_volumen("BOE", None) == ""


# ---------------------------------------------------------------- contra la planilla real

def _descarga_planilla(x6: float, t: float) -> float:
    """Columna V de la fila Descarga: x · ρ · t (t en s, usado como si fuera un caudal)."""
    return x6 * densidad(x6) * t


def _pesada_planilla(m3, x3, m4, x4, x5, x6, t) -> float:
    """Columna M de la fila F Pesada: masa total con el término de la descarga multiplicado por (x6 − x5)."""
    qd = VOLUMEN_PLANILLA * 3600 / t
    return (m3 * densidad(x3) - m4 * densidad(x4) - qd * densidad(x6) * (x6 - x5)) / densidad(x5)


@pytest.fixture(scope="module")
def muestreos():
    """Los 5 muestreos de verificación: datos crudos y lo que calculó Excel (valores guardados)."""
    if not PLANILLA.exists():
        pytest.skip("No está la planilla de centrífugas en data/historico.")
    openpyxl = pytest.importorskip("openpyxl")
    hoja = openpyxl.load_workbook(PLANILLA, read_only=True, data_only=True)["Datos"]
    resultado = []
    for fila in FILAS_VERIFICACION:
        emulsion, liviana, pesada, descarga = hoja.iter_rows(min_row=fila, max_row=fila + 3, values_only=True)
        fases = [str(f[10]).lower() for f in (emulsion, liviana, pesada, descarga)]   # "F Livina" o "Fase livina"
        assert fases[0] == "emulsion" and "liv" in fases[1] and "pes" in fases[2] and fases[3] == "descarga"
        resultado.append({"fila": fila, "e": emulsion, "l": liviana, "p": pesada, "d": descarga})
    return resultado


@pytest.mark.parametrize("indice", range(len(FILAS_VERIFICACION)))
def test_igual_que_la_planilla_en_p_u_y_v(muestreos, indice):
    e, l, p, d = (muestreos[indice][k] for k in "elpd")
    n01, n001, m3, t = e[8], e[9], e[12], d[16]
    # columna P: aceite %
    a3, a5, a6 = aceite_pct(e[14], BLANCO, n01, e[13], FACTOR), aceite_pct(p[14], BLANCO, n001, p[13], FACTOR), \
        aceite_pct(d[14], BLANCO, n01, d[13], FACTOR)
    assert (a3, a5, a6) == pytest.approx((e[15], p[15], d[15]), rel=1e-9)
    x3, x4, x5, x6 = a3 / 100, l[15] / 100, a5 / 100, a6 / 100

    # con 16 L el balance de aceite es el mismo que el de la planilla: caudal de liviana y U
    balance = balance_centrifuga(caudal_emulsion=m3, aceite_emulsion_pct=a3, aceite_liviana_pct=l[15],
                                 aceite_pesada_pct=a5, aceite_descarga_pct=a6, volumen_descarga_l=VOLUMEN_PLANILLA,
                                 t_descarga_s=t)
    assert balance["caudal_liviana"] == pytest.approx(l[12], rel=1e-9)
    assert balance["eficiencia"].pct == pytest.approx(100 * e[20], rel=1e-9)

    # columna V: emulsión y liviana con las mismas fórmulas; pesada y descarga con las de la planilla
    m4 = balance["caudal_liviana"]
    m5_planilla = _pesada_planilla(m3, x3, m4, x4, x5, x6, t)
    assert m5_planilla == pytest.approx(p[12], rel=1e-9)
    assert aceite_perdido_kgh(m3, x3) == pytest.approx(e[21], rel=1e-9)
    assert aceite_perdido_kgh(m4, x4) == pytest.approx(l[21], rel=1e-9)
    assert aceite_perdido_kgh(m5_planilla, x5) == pytest.approx(p[21], rel=1e-9)
    assert _descarga_planilla(x6, t) == pytest.approx(d[21], rel=1e-9)

    # las versiones corregidas: la planilla sobreestima la descarga (t² / (16 · 3600) veces: 4,3 con 500 s)
    assert _descarga_planilla(x6, t) / balance["perdida_descarga_kgh"] == pytest.approx(t ** 2 / (16 * 3600))
    assert balance["caudal_pesada"] < m5_planilla
    assert balance["caudal_pesada"] == pytest.approx(m5_planilla, rel=0.01)
