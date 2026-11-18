"""Diagnóstico de la emulsión (licor) del eBOE (Sistema › Emulsión; informe Brown 2023).

- zona_emulsion(): dónde cae cada muestra en el plano °Brix–pH (límites de RangoReferencia: °Brix máximo de la
  emulsión y el aviso de pH):
  · °Brix alto + pH bajo (< aviso) → posible fruta rota en la mesa (jugo ácido en el licor);
  · °Brix alto + pH ≥ aviso → ensuciamiento de la línea;
  · pH > aviso (con °Brix normal) → riesgo en la separación (revisar la dureza del agua);
  · el resto, normal.
- dilucion_por_turno(): aceite en la emulsión de alimentación a las primarias / aceite en la emulsión del
  laboratorio del BOE (promedio de los eBOE, ponderado por el caudal de fase pesada de cada eBOE si todos lo tienen;
  si no, promedio simple, y se aclara). Por turno, con la mediana de cada lado. Menos de 1 = la emulsión llega más
  diluida a las centrífugas que como sale del BOE. Las muestras de centrífuga copiadas no entran (dato inválido).
- pct_sobre_mediana(): % de muestras de emulsión por encima de la mediana de su temporada (indicador de riesgo de
  volatilización del informe 2023), por mes o por eBOE.
"""

import numpy as np
import pandas as pd

from core.analisis_centrifugas import PRIMARIA
from core.fase_pesada import CLAVES_TURNO
from core.mensual import etiqueta_mes, mes_de

NORMAL = "normal"
FRUTA_ROTA = "posible fruta rota en la mesa"
ENSUCIAMIENTO = "ensuciamiento de la línea"
SEPARACION = "riesgo en la separación (revisar dureza del agua)"
ZONAS = [NORMAL, FRUTA_ROTA, ENSUCIAMIENTO, SEPARACION]


def zona_emulsion(brix, ph, brix_maximo: float, ph_aviso: float) -> str | None:
    """Zona de una muestra en el plano °Brix–pH (ver el docstring del módulo). None si falta el °Brix o el pH."""
    if brix is None or ph is None or pd.isna(brix) or pd.isna(ph):
        return None
    if brix > brix_maximo:
        return ENSUCIAMIENTO if ph >= ph_aviso else FRUTA_ROTA
    return SEPARACION if ph > ph_aviso else NORMAL


def zonas(emulsion: pd.DataFrame, brix_maximo: float, ph_aviso: float) -> pd.DataFrame:
    """La tabla de emulsión (core.analisis.tabla_emulsion) con la zona de cada muestra."""
    return emulsion.assign(zona=[zona_emulsion(b, p, brix_maximo, ph_aviso)
                                 for b, p in zip(emulsion["brix"], emulsion["ph"])])


def resumen_zonas(datos: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    """Cantidad y % de muestras de cada zona por grupo (por ejemplo, ["temporada", "eboe"]). Columnas: por + zona,
    n, pct (sobre las muestras del grupo con °Brix y pH)."""
    validos = datos.dropna(subset=["zona"])
    if validos.empty:
        return pd.DataFrame(columns=por + ["zona", "n", "pct"])
    conteo = validos.groupby(por + ["zona"]).size().rename("n").reset_index()
    total = conteo.groupby(por)["n"].transform("sum")
    return conteo.assign(pct=conteo["n"] / total * 100)


def dilucion_por_turno(muestras: pd.DataFrame, emulsion: pd.DataFrame, analisis: pd.DataFrame) -> pd.DataFrame:
    """Dilución de la emulsión entre el BOE y las centrífugas, por turno.

    muestras: core.analisis_centrifugas.tabla_muestras (la emulsión de alimentación de las primarias, sin las
    copiadas); emulsion: core.analisis.tabla_emulsion (laboratorio del BOE); analisis: core.analisis.tabla_analisis
    (el caudal de fase pesada de cada eBOE, en las filas de Entrada). Alimentación = mediana del aceite en la emulsión
    de las primarias en el turno. BOE = mediana de cada eBOE en el turno, promediadas con el peso del caudal de fase
    pesada de cada eBOE (mediana del turno) si todos lo tienen (ponderada = True); si no, promedio simple.
    dilucion = alimentación / BOE. Columnas: temporada, fecha_produccion, turno, mes, alimentacion, boe, ponderada,
    dilucion, n_primaria, n_boe."""
    columnas = ["temporada", "fecha_produccion", "turno", "mes", "alimentacion", "boe", "ponderada", "dilucion",
                "n_primaria", "n_boe"]
    alimentacion = muestras[(muestras["tipo"] == PRIMARIA) & (muestras["fase"] == "emulsion")
                            & ~muestras["copiada"].fillna(False).astype(bool)].dropna(subset=["aceite_pct"])
    boe = emulsion.dropna(subset=["aceite_emulsion"])
    if alimentacion.empty or boe.empty:
        return pd.DataFrame(columns=columnas)
    por_turno = alimentacion.groupby(CLAVES_TURNO).agg(alimentacion=("aceite_pct", "median"),
                                                       n_primaria=("aceite_pct", "size"))
    por_eboe = boe.groupby(CLAVES_TURNO + ["eboe"]).agg(aceite=("aceite_emulsion", "median"),
                                                        n=("aceite_emulsion", "size"),
                                                        temporada=("temporada", "first"))
    entradas = analisis[analisis["lugar"] == "Entrada"]
    caudal = entradas.groupby(CLAVES_TURNO + ["eboe"])["caudal_fase_pesada"].median().rename("caudal")
    por_eboe = por_eboe.join(caudal).reset_index()
    filas = []
    for clave, sub in por_eboe.groupby(CLAVES_TURNO):
        if clave not in por_turno.index:
            continue
        ponderada = bool(sub["caudal"].notna().all() and (sub["caudal"] > 0).all())
        valor = float(np.average(sub["aceite"], weights=sub["caudal"])) if ponderada else float(sub["aceite"].mean())
        prim = por_turno.loc[clave]
        filas.append({"temporada": sub["temporada"].iloc[0], "fecha_produccion": clave[0], "turno": clave[1],
                      "mes": etiqueta_mes(mes_de(pd.Series([clave[0]])).iloc[0]), "alimentacion": prim["alimentacion"],
                      "boe": valor, "ponderada": ponderada,
                      "dilucion": prim["alimentacion"] / valor if valor > 0 else None,
                      "n_primaria": int(prim["n_primaria"]), "n_boe": int(sub["n"].sum())})
    return pd.DataFrame(filas, columns=columnas)


def resumen_dilucion(turnos: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    """Mediana de la dilución, N turnos, % de turnos con dilución < 1 y si todos fueron ponderados, por grupo (por
    ejemplo ["temporada"] o ["temporada", "mes"]). Columnas: por + mediana, n, pct_menor_1, ponderada."""
    validos = turnos.dropna(subset=["dilucion"])
    if validos.empty:
        return pd.DataFrame(columns=por + ["mediana", "n", "pct_menor_1", "ponderada"])
    grupos = validos.groupby(por, sort=False)
    return grupos.agg(mediana=("dilucion", "median"), n=("dilucion", "size"),
                      pct_menor_1=("dilucion", lambda s: (s < 1).mean() * 100),
                      ponderada=("ponderada", "all")).reset_index()


def pct_sobre_mediana(emulsion: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    """% de muestras de emulsión por encima de la mediana de su temporada, por grupo (por ejemplo ["temporada",
    "mes"] o ["temporada", "eboe"]). Columnas: por + mediana_temporada, n, pct."""
    datos = emulsion.dropna(subset=["aceite_emulsion"])
    if datos.empty:
        return pd.DataFrame(columns=por + ["mediana_temporada", "n", "pct"])
    datos = datos.assign(mediana_temporada=datos.groupby("temporada")["aceite_emulsion"].transform("median"),
                         mes=[etiqueta_mes(m) for m in mes_de(datos["fecha_produccion"])])
    datos = datos.assign(sobre=datos["aceite_emulsion"] > datos["mediana_temporada"])
    return datos.groupby(por, sort=False).agg(mediana_temporada=("mediana_temporada", "first"),
                                              n=("sobre", "size"), pct=("sobre", lambda s: s.mean() * 100)).reset_index()
