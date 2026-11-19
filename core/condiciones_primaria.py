"""Condiciones de buena eficiencia de la centrífuga primaria (Centrífugas › Condiciones de eficiencia; informe Brown
2023: "hasta 16.000 L/h, buena performance" y "más caudal de fase liviana → más eficiencia").

Datos: el balance de cada muestreo de las primarias (core.analisis_centrifugas.balance_por_muestreo), sin copiados ni
fuera de rango físico (dato inválido), como en el resto de la app.
- por_rango_caudal(): eficiencia por rango de caudal de alimentación (de a 1.000 L/h) y temporada, con IC 95 % y N.
- por_cuantiles(): eficiencia por quintil de otra variable (caudal de fase liviana, aceite en la emulsión, CP de la
  fase liviana), con los mismos cortes en todas las temporadas.
- hasta_nominal(): eficiencia con caudal hasta el nominal (máximo del rango "Caudal de emulsión" de RangoReferencia,
  16.000 L/h de GEA) y por encima, por temporada: medias simples y diferencia ajustada por equipo, mes, turno, químico
  y aceite en la emulsión (cuartiles), con su p.
- pendiente(): cuánto cambia la eficiencia por unidad de una variable, por temporada, ajustada por lo mismo.
Ojo: la eficiencia sale del caudal de fase liviana (eficiencia = Q liviana × ρ × aceite liviana / aceite que entra),
así que "más fase liviana → más eficiencia" se cumple por construcción. La CP de la fase liviana es una medición
independiente del balance.
"""

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from core.analisis_centrifugas import PRIMARIA
from core.efectos import contraste
from core.factores import asignar_cuartil, cortes_cuartiles
from core.formato import numero
from core.mensual import MESES
from core.volatilizacion import media_ic

ANCHO_CAUDAL = 1000                     # L/h por rango de caudal de alimentación
AJUSTE = ["equipo", "mes", "turno", "analista", "aceite_rango"]
N_MINIMO_LADO = 10                      # datos mínimos de cada lado del nominal para comparar
ALFA = 0.05
VARIABLES = {"caudal_liviana": ("Caudal de fase liviana", "L/h", 0),
             "aceite_emulsion": ("Aceite en la emulsión de alimentación", "%", 2),
             "cp_liviana": ("CP de la fase liviana", "bar", 2),
             "caudal_emulsion": ("Caudal de alimentación", "L/h", 0)}


def cp_liviana_por_muestreo(muestras: pd.DataFrame) -> pd.DataFrame:
    """CP de la fase liviana de cada muestreo (mediana de las muestras con CP medible; las marcadas "no medible" no
    entran). muestras: core.analisis_centrifugas.tabla_muestras. Columnas: codigo, fecha_hora, cp_liviana (bar)."""
    medibles = muestras[muestras["cp_liviana_marca"].isna()].dropna(subset=["cp_liviana_bar"])
    return (medibles.groupby(["codigo", "fecha_hora"], as_index=False)["cp_liviana_bar"].median()
            .rename(columns={"cp_liviana_bar": "cp_liviana"}))


def datos_primarias(balance: pd.DataFrame, cp: pd.DataFrame | None = None, personas: pd.DataFrame | None = None
                    ) -> pd.DataFrame:
    """Balances de las primarias con eficiencia válida (sin copiados ni fuera de rango físico), con la CP liviana
    (cp_liviana_por_muestreo) y el químico y el operador (core.personas.personas_centrifugas) si se pasan. Agrega
    mes (nombre del mes calendario), aceite_rango (cuartil del aceite en la emulsión, cortes de todos los datos) y
    caudal_desde (inicio del rango de ANCHO_CAUDAL L/h). equipo, temporada y turno como texto."""
    datos = balance[(balance["tipo"] == PRIMARIA) & ~balance["copiada"].fillna(False).astype(bool)
                    & ~balance["fuera_fisico"].fillna(False).astype(bool) & balance["eficiencia_pct"].notna()].copy()
    for extra in (cp, personas):
        if extra is not None and not extra.empty:
            datos = datos.drop(columns=[c for c in extra if c not in ("codigo", "fecha_hora")], errors="ignore").merge(
                extra, on=["codigo", "fecha_hora"], how="left")
    if "cp_liviana" not in datos:
        datos["cp_liviana"] = np.nan
    if "analista" not in datos:
        datos["analista"] = None
    for columna in ("equipo", "temporada", "turno"):
        datos[columna] = datos[columna].map(lambda v: None if pd.isna(v) else str(v))
    fechas = pd.to_datetime(datos["fecha_produccion"]) if "fecha_produccion" in datos else pd.Series(pd.NaT, datos.index)
    datos["mes"] = fechas.dt.month.map(lambda m: None if pd.isna(m) else MESES[int(m) - 1])
    aceite = datos["aceite_emulsion"] if "aceite_emulsion" in datos else pd.Series(np.nan, datos.index)
    datos["aceite_rango"] = (asignar_cuartil(aceite, cortes_cuartiles(aceite)).astype("string")
                             if aceite.notna().sum() >= 4 else None)
    caudal = datos["caudal_emulsion"] if "caudal_emulsion" in datos else pd.Series(np.nan, datos.index)
    datos["caudal_desde"] = (caudal // ANCHO_CAUDAL) * ANCHO_CAUDAL
    return datos.reset_index(drop=True)


def _media_ic(valores) -> dict:
    """media_ic() con IC solo si N ≥ 3: con 2 datos el IC de la t es enorme y no informa (el N < 3 se avisa)."""
    resultado = media_ic(valores)
    if resultado["n"] < 3:
        resultado.update(ic_inf=None, ic_sup=None)
    return resultado


def etiqueta_caudal(desde: float, ancho: int = ANCHO_CAUDAL) -> str:
    """15000 → "15.000–16.000"."""
    return f"{numero(desde, 0)}–{numero(desde + ancho, 0)}"


def por_rango_caudal(datos: pd.DataFrame) -> pd.DataFrame:
    """Eficiencia media, IC 95 % y N por temporada y rango de caudal de alimentación (ANCHO_CAUDAL L/h). Columnas:
    temporada, desde, rango, n, media, ic_inf, ic_sup (IC solo con N ≥ 3); en orden de caudal."""
    filas = [{"temporada": temporada, "desde": desde, "rango": etiqueta_caudal(desde),
              **_media_ic(sub["eficiencia_pct"])}
             for (temporada, desde), sub in datos.dropna(subset=["caudal_desde"]).groupby(["temporada", "caudal_desde"])]
    return pd.DataFrame(filas, columns=["temporada", "desde", "rango", "n", "media", "ic_inf", "ic_sup"])


def por_cuantiles(datos: pd.DataFrame, variable: str, partes: int = 5) -> pd.DataFrame:
    """Eficiencia media, IC 95 % y N por temporada y quintil (o `partes`) de la variable, con los mismos cortes en
    todas las temporadas. Columnas: temporada, orden (1…partes), grupo ("≤ 150", "150–180", …, "> 350"), n, media,
    ic_inf, ic_sup."""
    columnas = ["temporada", "orden", "grupo", "n", "media", "ic_inf", "ic_sup"]
    valores = datos[variable].dropna()
    if len(valores) < partes:
        return pd.DataFrame(columns=columnas)
    cortes = sorted(set(float(valores.quantile(q / partes)) for q in range(1, partes)))
    decimales = VARIABLES[variable][2]
    c = [numero(v, decimales) for v in cortes]
    etiquetas = [f"≤ {c[0]}", *[f"{a}–{b}" for a, b in zip(c[:-1], c[1:])], f"> {c[-1]}"]
    orden = pd.cut(datos[variable], [-np.inf, *cortes, np.inf], labels=range(1, len(cortes) + 2))
    filas = [{"temporada": temporada, "orden": int(k), "grupo": etiquetas[int(k) - 1],
              **_media_ic(sub["eficiencia_pct"])}
             for (temporada, k), sub in datos.assign(_orden=orden).dropna(subset=["_orden"]).groupby(
                 ["temporada", "_orden"], observed=True)]
    return pd.DataFrame(filas, columns=columnas)


def _modelo(sub: pd.DataFrame, termino: str, ajuste: list[str]):
    """OLS eficiencia ~ termino + factores de ajuste que varían en sub (sin vacíos)."""
    factores = [f for f in ajuste if f in sub and sub[f].notna().all() and sub[f].nunique() > 1]
    return smf.ols(f"eficiencia_pct ~ {termino}" + "".join(f" + C({f})" for f in factores), data=sub).fit()


def hasta_nominal(datos: pd.DataFrame, nominal: float, ajuste: list[str] = AJUSTE,
                  n_minimo: int = N_MINIMO_LADO) -> pd.DataFrame:
    """Eficiencia con caudal de alimentación ≤ nominal y > nominal (L/h), por temporada. Medias simples con N y
    diferencia (por encima − hasta el nominal, puntos) ajustada por `ajuste`, con su p. Sin diferencia si algún lado
    tiene menos de n_minimo datos. Columnas: temporada, n_hasta, media_hasta, n_sobre, media_sobre, dif, p."""
    filas = []
    for temporada, sub in datos.dropna(subset=["caudal_emulsion"]).groupby("temporada", sort=True):
        sub = sub.dropna(subset=[f for f in ajuste if f in sub and sub[f].notna().any()]).assign(
            sobre=lambda d: (d["caudal_emulsion"] > nominal).astype(int))
        hasta, sobre = sub[sub["sobre"] == 0], sub[sub["sobre"] == 1]
        fila = {"temporada": temporada, "n_hasta": len(hasta), "media_hasta": hasta["eficiencia_pct"].mean(),
                "n_sobre": len(sobre), "media_sobre": sobre["eficiencia_pct"].mean(), "dif": None, "p": None}
        if len(hasta) >= n_minimo and len(sobre) >= n_minimo:
            modelo = _modelo(sub, "sobre", ajuste)
            resultado = contraste(modelo, np.array([n == "sobre" for n in modelo.model.exog_names], dtype=float))
            if resultado:
                fila["dif"], fila["p"] = resultado
        filas.append(fila)
    return pd.DataFrame(filas, columns=["temporada", "n_hasta", "media_hasta", "n_sobre", "media_sobre", "dif", "p"])


def pendiente(datos: pd.DataFrame, variable: str, por: float = 1.0, ajuste: list[str] = AJUSTE,
              n_minimo: int = 20) -> pd.DataFrame:
    """Cambio de la eficiencia (puntos) por cada `por` unidades de la variable, por temporada, ajustado por `ajuste`
    (sin el aceite en la emulsión si la variable es ese aceite). Columnas: temporada, n, pendiente, ic_inf, ic_sup, p."""
    ajuste = [f for f in ajuste if not (variable == "aceite_emulsion" and f == "aceite_rango")]
    filas = []
    for temporada, sub in datos.dropna(subset=[variable]).groupby("temporada", sort=True):
        sub = sub.dropna(subset=[f for f in ajuste if f in sub and sub[f].notna().any()])
        if len(sub) < n_minimo or sub[variable].nunique() < 3:
            continue
        modelo = _modelo(sub.assign(_x=sub[variable] / por), "_x", ajuste)
        inferior, superior = modelo.conf_int(ALFA).loc["_x"]
        filas.append({"temporada": temporada, "n": len(sub), "pendiente": float(modelo.params["_x"]),
                      "ic_inf": float(inferior), "ic_sup": float(superior), "p": float(modelo.pvalues["_x"])})
    return pd.DataFrame(filas, columns=["temporada", "n", "pendiente", "ic_inf", "ic_sup", "p"])


def frase_nominal(tabla: pd.DataFrame, nominal: float, alfa: float = ALFA) -> str:
    """¿Se repite "hasta el nominal, buena performance"? Sí si en todas las temporadas comparables la eficiencia por
    encima del nominal es significativamente menor (ajustada); en parte si pasa solo en algunas."""
    caudal = numero(nominal, 0)
    comparables = tabla.dropna(subset=["dif"])
    if comparables.empty:
        return f"No hay temporadas con datos suficientes de los dos lados de {caudal} L/h para comparar."
    peor = (comparables["dif"] < 0) & (comparables["p"] < alfa)
    sin_datos = [str(t) for t in tabla.loc[tabla["dif"].isna(), "temporada"]]
    nota = f" ({', '.join(sin_datos)}: pocos datos de un lado)" if sin_datos else ""
    if peor.all():
        return f"Se repite: por encima de {caudal} L/h la eficiencia es menor en todas las temporadas comparables{nota}."
    if not peor.any():
        return f"No se repite: por encima de {caudal} L/h la eficiencia no es significativamente menor{nota}."
    temporadas = ", ".join(str(t) for t in comparables.loc[peor, "temporada"])
    return f"Se repite solo en {temporadas}: en las otras temporadas, por encima de {caudal} L/h no es menor{nota}."


def frase_pendiente(tabla: pd.DataFrame, alfa: float = ALFA) -> str:
    """"sube" si la pendiente es positiva y significativa en todas las temporadas, "baja" si negativa en todas, "en
    parte" si en algunas, "no cambia" si en ninguna."""
    if tabla.empty:
        return "sin datos"
    positiva = (tabla["pendiente"] > 0) & (tabla["p"] < alfa)
    negativa = (tabla["pendiente"] < 0) & (tabla["p"] < alfa)
    if positiva.all():
        return "sube"
    if negativa.all():
        return "baja"
    if not (positiva | negativa).any():
        return "no cambia"
    return "en parte"
