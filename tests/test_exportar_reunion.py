"""Prueba de privacidad de scripts/exportar_reunion.py (nombres inventados): sin --con-nombres no aparece ningún
nombre de operador o químico en ningún archivo; con el flag, solo en los de las figuras de personas (19d, 20b y 20c),
nunca en indice.md ni en resumen.json."""

import importlib.util
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from core.models import AnalisisScott, LecturaOperacion
from tests.test_carga_scott import sesion  # noqa: F401  (fixture)
from tests.test_inicio import _muestra, _ronda, planta  # noqa: F401  (fixture)

OPERADORES, QUIMICOS, CENTRIFUGAS = ("Pérez", "Gómez"), ("Zulema", "Ofelia"), "Sosa"
NOMBRES = (*OPERADORES, *QUIMICOS, CENTRIFUGAS)


def _modulo():
    ruta = Path(__file__).resolve().parent.parent / "scripts" / "exportar_reunion.py"
    especificacion = importlib.util.spec_from_file_location("exportar_reunion", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def _texto(archivo: Path) -> str:
    """El texto de un archivo; de un Excel, el de todas sus partes (los textos van en sharedStrings.xml)."""
    if archivo.suffix == ".xlsx":
        with zipfile.ZipFile(archivo) as excel:
            return " ".join(excel.read(n).decode("utf-8", "ignore") for n in excel.namelist())
    return archivo.read_text(encoding="utf-8")


@pytest.fixture
def base_con_personas(sesion, planta):  # noqa: F811
    """40 rondas con operadores y químicos que rotan entre los eBOE, y muestreos de centrífugas con su operador."""
    inicio = datetime(2026, 7, 1, 6, 10)
    for k in range(40):
        _ronda(sesion, inicio + timedelta(hours=2 * k), salida_ml={1: 10.0 + k % 5, 2: 11.0 + k % 3})
    for lectura in sesion.scalars(select(LecturaOperacion)):
        lectura.operador = OPERADORES[(lectura.equipo.numero + lectura.fecha_hora.hour // 2) % 2]
    for analisis in sesion.scalars(select(AnalisisScott)):
        analisis.analista = QUIMICOS[(analisis.fecha_hora.hour // 2 + analisis.fecha_hora.day) % 2]
    sesion.commit()
    for k in range(3):
        momento = inicio + timedelta(hours=2 * k, minutes=20)
        _muestra(sesion, "N-CEN-01", momento, "emulsion", ml=20.0, analista=QUIMICOS[0], operador=CENTRIFUGAS)
    planta.omega_licor_objetivo_pct, planta.omega_fp_pct = 0.9, 0.1
    planta.caudal_aspersores_lh, planta.eficiencia_ref_rango_pct, planta.tolerancia_calibre_pct = 350.0, 85.0, 5.0
    sesion.commit()
    return sesion, planta


@pytest.mark.parametrize("con_nombres", [False, True])
def test_nombres_solo_con_el_flag_y_solo_en_las_figuras_de_personas(base_con_personas, tmp_path, con_nombres):
    sesion_, planta_ = base_con_personas
    figuras = _modulo().exportar(sesion_, planta_, tmp_path, con_nombres=con_nombres, png=False,
                                 avisar=lambda *_: None)
    archivos = sorted(tmp_path.iterdir())
    assert {"indice.md", "resumen.json"} <= {a.name for a in archivos}
    con_nombre = {a.name for a in archivos if any(n in _texto(a) for n in NOMBRES)}
    if not con_nombres:
        assert con_nombre == set()
        assert any(f.codigo == "20b" and f.motivo is None for f in figuras)       # la figura está, anonimizada
        assert "Operador A" in _texto(next(a for a in archivos if a.name.startswith("20b") and a.suffix == ".xlsx"))
    else:
        assert con_nombre and all(n.startswith(("19d", "20b", "20c")) for n in con_nombre)
        assert any(n.startswith("20b") for n in con_nombre)
