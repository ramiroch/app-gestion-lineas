"""Pruebas de core/duplicados.py."""

from datetime import datetime

from core.duplicados import separar_repetidas

CLAVE = ["eBOE", "lugar", "fecha_hora"]
HORA = datetime(2025, 6, 25, 2, 0)


def fila(numero, eboe=1, lugar="Entrada", fecha_hora=HORA, vol=7.4, normalidad=0.1, aceite=None):
    """Fila como las devuelve leer_hoja(); "aceite" simula una columna calculada del Excel."""
    return (numero, {"eBOE": eboe, "lugar": lugar, "fecha_hora": fecha_hora, "vol_ml": vol,
                     "normalidad": normalidad, "aceite": aceite})


def test_claves_unicas_se_cargan_todas():
    filas = [fila(4), fila(5, lugar="Salida"), fila(6, eboe=2)]

    para_cargar, errores, avisos = separar_repetidas("BOE", filas, CLAVE)

    assert para_cargar == filas
    assert errores == [] and avisos == []


def test_misma_clave_con_datos_distintos_no_se_carga_ninguna():
    filas = [fila(4), fila(5, lugar="Salida"), fila(9, vol=8.1, normalidad=0.01)]

    para_cargar, errores, avisos = separar_repetidas("BOE", filas, CLAVE)

    assert [numero for numero, _ in para_cargar] == [5]
    assert [(hoja, numero) for hoja, numero, _ in errores] == [("BOE", 4), ("BOE", 9)]
    motivo = errores[0][2]
    assert "Hay 2 filas con la misma clave" in motivo
    assert "datos distintos en vol_ml, normalidad (filas 4 y 9)" in motivo
    assert "No se carga ninguna" in motivo
    assert avisos == []


def test_filas_identicas_se_cargan_una_vez_y_avisan():
    filas = [fila(4), fila(7), fila(8)]

    para_cargar, errores, avisos = separar_repetidas("BOE", filas, CLAVE)

    assert [numero for numero, _ in para_cargar] == [4]
    assert errores == []
    assert [(numero, motivo) for _, numero, motivo in avisos] == [
        (7, "Fila idéntica a la fila 4: se carga una sola vez."),
        (8, "Fila idéntica a la fila 4: se carga una sola vez."),
    ]


def test_columnas_datos_ignora_las_columnas_calculadas():
    filas = [fila(4, aceite=10.1), fila(9, aceite=10.2)]   # solo difiere una columna calculada

    para_cargar, errores, avisos = separar_repetidas("BOE", filas, CLAVE,
                                                     columnas_datos=["vol_ml", "normalidad"])

    assert [numero for numero, _ in para_cargar] == [4]
    assert errores == [] and len(avisos) == 1


def test_filas_con_clave_incompleta_pasan_para_que_las_valide_el_importador():
    filas = [fila(4, fecha_hora=None), fila(5, fecha_hora=None, vol=1.0)]

    para_cargar, errores, avisos = separar_repetidas("BOE", filas, CLAVE)

    assert para_cargar == filas
    assert errores == [] and avisos == []
