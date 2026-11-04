"""Gráficos de control de las variables de la planta: arma las muestras y los subgrupos y elige la carta.

La estadística (límites, reglas de Nelson, fases) está en core/spc.py; acá se preparan los datos de
core.analisis (tabla_analisis y tabla_emulsion, ya filtradas) para un eBOE:
- Por turno: un subgrupo = las muestras de ese eBOE en ese turno de producción. Si todos los turnos
  tienen el mismo tamaño se usa X̄-R; si no (lo normal: de 1 a 4 muestras por turno), X̄-S con límites
  por subgrupo, como Minitab.
- Por muestra: carta I-MR, un punto por análisis.
- Fases: los límites se calculan con la temporada base (fase I) y se aplican a la nueva (fase II);
  sin fases, con todo el período.
"""

import numpy as np
import pandas as pd

from core.formato import numero
from core.spc import REGLAS, carta_imr, carta_xbar_r, carta_xbar_s, evaluar_carta

# clave → nombre, unidad y decimales para mostrar
VARIABLES = {
    "eficiencia": {"nombre": "Eficiencia eBOE", "unidad": "%", "decimales": 1},
    "aceite_salida": {"nombre": "Aceite en fruta de salida", "unidad": "kg/MT", "decimales": 2},
    "aceite_entrada": {"nombre": "Aceite en fruta de entrada", "unidad": "kg/MT", "decimales": 2},
    "aceite_emulsion": {"nombre": "Aceite en emulsión", "unidad": "%", "decimales": 2},
}
POR_TURNO, POR_MUESTRA = "turno", "muestra"
DESCRIPTORES = ["fecha_hora", "fecha_produccion", "turno", "temporada", "proceso", "programa", "tph"]
COLUMNAS_MUESTRAS = DESCRIPTORES + ["eboe", "valor"]


def muestras_de(fruta: pd.DataFrame, emulsion: pd.DataFrame, variable: str) -> pd.DataFrame:
    """Un renglón por análisis con el valor de la variable (clave de VARIABLES), ordenado por fecha_hora.

    eficiencia: filas de Entrada con par (eficiencia_par × 100, %); aceite_entrada y aceite_salida:
    aceite_fruta de las filas de Entrada o Salida (kg/MT); aceite_emulsion: tabla de emulsión (%).
    Las filas sin valor no entran. Columnas: COLUMNAS_MUESTRAS.
    """
    if variable == "eficiencia":
        filas = fruta[fruta["lugar"] == "Entrada"].assign(valor=fruta["eficiencia_par"] * 100)
    elif variable == "aceite_entrada":
        filas = fruta[fruta["lugar"] == "Entrada"].assign(valor=fruta["aceite_fruta"])
    elif variable == "aceite_salida":
        filas = fruta[fruta["lugar"] == "Salida"].assign(valor=fruta["aceite_fruta"])
    elif variable == "aceite_emulsion":
        filas = emulsion.assign(valor=emulsion["aceite_emulsion"])
    else:
        raise ValueError(f"Variable desconocida: {variable}. Las válidas son: {', '.join(VARIABLES)}.")
    filas = filas.dropna(subset=["valor"])
    return filas[COLUMNAS_MUESTRAS].sort_values(["fecha_hora", "eboe"], ignore_index=True)


def _unicos(serie: pd.Series) -> str:
    """Valores distintos, en el orden en que aparecen: "N3" o "N3, N4"; vacío si no hay."""
    return ", ".join(str(v) for v in pd.unique(serie.dropna()))


def subgrupos_por_turno(muestras: pd.DataFrame) -> pd.DataFrame:
    """Un subgrupo por turno de producción (muestras de un mismo eBOE), en orden cronológico.

    Columnas: fecha_hora (de la primera muestra), fecha_produccion, turno, temporada, proceso y
    programa (los distintos del turno, separados por coma), mtph (promedio), n, media, rango y desvio
    (desvío estándar muestral; vacío si n = 1).
    """
    filas = []
    for (fecha, turno), sub in muestras.groupby(["fecha_produccion", "turno"], sort=False):
        valores = sub["valor"]
        filas.append({
            "fecha_hora": sub["fecha_hora"].min(), "fecha_produccion": fecha, "turno": turno,
            "temporada": sub["temporada"].iloc[0], "proceso": _unicos(sub["proceso"]),
            "programa": _unicos(sub["programa"]), "mtph": sub["tph"].mean(), "n": len(valores),
            "media": valores.mean(), "rango": valores.max() - valores.min(),
            "desvio": valores.std(ddof=1) if len(valores) > 1 else np.nan,
        })
    columnas = ["fecha_hora", "fecha_produccion", "turno", "temporada", "proceso", "programa", "mtph", "n", "media",
                "rango", "desvio"]
    return pd.DataFrame(filas, columns=columnas).sort_values("fecha_hora", ignore_index=True)


def carta_de_control(muestras: pd.DataFrame, por: str, fases: tuple | None = None, reglas=REGLAS) -> dict:
    """Carta de control de las muestras de un eBOE, evaluada con las reglas de Nelson elegidas.

    por: POR_TURNO (X̄-R si todos los turnos tienen el mismo tamaño, si no X̄-S) o POR_MUESTRA (I-MR).
    fases: (temporada base, temporada nueva) → solo esas dos temporadas, límites con la base; None →
    límites con todos los datos (todo es fase I).
    Devuelve {"tipo": "X̄-R" | "X̄-S" | "I-MR", "dispersion": "R" | "S" | "MR", "tabla": DataFrame}. La
    tabla tiene un renglón por punto: punto (1, 2, …), los datos del turno o de la muestra (fecha_hora,
    fecha_produccion, turno, temporada, proceso, programa, mtph, n) y las columnas de
    core.spc.evaluar_carta() (valor, lc, lcs, lci, sigma, disp…, fase, reglas, fuera_disp, alguna).
    """
    if muestras["eboe"].nunique() > 1:
        raise ValueError("La carta de control es de un solo eBOE: filtrá las muestras antes.")
    if fases is not None:
        base, nueva = fases
        muestras = muestras[muestras["temporada"].isin([base, nueva])]
        if not (muestras["temporada"] == base).any():
            raise ValueError(f"La temporada {base} no tiene datos con estos filtros: no hay período base.")

    if por == POR_MUESTRA:
        datos = muestras.rename(columns={"tph": "mtph"}).assign(n=1).reset_index(drop=True)
        mascara = None if fases is None else (datos["temporada"] == fases[0]).to_numpy()
        carta, tipo, dispersion = carta_imr(datos["valor"], mascara), "I-MR", "MR"
    elif por == POR_TURNO:
        datos = subgrupos_por_turno(muestras)
        if datos.empty or (datos["n"] == 1).all():
            raise ValueError("Ningún turno tiene 2 o más muestras: usá la carta I-MR por muestra.")
        mascara = None if fases is None else (datos["temporada"] == fases[0]).to_numpy()
        tamanos = datos["n"].unique()
        if len(tamanos) == 1:
            carta, tipo, dispersion = carta_xbar_r(datos["media"], datos["rango"], int(tamanos[0]), mascara), "X̄-R", "R"
        else:
            carta, tipo, dispersion = carta_xbar_s(datos["media"], datos["desvio"], datos["n"], mascara), "X̄-S", "S"
    else:
        raise ValueError(f"Tipo de carta desconocido: {por} (tiene que ser '{POR_TURNO}' o '{POR_MUESTRA}').")

    evaluada = evaluar_carta(carta, reglas)
    descriptores = datos[["fecha_hora", "fecha_produccion", "turno", "temporada", "proceso", "programa", "mtph", "n"]]
    tabla = pd.concat([descriptores.reset_index(drop=True), evaluada.reset_index(drop=True)], axis=1)
    tabla.insert(0, "punto", np.arange(1, len(tabla) + 1))
    return {"tipo": tipo, "dispersion": dispersion, "tabla": tabla}


def frase_control(resumen: pd.DataFrame, eboe, por: str, fases: tuple | None = None) -> str:
    """Frase simple con el % de puntos que violan alguna regla (resumen de core.spc.resumen_reglas()).

    Con fases: "En 2026, el 18 % de los turnos del eBOE 2 violan alguna regla, contra el 6 % en 2025
    (con los límites de 2025)." Sin fases: "En el período filtrado, el 12 % de …".
    """
    alguna = resumen.set_index("regla").loc["alguna"]
    puntos = "los turnos" if por == POR_TURNO else "las muestras"

    def pct(fase):
        return f"el {numero(alguna[f'pct_{fase}'], 0)} %"

    if fases is None:
        return (f"En el período filtrado, {pct('I')} de {puntos} del eBOE {eboe} violan alguna regla "
                f"(con límites calculados con ese mismo período).")
    base, nueva = fases
    if pd.isna(alguna["pct_II"]):
        return (f"No hay {puntos.split()[1]} de {nueva} con estos filtros. En {base}, {pct('I')} de {puntos} del "
                f"eBOE {eboe} violan alguna regla (con sus propios límites).")
    return (f"En {nueva}, {pct('II')} de {puntos} del eBOE {eboe} violan alguna regla, contra "
            f"{pct('I')} en {base} (con los límites de {base}).")
