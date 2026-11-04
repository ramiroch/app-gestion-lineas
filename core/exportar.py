"""Archivos para descargar: Excel con tablas y ZIP con varios archivos.

Las tablas de las páginas muestran los números como texto con coma decimal ("1.234,50"); para que en
Excel sean números se convierten de vuelta (texto_a_numero), solo en las columnas donde todas las
celdas con dato se pueden convertir.
"""

import io
import re
import zipfile

import pandas as pd

_NUMERO = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$|^-?\d+(,\d+)?$")
_INVALIDOS_HOJA = re.compile(r"[\[\]:*?/\\]")


def texto_a_numero(texto):
    """"1.234,50" → 1234.5; "−3,2" → −3.2; "12 %" → 12.0. Si no es un número, devuelve None."""
    if not isinstance(texto, str):
        return None
    limpio = texto.strip().replace("−", "-").removesuffix("%").strip()
    if not _NUMERO.match(limpio):
        return None
    return float(limpio.replace(".", "").replace(",", "."))


def columnas_a_numeros(tabla: pd.DataFrame) -> pd.DataFrame:
    """Copia de la tabla con las columnas de texto numérico convertidas a números.

    Una columna se convierte solo si todas sus celdas con dato son números escritos con coma decimal
    ("—" y "" cuentan como vacías). Las demás quedan como están.
    """
    copia = tabla.copy()
    for columna in copia.columns:
        serie = copia[columna]
        if not (serie.dtype == object or pd.api.types.is_string_dtype(serie)):
            continue
        vacias = serie.isna() | serie.isin(["", "—"])
        convertidas = serie.map(texto_a_numero)
        if (~vacias).any() and convertidas[~vacias].notna().all():
            copia[columna] = convertidas.where(~vacias)
    return copia


def _nombre_hoja(nombre: str, usados: set) -> str:
    """Nombre válido para una hoja de Excel: sin caracteres prohibidos, hasta 31 y sin repetir."""
    base = _INVALIDOS_HOJA.sub(" ", nombre).strip()[:31] or "Tabla"
    candidato, numero = base, 2
    while candidato.lower() in usados:
        sufijo = f" ({numero})"
        candidato, numero = base[:31 - len(sufijo)] + sufijo, numero + 1
    usados.add(candidato.lower())
    return candidato


def excel_de_tablas(tablas: dict[str, pd.DataFrame]) -> bytes:
    """Un Excel (.xlsx) con una hoja por tabla, con los números como números."""
    salida, usados = io.BytesIO(), set()
    with pd.ExcelWriter(salida, engine="openpyxl") as escritor:
        for nombre, tabla in tablas.items():
            columnas_a_numeros(tabla).to_excel(escritor, sheet_name=_nombre_hoja(nombre, usados), index=False)
    return salida.getvalue()


def zip_de_archivos(archivos: dict[str, bytes]) -> bytes:
    """Un ZIP con los archivos dados ({nombre: contenido})."""
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as archivo_zip:
        for nombre, contenido in archivos.items():
            archivo_zip.writestr(nombre, contenido)
    return salida.getvalue()


def nombre_de_archivo(texto: str) -> str:
    """"Aceite en fruta por eBOE (kg/MT)" → "aceite_en_fruta_por_eboe_kg_mt", para nombres de archivo."""
    reemplazos = str.maketrans("áéíóúñü", "aeiounu")
    limpio = re.sub(r"[^a-z0-9]+", "_", texto.lower().translate(reemplazos))
    return limpio.strip("_") or "archivo"
