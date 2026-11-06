"""Pruebas de core/importar_centrifugas.py con una planilla chica inventada (misma estructura que la real)."""

from datetime import date, datetime, time

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from core.crear_base import crear_tablas
from core.db import crear_motor, crear_sesion
from core.importar_centrifugas import (REVISAR_COPIADA, REVISAR_DIA, REVISAR_ESB_PRIMARIA, REVISAR_FECHA,
                                       REVISAR_FECHA_CORREGIDA, REVISAR_FISICO,
                                       REVISAR_NORMALIDAD, REVISAR_NORMALIDAD_PESADA, REVISAR_PEGADO,
                                       REVISAR_SIN_LIVIANA, importar_centrifugas, leer_solidos, normalizar_equipo,
                                       normalizar_fase, texto_resumen)
from core.models import Cliente, Equipo, Linea, MuestraCentrifuga, Planta
from core.turnos import fecha_produccion

ENCABEZADO = {"A": "PROCESO", "B": "Día", "C": "Especie", "D": "Fecha", "E": "Hora", "F": "Químico", "G": "Operador",
              "H": "Turno", "I": "N\n0,1", "J": "N\n0,01", "K": "Fase", "L": "1° Centrifuga", "M": "1°\nCaudal\n[l/h]",
              "N": "1° Muestra [g]", "O": "1° Vol [ml]", "P": "1° Aceite [%]", "Q": "1° Tiempo descarga [s]",
              "R": "1° Sólidos Centrífugables [%]", "S": "1° CP Fase Liviana [Bar]", "T": "1° CP Fase Pesada [Bar]",
              "U": "1° Eficiencia", "V": "1° Aceite [kg/h]", "W": "Observaciones", "X": "2° Centrifuga",
              "Y": "2° Caudal", "Z": "2° Muestra [g]", "AA": "2° Vol [ml]", "AB": "2° Aceite [%]",
              "AC": "2° Tiempo descarga [s]", "AD": "2° CP Fase Liviana [Bar]", "AE": "2° CP Fase Pesada [Bar]",
              "AF": "2° Eficiencia", "AG": "2° Aceite [kg/h]", "AH": "3° Tiempo descarga [min]",
              "AI": "3° CP Fase Liviana [Bar]"}
FASES = ("Emulsion", "F Livina", "F Pesada", "Descarga")
DIA_1 = datetime(2025, 6, 24)
NORMAL = {"emulsion": {"M": 15000, "N": 3.5, "O": 7.0, "R": 0.5},       # aceite 0,680 %
          "liviana": {"P": 75, "S": 0.8},
          "pesada": {"N": 2.5, "O": 3.0, "T": 2.5},                       # aceite 0,040 % (N 0,01)
          "descarga": {"N": 9.0, "O": 2.0, "Q": 500}}                     # aceite 0,073 %


def _aceite(ml, n, g):
    return (ml - 0.1) * n * 3.45 / g


def bloque(hora, equipo="ESE 230 (1)", fecha=DIA_1, proceso=1, dia=1, cambios=None, fases=FASES, extra=None):
    """4 filas {letra: valor}. cambios y extra = {"emulsion": {...}, ...}: se aplican sobre NORMAL (extra, al final)."""
    filas = []
    for clave, fase in zip(("emulsion", "liviana", "pesada", "descarga"), fases):
        valores = dict(NORMAL[clave]) | (cambios or {}).get(clave, {}) | (extra or {}).get(clave, {})
        filas.append({"A": proceso, "B": dia, "C": "N Val S/R", "D": fecha, "E": hora, "F": "perez", "G": "gomez",
                      "I": 0.1, "J": 0.01, "K": fase, "L": equipo} | {k: v for k, v in valores.items() if v is not None})
    return filas


def escribir(ruta, filas):
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Datos"
    hoja["A1"] = "CENTRÍFUGA PRIMARIA"
    for letra, texto in ENCABEZADO.items():
        hoja[f"{letra}2"] = texto
    for numero, fila in enumerate(filas, start=3):
        for letra, valor in fila.items():
            hoja[f"{letra}{numero}"] = valor
    libro.save(ruta)
    return ruta


def planilla_completa():
    """Una fila por caso especial. El número de fila de Excel de cada bloque va en el comentario."""
    filas = []
    # 3: dos primarias a las 14:30; la secundaria (ESB 130) y la pulidora vienen en el bloque de la primera
    filas += bloque(time(14, 30), extra={"emulsion": {"X": "ESB 130", "AH": 15, "AI": 1, "R": "<1"},
                                         "liviana": {"AB": 93},
                                         "pesada": {"Z": 3.0, "AA": 1.8},
                                         "descarga": {"Z": 4.0, "AA": 0.7, "AC": 500}})
    # 7: la ESE 230 (2) con la fase pesada copiada de la (1); fases escritas de otra forma
    filas += bloque(time(14, 30), "ESE 230 (2)", fases=("EMULSIÓN", "Fase livina", " fase  pesada ", "descarga"),
                    cambios={"emulsion": {"R": ">7 y <8"}})
    # 11: madrugada (01:10, misma Fecha) con "130" → ESB 130 y eficiencia negativa (descarga muy cargada)
    filas += bloque(time(1, 10), cambios={"descarga": {"O": 58.1, "N": 2.0, "Q": 40}},
                    extra={"emulsion": {"X": 130}, "liviana": {"AB": 90}, "descarga": {"Z": 4.0, "AA": 0.7, "AC": 500}})
    # 15: hora "5;10" (Día 2, Fecha +1) sin secundaria ("0"); descarga titulada con N 0,01 según la planilla
    filas += bloque("5;10", fecha=datetime(2025, 6, 25), dia=2,
                    cambios={"descarga": {"P": _aceite(2.0, 0.01, 9.0)}}, extra={"emulsion": {"X": 0, "AH": 20, "AI": 0}})
    # 19: emulsión sin g ni mL, solo el % pegado; la planilla da eficiencia 0 (sin caudal de fase liviana)
    filas += bloque(time(7, 10), fecha=datetime(2025, 6, 25), dia=2,
                    cambios={"emulsion": {"N": None, "O": None, "P": 0.7, "U": 0}})
    # 23: ESB 130 anotada como primaria; la secundaria MSD 50 con la pesada calculada con N 0,1 en la planilla
    filas += bloque("09.10", "ESB 130", fecha=datetime(2025, 6, 25), dia=2,
                    extra={"emulsion": {"X": "MSD 50"}, "pesada": {"Z": 3.0, "AA": 1.8, "AB": _aceite(1.8, 0.1, 3.0)},
                           "descarga": {"Z": 4.0, "AA": None, "AC": 500}})          # descarga sin mL: incompleta
    # 27: Día 3 pero con la Fecha del Día 2 → se corrige a la del Día 2 + 1 (26/06); pulidora sin CP (AI vacío)
    filas += bloque(time(11, 10), fecha=datetime(2025, 6, 25), dia=3, extra={"emulsion": {"AH": 25}})
    # 31: bloque de plantilla sin datos
    filas += [{"A": 1, "B": 3, "D": datetime(2025, 6, 25), "E": time(13, 10), "I": 0.1, "J": 0.01, "K": f,
               "L": "ESE 230 (1)", "X": "ESB 130"} for f in FASES]
    # 35: fila suelta (fase pesada fuera de un bloque)
    filas.append({"A": 1, "B": 3, "D": datetime(2025, 6, 25), "E": time(15, 10), "K": "F Pesada", "N": 2.0, "O": 3.0})
    # 36: la Fecha avanza (27/06) y el Día sigue en 3: el Día está mal, la Fecha se deja
    filas += bloque(time(9, 10), fecha=datetime(2025, 6, 27), dia=3)
    return filas


@pytest.fixture
def sesion():
    motor = crear_motor("sqlite://")
    crear_tablas(motor)
    with crear_sesion(motor) as sesion:
        cliente = Cliente(nombre="LEDESMA", pais="Argentina")
        planta = Planta(nombre="LEDESMA", pais="Argentina", cliente=cliente, blanco_ml=0.2, blanco_centrifugas_ml=0.1,
                        factor_aceite_emulsion=3.45, hora_inicio_dia=time(5, 0))
        linea = Linea(nombre="Línea 1", planta=planta)
        for codigo, nombre, tipo, numero, volumen in [
                ("LED-CEN-01", "ESE 230 (1)", "Centrífuga primaria", 1, 16), ("LED-CEN-02", "ESE 230 (2)",
                                                                            "Centrífuga primaria", 2, 16),
                ("LED-CSE-01", "ESB 130", "Centrífuga secundaria", 1, None),
                ("LED-CSE-02", "MSD 50", "Centrífuga secundaria", 2, 2),
                ("LED-PUL-01", "OSD 60", "Pulidora", 1, None)]:
            Equipo(codigo=codigo, nombre=nombre, tipo=tipo, numero=numero, volumen_descarga_l=volumen, linea=linea)
        sesion.add(cliente)
        sesion.commit()
        yield sesion


@pytest.fixture
def importado(sesion, tmp_path):
    resumen, reporte = importar_centrifugas(sesion, escribir(tmp_path / "cen.xlsx", planilla_completa()))
    muestras = {(m.equipo.codigo, m.fecha_hora, m.fase): m for m in sesion.scalars(select(MuestraCentrifuga))}
    return resumen, reporte, muestras


def _motivos(reporte, tipo):
    return {fila: motivo for _, fila, t, motivo in reporte.filas if t == tipo}


# ---------------------------------------------------------------- funciones puras

def test_normalizar_fase_equipo_y_solidos():
    for texto in ("Emulsion", "EMULSIÓN", " emulsion "):
        assert normalizar_fase(texto) == "emulsion"
    for texto in ("F Livina", "Fase livina", "F. Liviana", "FASE LIVIANA"):
        assert normalizar_fase(texto) == "fase_liviana"
    assert normalizar_fase("Fase pesada") == normalizar_fase("F Pesada") == "fase_pesada"
    assert normalizar_fase("Descarga") == "descarga" and normalizar_fase("Total") is None
    assert normalizar_equipo(130) == normalizar_equipo("130") == "ESB 130"
    assert normalizar_equipo(0) is None and normalizar_equipo("0") is None and normalizar_equipo(None) is None
    assert normalizar_equipo(" MSD  50 ") == "MSD 50"
    assert leer_solidos(2) == (2.0, None, None)
    assert leer_solidos("<1") == (1.0, "<1", "menor que")
    assert leer_solidos(">7 y <8") == (7.5, ">7 y <8", "rango")
    assert leer_solidos(">10 y <11") == (10.5, ">10 y <11", "rango")


# ---------------------------------------------------------------- importación

def test_datos_crudos_normalidad_y_origen(importado):
    _, _, muestras = importado
    hora = datetime(2025, 6, 24, 14, 30)
    emulsion = muestras[("LED-CEN-01", hora, "emulsion")]
    assert (emulsion.caudal_lh, emulsion.gramos, emulsion.ml, emulsion.normalidad, emulsion.blanco_ml) == (
        15000, 3.5, 7.0, 0.1, 0.1)
    assert (emulsion.analista, emulsion.operador, emulsion.fruta) == ("Perez", "Gomez", "N Val S/R")
    assert emulsion.origen == "cen.xlsx › Datos › fila 3" and emulsion.revisar is None
    liviana = muestras[("LED-CEN-01", hora, "fase_liviana")]
    assert (liviana.aceite_pct_medido, liviana.gramos, liviana.normalidad, liviana.cp_liviana_bar) == (75, None, None,
                                                                                                    0.8)
    assert muestras[("LED-CEN-01", hora, "fase_pesada")].normalidad == 0.01                 # N 0,01 (columna J)
    descarga = muestras[("LED-CEN-01", hora, "descarga")]
    assert (descarga.normalidad, descarga.t_descarga_s, descarga.caudal_lh) == (0.1, 500, None)
    assert muestras[("LED-CEN-01", hora, "fase_pesada")].caudal_lh is None                # caudal calculado: no


def test_secundaria_una_vez_por_muestreo_y_sin_emulsion(importado):
    _, _, muestras = importado
    hora = datetime(2025, 6, 24, 14, 30)
    secundaria = {fase: m for (codigo, fh, fase), m in muestras.items() if codigo == "LED-CSE-01" and fh == hora}
    assert set(secundaria) == {"fase_liviana", "fase_pesada", "descarga"}              # la alimentación se calcula
    assert secundaria["fase_liviana"].aceite_pct_medido == 93
    assert (secundaria["fase_pesada"].gramos, secundaria["fase_pesada"].normalidad) == (3.0, 0.01)
    assert (secundaria["descarga"].normalidad, secundaria["descarga"].t_descarga_s) == (0.1, 500)
    # "130" → ESB 130 a la madrugada; "0" → sin secundaria
    assert ("LED-CSE-01", datetime(2025, 6, 25, 1, 10), "fase_liviana") in muestras
    assert not [k for k in muestras if k[0].startswith("LED-CSE") and k[1] == datetime(2025, 6, 25, 5, 10)]
    pulidora = {(fh, fase): m for (codigo, fh, fase), m in muestras.items() if codigo == "LED-PUL-01"}
    assert (pulidora[(hora, "descarga")].t_descarga_s, pulidora[(hora, "fase_liviana")].cp_liviana_bar) == (900, 1)
    assert pulidora[(hora, "fase_liviana")].cp_liviana_marca is None and pulidora[(hora, "descarga")].gramos is None
    cero = pulidora[(datetime(2025, 6, 25, 5, 10), "fase_liviana")]
    assert (cero.cp_liviana_bar, cero.cp_liviana_marca, cero.revisar) == (0, "cero a confirmar", None)
    assert (datetime(2025, 6, 26, 11, 10), "fase_liviana") not in pulidora              # AI vacío: queda vacío
    assert pulidora[(datetime(2025, 6, 26, 11, 10), "descarga")].t_descarga_s == 1500


def test_fecha_de_produccion_como_el_boe(sesion, importado):
    _, reporte, muestras = importado
    madrugada = muestras[("LED-CEN-01", datetime(2025, 6, 25, 1, 10), "emulsion")]      # Fecha 24/06, 01:10
    planta = sesion.scalars(select(Planta)).one()
    assert fecha_produccion(planta, madrugada.fecha_hora) == date(2025, 6, 24)
    assert ("LED-CEN-01", datetime(2025, 6, 25, 5, 10), "emulsion") in muestras          # hora "5;10"
    assert ("LED-CSE-01", datetime(2025, 6, 25, 9, 10), "emulsion") in muestras          # hora "09.10"
    assert 'Hora "5;10" → 05:10.' in _motivos(reporte, "corregida").values()


def test_solidos_con_texto(importado):
    _, _, muestras = importado
    uno = muestras[("LED-CEN-01", datetime(2025, 6, 24, 14, 30), "emulsion")]
    rango = muestras[("LED-CEN-02", datetime(2025, 6, 24, 14, 30), "emulsion")]
    assert (uno.solidos_pct, uno.solidos_texto, uno.solidos_marca) == (1, "<1", "menor que")
    assert (rango.solidos_pct, rango.solidos_texto, rango.solidos_marca) == (7.5, ">7 y <8", "rango")


def test_marcas_para_revisar(importado):
    _, _, muestras = importado
    revisar = lambda codigo, fh, fase: muestras[(codigo, fh, fase)].revisar or ""        # noqa: E731
    h1, h2, h3 = datetime(2025, 6, 24, 14, 30), datetime(2025, 6, 25, 1, 10), datetime(2025, 6, 25, 5, 10)
    h4, h5, h6 = datetime(2025, 6, 25, 7, 10), datetime(2025, 6, 25, 9, 10), datetime(2025, 6, 26, 11, 10)
    assert revisar("LED-CEN-02", h1, "fase_pesada") == REVISAR_COPIADA
    assert revisar("LED-CEN-02", h1, "descarga") == REVISAR_COPIADA      # misma regla que la fase pesada
    assert revisar("LED-CEN-02", h1, "emulsion") == ""                   # la emulsión es la misma: no es copia
    assert all(revisar("LED-CEN-01", h2, f) == REVISAR_FISICO for f in ("emulsion", "fase_liviana", "descarga"))
    assert revisar("LED-CEN-01", h3, "descarga") == REVISAR_NORMALIDAD
    pegada = muestras[("LED-CEN-01", h4, "emulsion")]
    assert (pegada.gramos, pegada.aceite_pct_medido) == (None, 0.7)
    assert REVISAR_PEGADO in pegada.revisar and REVISAR_SIN_LIVIANA in pegada.revisar
    assert revisar("LED-CSE-01", h5, "emulsion") == REVISAR_ESB_PRIMARIA
    assert revisar("LED-CSE-02", h5, "fase_pesada") == REVISAR_NORMALIDAD_PESADA
    assert muestras[("LED-CSE-02", h5, "descarga")].gramos is None                    # titulación incompleta
    corregida = muestras[("LED-CEN-01", h6, "emulsion")]                                # Fecha 25/06 → 26/06
    assert (corregida.revisar, corregida.fecha_original) == (REVISAR_FECHA_CORREGIDA, date(2025, 6, 25))
    assert muestras[("LED-PUL-01", h6, "descarga")].revisar == REVISAR_FECHA_CORREGIDA
    dia_mal = muestras[("LED-CEN-01", datetime(2025, 6, 27, 9, 10), "emulsion")]
    assert (dia_mal.revisar, dia_mal.fecha_original) == (REVISAR_DIA, None)
    assert muestras[("LED-CEN-01", h1, "emulsion")].fecha_original is None


def test_reporte_de_saltadas_y_resumen(importado):
    resumen, reporte, muestras = importado
    saltadas = _motivos(reporte, "rechazada")
    assert saltadas[31].startswith("Bloque sin datos (filas 31–34")
    assert saltadas[35].startswith("Fila de fase_pesada fuera de un bloque")
    assert "titulación incompleta" in _motivos(reporte, "aviso")[26]
    assert resumen["filas_leidas"] == 37 and resumen["creados"] == len(muestras)
    assert resumen["cp_cero"] == 1
    assert resumen["revisar"][REVISAR_FISICO] == 4
    texto = texto_resumen(resumen, reporte)
    assert "Filas leídas:" in texto and "Para revisar:" in texto and "cero a confirmar" in texto


def test_sin_corregir_fechas_solo_marca(sesion, tmp_path):
    importar_centrifugas(sesion, escribir(tmp_path / "cen.xlsx", planilla_completa()), corregir_fechas=False)
    muestra = sesion.scalars(select(MuestraCentrifuga).where(MuestraCentrifuga.fecha_hora == datetime(2025, 6, 25, 11, 10))
                             .where(MuestraCentrifuga.fase == "emulsion")).one()
    assert (muestra.revisar, muestra.fecha_original) == (REVISAR_FECHA, None)


def test_correr_dos_veces_no_duplica(sesion, tmp_path):
    ruta = escribir(tmp_path / "cen.xlsx", planilla_completa())
    primero, _ = importar_centrifugas(sesion, ruta)
    segundo, _ = importar_centrifugas(sesion, ruta)
    assert segundo["creados"] == 0 and segundo["actualizados"] == primero["creados"]
    assert len(sesion.scalars(select(MuestraCentrifuga)).all()) == primero["creados"]


def test_simular_no_guarda(sesion, tmp_path):
    resumen, _ = importar_centrifugas(sesion, escribir(tmp_path / "cen.xlsx", planilla_completa()), guardar=False)
    assert resumen["creados"] > 0 and sesion.scalars(select(MuestraCentrifuga)).all() == []


def test_misma_clave_con_datos_distintos_no_se_carga(sesion, tmp_path):
    filas = bloque(time(14, 30)) + bloque(time(14, 30), cambios={"emulsion": {"M": 18000}})
    resumen, reporte = importar_centrifugas(sesion, escribir(tmp_path / "cen.xlsx", filas))
    saltadas = _motivos(reporte, "rechazada")
    assert 3 in saltadas and 7 in saltadas and "datos distintos en caudal_lh" in saltadas[3]
    assert resumen["creados"] == 3                       # las otras 3 fases son iguales: se cargan una vez
    assert "Fila idéntica a la fila 4" in _motivos(reporte, "aviso")[8]


def test_esb_como_secundaria_y_como_primaria_en_el_mismo_muestreo(sesion, tmp_path):
    filas = bloque(time(17, 15), extra={"emulsion": {"X": "ESB 130"}, "liviana": {"AB": 93}}) \
        + bloque(time(17, 15), "ESB 130", cambios={"liviana": {"P": 97}})
    _, reporte = importar_centrifugas(sesion, escribir(tmp_path / "cen.xlsx", filas))
    saltadas = _motivos(reporte, "rechazada")
    assert "como primaria y como secundaria (filas 4 y 8)" in saltadas[4] and 8 in saltadas
    liviana = sesion.scalars(select(MuestraCentrifuga).where(MuestraCentrifuga.fase == "fase_liviana")
                             .join(Equipo).where(Equipo.codigo == "LED-CSE-01")).all()
    assert liviana == []                                 # 93 contra 97: no se carga ninguna


def test_encabezado_cambiado_da_error_claro(sesion, tmp_path):
    ruta = escribir(tmp_path / "cen.xlsx", bloque(time(14, 30)))
    from openpyxl import load_workbook
    libro = load_workbook(ruta)
    libro["Datos"]["N2"] = "Peso"
    libro.save(ruta)
    with pytest.raises(ValueError, match='La columna N de la hoja "Datos" tendría que ser "Muestra"'):
        importar_centrifugas(sesion, ruta)
