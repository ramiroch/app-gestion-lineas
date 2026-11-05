"""Pruebas de core/operacion.py con un CSV del PLC inventado (leído con el importador)."""

from datetime import datetime, time, timedelta

import pandas as pd
import pytest

from core.importar_plc import leer_carpeta
from core.models import Planta, Turno
from core.operacion import (NOTA_TON, carga, especie, filtrar_plc, frase_carga, frase_horas, frase_lab_plc, frase_marcha, horas_operacion,
                            horometro, laboratorio_vs_plc, marcha_y_paradas, preparar_plc, texto_horas,
                            uso_programas_plc, ventana_temporada)
from core.analisis import Filtros

DIA = datetime(2026, 7, 1)


def _planta() -> Planta:
    planta = Planta(nombre="LEDESMA", pais="Argentina", hora_inicio_dia=time(5, 0))
    planta.turnos = [Turno(nombre="M", hora_inicio=time(5), hora_fin=time(12, 59)),
                     Turno(nombre="T", hora_inicio=time(13), hora_fin=time(20, 59)),
                     Turno(nombre="N", hora_inicio=time(21), hora_fin=time(4, 59))]
    return planta


def _texto_fecha(momento: datetime, k: int) -> str:
    """Una fila con ceros adelante y la siguiente sin ceros (como los CSV reales)."""
    if k % 2:
        return f"{momento.day}/{momento.month}/{momento.year} {momento.hour}:{momento.minute:02d}:01"
    return f"{momento:%d/%m/%Y %H:%M}:01"


def _csv(carpeta):
    """Un día cada 5 min desde las 05:00. Los tres eBOE andan de 06:00 a 10:00 (48 lecturas) con N3 y 12 t/h.
    - eBOE 1: las TON calculadas caen a 6 entre 08:00 y 09:00 (por debajo del 80 % del setpoint).
    - eBOE 2: el horómetro se resetea a 0 a las 08:00 y sigue contando; una parada de 07:00 a 07:30.
    - eBOE 3: elevador en marcha sin fruta (0 t calculadas) de 09:00 a 10:00; programa con espacios.
    """
    contadores = {1: 1000 * 60, 2: 2000 * 60, 3: 500 * 60}                 # en minutos
    filas = []
    for k in range(24 * 12):
        momento = DIA + timedelta(hours=5, minutes=5 * k)
        hora = momento.hour + momento.minute / 60
        columnas = [_texto_fecha(momento, k)]
        for eboe in (1, 2, 3):
            marcha = 6 <= hora < 10 and not (eboe == 2 and 7 <= hora < 7.5)
            if marcha:
                contadores[eboe] += 5
            if eboe == 2 and momento.hour == 8 and momento.minute == 0:
                contadores[2] = 0                                           # reseteo
            calculadas = 0.0 if not marcha else 6.0 if (eboe == 1 and 8 <= hora < 9) else 0.0 if (
                eboe == 3 and hora >= 9) else 12.0
            programa = "      Programa N3" if eboe == 3 else "Programa N3"
            columnas.append(f"{contadores[eboe] // 60}.000000,{contadores[eboe] % 60}.000000,{int(marcha)},{programa},"
                            f"12.000000,{calculadas:.6f}")
        filas.append(",".join(columnas))
    (carpeta / "Ledesma712026.csv").write_text("\n".join(filas), encoding="utf-8")
    lecturas, reporte = leer_carpeta(carpeta)
    assert not reporte
    return pd.DataFrame(lecturas).rename(columns={})


@pytest.fixture
def plc(tmp_path):
    return preparar_plc(_csv(tmp_path).assign(equipo_id=lambda d: d["eboe"]), _planta())


def test_preparar_y_filtrar(plc):
    fila = plc[(plc["eboe"] == 3) & (plc["fecha_hora"] == DIA + timedelta(hours=6, minutes=5, seconds=1))].iloc[0]
    assert fila["programa"] == "N3" and fila["turno"] == "M" and fila["fecha_produccion"] == pd.Timestamp(DIA)
    madrugada = plc[plc["fecha_hora"] >= DIA + timedelta(days=1)]
    assert (madrugada["turno"] == "N").all() and (madrugada["fecha_produccion"] == pd.Timestamp(DIA)).all()
    assert plc["duracion_h"].iloc[0] == pytest.approx(5 / 60)
    assert len(filtrar_plc(plc, Filtros(eboes=[2], turnos=["M"]))) == 8 * 12
    assert especie("N Val S/R") == "naranja" and especie("L Eureka") == "limón" and especie(None) is None


def test_horas_de_operacion_con_reseteo(plc):
    tabla = horas_operacion(plc, DIA + timedelta(hours=5, minutes=30), DIA + timedelta(days=1, hours=5))
    tabla = tabla.set_index("eboe")
    uno = tabla.loc[1]
    assert uno["inicio"] == DIA + timedelta(hours=6, minutes=0, seconds=1)          # primera variación del horómetro
    assert (uno["contador_inicio"], uno["horas"], uno["suma_incrementos"]) == pytest.approx((1000, 4, 4))
    assert (uno["horas_elevador"], uno["horas_con_fruta"], uno["horas_sin_fruta"]) == pytest.approx((4, 4, 0))
    assert uno["horas_periodo"] == pytest.approx(23 - 1 / 60 / 60)                     # 06:00:01 → 05:00 del día 2
    assert tabla.loc[3, "horas_sin_fruta"] == pytest.approx(1)

    dos = tabla.loc[2]
    assert dos["reseteos"] == 1 and dos["saltos"] == 0
    assert dos["horas"] < 0                                    # el contador bajó: la diferencia no sirve…
    assert dos["suma_incrementos"] == pytest.approx(3.5 - 5 / 60)      # …la suma de incrementos sí (sin el de 08:00)
    frase = frase_horas(tabla.reset_index(), "naranja", 2026)
    assert "Ojo: el horómetro tiene reseteos o saltos en el eBOE 2" in frase
    assert texto_horas(31549.4833) == "31.549 h 29 min"


def test_frase_horas_y_horometro(plc):
    tabla = pd.DataFrame({"eboe": [1, 2, 3], "horas": [2398.4, 2408.4, 2382.0], "utilizacion": [85.0, 85.4, 84.4],
                          "contador_fin": [31777.1, 32949.1, 14976.1], "reseteos": 0, "saltos": 0})
    assert frase_horas(tabla, "naranja", 2026) == (
        "En naranja 2026 los tres eBOE trabajaron entre 2.382 y 2.408 h (85 % del período). El eBOE 3 acumula "
        "14.976 h contra más de 31.000 de los otros dos.")
    assert "No hay lecturas" in frase_horas(tabla.iloc[0:0], "naranja", 2026)

    acumulado, diario = horometro(plc)
    assert acumulado.set_index("eboe").loc[1, "horas"] == pytest.approx(1004)
    assert len(diario) == 3 and set(diario["fecha_produccion"]) == {pd.Timestamp(DIA)}


def test_ventana_de_la_temporada():
    analisis = pd.DataFrame({"fruta": ["N Val S/R", "N Val C/R", "L Eureka"], "temporada": [2026, 2026, 2026],
                             "fecha_hora": pd.to_datetime(["2026-07-01 15:15", "2026-10-26 07:10", "2026-04-01 10:00"]),
                             "fecha_produccion": pd.to_datetime(["2026-07-01", "2026-10-26", "2026-04-01"])})
    assert ventana_temporada(analisis, "naranja", 2026, time(5)) == (pd.Timestamp("2026-07-01 15:15"),
                                                                     pd.Timestamp("2026-10-27 05:00"))
    assert ventana_temporada(analisis, "naranja", 2025, time(5)) is None


def test_marcha_paradas_carga_y_programas(plc):
    marcha = marcha_y_paradas(plc).set_index("eboe")
    assert marcha.loc[1, "pct_marcha"] == pytest.approx(100 * 4 / 24)
    assert marcha.loc[1, "paradas"] == 2 and marcha.loc[2, "paradas"] == 3          # antes, después y la de 07:00
    assert marcha.loc[2, "dias"] == 1 and marcha.loc[2, "paradas_dia"] == 3
    assert frase_marcha(marcha.reset_index()).startswith("Los eBOE estuvieron en marcha el 15–17 % del tiempo "
                                                         "registrado, con 2,0–3,0 paradas por día")

    tabla = carga(plc).set_index("eboe")
    assert tabla.loc[1, "seteadas"] == pytest.approx(12) and tabla.loc[1, "calculadas"] == pytest.approx(10.5)
    assert tabla.loc[1, "pct_bajo"] == pytest.approx(25) and tabla.loc[2, "pct_bajo"] == 0
    assert tabla.loc[2, "variacion_hora"] == pytest.approx(0) and tabla.loc[1, "relacion"] == pytest.approx(10.5 / 12)
    assert frase_carga(tabla.reset_index()).endswith("pasa el 25 % del tiempo por debajo del 80 % del setpoint.")
    assert "no son un peso real" in NOTA_TON

    usos = uso_programas_plc(plc)
    assert usos["programa"].unique().tolist() == ["N3"] and usos["pct"].eq(100).all()


def test_laboratorio_vs_plc():
    analisis = pd.DataFrame({
        "lugar": ["Entrada"] * 4 + ["Salida"], "programa": ["N3", "N3", "N4", "N5", "N3"],
        "programa_plc": ["N3", "N3", "N5", None, "N9"], "tph": [12.0, 11.0, 12.0, 10.0, 12.0],
        "ton_set_plc": [12.0, 11.0, 13.0, 10.0, 1.0], "ton_calc_plc": [10.8, 0.0, 12.0, 9.0, 1.0]})
    resultado = laboratorio_vs_plc(analisis)
    assert resultado["n"] == 3 and resultado["pct_programa"] == pytest.approx(200 / 3)
    assert resultado["cruzada"].loc["N4", "N5"] == 1 and resultado["cruzada"].loc["N3", "N3"] == 2
    assert resultado["pct_set"] == 75 and resultado["dif_set"] == pytest.approx(-0.25)
    assert resultado["dif_calc"] == pytest.approx((1.2 + 0 + 1.0) / 3)               # sin la de 0 t calculadas
    assert frase_lab_plc(resultado) == ("La Ton/H del laboratorio es el setpoint (coincide en el 75 %); el programa "
                                        "anotado coincide con el del PLC en el 67 %.")
