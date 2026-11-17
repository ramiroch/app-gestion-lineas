"""Caudal de fase pesada del eBOE con el balance de aceite del licor, y el rango de fase pesada (gráfico Brown).

Balance (por hora, en el eBOE): al licor (emulsión que sale del eBOE) llegan la fase pesada que vuelve de las
centrífugas, el agua de los aspersores y el aceite que el eBOE saca de la fruta. El aceite del licor es el de la fase
pesada más el removido (el agua de los aspersores no trae aceite):

    ω_licor · (FP + agua + removido) = ω_FP · FP + removido

    FP (kg/h) = [removido · (1 − ω_licor) − agua · ω_licor] / (ω_licor − ω_FP)

Pasado a volumen con la densidad de la fase pesada [ω_FP · (ρ_aceite − ρ_agua) + ρ_agua] (core.centrifugas.densidad)
y a m³/h. La planilla de LEDESMA (columna "Caudal fase pesada calc.", docs/mapeo_ledesma.md §5) usa una versión
simplificada: omite (1 − ω_licor) y pone 0,15 % de aceite en la densidad de la fase pesada; da ~1 % más.

- caudal_fp_balance(): el balance, con un ω_licor dado.
- Caudal requerido: con el aceite objetivo en el licor (parámetro omega_licor_objetivo_pct).
- Coherencia del muestreo (licor): el caudal que explicaría el aceite que midió el laboratorio en el licor de ese
  análisis. Si queda por debajo del real, la muestra de licor sale más rica que el licor promedio. Indicador interno:
  no es una pérdida y no se usa en otros análisis (columna "coherente").
- rango_fase_pesada(): (real − requerido) / requerido × 100 (%). Negativo = menos fase pesada que la requerida. Es el
  del gráfico Brown y solo se muestra contra él: como el requerido usa la eficiencia del mismo análisis, comparar la
  eficiencia contra este rango tiene una relación matemática (circularidad).
- Para comparar contra la eficiencia (Sistema › Volatilización y efectos principales) se usan dos variables sin esa
  circularidad: rango_fp_ref, el mismo rango con la eficiencia fija en eficiencia_ref_rango_pct (parámetro de la
  planta), y fp_especifica = caudal real / MTPH (m³/t).
Parámetros de la planta (sin valor por defecto): omega_licor_objetivo_pct, omega_fp_pct, caudal_aspersores_lh y
eficiencia_ref_rango_pct.
"""

import pandas as pd

from core.centrifugas import DENSIDAD_ACUOSA, densidad
from core.parametros import validar_parametros

PARAMETROS_FP = ["omega_licor_objetivo_pct", "omega_fp_pct", "caudal_aspersores_lh", "eficiencia_ref_rango_pct"]
LITROS_POR_M3 = 1000
CLAVES_TURNO = ["fecha_produccion", "turno"]


def caudal_fp_balance(mtph: float, aceite_removido_kg_mt: float, omega_licor_pct: float, omega_fp_pct: float,
                      caudal_agua_lh: float) -> float:
    """Caudal de fase pesada (m³/h) que lleva el licor del eBOE a omega_licor_pct de aceite.

    Q_FP = [MTPH · removido · (1 − ω_licor) − Q_agua · ρ_agua · ω_licor] / ([ω_FP · (ρ_aceite − ρ_agua) + ρ_agua]
           · (ω_licor − ω_FP)) / 1000
    - MTPH · removido: aceite que el eBOE saca de la fruta (kg/h); removido = aceite en fruta de entrada × eficiencia
      eBOE = entrada − salida (kg/MT).
    - × (1 − ω_licor): ese aceite también suma masa al licor, y el licor lleva solo ω_licor de aceite.
    - Q_agua · ρ_agua · ω_licor: el agua de los aspersores (L/h → kg/h) ya diluye el licor; hace falta menos fase
      pesada.
    - (ω_licor − ω_FP): cuánto aceite levanta cada kg de fase pesada (llega con ω_FP y sale con ω_licor).
    - ω_FP · (ρ_aceite − ρ_agua) + ρ_agua: densidad de la fase pesada (kg/L), para pasar de kg/h a L/h; / 1000 → m³/h.
    ω en % (0,9 = 0,9 %); densidades 0,852 y 0,997 kg/L (core.centrifugas). Puede dar ≤ 0 si el agua de los
    aspersores ya alcanza para diluir el licor. Fuente: balance de masa del licor (Brown); planilla de LEDESMA.
    """
    for nombre, valor in (("las MTPH", mtph), ("el aceite removido", aceite_removido_kg_mt),
                          ("el aceite en el licor", omega_licor_pct), ("el aceite en la fase pesada", omega_fp_pct),
                          ("el agua de los aspersores", caudal_agua_lh)):
        if valor is None or pd.isna(valor):
            raise ValueError(f"Falta {nombre}: el caudal de fase pesada no se puede calcular.")
        if valor < 0:
            raise ValueError(f"{nombre[0].upper()}{nombre[1:]} no puede ser negativo (dice {valor}).")
    if mtph <= 0:
        raise ValueError(f"Las MTPH tienen que ser mayores que 0 (dice {mtph}).")
    if omega_licor_pct >= 100:
        raise ValueError(f"El aceite en el licor tiene que ser menor que 100 % (dice {omega_licor_pct}).")
    if omega_licor_pct <= omega_fp_pct:
        raise ValueError(f"El aceite en el licor ({omega_licor_pct} %) tiene que ser mayor que el de la fase pesada "
                         f"({omega_fp_pct} %): con eso la fase pesada no levanta aceite.")
    licor, fase_pesada = omega_licor_pct / 100, omega_fp_pct / 100
    aceite_kgh = mtph * aceite_removido_kg_mt * (1 - licor) - caudal_agua_lh * DENSIDAD_ACUOSA * licor
    return aceite_kgh / (densidad(fase_pesada) * (licor - fase_pesada)) / LITROS_POR_M3


def rango_fase_pesada(real: float | None, requerido: float | None) -> float | None:
    """Rango de fase pesada (%) = (caudal real − requerido) / requerido × 100 (gráfico Brown). Negativo: entra menos
    fase pesada que la requerida. None si falta un dato o si el requerido es ≤ 0 (no hay referencia)."""
    if real is None or requerido is None or pd.isna(real) or pd.isna(requerido) or requerido <= 0:
        return None
    return (real - requerido) / requerido * 100


def _o_nada(funcion, *argumentos):
    """El resultado de funcion, o None si faltan datos o son inválidos (una fila mala no corta la tabla)."""
    try:
        return funcion(*argumentos)
    except ValueError:
        return None


def tabla_fase_pesada(pares: pd.DataFrame, parametros: dict) -> pd.DataFrame:
    """Una fila por par entrada/salida con el caudal de fase pesada real (laboratorio), el requerido, el coherente con
    lo medido y el rango de fase pesada.

    pares: core.analisis.pares_con_emulsion() (aceite_fruta = entrada, eficiencia_par, tph = MTPH,
    caudal_fase_pesada = caudal real del laboratorio en m³/h, aceite_emulsion = aceite en el licor medido).
    parametros: los de PARAMETROS_FP de la planta.
    Agrega removido (kg/MT), requerido y coherente (m³/h), rango_fp_pct (%), requerido_ref (m³/h, con la eficiencia
    fija en eficiencia_ref_rango_pct), rango_fp_ref (%) y fp_especifica (m³/t = caudal real / MTPH). Vacíos si falta un
    dato (por ejemplo, sin MTPH o sin emulsión) o si el balance da ≤ 0.
    """
    omega_fp, agua = parametros["omega_fp_pct"], parametros["caudal_aspersores_lh"]
    datos = pares.assign(removido=pares["aceite_fruta"] * pares["eficiencia_par"])
    requerido = [_o_nada(caudal_fp_balance, m, r, parametros["omega_licor_objetivo_pct"], omega_fp, agua)
                 for m, r in zip(datos["tph"], datos["removido"])]
    coherente = [_o_nada(caudal_fp_balance, m, r, licor, omega_fp, agua)
                 for m, r, licor in zip(datos["tph"], datos["removido"], datos["aceite_emulsion"])]
    datos = datos.assign(requerido=pd.Series(requerido, index=datos.index, dtype=float),
                         coherente=pd.Series(coherente, index=datos.index, dtype=float))
    datos.loc[datos["requerido"] <= 0, "requerido"] = None
    datos.loc[datos["coherente"] <= 0, "coherente"] = None
    rango = [rango_fase_pesada(real, req) for real, req in zip(datos["caudal_fase_pesada"], datos["requerido"])]
    eficiencia_ref = parametros["eficiencia_ref_rango_pct"] / 100
    requerido_ref = [_o_nada(caudal_fp_balance, m, entrada * eficiencia_ref, parametros["omega_licor_objetivo_pct"],
                             omega_fp, agua) for m, entrada in zip(datos["tph"], datos["aceite_fruta"])]
    datos = datos.assign(rango_fp_pct=pd.Series(rango, index=datos.index, dtype=float),
                         requerido_ref=pd.Series(requerido_ref, index=datos.index, dtype=float))
    datos.loc[datos["requerido_ref"] <= 0, "requerido_ref"] = None
    rango_ref = [rango_fase_pesada(real, req) for real, req in zip(datos["caudal_fase_pesada"], datos["requerido_ref"])]
    mtph = datos["tph"].where(datos["tph"] > 0)
    return datos.assign(rango_fp_ref=pd.Series(rango_ref, index=datos.index, dtype=float),
                        fp_especifica=datos["caudal_fase_pesada"] / mtph)


def parametros_fase_pesada(planta) -> dict:
    """Los parámetros del balance de la planta (core.parametros: error claro si falta alguno)."""
    return validar_parametros(planta, PARAMETROS_FP)


def mediana_por(tabla: pd.DataFrame, claves: list[str]) -> pd.DataFrame:
    """Medianas de los análisis por grupo (claves: por turno CLAVES_TURNO, por día ["fecha_produccion"], por
    temporada ["temporada"], por temporada y eBOE…): rango de fase pesada (%) y caudales real, requerido y coherente
    (m³/h), con N = análisis con rango. Columnas: claves + rango_fp_pct, real, requerido, coherente, n."""
    validos = tabla.dropna(subset=["rango_fp_pct"])
    columnas = claves + ["rango_fp_pct", "real", "requerido", "coherente", "n"]
    if validos.empty:
        return pd.DataFrame(columns=columnas)
    grupos = validos.groupby(claves, sort=True)
    resultado = grupos.agg(rango_fp_pct=("rango_fp_pct", "median"), real=("caudal_fase_pesada", "median"),
                           requerido=("requerido", "median"), coherente=("coherente", "median"),
                           n=("rango_fp_pct", "size")).reset_index()
    return resultado[columnas]
