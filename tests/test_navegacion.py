"""Pruebas del menú (app/navegacion.py), del estilo (app/estilo.py) y del punto de entrada con st.navigation
(app/app.py): todas las páginas en el menú, las secciones y títulos, los enlaces entre páginas y la app corriendo."""

import re
import sys
from datetime import date
from pathlib import Path

import pytest

CARPETA_APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(CARPETA_APP))

from estilo import LOGO_BROWN, PIE, encabezado_cobrand, titulo_planta  # noqa: E402
from navegacion import INICIO, MENU  # noqa: E402


def _rutas():
    return [ruta for lista in MENU.values() for ruta, _, _, _ in lista]


def test_menu_con_todas_las_paginas_y_sus_secciones():
    paginas = sorted(f"pages/{p.name}" for p in (CARPETA_APP / "pages").glob("*.py"))
    assert sorted(_rutas()) == sorted(paginas + [INICIO]) and len(set(_rutas())) == len(_rutas())   # una vez cada una
    assert all((CARPETA_APP / ruta).exists() for ruta in _rutas())
    assert {seccion: [titulo for _, titulo, _, _ in lista] for seccion, lista in MENU.items()} == {
        "Planta": ["Inicio", "Planta"],
        "Análisis": ["Análisis", "Gráficos de control", "Laboratorio", "Operación", "Centrífugas", "Sistema"],
        "Carga diaria": ["Carga Scott", "Datos", "Producción"],
        "Gestión": ["Alertas"]}
    for lista in MENU.values():
        for ruta, _, icono, direccion in lista:
            assert icono.startswith(":material/")
            if ruta != INICIO:                       # la misma dirección que antes: /Analisis, /Control…
                assert direccion == re.sub(r"^\d+_", "", Path(ruta).stem)


def test_los_enlaces_entre_paginas_apuntan_al_menu():
    enlaces = set()
    for archivo in [*CARPETA_APP.glob("*.py"), *(CARPETA_APP / "pages").glob("*.py")]:
        enlaces |= set(re.findall(r'"(pages/[\w]+\.py)"', archivo.read_text(encoding="utf-8")))
    assert enlaces and enlaces <= set(_rutas())


def test_encabezado_cobrand():
    sin_logo = encabezado_cobrand(None, "LEDESMA", "Gestión de líneas de aceite y jugo", "Período: 06/11/2026")
    assert '<span class="cobrand-planta">LEDESMA</span>' in sin_logo         # sin logo de la planta: el nombre
    assert sin_logo.count("data:image/png;base64,") == 1 and 'alt="Brown"' in sin_logo
    assert "Gestión de líneas de aceite y jugo" in sin_logo and "Período: 06/11/2026" in sin_logo
    con_logo = encabezado_cobrand(LOGO_BROWN, "Planta <X>", "T", "")
    assert con_logo.count("data:image/png;base64,") == 2 and 'alt="Planta &lt;X&gt;"' in con_logo
    assert "&lt;b&gt;" in titulo_planta("<b>") and PIE == "Brown · A Gulftech family brand"


@pytest.fixture
def app_vacia(tmp_path, monkeypatch):
    """La app con una base vacía (APP_DB), corriendo desde app/ como con streamlit run."""
    from core.crear_base import crear_tablas
    from core.db import crear_motor
    base = tmp_path / "vacia.db"
    crear_tablas(crear_motor(f"sqlite:///{base.as_posix()}"))
    monkeypatch.setenv("APP_DB", str(base))
    monkeypatch.chdir(CARPETA_APP)
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(CARPETA_APP / "app.py"), default_timeout=120)


def test_la_app_arranca_en_el_inicio_y_cambia_de_pagina(app_vacia):
    from core.cartas_periodo import parametros_control
    app = app_vacia.run()
    assert not app.exception
    assert any("No hay plantas cargadas" in w.value for w in app.warning)      # el Inicio, con la base vacía
    assert [t.value for t in app.title] == ["Gestión de líneas de aceite y jugo"]
    app.switch_page("pages/3_Control.py")                                       # la misma ruta que usa el flowsheet
    for clave, valor in parametros_control("eficiencia", "N-BOE-02", date(2026, 7, 1), date(2026, 7, 2), None).items():
        app.query_params[clave] = valor
    app.run()
    assert not app.exception
    assert any("Gráficos de control" in t.value for t in app.title)
