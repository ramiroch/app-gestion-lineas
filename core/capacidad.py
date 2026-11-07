"""Capacidad de proceso: Cp, Cpk, Pp, Ppk, PPM fuera de especificación, normalidad y Box-Cox.

Fuente: Montgomery, Introduction to Statistical Quality Control (cap. 8), y los métodos de Minitab ("Capability
Analysis (Normal)", "Normality Test", "Box-Cox Transformation").

- Sigma dentro (variación de corto plazo, la de los subgrupos o la de un dato al siguiente) → Cp, Cpk, Cpl, Cpu.
  · Datos individuales: MR̄ / d2, con MR̄ = media de |xᵢ − xᵢ₋₁| y d2(2) = 1,128.
  · Con subgrupos: desvío combinado (pooled) Sp = √(Σ (xᵢⱼ − x̄ᵢ)² / Σ (nᵢ − 1)), dividido por c4(d + 1), con
    d = Σ (nᵢ − 1) (como Minitab por defecto; usar_c4=False lo deja sin corregir).
- Sigma total (variación de largo plazo: todos los datos juntos) → Pp, Ppk, Ppl, Ppu. Desvío estándar de todos
  los datos (n − 1); usar_c4_total=True lo divide por c4(N).
- Índices: Cpu = (LSE − x̄) / 3σ, Cpl = (x̄ − LIE) / 3σ, Cpk = mín(Cpl, Cpu), Cp = (LSE − LIE) / 6σ; igual con
  sigma total para Pp. Con un solo límite, solo el índice de ese lado y Cp/Pp "no aplica".
- PPM: observado = datos fuera / N × 10⁶; esperado = P(X < LIE) + P(X > LSE) con una normal de media x̄ y sigma
  dentro (o total), × 10⁶.
- Datos no normales (Anderson-Darling p < 0,05): índices por percentiles (ISO 22514-2), capacidad_percentiles().
- Estabilidad: carta I-MR y regla 1 de Nelson (core.spc), estabilidad(). Un proceso inestable no tiene una
  capacidad definida: los índices quedan como orientativos.
"""

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.diagnostic import normal_ad

from core.spc import c4, carta_imr, d2, evaluar_carta

NO_APLICA = "no aplica"
N_MINIMO = 3
N_CONFIABLE = 30
AVISO_POCOS = "N < 30: índices poco confiables."
AVISO_NO_CALCULA = "N < 3: no se calcula la capacidad."
AVISO_PERCENTILES = "N < 100: percentiles extremos poco confiables."
AVISO_INESTABLE = "Proceso inestable: los índices son orientativos; mirar el % observado."
N_PERCENTILES = 100
P_BAJO, P_ALTO = 0.00135, 0.99865          # equivalen a ±3σ en una normal
METODO_PERCENTILES = "weibull"             # (n + 1)·p, como los cuartiles de Minitab
FUERA_POR_AZAR = 0.0027                    # en una normal estable, 0,27 % de los puntos cae fuera de ±3σ
ESTABLE, INESTABLE = "estable", "inestable"
LAMBDAS_MINITAB = (-5.0, 5.0)        # rango de búsqueda de λ de Box-Cox
PASO_REDONDEO = 0.5                  # λ redondeado: 0,5, 0, −0,5, …
MILLON = 1_000_000


def _limpios(valores) -> np.ndarray:
    return pd.Series(valores, dtype="float64").dropna().to_numpy()


# ---------------------------------------------------------------- sigmas

def sigma_dentro_individuales(x) -> float | None:
    """Sigma dentro para datos individuales: MR̄ / d2(2) (d2 = 1,128). MR̄ = media de |xᵢ − xᵢ₋₁|, en el orden
    en que llegan los datos (el orden en el tiempo importa)."""
    x = np.asarray(x, dtype=float)
    if len(x) < 2:
        return None
    return float(np.mean(np.abs(np.diff(x))) / d2(2))


def sigma_dentro_subgrupos(x, grupos, usar_c4: bool = True) -> float | None:
    """Sigma dentro con subgrupos: desvío combinado Sp = √(Σ (xᵢⱼ − x̄ᵢ)² / Σ (nᵢ − 1)), dividido por c4(d + 1)
    con d = Σ (nᵢ − 1) si usar_c4 (Minitab por defecto). Los subgrupos de 1 dato no aportan."""
    datos = pd.DataFrame({"x": np.asarray(x, dtype=float), "g": np.asarray(grupos)})
    desvios = datos.groupby("g")["x"].agg(lambda s: ((s - s.mean()) ** 2).sum())
    libertad = int((datos.groupby("g").size() - 1).sum())
    if libertad < 1:
        return None
    sp = float(np.sqrt(desvios.sum() / libertad))
    return sp / c4(libertad + 1) if usar_c4 else sp


def grupos_consecutivos(n: int, tamano: int) -> np.ndarray:
    """Etiquetas de subgrupo para n datos tomados de a `tamano` seguidos (0, 0, 0, 1, 1, 1, …)."""
    return np.arange(n) // tamano


# ---------------------------------------------------------------- índices

def indices(media: float, sigma: float | None, lie: float | None, lse: float | None) -> dict:
    """Cp, Cpk, Cpl, Cpu de una media y un sigma (con sigma total son Pp, Ppk, Ppl, Ppu).

    Cpu = (LSE − media) / 3σ; Cpl = (media − LIE) / 3σ; Cpk = mín de los que hay; Cp = (LSE − LIE) / 6σ, o
    "no aplica" si falta un límite. None si no hay sigma o es 0.
    """
    if sigma is None or sigma <= 0:
        return {"cp": None, "cpk": None, "cpl": None, "cpu": None}
    cpl = None if lie is None else (media - lie) / (3 * sigma)
    cpu = None if lse is None else (lse - media) / (3 * sigma)
    cp = NO_APLICA if lie is None or lse is None else (lse - lie) / (6 * sigma)
    lados = [v for v in (cpl, cpu) if v is not None]
    return {"cp": cp, "cpk": min(lados) if lados else None, "cpl": cpl, "cpu": cpu}


def ppm_esperado(media: float, sigma: float | None, lie: float | None, lse: float | None) -> dict | None:
    """PPM esperados fuera de especificación con una normal de esa media y sigma: {"lie", "lse", "total"}."""
    if sigma is None or sigma <= 0:
        return None
    abajo = 0.0 if lie is None else stats.norm.cdf(lie, media, sigma) * MILLON
    arriba = 0.0 if lse is None else stats.norm.sf(lse, media, sigma) * MILLON
    return {"lie": float(abajo), "lse": float(arriba), "total": float(abajo + arriba)}


def ppm_observado(x, lie: float | None, lse: float | None) -> dict:
    """PPM observados: datos por debajo del LIE y por encima del LSE, sobre N, × 10⁶."""
    x = np.asarray(x, dtype=float)
    abajo = 0.0 if lie is None else float(np.mean(x < lie)) * MILLON
    arriba = 0.0 if lse is None else float(np.mean(x > lse)) * MILLON
    return {"lie": abajo, "lse": arriba, "total": abajo + arriba}


def capacidad(valores, lie: float | None = None, lse: float | None = None, subgrupos=None, usar_c4: bool = True,
              usar_c4_total: bool = False) -> dict:
    """Capacidad del proceso contra la especificación [LIE, LSE] (uno de los dos puede faltar).

    valores: datos en orden de tiempo (los vacíos se descartan, junto con su subgrupo). subgrupos: None (datos
    individuales: sigma dentro = MR̄ / d2), un entero (subgrupos de ese tamaño, seguidos) o una etiqueta por dato.
    Devuelve n, media, sigma_dentro, sigma_total, metodo_dentro, cp, cpk, cpl, cpu, pp, ppk, ppl, ppu,
    ppm_observado, ppm_esperado_dentro, ppm_esperado_total (cada PPM con "lie", "lse" y "total"), lie, lse y avisos.
    Con N < 3 no calcula (índices None); con N < 30 avisa que los índices son poco confiables.
    """
    if lie is None and lse is None:
        raise ValueError("Hace falta al menos un límite de especificación (LIE o LSE).")
    if lie is not None and lse is not None and lie >= lse:
        raise ValueError(f"El LIE ({lie}) tiene que ser menor que el LSE ({lse}).")
    serie = pd.Series(valores, dtype="float64").reset_index(drop=True)
    if subgrupos is not None and not isinstance(subgrupos, (int, np.integer)):
        grupos = pd.Series(np.asarray(subgrupos)).reset_index(drop=True)
    elif subgrupos is not None:
        grupos = pd.Series(grupos_consecutivos(len(serie), int(subgrupos)))
    else:
        grupos = None
    validos = serie.notna()
    x = serie[validos].to_numpy()
    n = len(x)
    resultado = {"n": n, "lie": lie, "lse": lse, "media": None, "sigma_dentro": None, "sigma_total": None,
                 "metodo_dentro": None, "ppm_observado": None, "ppm_esperado_dentro": None, "ppm_esperado_total": None,
                 "avisos": []}
    resultado |= {k: None for k in ("cp", "cpk", "cpl", "cpu", "pp", "ppk", "ppl", "ppu")}
    if n < N_MINIMO:
        resultado["avisos"].append(AVISO_NO_CALCULA)
        return resultado
    if n < N_CONFIABLE:
        resultado["avisos"].append(AVISO_POCOS)

    media = float(np.mean(x))
    if grupos is None:
        dentro, metodo = sigma_dentro_individuales(x), "MR̄ / d2 (datos individuales)"
    else:
        dentro = sigma_dentro_subgrupos(x, grupos[validos].to_numpy(), usar_c4)
        metodo = "desvío combinado / c4" if usar_c4 else "desvío combinado"
    total = float(np.std(x, ddof=1))
    if usar_c4_total:
        total /= c4(n)
    con_dentro, con_total = indices(media, dentro, lie, lse), indices(media, total, lie, lse)
    resultado.update(media=media, sigma_dentro=dentro, sigma_total=total, metodo_dentro=metodo,
                     cp=con_dentro["cp"], cpk=con_dentro["cpk"], cpl=con_dentro["cpl"], cpu=con_dentro["cpu"],
                     pp=con_total["cp"], ppk=con_total["cpk"], ppl=con_total["cpl"], ppu=con_total["cpu"],
                     ppm_observado=ppm_observado(x, lie, lse),
                     ppm_esperado_dentro=ppm_esperado(media, dentro, lie, lse),
                     ppm_esperado_total=ppm_esperado(media, total, lie, lse))
    return resultado


# ---------------------------------------------------------------- normalidad y Box-Cox

def normalidad(valores) -> dict:
    """Prueba de normalidad de Anderson-Darling (statsmodels normal_ad), como Minitab.

    A² = −n − (1/n)·Σ (2i − 1)·[ln F(zᵢ) + ln(1 − F(zₙ₊₁₋ᵢ))], con z estandarizado con la media y el desvío
    muestral. El p sale de A²·(1 + 0,75/n + 2,25/n²) con las fórmulas de D'Agostino y Stephens (1986), las mismas
    de Minitab. Devuelve n, ad (A², el que muestra Minitab), p y normal (p ≥ 0,05). Con N < 3, ad y p None.
    """
    x = _limpios(valores)
    if len(x) < N_MINIMO or np.std(x) == 0:
        return {"n": len(x), "ad": None, "p": None, "normal": None}
    ad, p = normal_ad(x)
    return {"n": len(x), "ad": float(ad), "p": float(p), "normal": bool(p >= 0.05)}


def transformar(valores, lam: float) -> np.ndarray:
    """Box-Cox como la usa Minitab en capacidad: Y^λ (ln Y si λ = 0). Necesita valores > 0."""
    y = np.asarray(valores, dtype=float)
    return np.log(y) if lam == 0 else y ** lam


def box_cox(valores, lie: float | None = None, lse: float | None = None, subgrupos=None,
            usar_c4: bool = True, usar_c4_total: bool = False) -> dict:
    """Transformación de Box-Cox para datos no normales, y capacidad y normalidad de los datos transformados.

    λ estimado por máxima verosimilitud (scipy boxcox_normmax) en [−5, 5] y redondeado a 0,5 (como el "λ
    redondeado" de Minitab); se usa el redondeado. Datos y límites se transforman con Y^λ (ln Y si λ = 0); con
    λ < 0 la transformación invierte el orden y los límites se dan vuelta. Un límite ≤ 0 no se puede transformar
    y ese lado queda sin especificación. Si algún dato es ≤ 0, avisa y no transforma.
    Devuelve lambda_estimado, lambda_redondeado, datos (transformados), lie, lse (transformados), capacidad,
    normalidad y avisos.
    """
    x = _limpios(valores)
    resultado = {"lambda_estimado": None, "lambda_redondeado": None, "datos": None, "lie": None, "lse": None,
                 "capacidad": None, "normalidad": None, "avisos": []}
    if len(x) < N_MINIMO:
        resultado["avisos"].append(AVISO_NO_CALCULA)
        return resultado
    if (x <= 0).any():
        resultado["avisos"].append("Hay valores ≤ 0: Box-Cox necesita datos positivos. No se transforma.")
        return resultado
    estimado = float(np.clip(stats.boxcox_normmax(x, method="mle"), *LAMBDAS_MINITAB))
    lam = round(estimado / PASO_REDONDEO) * PASO_REDONDEO + 0.0          # + 0.0: sin −0
    limites = []
    for nombre, limite in (("LIE", lie), ("LSE", lse)):
        if limite is not None and limite <= 0:
            resultado["avisos"].append(f"El {nombre} ({limite}) es ≤ 0 y no se puede transformar: ese lado queda "
                                       "sin especificación.")
            limite = None
        limites.append(None if limite is None else float(transformar([limite], lam)[0]))
    nuevo_lie, nuevo_lse = limites if lam >= 0 else limites[::-1]
    datos = transformar(x, lam)
    resultado.update(lambda_estimado=estimado, lambda_redondeado=lam, datos=datos, lie=nuevo_lie, lse=nuevo_lse,
                     normalidad=normalidad(datos))
    if nuevo_lie is not None or nuevo_lse is not None:
        grupos = subgrupos
        if subgrupos is not None and not isinstance(subgrupos, (int, np.integer)):
            grupos = pd.Series(np.asarray(subgrupos))[pd.Series(valores, dtype="float64").notna().to_numpy()]
        resultado["capacidad"] = capacidad(datos, nuevo_lie, nuevo_lse, grupos, usar_c4, usar_c4_total)
    return resultado


# ---------------------------------------------------------------- datos no normales y estabilidad

def capacidad_percentiles(valores, lie: float | None = None, lse: float | None = None) -> dict:
    """Índices de capacidad por percentiles, para datos no normales (ISO 22514-2, método de percentiles).

    En una normal, media ± 3σ deja afuera el 0,135 % de cada lado. Sin suponer una forma, se usan los percentiles
    de los datos: P0,135 y P99,865 en lugar de media − 3σ y media + 3σ, y la mediana en lugar de la media:
    Pp = (LSE − LIE) / (P99,865 − P0,135); Ppl = (mediana − LIE) / (mediana − P0,135);
    Ppu = (LSE − mediana) / (P99,865 − mediana); Ppk = mín(Ppl, Ppu). Con una normal da lo mismo que Pp/Ppk.
    Con un solo límite, Pp = "no aplica". Percentiles con el método (n + 1)·p; con N < 100 los extremos casi
    son el mínimo y el máximo y se avisa.
    """
    x = _limpios(valores)
    resultado = {"n": len(x), "p_bajo": None, "mediana": None, "p_alto": None, "pp": None, "ppk": None,
                 "ppl": None, "ppu": None, "avisos": []}
    if len(x) < N_MINIMO:
        resultado["avisos"].append(AVISO_NO_CALCULA)
        return resultado
    if len(x) < N_PERCENTILES:
        resultado["avisos"].append(AVISO_PERCENTILES)
    bajo, mediana, alto = (float(v) for v in np.quantile(x, [P_BAJO, 0.5, P_ALTO], method=METODO_PERCENTILES))
    ppl = None if lie is None or mediana == bajo else (mediana - lie) / (mediana - bajo)
    ppu = None if lse is None or alto == mediana else (lse - mediana) / (alto - mediana)
    pp = NO_APLICA if lie is None or lse is None else (None if alto == bajo else (lse - lie) / (alto - bajo))
    lados = [v for v in (ppl, ppu) if v is not None]
    resultado.update(p_bajo=bajo, mediana=mediana, p_alto=alto, pp=pp, ppk=min(lados) if lados else None,
                     ppl=ppl, ppu=ppu)
    return resultado


def estabilidad(valores, alfa: float = 0.05) -> dict:
    """¿El proceso es estable? Carta I-MR (core.spc) y regla 1 de Nelson: puntos fuera de ±3σ (σ = MR̄/d2).

    Aun estable, una normal deja el 0,27 % de los puntos afuera por azar: se considera inestable si hay más
    puntos fuera de los que el azar explica (binomial, P(X ≥ k) < alfa con p = 0,27 %). Devuelve estado
    ("estable" o "inestable"), n, n_fuera, pct_fuera, lc, lcs y lci.
    """
    x = _limpios(valores)
    if len(x) < N_MINIMO or sigma_dentro_individuales(x) in (None, 0):
        return {"estado": None, "n": len(x), "n_fuera": None, "pct_fuera": None, "lc": None, "lcs": None,
                "lci": None}
    carta = evaluar_carta(carta_imr(x), reglas=(1,))
    fuera = int(carta["reglas"].map(bool).sum())
    p = stats.binom.sf(fuera - 1, len(x), FUERA_POR_AZAR) if fuera else 1.0
    return {"estado": INESTABLE if p < alfa else ESTABLE, "n": len(x), "n_fuera": fuera,
            "pct_fuera": 100 * fuera / len(x), "lc": float(carta["lc"].iloc[0]), "lcs": float(carta["lcs"].iloc[0]),
            "lci": float(carta["lci"].iloc[0])}


def evaluar_capacidad(valores, lie: float | None = None, lse: float | None = None, subgrupos=None) -> dict:
    """Todo lo de la capacidad de un conjunto de datos (en orden de tiempo):

    capacidad (normal), normalidad (Anderson-Darling), percentiles y box_cox (solo si p < 0,05), estabilidad,
    pct_fuera (% observado fuera de especificación) y avisos (los de la capacidad, más el de proceso inestable).
    """
    resultado = capacidad(valores, lie, lse, subgrupos)
    prueba = normalidad(valores)
    no_normal = prueba["p"] is not None and prueba["p"] < 0.05
    estable = estabilidad(valores)
    avisos = list(resultado["avisos"])
    if estable["estado"] == INESTABLE:
        avisos.append(AVISO_INESTABLE)
    percentiles = capacidad_percentiles(valores, lie, lse) if no_normal else None
    if percentiles:
        avisos += [a for a in percentiles["avisos"] if a not in avisos]
    observado = resultado["ppm_observado"]
    return {"capacidad": resultado, "normalidad": prueba, "percentiles": percentiles,
            "box_cox": box_cox(valores, lie, lse, subgrupos) if no_normal else None, "estabilidad": estable,
            "pct_fuera": None if observado is None else observado["total"] / 10_000, "avisos": avisos}


def comparar_equipos(datos: pd.DataFrame, lie: float | None, lse: float | None) -> pd.DataFrame:
    """Una fila por eBOE (datos con columnas eboe y valor, en orden de tiempo), del peor al mejor por Ppk.

    Columnas: eboe, n, media, cpk, cpl, cpu, ppk, ppl, ppu, ppk_percentiles (si no es normal), pct_fuera, p_ad,
    normal, estabilidad, pct_fuera_control. Con un solo límite, Ppk es el índice de ese lado (Ppl o Ppu).
    """
    filas = []
    for eboe, grupo in datos.groupby("eboe"):
        r = evaluar_capacidad(grupo["valor"], lie, lse)
        c, percentiles = r["capacidad"], r["percentiles"]
        filas.append({"eboe": eboe, "n": c["n"], "media": c["media"], "cpk": c["cpk"], "cpl": c["cpl"],
                      "cpu": c["cpu"], "ppk": c["ppk"], "ppl": c["ppl"], "ppu": c["ppu"],
                      "ppk_percentiles": percentiles["ppk"] if percentiles else None,
                      "pct_fuera": r["pct_fuera"], "p_ad": r["normalidad"]["p"], "normal": r["normalidad"]["normal"],
                      "estabilidad": r["estabilidad"]["estado"], "pct_fuera_control": r["estabilidad"]["pct_fuera"]})
    tabla = pd.DataFrame(filas)
    return tabla.sort_values("ppk", na_position="last", ignore_index=True) if not tabla.empty else tabla


# ---------------------------------------------------------------- desde la base

# Variable → (nombre en RangoReferencia, punto o equipo, unidad)
VARIABLES_CAPACIDAD = {
    "eficiencia": ("Eficiencia eBOE", "BOE (todos)", "%"),
    "rendimiento": ("Rendimiento eBOE", "BOE (todos)", "kg/MT"),
    "aceite_emulsion": ("Aceite en emulsión", "Emulsión (licor)", "%"),
    "brix": ("°Brix emulsión", "Emulsión (licor)", "°Brix"),
    "ph": ("pH emulsión", "Emulsión (licor)", "pH"),
}


def datos_capacidad(analisis: pd.DataFrame, emulsion: pd.DataFrame, variable: str, equipo: int | None = None,
                    desde=None, hasta=None, fruta: str | None = None) -> pd.DataFrame:
    """Datos de una variable para la capacidad, en orden de tiempo: columnas fecha_hora, eboe, valor.

    eficiencia (%) = eficiencia del par entrada/salida × 100; rendimiento (kg/MT) = aceite entrada − salida del
    mismo eBOE y hora; aceite_emulsion (%), brix y ph de los análisis de emulsión. Filtros: eBOE (equipo), días
    de producción desde/hasta (incluidos) y fruta (especie: "naranja", "limón"…).
    """
    from core.operacion import especie       # acá para no cargar el PLC al importar el módulo
    if variable not in VARIABLES_CAPACIDAD:
        raise ValueError(f'Variable "{variable}" no válida. Usá una de: {", ".join(VARIABLES_CAPACIDAD)}.')
    if variable in ("eficiencia", "rendimiento"):
        tabla = analisis
        if variable == "eficiencia":
            datos = tabla[tabla["lugar"] == "Entrada"].assign(valor=lambda d: d["eficiencia_par"] * 100)
        else:
            pares = tabla.pivot_table(index=["eboe", "fecha_hora"], columns="lugar", values="aceite_fruta",
                                      aggfunc="first")
            pares = pares.dropna(subset=[c for c in ("Entrada", "Salida") if c in pares]).reset_index()
            datos = pares.assign(valor=pares.get("Entrada") - pares.get("Salida")).merge(
                tabla[tabla["lugar"] == "Entrada"][["eboe", "fecha_hora", "fecha_produccion", "fruta"]],
                on=["eboe", "fecha_hora"])
    else:
        columna = {"aceite_emulsion": "aceite_emulsion", "brix": "brix", "ph": "ph"}[variable]
        tabla = emulsion
        datos = tabla.assign(valor=tabla[columna])
    if equipo is not None:
        datos = datos[datos["eboe"] == equipo]
    if desde is not None:
        datos = datos[datos["fecha_produccion"] >= pd.Timestamp(desde)]
    if hasta is not None:
        datos = datos[datos["fecha_produccion"] <= pd.Timestamp(hasta)]
    if fruta is not None and "fruta" in datos:
        datos = datos[datos["fruta"].map(especie) == fruta.lower()]
    return datos.dropna(subset=["valor"]).sort_values("fecha_hora")[["fecha_hora", "eboe", "valor"]].reset_index(
        drop=True)


def limites_capacidad(sesion, planta_id: int, variable: str, fruta: str | None = None) -> dict | None:
    """Límites de especificación de RangoReferencia para la variable.

    Devuelve {"lie", "lse", "lado", "banda", "unidad", "fuente"}: lie y lse según lado_capacidad ("inferior" →
    solo LIE; "superior" → solo LSE; vacío o "ambos" → los dos); banda = (mínimo, máximo) del rango, la banda
    objetivo. Si hay uno de la fruta y uno general (sin fruta), se usa el de la fruta. None si la planta no lo tiene.
    """
    from sqlalchemy import select

    from core.models import RangoReferencia
    nombre, punto, _ = VARIABLES_CAPACIDAD[variable]
    rangos = sesion.scalars(select(RangoReferencia).where(RangoReferencia.planta_id == planta_id)
                            .where(RangoReferencia.variable == nombre)
                            .where(RangoReferencia.punto_o_equipo == punto)).all()
    candidatos = [r for r in rangos if fruta and r.fruta and r.fruta.lower() == fruta.lower()] or \
                 [r for r in rangos if r.fruta is None] or rangos
    if not candidatos:
        return None
    rango = candidatos[0]
    lado = rango.lado_capacidad or "ambos"
    return {"lie": None if lado == "superior" else rango.minimo, "lse": None if lado == "inferior" else rango.maximo,
            "lado": lado, "banda": (rango.minimo, rango.maximo), "unidad": rango.unidad, "fuente": rango.fuente}


def capacidad_desde_base(sesion, planta_id: int, variable: str, equipo: int | None = None, desde=None, hasta=None,
                         fruta: str | None = "naranja", subgrupos=None, analisis: pd.DataFrame | None = None,
                         emulsion: pd.DataFrame | None = None) -> dict:
    """Capacidad de una variable de la planta con los límites de RangoReferencia.

    Variables: eficiencia, rendimiento, aceite_emulsion, brix, ph (VARIABLES_CAPACIDAD). analisis y emulsion: las
    tablas de core.analisis ya leídas (y filtradas, si se quiere); si faltan, se leen de la base. Devuelve
    variable, equipo, limites, datos (fecha_hora, eboe, valor) y lo de evaluar_capacidad(): capacidad, normalidad,
    percentiles y box_cox (solo si la normalidad da p < 0,05), estabilidad, pct_fuera y avisos.
    """
    from core.analisis import tabla_analisis, tabla_emulsion
    limites = limites_capacidad(sesion, planta_id, variable, fruta)
    if limites is None:
        raise ValueError(f'La planta no tiene el rango "{VARIABLES_CAPACIDAD[variable][0]}" en RangoReferencia.')
    if analisis is None and variable in ("eficiencia", "rendimiento"):
        analisis = tabla_analisis(sesion, planta_id)
    if emulsion is None and variable not in ("eficiencia", "rendimiento"):
        emulsion = tabla_emulsion(sesion, planta_id)
    datos = datos_capacidad(analisis, emulsion, variable, equipo, desde, hasta, fruta)
    return {"variable": variable, "equipo": equipo, "limites": limites, "datos": datos} | evaluar_capacidad(
        datos["valor"], limites["lie"], limites["lse"], subgrupos)
