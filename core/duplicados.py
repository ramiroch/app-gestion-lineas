"""Filas repetidas en un Excel a importar.

Regla: si dos o más filas tienen la misma clave (por ejemplo punto de muestreo +
fecha_hora) y datos distintos, no se carga ninguna y todas van al reporte. Si son
idénticas, es la misma fila repetida: se carga una sola vez y las demás quedan como aviso.
"""

from collections import defaultdict


def separar_repetidas(hoja: str, filas: list[tuple[int, dict]], columnas_clave: list[str],
                      columnas_datos: list[str] | None = None) -> tuple[list, list, list]:
    """Separa las filas que se pueden cargar de las que tienen la clave repetida.

    filas: [(n.º de fila de Excel, {columna: valor})], como las devuelve leer_hoja().
    columnas_clave: columnas que identifican el registro.
    columnas_datos: columnas que se comparan para decidir si dos filas son iguales
    (None = todas). Sirve para ignorar columnas calculadas del Excel.
    Las filas con algún valor de la clave vacío no se agrupan: pasan para que su
    validación las reporte.

    Devuelve (para_cargar, errores, avisos), en el orden original de las filas:
    - para_cargar: filas con clave única y la primera de cada grupo de filas idénticas.
    - errores: (hoja, fila, motivo) de cada fila de un grupo con datos distintos.
    - avisos: (hoja, fila, motivo) de las filas idénticas que no se cargan.
    """
    grupos = defaultdict(list)
    for numero_fila, datos in filas:
        clave = tuple(datos.get(columna) for columna in columnas_clave)
        if None not in clave:
            grupos[clave].append((numero_fila, datos))

    motivo_por_fila = {}   # n.º de fila → ("error" o "aviso", motivo)
    for clave, grupo in grupos.items():
        if len(grupo) == 1:
            continue
        numeros = [numero for numero, _ in grupo]
        difieren = _columnas_que_difieren([datos for _, datos in grupo], columnas_datos)
        if difieren:
            descripcion = ", ".join(f'{c} "{v}"' for c, v in zip(columnas_clave, clave))
            motivo = (f"Hay {len(grupo)} filas con la misma clave ({descripcion}) y datos distintos "
                      f"en {', '.join(difieren)} (filas {_enumerar(numeros)}). "
                      "No se carga ninguna: corregí el Excel.")
            for numero in numeros:
                motivo_por_fila[numero] = ("error", motivo)
        else:
            for numero in numeros[1:]:
                motivo_por_fila[numero] = ("aviso", f"Fila idéntica a la fila {numeros[0]}: "
                                                    "se carga una sola vez.")

    para_cargar, errores, avisos = [], [], []
    for numero_fila, datos in filas:
        if numero_fila not in motivo_por_fila:
            para_cargar.append((numero_fila, datos))
            continue
        tipo, motivo = motivo_por_fila[numero_fila]
        (errores if tipo == "error" else avisos).append((hoja, numero_fila, motivo))
    return para_cargar, errores, avisos


def _columnas_que_difieren(filas: list[dict], columnas: list[str] | None) -> list[str]:
    """Columnas cuyo valor no es el mismo en todas las filas."""
    if columnas is None:
        columnas = list(dict.fromkeys(c for datos in filas for c in datos))   # sin repetir, en orden
    return [c for c in columnas if len({datos.get(c) for datos in filas}) > 1]


def _enumerar(numeros: list[int]) -> str:
    """[12, 15, 20] → "12, 15 y 20"."""
    textos = [str(n) for n in numeros]
    return textos[0] if len(textos) == 1 else ", ".join(textos[:-1]) + " y " + textos[-1]
