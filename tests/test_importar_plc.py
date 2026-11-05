"""Pruebas de core/importar_plc.py con CSV inventados."""

from datetime import datetime

import pytest
from sqlalchemy import select

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.importar_plc import (guardar_reporte, importar_plc, leer_carpeta, leer_fecha, leer_fila, limpiar_programa,
                               texto_resumen)
from core.models import Cliente, Equipo, LecturaPLC, Linea, Planta


def fila(fecha: str, *eboes) -> str:
    """Una fila del CSV; cada eBOE es (horas, minutos, elevador, programa, ton seteadas, ton calculadas)."""
    eboes = eboes or [(31549, 29, 1, "Programa N1", 12, 10.8)] * 3
    return ",".join([fecha, *(f"{h:.6f},{m:.6f},{e},{p},{s:.6f},{c:.6f}" for h, m, e, p, s, c in eboes)])


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        planta = Planta(nombre="LEDESMA", pais="Argentina", cliente=Cliente(nombre="LEDESMA", pais="Argentina"))
        linea = Linea(nombre="Línea 1", planta=planta)
        for n in (1, 2, 3):
            Equipo(codigo=f"LED-BOE-0{n}", nombre=f"eBOE {n}", tipo="BOE", numero=n, linea=linea)
        sesion.add(planta)
        sesion.commit()
        yield sesion


def test_fechas_y_programas():
    assert leer_fecha("6/7/2026 9:05:01") == datetime(2026, 7, 6, 9, 5, 1)          # sin ceros adelante
    assert leer_fecha("06/07/2026 10:05:00") == datetime(2026, 7, 6, 10, 5)          # con ceros
    assert leer_fecha("13/10/2026 0:00") == datetime(2026, 10, 13, 0, 0)            # día > 12: día/mes
    with pytest.raises(ValueError, match="formato desconocido"):
        leer_fecha("2026-07-06 10:00")
    with pytest.raises(ValueError, match="imposible"):
        leer_fecha("31/02/2026 10:00")

    assert limpiar_programa("          Programa N3") == "N3"
    assert limpiar_programa("Programa 9") == "9" and limpiar_programa("  ") is None


def test_leer_fila():
    fecha, eboes = leer_fila(fila("1/7/2026 5:00:01", (100, 5, 1, " Programa N3", 12, 11.4), (200, 0, 0, "Programa 9", 10, 0),
                                  (300, 59, 1, "Programa N0", 11, 10.8)).split(","))
    assert fecha == datetime(2026, 7, 1, 5, 0, 1)
    assert eboes[1] == {"horas_maquina": 100, "minutos": 5, "elevador": 1, "programa": "N3", "ton_seteadas": 12,
                        "ton_calculadas": 11.4}
    assert eboes[2]["programa"] == "9" and eboes[2]["elevador"] == 0
    with pytest.raises(ValueError, match="19"):
        leer_fila(["1/7/2026 5:00"])
    with pytest.raises(ValueError, match="elevador tiene que ser 0 o 1"):
        leer_fila(fila("1/7/2026 5:00", *[(1, 1, 2, "Programa N1", 1, 1)] * 3).split(","))
    with pytest.raises(ValueError, match="no puede ser negativo"):
        leer_fila(fila("1/7/2026 5:00", *[(1, 1, 1, "Programa N1", 1, -1)] * 3).split(","))


def _carpeta(tmp_path):
    """Dos archivos: uno con un reseteo de contador, una fila mala y una repetida idéntica en el otro."""
    (tmp_path / "LedesmaA.csv").write_text("\n".join([
        fila("1/7/2026 5:00:01"),
        fila("1/7/2026 5:05:01", *[(31549, 34, 1, "   Programa N2", 12, 12.0)] * 3),
        fila("01/07/2026 05:10:01", *[(0, 0, 1, "Programa N2", 12, 12.0)] * 3),            # reseteo del contador
        "1/7/2026 5:15:01,basura",
    ]), encoding="utf-8")
    (tmp_path / "LedesmaB.csv").write_text("\n".join([
        fila("1/7/2026 5:00:01"),                                                          # idéntica a la de A
        fila("2/7/2026 5:00:01", *[(31560, 0, 0, "Programa 9", 10, 0.0)] * 3),
    ]), encoding="utf-8")
    return tmp_path


def test_leer_carpeta_y_repetidas(tmp_path):
    lecturas, reporte = leer_carpeta(_carpeta(tmp_path))
    assert len(lecturas) == 4 * 3                                   # 4 momentos distintos × 3 eBOE
    tipos = sorted((archivo, numero, tipo) for archivo, numero, tipo, _ in reporte)
    assert tipos == [("LedesmaA.csv", 4, "rechazada"), ("LedesmaB.csv", 1, "aviso"), ("LedesmaB.csv", 1, "aviso"),
                     ("LedesmaB.csv", 1, "aviso")]

    (tmp_path / "LedesmaC.csv").write_text(fila("2/7/2026 5:00:01"), encoding="utf-8")    # mismo momento, otros datos
    lecturas, reporte = leer_carpeta(tmp_path)
    assert not any(l["fecha_hora"] == datetime(2026, 7, 2, 5, 0, 1) for l in lecturas)
    assert sum(1 for f in reporte if "datos distintos" in f[3]) == 6                       # 2 filas × 3 eBOE
    with pytest.raises(FileNotFoundError):
        leer_carpeta(tmp_path / "vacia")


def test_importar_dos_veces_no_duplica(sesion, tmp_path):
    carpeta = _carpeta(tmp_path)
    resumen, reporte = importar_plc(sesion, carpeta)
    assert (resumen["nuevas"], resumen["actualizadas"], resumen["rechazadas"]) == (12, 0, 1)
    assert resumen["por_eboe"][2] == {"filas": 4, "desde": datetime(2026, 7, 1, 5, 0, 1),
                                      "hasta": datetime(2026, 7, 2, 5, 0, 1)}
    lectura = sesion.scalars(select(LecturaPLC).join(Equipo).where(
        Equipo.codigo == "LED-BOE-01", LecturaPLC.fecha_hora == datetime(2026, 7, 2, 5, 0, 1))).one()
    assert (lectura.programa, lectura.elevador, lectura.origen) == ("9", 0, "LedesmaB.csv › fila 2")

    resumen, _ = importar_plc(sesion, carpeta)
    assert (resumen["nuevas"], resumen["actualizadas"], resumen["sin_cambios"]) == (0, 0, 12)
    assert len(sesion.scalars(select(LecturaPLC)).all()) == 12

    (carpeta / "LedesmaB.csv").write_text(fila("2/7/2026 5:00:01", *[(31560, 0, 1, "Programa N4", 10, 9.5)] * 3),
                                          encoding="utf-8")
    resumen, _ = importar_plc(sesion, carpeta)
    assert resumen["actualizadas"] == 3
    sesion.expire_all()
    assert lectura.programa == "N4" and lectura.ton_calculadas == 9.5
    texto = texto_resumen(resumen, guardar_reporte(reporte, tmp_path / "reporte.csv"))
    assert "eBOE 1: 4 lecturas en la base, del 01/07/2026 05:00 al 02/07/2026 05:00" in texto


def test_sin_equipos_da_error_claro(tmp_path):
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as vacia, pytest.raises(ValueError, match="Faltan los equipos LED-BOE-01"):
        importar_plc(vacia, _carpeta(tmp_path))
