"""Pruebas de core/calculos.py.

Las pruebas con filas reales leen el Excel de LEDESMA en el momento (data/ no se sube a
GitHub, así no quedan datos del cliente en el código) y comparan con lo que calculó Excel.
Si el archivo no está, se saltean.
"""

from pathlib import Path

import pytest
from openpyxl import load_workbook

from core.calculos import aceite_en_emulsion, aceite_en_fruta, eficiencia_eboe, rendimiento_eboe
from core.models import Planta
from core.parametros import ParametrosIncompletos

ARCHIVO = Path(__file__).resolve().parent.parent / "data" / "historico" / "eficiencia eBOE Naranja 25 2.xlsm"
FILAS = [4, 5, 10, 9000, 9001]          # dos pares Entrada/Salida (2025 y 2026) y dos con emulsión
PARES = [(4, 5), (9000, 9001)]
TOLERANCIA = 0.001
BLANCO_EXCEL = 0.2                       # el Excel de LEDESMA usa blanco fijo 0,2 mL


@pytest.fixture
def ledesma():
    return Planta(nombre="LEDESMA", factor_aceite_fruta=68.8416, factor_aceite_emulsion=3.45)


@pytest.fixture(scope="module")
def filas_excel() -> dict:
    """{n.º de fila: {encabezado: valor}} de la hoja BOE, con los valores que calculó Excel."""
    if not ARCHIVO.exists():
        pytest.skip(f"No está {ARCHIVO.name} (data/ no se sube a GitHub).")
    hoja = load_workbook(ARCHIVO, data_only=True, read_only=True)["BOE"]
    encabezado = None
    filas = {}
    for numero, valores in enumerate(hoja.iter_rows(min_row=3, max_row=max(FILAS), max_col=36,
                                                    values_only=True), start=3):
        if numero == 3:
            encabezado = [" ".join(c.split()) if c else c for c in valores]
        elif numero in FILAS:
            filas[numero] = dict(zip(encabezado, valores))
    return filas


@pytest.mark.parametrize("numero", FILAS)
def test_aceite_en_fruta_igual_que_excel(ledesma, filas_excel, numero):
    fila = filas_excel[numero]

    calculado = aceite_en_fruta(ledesma, fila["Vol [ml]2"], BLANCO_EXCEL, fila["N BrO3"], fila["Peso muestra [g]"])

    assert calculado == pytest.approx(fila["Aceite en fruta [kg/MT]"], abs=TOLERANCIA)


@pytest.mark.parametrize("numero", [10, 9000])
def test_aceite_en_emulsion_igual_que_excel(ledesma, filas_excel, numero):
    fila = filas_excel[numero]   # la emulsión usa la normalidad de la misma fila de Entrada

    calculado = aceite_en_emulsion(ledesma, fila["Vol [ml]"], BLANCO_EXCEL, fila["N BrO3"], fila["Muestra [g]"])

    assert calculado == pytest.approx(fila["Aceite [%]"], abs=TOLERANCIA)


@pytest.mark.parametrize("entrada, salida", PARES)
def test_eficiencia_y_rendimiento_igual_que_excel(ledesma, filas_excel, entrada, salida):
    fila_e, fila_s = filas_excel[entrada], filas_excel[salida]
    assert (fila_e["Lugar de muestreo"], fila_s["Lugar de muestreo"]) == ("Entrada", "Salida")
    aceite_e = aceite_en_fruta(ledesma, fila_e["Vol [ml]2"], BLANCO_EXCEL, fila_e["N BrO3"], fila_e["Peso muestra [g]"])
    aceite_s = aceite_en_fruta(ledesma, fila_s["Vol [ml]2"], BLANCO_EXCEL, fila_s["N BrO3"], fila_s["Peso muestra [g]"])

    assert eficiencia_eboe(aceite_e, aceite_s) == pytest.approx(fila_e["Eficiencia eBOE"], abs=TOLERANCIA)
    assert rendimiento_eboe(aceite_e, aceite_s) == pytest.approx(fila_e["Rend. eBOE [kg/MT]"], abs=TOLERANCIA)


# ---------------------------------------------------------------- con números inventados (corren siempre)

def test_formulas_con_numeros_inventados():
    planta = Planta(nombre="Prueba", factor_aceite_fruta=70.0, factor_aceite_emulsion=3.5)

    assert aceite_en_fruta(planta, 10.2, 0.2, 0.1, 10.0) == pytest.approx(7.0)        # 10 × 0,1 × 70 / 10
    assert aceite_en_emulsion(planta, 5.2, 0.2, 0.1, 2.5) == pytest.approx(0.7)       # 5 × 0,1 × 3,5 / 2,5
    assert eficiencia_eboe(8.0, 1.0) == pytest.approx(0.875)
    assert rendimiento_eboe(8.0, 1.0) == pytest.approx(7.0)


def test_valida_datos_crudos_y_parametros():
    planta = Planta(nombre="Prueba", factor_aceite_fruta=70.0)

    with pytest.raises(ValueError, match="peso de la muestra"):
        aceite_en_fruta(planta, 10.0, 0.2, 0.1, 0)
    with pytest.raises(ValueError, match="normalidad"):
        aceite_en_fruta(planta, 10.0, 0.2, 0, 10.0)
    with pytest.raises(ValueError, match="volumen"):
        aceite_en_fruta(planta, -1, 0.2, 0.1, 10.0)
    with pytest.raises(ParametrosIncompletos, match="factor de aceite en emulsión"):
        aceite_en_emulsion(planta, 5.0, 0.2, 0.1, 2.5)
    with pytest.raises(ValueError, match="entrada tiene que ser mayor que 0"):
        eficiencia_eboe(0, 1.0)
