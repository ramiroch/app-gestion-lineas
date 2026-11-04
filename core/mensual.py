"""Evolución mensual: indicadores agrupados por mes de producción.

El mes sale de la fecha de producción (no de la de calendario): una muestra del 1/08 a las 02:00
es del día de producción 31/07 y cuenta en julio. Las medias de aceite y eficiencia se ponderan por
las toneladas del turno (core.estadistica.media_ponderada_por_turno); el MTPH es promedio simple; la
eficiencia del proceso del mes se calcula como en la vista de proceso (core.proceso.resumen_proceso):
Σ aceite envasado / Σ fruta procesada del mes, dividido por el aceite en fruta de entrada ponderado.
"""

import pandas as pd

from core.estadistica import media_ponderada_por_turno
from core.proceso import resumen_proceso

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def mes_de(fechas: pd.Series) -> pd.Series:
    """Primer día del mes de cada fecha de producción (como fecha de pandas)."""
    return pd.to_datetime(fechas).dt.to_period("M").dt.to_timestamp()


def etiqueta_mes(mes) -> str:
    """Timestamp(2025, 7, 1) → "Jul 2025"."""
    return f"{MESES[mes.month - 1]} {mes.year}"


def eje_de_meses(meses) -> list[str]:
    """Etiquetas del eje X en orden, con un hueco (" ", "  ", ...) donde faltan meses entre dos con datos.

    Sirve para que entre temporadas (octubre a junio sin datos) quede un espacio visible.
    """
    ordenados = sorted(set(pd.to_datetime(list(meses))))
    eje, huecos = [], 0
    for anterior, mes in zip([None] + ordenados[:-1], ordenados):
        if anterior is not None and (mes.year - anterior.year) * 12 + mes.month - anterior.month > 1:
            huecos += 1
            eje.append(" " * huecos)          # etiqueta vacía y única para cada hueco
        eje.append(etiqueta_mes(mes))
    return eje


def por_mes_y_eboe(pares: pd.DataFrame, variable: str, toneladas: pd.DataFrame | None) -> pd.DataFrame:
    """Valor de `variable` por mes de producción y eBOE.

    Con toneladas, la media se pondera por las toneladas del turno (promedio simple si el mes no tiene
    producción); con toneladas=None, promedio simple. Devuelve mes, etiqueta, eboe, valor, n (valores
    no vacíos) y ponderada, ordenado por mes y eBOE.
    """
    datos = pares.assign(mes=mes_de(pares["fecha_produccion"]))
    filas = []
    for (mes, eboe), sub in datos.groupby(["mes", "eboe"], sort=True):
        if toneladas is None:
            valores = sub[variable].dropna()
            resultado = {"valor": float(valores.mean()) if len(valores) else None, "n": len(valores),
                         "ponderada": False}
        else:
            resultado = media_ponderada_por_turno(sub, variable, toneladas)
        filas.append({"mes": mes, "etiqueta": etiqueta_mes(mes), "eboe": eboe, "valor": resultado["valor"],
                      "n": resultado["n"], "ponderada": resultado["ponderada"]})
    return pd.DataFrame(filas, columns=["mes", "etiqueta", "eboe", "valor", "n", "ponderada"])


def eficiencia_proceso_por_mes(pares: pd.DataFrame, toneladas: pd.DataFrame, produccion: pd.DataFrame) -> pd.DataFrame:
    """Eficiencia del proceso de la planta por mes de producción.

    Para cada mes con pares o con producción: rendimiento (kg/MT) = Σ aceite envasado / Σ fruta
    procesada de los turnos con los dos datos; aceite_entrada = aceite en fruta de entrada ponderado
    del mes; eficiencia_proceso (%) = rendimiento / aceite_entrada × 100. n = turnos con fruta y aceite
    envasado (los que entran en el rendimiento); n_pares = pares del mes.
    """
    pares = pares.assign(mes=mes_de(pares["fecha_produccion"]))
    produccion = produccion.assign(mes=mes_de(produccion["fecha_produccion"]))
    filas = []
    for mes in sorted(set(pares["mes"]) | set(produccion["mes"])):
        totales = resumen_proceso(pares[pares["mes"] == mes], toneladas, produccion[produccion["mes"] == mes])["totales"]
        filas.append({"mes": mes, "etiqueta": etiqueta_mes(mes), "rendimiento": totales["rendimiento"],
                      "aceite_entrada": totales["aceite_entrada"], "eficiencia_proceso": totales["eficiencia_proceso"],
                      "n": totales["n_turnos_rendimiento"], "n_pares": totales["n_pares"]})
    return pd.DataFrame(filas, columns=["mes", "etiqueta", "rendimiento", "aceite_entrada", "eficiencia_proceso",
                                        "n", "n_pares"])
