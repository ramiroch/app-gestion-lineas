"""Vista de proceso (análisis 5 de la sección 9 del mapeo, el flowsheet): KPI por eBOE, totales del
período y comparación de dos períodos.

- Por eBOE: aceite en fruta de entrada y de salida (kg/MT) y eficiencia, ponderados por las toneladas
  del turno (core.estadistica.media_ponderada_por_turno; promedio simple si no hay producción); MTPH
  promedio simple.
- Totales: eficiencia eBOE global (ponderada), fruta procesada (MT) y aceite envasado (kg) sumados de
  RegistroProduccion; rendimiento de aceite = Σ aceite envasado / Σ fruta procesada (kg/MT), con los
  turnos que tienen los dos datos; eficiencia del proceso = rendimiento / aceite en fruta de entrada.
  Se usan totales del período y no promedios de turnos: por turno hay desfase de envasado y salen
  valores mayores a 100 %.
"""

import pandas as pd

from core.analisis import Filtros
from core.estadistica import media_ponderada_por_turno

# (clave, nombre, unidad, decimales, sentido): sentido +1 = mejor si sube, −1 = mejor si baja,
# 0 = neutro (depende de la fruta o del volumen, no de la operación).
INDICADORES_EBOE = [
    ("aceite_entrada", "Aceite en fruta entrada", "kg/MT", 2, 0),
    ("aceite_salida", "Aceite en fruta salida", "kg/MT", 2, -1),
    ("mtph", "MTPH promedio", "MTPH", 1, 0),
    ("eficiencia", "Eficiencia eBOE", "%", 1, +1),
]
INDICADORES_TOTALES = [
    ("eficiencia", "Eficiencia eBOE global", "%", 1, +1),
    ("aceite_entrada", "Aceite en fruta entrada", "kg/MT", 2, 0),
    ("fruta_t", "Fruta procesada", "MT", 0, 0),
    ("aceite_envasado_kg", "Aceite envasado", "kg", 0, 0),
    ("rendimiento", "Rendimiento de aceite", "kg/MT", 2, +1),
    ("eficiencia_proceso", "Eficiencia del proceso", "%", 1, +1),
]


def filtrar_produccion(produccion: pd.DataFrame, filtros: Filtros) -> pd.DataFrame:
    """Registros de core.produccion.tabla_produccion() dentro de los filtros que le aplican.

    La producción es de la planta (no de cada eBOE): se filtra por fechas de producción, turno y
    proceso; el eBOE y el programa no le aplican.
    """
    seleccion = pd.Series(True, index=produccion.index)
    if filtros.desde is not None:
        seleccion &= produccion["fecha_produccion"] >= pd.Timestamp(filtros.desde)
    if filtros.hasta is not None:
        seleccion &= produccion["fecha_produccion"] <= pd.Timestamp(filtros.hasta)
    if filtros.turnos:
        seleccion &= produccion["turno"].isin(filtros.turnos)
    if filtros.procesos:
        seleccion &= produccion["proceso_id"].isin(filtros.procesos)
    return produccion[seleccion].reset_index(drop=True)


def _media_simple(serie: pd.Series) -> float | None:
    serie = serie.dropna()
    return float(serie.mean()) if len(serie) else None


def resumen_proceso(pares: pd.DataFrame, toneladas: pd.DataFrame, produccion: pd.DataFrame) -> dict:
    """KPI del período.

    pares: core.analisis.pares_con_emulsion() (aceite_fruta = entrada, aceite_salida, eficiencia_pct,
    tph, fecha_produccion, turno). toneladas: core.produccion.toneladas_por_turno(). produccion: los
    registros del período (filtrar_produccion()).
    Devuelve {"eboes": [dict por eBOE], "totales": dict}. Por eBOE: eboe, n (pares), aceite_entrada,
    aceite_salida, mtph, eficiencia (%) y ponderada (True si la eficiencia se ponderó por t).
    Totales: eficiencia, ponderada, n_pares, aceite_entrada, fruta_t, aceite_envasado_kg, n_turnos (con
    fruta), rendimiento, n_turnos_rendimiento (con fruta y aceite) y eficiencia_proceso (%).
    """
    eboes = []
    for eboe, sub in pares.groupby("eboe", sort=True):
        eficiencia = media_ponderada_por_turno(sub, "eficiencia_pct", toneladas)
        eboes.append({
            "eboe": eboe, "n": len(sub),
            "aceite_entrada": media_ponderada_por_turno(sub, "aceite_fruta", toneladas)["valor"],
            "aceite_salida": media_ponderada_por_turno(sub, "aceite_salida", toneladas)["valor"],
            "mtph": _media_simple(sub["tph"]),
            "eficiencia": eficiencia["valor"], "ponderada": eficiencia["ponderada"],
        })

    eficiencia = media_ponderada_por_turno(pares, "eficiencia_pct", toneladas)
    aceite_entrada = media_ponderada_por_turno(pares, "aceite_fruta", toneladas)["valor"]
    con_fruta = produccion[produccion["fruta_t"] > 0]
    con_ambos = con_fruta.dropna(subset=["aceite_envasado_kg"])
    rendimiento = (con_ambos["aceite_envasado_kg"].sum() / con_ambos["fruta_t"].sum()) if len(con_ambos) else None
    totales = {
        "eficiencia": eficiencia["valor"], "ponderada": eficiencia["ponderada"], "n_pares": len(pares),
        "aceite_entrada": aceite_entrada,
        "fruta_t": float(con_fruta["fruta_t"].sum()) if len(con_fruta) else None,
        "aceite_envasado_kg": float(produccion["aceite_envasado_kg"].sum()) if produccion["aceite_envasado_kg"].notna().any() else None,
        "n_turnos": len(con_fruta),
        "rendimiento": float(rendimiento) if rendimiento is not None else None,
        "n_turnos_rendimiento": len(con_ambos),
        "eficiencia_proceso": (rendimiento / aceite_entrada * 100) if rendimiento is not None and aceite_entrada else None,
    }
    return {"eboes": eboes, "totales": totales}


def _evaluar(diferencia, sentido: int) -> str | None:
    if diferencia is None:
        return None
    if sentido == 0:
        return "neutro"
    if diferencia == 0:
        return "igual"
    return "mejora" if diferencia * sentido > 0 else "empeora"


def comparar_resumenes(a: dict, b: dict) -> pd.DataFrame:
    """Compara dos resumen_proceso(): diferencia = b − a, y si cada indicador mejora o empeora.

    Una fila por indicador: seccion ("Totales" o "eBOE n"), clave, indicador, unidad, decimales,
    valor_a, valor_b, diferencia (en puntos para los %), sentido y evaluacion ("mejora", "empeora",
    "igual", "neutro", o vacío si falta un valor).
    """
    filas = []

    def agregar(seccion, valores_a, valores_b, indicadores):
        for clave, nombre, unidad, decimales, sentido in indicadores:
            va, vb = valores_a.get(clave), valores_b.get(clave)
            diferencia = vb - va if va is not None and vb is not None else None
            filas.append({"seccion": seccion, "clave": clave, "indicador": nombre, "unidad": unidad,
                          "decimales": decimales, "valor_a": va, "valor_b": vb, "diferencia": diferencia,
                          "sentido": sentido, "evaluacion": _evaluar(diferencia, sentido)})

    agregar("Totales", a["totales"], b["totales"], INDICADORES_TOTALES)
    por_eboe_a = {e["eboe"]: e for e in a["eboes"]}
    por_eboe_b = {e["eboe"]: e for e in b["eboes"]}
    for eboe in sorted(set(por_eboe_a) | set(por_eboe_b)):
        agregar(f"eBOE {eboe}", por_eboe_a.get(eboe, {}), por_eboe_b.get(eboe, {}), INDICADORES_EBOE)
    return pd.DataFrame(filas)
