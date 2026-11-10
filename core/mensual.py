"""Evolución mensual: indicadores agrupados por mes de producción.

El mes sale de la fecha de producción (no de la de calendario): una muestra del 1/08 a las 02:00
es del día de producción 31/07 y cuenta en julio. Las medias de aceite y eficiencia se ponderan por
las toneladas del turno (core.estadistica.media_ponderada_por_turno); el MTPH es promedio simple; la
eficiencia del proceso del mes se calcula como en la vista de proceso (core.proceso.resumen_proceso):
Σ aceite envasado / Σ fruta procesada del mes, dividido por el aceite en fruta de entrada ponderado.
- eficiencia_eboe_planta_por_mes(): la eficiencia eBOE de la planta (los eBOE filtrados juntos), ponderada.
- cajas_por_mes(): cajas de un boxplot por mes y lugar (entrada, salida) con los cuartiles de Minitab y los límites
  IQR (core.estadistica), por muestra (sin ponderar), y sus outliers.
"""

import pandas as pd

from core.estadistica import cuartiles, es_outlier_iqr, limites_iqr, media_ponderada_por_turno
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


def eficiencia_eboe_planta_por_mes(pares: pd.DataFrame, toneladas: pd.DataFrame | None) -> pd.DataFrame:
    """Eficiencia eBOE (%) de la planta por mes de producción: todos los pares de los eBOE filtrados juntos,
    ponderada por las toneladas del turno (promedio simple si el mes no tiene producción, como por_mes_y_eboe()).
    pares: con eficiencia_pct (core.analisis.pares_con_emulsion). Devuelve mes, etiqueta, valor, n y ponderada."""
    datos = pares.assign(mes=mes_de(pares["fecha_produccion"]))
    filas = []
    for mes, sub in datos.groupby("mes", sort=True):
        resultado = media_ponderada_por_turno(sub, "eficiencia_pct", toneladas)
        filas.append({"mes": mes, "etiqueta": etiqueta_mes(mes), "valor": resultado["valor"], "n": resultado["n"],
                      "ponderada": resultado["ponderada"]})
    return pd.DataFrame(filas, columns=["mes", "etiqueta", "valor", "n", "ponderada"])


def cajas_por_mes(datos: pd.DataFrame, variable: str = "aceite_fruta") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Boxplot por mes de producción y lugar, por muestra (no ponderado).

    datos: una fila por análisis con fecha_produccion, lugar y la variable (core.analisis.tabla_analisis ya
    filtrada). Devuelve (cajas, outliers). cajas: mes, etiqueta, lugar, n, q1, mediana, q3 (cuartiles de Minitab),
    media, bigote_inferior y bigote_superior (último valor dentro de los límites IQR). outliers: las filas de datos
    fuera de los límites IQR de su mes y lugar, con mes y etiqueta. Se marcan, nunca se borran.
    """
    datos = datos.dropna(subset=[variable]).assign(mes=lambda d: mes_de(d["fecha_produccion"]))
    filas = []
    for (mes, lugar), valores in datos.groupby(["mes", "lugar"], sort=True)[variable]:
        limites = limites_iqr(valores)
        q1, mediana, q3 = cuartiles(valores)
        filas.append({"mes": mes, "etiqueta": etiqueta_mes(mes), "lugar": lugar, "n": limites["n"], "q1": q1,
                      "mediana": mediana, "q3": q3, "media": float(valores.mean()),
                      "bigote_inferior": limites["bigote_inferior"], "bigote_superior": limites["bigote_superior"]})
    cajas = pd.DataFrame(filas, columns=["mes", "etiqueta", "lugar", "n", "q1", "mediana", "q3", "media",
                                         "bigote_inferior", "bigote_superior"])
    if datos.empty:
        return cajas, datos.assign(etiqueta=pd.Series(dtype=str))
    fuera = datos.groupby(["mes", "lugar"])[variable].transform(es_outlier_iqr).astype(bool)
    outliers = datos[fuera]
    return cajas, outliers.assign(etiqueta=outliers["mes"].map(etiqueta_mes))
