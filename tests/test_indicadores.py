"""Pruebas de core/indicadores.py.

La prueba con la base real compara contra la tabla de la sección 7 de docs/mapeo_ledesma.md,
que se lee en el momento (así los números del cliente no quedan copiados en el código).
Si no está data/app.db o no tiene LEDESMA, se saltea.
"""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from core.crear_base import crear_tablas
from core.db import RUTA_BASE, crear_motor, crear_sesion
from core.indicadores import resumen_eboe_por_temporada
from core.models import AnalisisScott, Cliente, Equipo, Linea, Planta, Proceso, PuntoMuestreo

DOC = Path(__file__).resolve().parent.parent / "docs" / "mapeo_ledesma.md"
TOLERANCIA_MEDIAS = 0.1      # puntos de eficiencia; kg/MT en rendimiento y aceite
TOLERANCIA_PORCENTAJE = 0.5  # puntos: la tabla redondea el % de pares < 80 % a enteros
TOLERANCIA_PARES = 5


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        yield sesion


def _analisis(punto, proceso, fecha_hora, vol_ml):
    return AnalisisScott(punto_muestreo=punto, proceso=proceso, fecha_hora=fecha_hora, analista="Prueba",
                         peso_muestra_g=10.0, vol_ml=vol_ml, blanco_ml=0.0, normalidad=0.1,
                         cargado_por="prueba")


def test_resumen_con_numeros_inventados(sesion):
    # factor 70, blanco 0, N 0,1 y 10 g → aceite = vol × 0,7 kg/MT
    planta = Planta(nombre="Planta Norte", pais="Argentina", factor_aceite_fruta=70.0,
                    cliente=Cliente(nombre="CLIENTE A", pais="Argentina"))
    equipo = Equipo(codigo="PN-BOE-01", nombre="eBOE 1", tipo="BOE", numero=1,
                    linea=Linea(nombre="Línea 1", planta=planta))
    entrada, salida, emulsion = (PuntoMuestreo(tipo=t, nombre=t, equipo=equipo)
                                 for t in ("fruta entrada", "fruta salida", "emulsión"))
    p2025, p2026 = Proceso(numero=1, temporada=2025, planta=planta), Proceso(numero=1, temporada=2026, planta=planta)
    h1, h2, h3 = datetime(2025, 6, 1, 8), datetime(2025, 6, 1, 10), datetime(2025, 6, 1, 12)
    sesion.add_all([
        planta,
        _analisis(entrada, p2025, h1, 10.0), _analisis(salida, p2025, h1, 1.0),    # 7 y 0,7 → 90 %
        _analisis(entrada, p2025, h2, 10.0), _analisis(salida, p2025, h2, 3.0),    # 7 y 2,1 → 70 %
        _analisis(emulsion, p2025, h2, 5.0),                                        # no es fruta: no cuenta
        _analisis(entrada, p2025, h3, 10.0),                                        # sin salida: no es par
        _analisis(entrada, p2026, datetime(2026, 6, 1, 8), 12.0),
        _analisis(salida, p2026, datetime(2026, 6, 1, 8), 1.2),                    # 8,4 y 0,84 → 90 %
    ])
    sesion.commit()

    resumen = resumen_eboe_por_temporada(sesion, planta.id, eficiencia_minima=0.80)

    assert [(r["temporada"], r["eboe"], r["pares"]) for r in resumen] == [(2025, 1, 2), (2026, 1, 1)]
    t2025 = resumen[0]
    assert t2025["codigo"] == "PN-BOE-01"
    assert t2025["eficiencia_media"] == pytest.approx(0.80)
    assert t2025["rendimiento_medio"] == pytest.approx((6.3 + 4.9) / 2)
    assert t2025["aceite_entrada_medio"] == pytest.approx(7.0)
    assert t2025["fraccion_bajo_minimo"] == pytest.approx(0.5)
    assert t2025["descartados"] == 0


def test_valida_entradas(sesion):
    with pytest.raises(ValueError, match="fracción entre 0 y 1"):
        resumen_eboe_por_temporada(sesion, 1, eficiencia_minima=80)
    with pytest.raises(ValueError, match="No existe la planta"):
        resumen_eboe_por_temporada(sesion, 999, eficiencia_minima=0.80)


# ---------------------------------------------------------------- con la base real (si existe)

def _numero(texto: str) -> float:
    """'85,3 %' → 85.3 ; '7,01' → 7.01"""
    return float(texto.replace("%", "").replace(",", ".").strip())


def _tabla_seccion_7() -> dict:
    """{(temporada, eboe): {pares, eficiencia, rendimiento, aceite, bajo_80}} leída del doc."""
    lineas = DOC.read_text(encoding="utf-8").split("\n## 7.")[1].split("\n## ")[0].splitlines()
    tabla = {}
    for linea in lineas:
        celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
        if len(celdas) == 7 and celdas[0].isdigit():
            temporada, eboe, pares, eficiencia, rendimiento, aceite, bajo_80 = celdas
            tabla[(int(temporada), int(eboe))] = {
                "pares": int(pares), "eficiencia": _numero(eficiencia), "rendimiento": _numero(rendimiento),
                "aceite": _numero(aceite), "bajo_80": _numero(bajo_80)}
    return tabla


@pytest.fixture(scope="module")
def resumen_real() -> dict:
    if not RUTA_BASE.exists():
        pytest.skip("No está data/app.db (data/ no se sube a GitHub).")
    with crear_sesion(crear_motor()) as sesion:
        planta = sesion.scalars(select(Planta).where(Planta.nombre == "LEDESMA")).first()
        if planta is None:
            pytest.skip("La base no tiene la planta LEDESMA.")
        filas = resumen_eboe_por_temporada(sesion, planta.id, eficiencia_minima=0.80)
    return {(r["temporada"], r["eboe"]): r for r in filas}


ESPERADO = _tabla_seccion_7() if DOC.exists() else {}


def test_la_tabla_del_doc_tiene_seis_filas():
    assert len(ESPERADO) == 6


@pytest.mark.parametrize("clave", sorted(ESPERADO), ids=lambda c: f"{c[0]}-eBOE{c[1]}")
def test_igual_que_la_seccion_7(resumen_real, clave):
    esperado, calculado = ESPERADO[clave], resumen_real.get(clave)
    assert calculado is not None, f"La base no tiene pares de {clave}."

    assert abs(calculado["pares"] - esperado["pares"]) <= TOLERANCIA_PARES
    assert calculado["eficiencia_media"] * 100 == pytest.approx(esperado["eficiencia"], abs=TOLERANCIA_MEDIAS)
    assert calculado["rendimiento_medio"] == pytest.approx(esperado["rendimiento"], abs=TOLERANCIA_MEDIAS)
    assert calculado["aceite_entrada_medio"] == pytest.approx(esperado["aceite"], abs=TOLERANCIA_MEDIAS)
    assert calculado["fraccion_bajo_minimo"] * 100 == pytest.approx(esperado["bajo_80"], abs=TOLERANCIA_PORCENTAJE)
