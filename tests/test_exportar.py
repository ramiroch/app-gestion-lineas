"""Pruebas de core/exportar.py."""

import io
import zipfile

import pandas as pd
import pytest
from openpyxl import load_workbook

from core.exportar import (columnas_a_numeros, excel_de_tablas, nombre_de_archivo, texto_a_numero,
                           zip_de_archivos)


def test_texto_a_numero():
    assert texto_a_numero("1.234,50") == pytest.approx(1234.5)
    assert texto_a_numero("8,40") == pytest.approx(8.4)
    assert texto_a_numero("−3,2") == pytest.approx(-3.2)
    assert texto_a_numero("12 %") == pytest.approx(12.0)
    assert texto_a_numero("2.422") == pytest.approx(2422)
    assert texto_a_numero("8,1 a 8,4") is None and texto_a_numero("N4") is None and texto_a_numero(3) is None


def test_columnas_a_numeros():
    tabla = pd.DataFrame({"eBOE": ["eBOE 1", "eBOE 2"], "Media": ["8,40", "—"], "IC": ["8,1 a 8,4", "1,2 a 1,3"],
                          "N": [812, 790]})

    convertida = columnas_a_numeros(tabla)

    assert convertida["Media"].iloc[0] == pytest.approx(8.4) and pd.isna(convertida["Media"].iloc[1])
    assert convertida["IC"].tolist() == ["8,1 a 8,4", "1,2 a 1,3"]          # no es un número: queda texto
    assert convertida["eBOE"].tolist() == ["eBOE 1", "eBOE 2"] and convertida["N"].tolist() == [812, 790]
    assert tabla["Media"].iloc[0] == "8,40"                                  # no cambia la original


def test_excel_de_tablas_hojas_y_numeros():
    tablas = {"Estadística de Salida (kg/MT)": pd.DataFrame({"Media": ["1,46", "1,82"]}),
              "Límites IQR": pd.DataFrame({"Q1": [1.0]}),
              "Estadística de Salida (kg/MT) otra": pd.DataFrame({"x": [1]})}

    libro = load_workbook(io.BytesIO(excel_de_tablas(tablas)))

    assert libro.sheetnames == ["Estadística de Salida (kg MT)", "Límites IQR", "Estadística de Salida (kg MT) o"]
    hoja = libro["Estadística de Salida (kg MT)"]
    assert hoja["A1"].value == "Media" and hoja["A2"].value == pytest.approx(1.46)


def test_excel_nombres_de_hoja_repetidos():
    tablas = {"Ranking eBOE 1": pd.DataFrame({"a": [1]}), "ranking ebOE 1": pd.DataFrame({"a": [2]})}

    assert load_workbook(io.BytesIO(excel_de_tablas(tablas))).sheetnames == ["Ranking eBOE 1", "ranking ebOE 1 (2)"]


def test_zip_de_archivos():
    contenido = zip_de_archivos({"a.png": b"\x89PNG", "tablas.xlsx": b"xlsx"})

    with zipfile.ZipFile(io.BytesIO(contenido)) as archivo_zip:
        assert archivo_zip.namelist() == ["a.png", "tablas.xlsx"]
        assert archivo_zip.read("a.png") == b"\x89PNG"


def test_nombre_de_archivo():
    assert nombre_de_archivo("Aceite en fruta por eBOE (kg/MT)") == "aceite_en_fruta_por_eboe_kg_mt"
    assert nombre_de_archivo("Límites de outliers: ¿IQR?") == "limites_de_outliers_iqr"
    assert nombre_de_archivo("—") == "archivo"
