"""Confiabilidad del laboratorio: controles sobre los datos crudos de la titulación Scott.

Trabaja sobre core.analisis.tabla_analisis() (ya filtrada), que trae los datos crudos de cada
análisis de fruta: peso_muestra_g (g), vol_ml (mL de titulante), blanco_ml (mL), normalidad (N) y analista.
- Gramaje y mL: referencia de la propia planta (mediana e IQR), prueba de Anderson-Darling como Minitab,
  gráfico de probabilidad normal, densidad real (KDE) y el tipo de desvío de la normal; mL/g (mL dividido
  gramaje) saca el efecto del gramaje. Muestras con más de 50 mL.
- Dos formas de pesar: "5 g fijo" (4,9 a 5,1 g) o "variable"; % por analista, turno y mes.
- ¿El gramaje cambia el resultado?: diferencia de aceite entre las dos formas, emparejada por eBOE,
  lugar y día, y recta de aceite en fruta vs gramaje.
- Precisión de la titulación: error relativo de lectura = 0,05 mL / (vol_ml − blanco_ml).
- Preferencia de dígitos: frecuencia del primer decimal y del último dígito (centésimo) de vol_ml por
  analista, con chi² contra la uniforme (cada dígito, 10 %), y pares contra impares.
- Entradas repetidas: el mismo peso y volumen, a la misma hora, en la Entrada de más de un eBOE. Cada eBOE
  tiene su propia muestra de entrada: si dan lo mismo, lo más probable es que se haya copiado (core.repetidas).
- Analistas: ANOVA de dos factores (analista + bloque eBOE × semana) para comparar analistas que
  trabajaron sobre el mismo eBOE en la misma semana, con medias ajustadas por bloque.
- Normalidad: un valor por día de producción y los períodos con la misma normalidad.
- Resumen: las frases principales, para el informe.
"""

import numpy as np
import pandas as pd
from scipy import stats

from core.estadistica import cuartiles
from core.formato import numero, texto_p
from core.mensual import etiqueta_mes, mes_de
from core.regresion import regresion_lineal
from core.spc import evaluar_carta, limites_imr

ALFA = 0.05
N_MINIMO = 3
VOL_MAXIMO_ML = 50.0
PESO_FIJO = (4.9, 5.1)      # g: banda de las muestras pesadas "a 5 g fijo"
FIJO, VARIABLE = "5 g fijo", "variable"
LECTURA_ML = 0.05           # mL: media división de una bureta graduada cada 0,1 mL
ERROR_MAXIMO_PCT = 1.0      # % de error de lectura aceptable
N_MINIMO_DIGITOS = 50       # con 50 muestras, cada dígito espera 5: el mínimo para usar chi²
DIGITOS = list(range(10))
POSICIONES = {"decimo": "primer decimal", "centesimo": "último dígito (centésimo)"}
DIAS_MISMA_NORMALIDAD = 15


# ---------------------------------------------------------------- gramaje y volumen

def _limpios(valores) -> np.ndarray:
    """Valores numéricos sin vacíos, como array de NumPy."""
    return pd.to_numeric(pd.Series(valores), errors="coerce").dropna().to_numpy(dtype=float)


def muestras_vol_alto(datos: pd.DataFrame, limite: float = VOL_MAXIMO_ML) -> pd.DataFrame:
    """Análisis con vol_ml mayor que `limite` (mL), ordenados por fecha_hora."""
    return datos[datos["vol_ml"] > limite].sort_values(["fecha_hora", "eboe", "lugar"], ignore_index=True)


def agregar_ml_por_g(datos: pd.DataFrame) -> pd.DataFrame:
    """Agrega ml_por_g = vol_ml / peso_muestra_g (mL/g): saca el efecto del gramaje y deja la variación
    del aceite (con normalidad y blanco casi fijos, el aceite en fruta es proporcional a los mL/g)."""
    return datos.assign(ml_por_g=datos["vol_ml"] / datos["peso_muestra_g"])


def referencia_planta(datos: pd.DataFrame, variables: list[str]) -> pd.DataFrame:
    """Referencia de la propia planta: mediana y rango intercuartil de cada variable por lugar.

    Cuartiles como Minitab (core.estadistica.cuartiles). Columnas: lugar, variable, n, q1, mediana, q3, iqr.
    """
    filas = []
    for variable in variables:
        for lugar, sub in datos.groupby("lugar", sort=True):
            valores = sub[variable].dropna()
            q = cuartiles(valores)
            q1, mediana, q3 = q if q else (None, None, None)
            filas.append({"lugar": lugar, "variable": variable, "n": len(valores), "q1": q1, "mediana": mediana,
                          "q3": q3, "iqr": None if q is None else q3 - q1})
    return pd.DataFrame(filas, columns=["lugar", "variable", "n", "q1", "mediana", "q3", "iqr"])


# ---------------------------------------------------------------- forma de la distribución

def anderson_darling(valores) -> dict:
    """Prueba de normalidad de Anderson-Darling, como Minitab.

    A² = −n − (1/n)·Σ (2i − 1)·[ln F(xᵢ) + ln(1 − F(xₙ₊₁₋ᵢ))], con F la normal de media x̄ y desvío s
    (estimados de los datos). El p sale del estadístico ajustado A*² = A²·(1 + 0,75/n + 2,25/n²) con las
    fórmulas de D'Agostino y Stephens (1986) que usa Minitab. Necesita al menos 3 valores.
    Devuelve n, ad (A², el que muestra Minitab) y p.
    """
    x = np.sort(_limpios(valores))
    n = len(x)
    if n < 3 or np.std(x) == 0:
        return {"n": n, "ad": None, "p": None}
    z = (x - x.mean()) / x.std(ddof=1)
    i = np.arange(1, n + 1)
    ad = float(-n - np.sum((2 * i - 1) * (stats.norm.logcdf(z) + stats.norm.logsf(z[::-1]))) / n)
    ajustado = ad * (1 + 0.75 / n + 2.25 / n ** 2)
    if ajustado >= 0.6:
        p = np.exp(1.2937 - 5.709 * ajustado + 0.0186 * ajustado ** 2)
    elif ajustado >= 0.34:
        p = np.exp(0.9177 - 4.279 * ajustado - 1.38 * ajustado ** 2)
    elif ajustado >= 0.2:
        p = 1 - np.exp(-8.318 + 42.796 * ajustado - 59.938 * ajustado ** 2)
    else:
        p = 1 - np.exp(-13.436 + 101.14 * ajustado - 223.73 * ajustado ** 2)
    return {"n": n, "ad": ad, "p": float(min(max(p, 0.0), 1.0))}


def texto_p_ad(p) -> str:
    """Valor p de Anderson-Darling como lo muestra Minitab: "p < 0,005" si es muy chico."""
    if p is None or pd.isna(p):
        return "p = —"
    return "p < 0,005" if p < 0.005 else f"p = {numero(p, 3)}"


def probabilidad_normal(valores) -> tuple[pd.DataFrame, dict]:
    """Datos del gráfico de probabilidad normal (como Minitab).

    Cada dato ordenado xᵢ va contra su cuantil teórico zᵢ = Φ⁻¹((i − 0,3)/(n + 0,4)) (rangos medianos de
    Benard). Si los datos son normales, quedan sobre la recta x = x̄ + s·z.
    Devuelve (tabla con z, valor y porcentaje; {"media", "desvio"} de la recta).
    """
    x = np.sort(_limpios(valores))
    n = len(x)
    probabilidad = (np.arange(1, n + 1) - 0.3) / (n + 0.4)
    tabla = pd.DataFrame({"z": stats.norm.ppf(probabilidad), "valor": x, "porcentaje": probabilidad * 100})
    return tabla, {"media": float(x.mean()) if n else None, "desvio": float(x.std(ddof=1)) if n > 1 else None}


def curva_kde(valores, puntos: int = 256) -> tuple[np.ndarray, np.ndarray]:
    """Curva de densidad real (KDE gaussiano, ancho de banda de Scott) entre el mínimo y el máximo."""
    x = _limpios(valores)
    if len(x) < 3 or np.std(x) == 0:
        return np.array([]), np.array([])
    eje = np.linspace(x.min(), x.max(), puntos)
    return eje, stats.gaussian_kde(x)(eje)


def _grupos_por_kde(x: np.ndarray) -> float | None:
    """Punto de corte entre dos grupos si la KDE tiene dos máximos claros; si no, None.

    Máximos: los que llegan al 20 % del más alto. Entre cada par de máximos seguidos se mira el valle:
    si baja a menos del 60 % del máximo más bajo de los dos, son dos grupos. Se corta en el valle más
    profundo (relativo).
    """
    eje, y = curva_kde(x, 512)
    if len(eje) == 0:
        return None
    maximos = [i for i in range(1, len(y) - 1) if y[i] > y[i - 1] and y[i] >= y[i + 1] and y[i] >= 0.2 * y.max()]
    mejor, corte = 1.0, None
    for a, b in zip(maximos, maximos[1:]):
        valle = a + int(np.argmin(y[a:b + 1]))
        relativo = y[valle] / min(y[a], y[b])
        if relativo < 0.6 and relativo < mejor:
            mejor, corte = relativo, float(eje[valle])
    return corte


def forma_distribucion(valores) -> dict:
    """Anderson-Darling y el tipo de desvío de la normal, para la frase.

    tipo: "normal" (p ≥ 0,05), "dos_grupos" (KDE con dos máximos claros), "cola_derecha" o
    "cola_izquierda" (asimetría |g1| ≥ 0,5), "colas_pesadas" (curtosis en exceso > 1) o "leve" (AD
    significativo sin un desvío claro: con N grande pasa seguido).
    Devuelve n, ad, p, media, desvio, asimetria, curtosis (en exceso), tipo y, si hay dos grupos, corte
    y grupos [(p10, p90) de cada grupo].
    """
    x = _limpios(valores)
    resultado = anderson_darling(x) | {"media": None, "desvio": None, "asimetria": None, "curtosis": None,
                                        "tipo": None, "corte": None, "grupos": None}
    if resultado["ad"] is None:
        return resultado
    resultado.update(media=float(x.mean()), desvio=float(x.std(ddof=1)), asimetria=float(stats.skew(x)),
                     curtosis=float(stats.kurtosis(x)))
    if resultado["p"] >= ALFA:
        resultado["tipo"] = "normal"
        return resultado
    corte = _grupos_por_kde(x)
    if corte is not None:
        resultado.update(tipo="dos_grupos", corte=corte,
                         grupos=[tuple(np.percentile(g, [10, 90])) for g in (x[x < corte], x[x >= corte])])
    elif abs(resultado["asimetria"]) >= 0.5:
        resultado["tipo"] = "cola_derecha" if resultado["asimetria"] > 0 else "cola_izquierda"
    elif resultado["curtosis"] > 1:
        resultado["tipo"] = "colas_pesadas"
    else:
        resultado["tipo"] = "leve"
    return resultado


def _rango(desde: float, hasta: float, unidad: str, decimales: int) -> str:
    """"en 5 g" si los dos extremos redondeados coinciden; si no, "entre 7 y 11 g"."""
    a, b = numero(desde, decimales), numero(hasta, decimales)
    return f"en {a} {unidad}" if a == b else f"entre {a} y {b} {unidad}"


def frase_forma(forma: dict, sujeto: str, unidad: str, decimales: int = 0, plural: bool = False,
                causa_cola_alta: str | None = None) -> str:
    """Frase simple con el resultado de forma_distribucion().

    Ejemplo: "El gramaje de entrada no es normal (AD = 85,2; p < 0,005): tiene dos grupos, uno en 5 g y
    otro entre 7 y 11 g. En el gráfico de probabilidad se ve como un escalón."
    sujeto: "El gramaje de entrada", "Los mL de salida" (plural=True); causa_cola_alta: explicación que
    se agrega si la cola es hacia valores altos ("por los eventos de baja eficiencia").
    """
    if forma["ad"] is None:
        return f"{sujeto}: no hay datos suficientes para la prueba de normalidad (N = {forma['n']})."
    es = "son" if plural else "es"
    prueba = f"(AD = {numero(forma['ad'], 1)}; {texto_p_ad(forma['p'])})"
    if forma["tipo"] == "normal":
        return f"{sujeto} {es} compatible{'s' if plural else ''} con una normal {prueba}."
    inicio = f"{sujeto} no {es} normal{'es' if plural else ''} {prueba}: "
    tiene = "tienen" if plural else "tiene"
    if forma["tipo"] == "dos_grupos":
        (a1, b1), (a2, b2) = forma["grupos"]
        return (f"{inicio}{tiene} dos grupos, uno {_rango(a1, b1, unidad, decimales)} y otro "
                f"{_rango(a2, b2, unidad, decimales)}. En el gráfico de probabilidad se ve como un escalón.")
    if forma["tipo"] == "cola_derecha":
        causa = f" {causa_cola_alta}" if causa_cola_alta else ""
        return (f"{inicio}{tiene} una cola larga hacia valores altos{causa} (asimetría "
                f"{numero(forma['asimetria'], 2)}). En el gráfico de probabilidad, los puntos de la derecha se "
                "curvan hacia arriba.")
    if forma["tipo"] == "cola_izquierda":
        return (f"{inicio}{tiene} una cola larga hacia valores bajos (asimetría {numero(forma['asimetria'], 2)}). "
                "En el gráfico de probabilidad, los puntos de la izquierda se curvan hacia abajo.")
    if forma["tipo"] == "colas_pesadas":
        return (f"{inicio}{tiene} colas pesadas: más valores extremos que una normal, para los dos lados (curtosis "
                f"{numero(forma['curtosis'], 2)}). En el gráfico de probabilidad, los extremos se separan de la "
                "recta en forma de S.")
    return (f"{inicio}se aparta{'n' if plural else ''} poco de la normal, sin un patrón claro; con N grande, "
            "Anderson-Darling detecta hasta diferencias chicas.")


# ---------------------------------------------------------------- dos formas de pesar

def forma_de_pesar(datos: pd.DataFrame, desde: float = PESO_FIJO[0], hasta: float = PESO_FIJO[1]) -> pd.Series:
    """"5 g fijo" si peso_muestra_g está entre desde y hasta (incluidos); si no, "variable"."""
    if desde > hasta:
        raise ValueError("El mínimo de la banda de 5 g fijo es mayor que el máximo.")
    fijo = datos["peso_muestra_g"].between(desde, hasta)
    return pd.Series(np.where(fijo, FIJO, VARIABLE), index=datos.index).where(datos["peso_muestra_g"].notna())


def porcentaje_fijo(datos: pd.DataFrame, por: str) -> pd.DataFrame:
    """% de muestras pesadas a 5 g fijo por lugar y por `por` (analista, turno o mes).

    datos tiene que tener la columna forma (forma_de_pesar()). Con por="mes" se agrupa por mes de
    producción y se agrega la etiqueta ("Jul 2025"). Columnas: lugar, por (o mes y etiqueta), n, n_fijo y
    pct_fijo (%).
    """
    d = datos.dropna(subset=["forma"])
    if por == "mes":
        d = d.assign(mes=mes_de(d["fecha_produccion"]))
    filas = []
    for (lugar, valor), sub in d.groupby(["lugar", por], sort=True):
        fila = {"lugar": lugar, por: valor, "n": len(sub), "n_fijo": int((sub["forma"] == FIJO).sum())}
        fila["pct_fijo"] = 100 * fila["n_fijo"] / len(sub)
        if por == "mes":
            fila["etiqueta"] = etiqueta_mes(valor)
        filas.append(fila)
    columnas = ["lugar", por, *(["etiqueta"] if por == "mes" else []), "n", "n_fijo", "pct_fijo"]
    return pd.DataFrame(filas, columns=columnas)


def comparar_formas(datos: pd.DataFrame, alfa: float = ALFA) -> pd.DataFrame:
    """¿El gramaje cambia el resultado? Aceite en fruta de "5 g fijo" contra "variable", emparejado por día.

    Para cada eBOE, lugar y día de producción con muestras de las dos formas: diferencia = media de
    aceite_fruta de las de 5 g − media de las variables (kg/MT). Por lugar, la diferencia media de esos
    días con su IC 1 − alfa y la prueba t emparejada (H0: diferencia = 0). Comparar dentro del mismo día y
    eBOE saca el efecto de la fruta y del proceso.
    Columnas: lugar, n_dias, n_fijo, n_variable, media_fijo, media_variable, diferencia, ic_inf, ic_sup, p,
    variable_p10 y variable_p90 (rango de gramaje de las variables, g).
    """
    d = datos.dropna(subset=["forma", "aceite_fruta"])
    filas = []
    for lugar, sub in d.groupby("lugar", sort=True):
        claves = ["eboe", "fecha_produccion"]
        con_ambas = sub[sub.groupby(claves)["forma"].transform("nunique") == 2]
        medias = con_ambas.pivot_table(index=claves, columns="forma", values="aceite_fruta", aggfunc="mean")
        fila = {"lugar": lugar, "n_dias": len(medias), "n_fijo": int((con_ambas["forma"] == FIJO).sum()),
                "n_variable": int((con_ambas["forma"] == VARIABLE).sum()), "media_fijo": None,
                "media_variable": None, "diferencia": None, "ic_inf": None, "ic_sup": None, "p": None,
                "variable_p10": None, "variable_p90": None}
        if len(medias) >= N_MINIMO:
            diferencias = medias[FIJO] - medias[VARIABLE]
            prueba = stats.ttest_1samp(diferencias, 0)
            ic = prueba.confidence_interval(1 - alfa)
            pesos = con_ambas.loc[con_ambas["forma"] == VARIABLE, "peso_muestra_g"]
            fila.update(media_fijo=float(medias[FIJO].mean()), media_variable=float(medias[VARIABLE].mean()),
                        diferencia=float(diferencias.mean()), ic_inf=float(ic.low), ic_sup=float(ic.high),
                        p=float(prueba.pvalue), variable_p10=float(np.percentile(pesos, 10)),
                        variable_p90=float(np.percentile(pesos, 90)))
        filas.append(fila)
    return pd.DataFrame(filas, columns=["lugar", "n_dias", "n_fijo", "n_variable", "media_fijo", "media_variable",
                                        "diferencia", "ic_inf", "ic_sup", "p", "variable_p10", "variable_p90"])


def frase_formas(fila: dict, alfa: float = ALFA) -> str:
    """Frase de una fila de comparar_formas().

    Ejemplo: "En la salida, las muestras de 5 g dan 0,08 kg/MT más que las de 7–11 g del mismo día
    (p = 0,020; 85 días)." o "…: no hay diferencia significativa (…)."
    """
    lugar = fila["lugar"].lower()
    if fila["diferencia"] is None or pd.isna(fila["diferencia"]):
        return (f"En la {lugar} hay {fila['n_dias']} días con muestras de 5 g y de gramaje variable en el mismo "
                f"eBOE: no alcanza para comparar (hacen falta {N_MINIMO}).")
    variables = f"{numero(fila['variable_p10'], 0)}–{numero(fila['variable_p90'], 0)} g"
    detalle = f"{texto_p(fila['p'])}; {numero(fila['n_dias'], 0)} días"
    if fila["p"] >= alfa:
        return (f"En la {lugar}, las muestras de 5 g y las de {variables} del mismo día dan lo mismo: no hay "
                f"diferencia significativa ({numero(fila['diferencia'], 2)} kg/MT; {detalle}).")
    sentido = "más" if fila["diferencia"] > 0 else "menos"
    return (f"En la {lugar}, las muestras de 5 g dan {numero(abs(fila['diferencia']), 2)} kg/MT {sentido} que las "
            f"de {variables} del mismo día ({detalle}).")


# ---------------------------------------------------------------- precisión de la titulación

def error_de_lectura(datos: pd.DataFrame) -> pd.Series:
    """Error relativo de lectura de cada muestra (%) = 100 × 0,05 / (vol_ml − blanco_ml).

    0,05 mL es la mitad de la menor división de una bureta de 0,1 mL. Vacío si los mL netos no son
    positivos. Con menos de 5 mL netos el error pasa del 1 %.
    """
    neto = datos["vol_ml"] - datos["blanco_ml"]
    return (100 * LECTURA_ML / neto).where(neto > 0)


def precision_titulacion(datos: pd.DataFrame, limite_pct: float = ERROR_MAXIMO_PCT) -> pd.DataFrame:
    """Error de lectura por lugar: n, mediana (%), n_mayor y pct_mayor (muestras con error > limite_pct)."""
    d = datos.assign(error_pct=error_de_lectura(datos)).dropna(subset=["error_pct"])
    filas = [{"lugar": lugar, "n": len(sub), "mediana": float(sub["error_pct"].median()),
              "n_mayor": int((sub["error_pct"] > limite_pct).sum()),
              "pct_mayor": 100 * float((sub["error_pct"] > limite_pct).mean())}
             for lugar, sub in d.groupby("lugar", sort=True)]
    return pd.DataFrame(filas, columns=["lugar", "n", "mediana", "n_mayor", "pct_mayor"])


def frase_precision(tabla: pd.DataFrame, limite_pct: float = ERROR_MAXIMO_PCT) -> str:
    """Ejemplo: "El error de lectura de la bureta supera el 1 % (menos de 5 mL netos) en el 0,4 % de las
    muestras de entrada y en el 2,1 % de las de salida. Mediana del error: 0,46 % y 0,35 %."
    """
    if tabla.empty:
        return "No hay muestras para calcular el error de lectura."
    neto = LECTURA_ML / (limite_pct / 100)
    porcentajes = " y ".join(f"en el {numero(f['pct_mayor'], 1)} % de las muestras de {f['lugar'].lower()}"
                             for f in tabla.to_dict("records"))
    medianas = " y ".join(f"{numero(f['mediana'], 2)} % en {f['lugar'].lower()}" for f in tabla.to_dict("records"))
    return (f"El error de lectura de la bureta supera el {numero(limite_pct, 0)} % (menos de {numero(neto, 0)} mL "
            f"netos) {porcentajes}. Mediana del error: {medianas}.")


# ---------------------------------------------------------------- preferencia de dígitos

def digitos(vol_ml: pd.Series) -> pd.DataFrame:
    """Primer decimal (decimo) y último dígito (centesimo) de cada volumen anotado con 2 decimales.

    12,35 → décimo 3, centésimo 5. Se redondea a centésimos antes, para que 12,35 guardado como
    12,3499999 no dé 4.
    """
    centesimos = (pd.to_numeric(vol_ml) * 100).round().astype("Int64")
    return pd.DataFrame({"decimo": (centesimos // 10) % 10, "centesimo": centesimos % 10}, index=vol_ml.index)


def preferencia_digitos(datos: pd.DataFrame, posicion: str, por: str = "analista") -> pd.DataFrame:
    """Frecuencia de cada dígito (0 a 9) en una posición de vol_ml ("decimo" o "centesimo"), por analista.

    Prueba chi² de bondad de ajuste contra la uniforme (10 % cada dígito), solo si hay al menos
    N_MINIMO_DIGITOS muestras. exceso_0_5: cuánto más (fracción) se anotan 0 y 5 que lo esperado
    (20 %): 0,35 = un 35 % más. digito_max y exceso_max: el dígito más anotado y su exceso.
    exceso_pares: cuánto más aparecen los dígitos pares que los impares (0,25 = un 25 % más), con p_pares
    de la prueba binomial (H0: mitad y mitad); con una bureta graduada cada 0,2 mL casi no hay impares.
    Columnas: grupo, n, pct_0 … pct_9 (%), chi2, p, exceso_0_5, digito_max, exceso_max, exceso_pares, p_pares.
    """
    if posicion not in POSICIONES:
        raise ValueError(f"Posición desconocida: {posicion} (tiene que ser 'decimo' o 'centesimo').")
    con_digitos = datos.dropna(subset=["vol_ml", por])
    con_digitos = con_digitos.assign(digito=digitos(con_digitos["vol_ml"])[posicion])
    filas = []
    for grupo, sub in con_digitos.groupby(por, sort=True):
        cuentas = sub["digito"].value_counts().reindex(DIGITOS, fill_value=0).to_numpy()
        n = int(cuentas.sum())
        fila = {"grupo": grupo, "n": n} | {f"pct_{d}": 100 * c / n for d, c in zip(DIGITOS, cuentas)}
        chi2 = p = p_pares = None
        pares = int(cuentas[0::2].sum())
        if n >= N_MINIMO_DIGITOS:
            prueba = stats.chisquare(cuentas)
            chi2, p = float(prueba.statistic), float(prueba.pvalue)
            p_pares = float(stats.binomtest(pares, n, 0.5).pvalue)
        maximo = int(np.argmax(cuentas))
        filas.append(fila | {"chi2": chi2, "p": p, "exceso_0_5": (cuentas[0] + cuentas[5]) / (0.2 * n) - 1,
                             "digito_max": maximo, "exceso_max": cuentas[maximo] / (0.1 * n) - 1,
                             "exceso_pares": pares / (n - pares) - 1 if n > pares else None, "p_pares": p_pares})
    return pd.DataFrame(filas, columns=["grupo", "n", *(f"pct_{d}" for d in DIGITOS), "chi2", "p", "exceso_0_5",
                                        "digito_max", "exceso_max", "exceso_pares", "p_pares"])


def frase_digitos(tabla: pd.DataFrame, posicion: str, alfa: float = ALFA) -> str:
    """Frase con los analistas que prefieren algún dígito (chi² significativo).

    Ejemplo: "En el primer decimal de los mL, Bulegio anota 0 y 5 un 35 % más de lo esperado
    (p < 0,001). Sin preferencia: Cuesta. Con menos de 50 muestras (no se prueba): Flores."
    Si el exceso de 0 y 5 es menor al 10 %, se nombra el dígito más anotado. En el primer decimal se
    agrega si los pares aparecen al menos un 15 % más que los impares ("En Bulegio, los primeros
    decimales pares aparecen un 25 % más que los impares (…; posible bureta graduada cada 0,2 mL).").
    """
    partes = []
    con_preferencia = tabla[tabla["p"].notna() & (tabla["p"] < alfa)].sort_values("exceso_0_5", ascending=False)
    for fila in con_preferencia.to_dict("records"):
        if fila["exceso_0_5"] >= 0.10:
            partes.append(f"{fila['grupo']} anota 0 y 5 un {numero(fila['exceso_0_5'] * 100, 0)} % más de lo esperado "
                          f"({texto_p(fila['p'])})")
        else:
            partes.append(f"{fila['grupo']} anota el {fila['digito_max']} un {numero(fila['exceso_max'] * 100, 0)} % "
                          f"más de lo esperado ({texto_p(fila['p'])})")
    sin = tabla.loc[tabla["p"].notna() & (tabla["p"] >= alfa), "grupo"].tolist()
    pocos = tabla.loc[tabla["p"].isna(), "grupo"].tolist()
    texto = f"En el {POSICIONES[posicion]} de los mL, "
    texto += ("; ".join(partes) + "." if partes else
              f"ningún analista muestra preferencia por algún dígito (chi², p ≥ {numero(alfa, 2)}).")
    if partes and sin:
        texto += f" Sin preferencia: {', '.join(map(str, sin))}."
    if pocos:
        texto += f" Con menos de {N_MINIMO_DIGITOS} muestras (no se prueba): {', '.join(map(str, pocos))}."
    if posicion == "decimo":
        pares = tabla[tabla["p_pares"].notna() & (tabla["p_pares"] < alfa) & (tabla["exceso_pares"] >= 0.15)]
        for fila in pares.to_dict("records"):
            texto += (f" En {fila['grupo']}, los primeros decimales pares aparecen un "
                      f"{numero(fila['exceso_pares'] * 100, 0)} % más que los impares ({texto_p(fila['p_pares'])}; "
                      "posible bureta graduada cada 0,2 mL).")
    return texto


# ---------------------------------------------------------------- entradas repetidas

def entradas_repetidas_grupos(datos: pd.DataFrame) -> pd.DataFrame:
    """Grupos de muestras de Entrada con la misma fecha_hora, peso_muestra_g y vol_ml en más de un eBOE.

    Cada eBOE tiene su propia muestra de entrada y es muy poco probable que dos frutas distintas den exactamente
    el mismo peso y volumen: lo más probable es que se haya copiado el dato de un eBOE a otro.
    Devuelve una fila por grupo: fecha_hora, fecha_produccion, peso_muestra_g, vol_ml, eboes ("1, 2"),
    n_eboes y analistas.
    """
    entradas = datos[datos["lugar"] == "Entrada"].dropna(subset=["peso_muestra_g", "vol_ml"])
    claves = ["fecha_hora", "peso_muestra_g", "vol_ml"]
    repetidas = entradas[entradas.groupby(claves)["eboe"].transform("nunique") > 1]
    filas = []
    for (fecha_hora, peso, vol), sub in repetidas.groupby(claves, sort=True):
        filas.append({"fecha_hora": fecha_hora, "fecha_produccion": sub["fecha_produccion"].iloc[0],
                      "peso_muestra_g": peso, "vol_ml": vol,
                      "eboes": ", ".join(str(e) for e in sorted(sub["eboe"].unique())),
                      "n_eboes": sub["eboe"].nunique(),
                      "analistas": ", ".join(sorted(sub["analista"].dropna().unique()))})
    return pd.DataFrame(filas, columns=["fecha_hora", "fecha_produccion", "peso_muestra_g", "vol_ml", "eboes",
                                        "n_eboes", "analistas"])


def repetidas_por_mes(datos: pd.DataFrame) -> pd.DataFrame:
    """Cantidad y % de muestras de Entrada repetidas en más de un eBOE, por mes de producción.

    Columnas: mes, etiqueta ("Jul 2025"), entradas (muestras de Entrada del mes), grupos (grupos de
    entradas repetidas), repetidas (muestras dentro de esos grupos) y pct (repetidas / entradas, %).
    """
    entradas = datos[datos["lugar"] == "Entrada"].dropna(subset=["peso_muestra_g", "vol_ml"])
    claves = ["fecha_hora", "peso_muestra_g", "vol_ml"]
    eboes_por_grupo = entradas.groupby(claves)["eboe"].transform("nunique")
    entradas = entradas.assign(mes=mes_de(entradas["fecha_produccion"]), repetida=eboes_por_grupo > 1)
    filas = []
    for mes, sub in entradas.groupby("mes", sort=True):
        repetidas = sub[sub["repetida"]]
        filas.append({"mes": mes, "etiqueta": etiqueta_mes(mes), "entradas": len(sub),
                      "grupos": len(repetidas.drop_duplicates(claves)), "repetidas": len(repetidas),
                      "pct": 100 * len(repetidas) / len(sub)})
    return pd.DataFrame(filas, columns=["mes", "etiqueta", "entradas", "grupos", "repetidas", "pct"])


# ---------------------------------------------------------------- analistas

def _minimos_cuadrados(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    """Ajuste por mínimos cuadrados con las ecuaciones normales: (X'X)·β = X'y.

    Devuelve β, la suma de cuadrados del error y (X'X)⁻¹ (para los errores estándar: cov(β) = CME·(X'X)⁻¹).
    X'X es chica (una columna por nivel), así que es mucho más rápido que descomponer X entera.
    """
    inversa = np.linalg.pinv(x.T @ x)
    beta = inversa @ (x.T @ y)
    residuos = y - x @ beta
    return beta, float(residuos @ residuos), inversa


def _codificacion_suma(valores: pd.Series, niveles: list) -> np.ndarray:
    """Columnas de un factor con codificación de suma: una por nivel menos el último; 1 en su nivel,
    −1 en el último nivel y 0 en el resto (como C(factor, Sum) de statsmodels)."""
    x = np.zeros((len(valores), len(niveles) - 1))
    codigos = pd.Categorical(valores, categories=niveles).codes
    for k in range(len(niveles) - 1):
        x[codigos == k, k] = 1
    x[codigos == len(niveles) - 1, :] = -1
    return x


def semana_de(fechas: pd.Series) -> pd.Series:
    """Lunes de la semana de cada fecha de producción."""
    return pd.to_datetime(fechas).dt.to_period("W-SUN").dt.start_time


def comparar_analistas(datos: pd.DataFrame, variable: str, alfa: float = ALFA) -> dict:
    """Compara analistas que midieron el mismo eBOE en la misma semana (ANOVA de dos factores).

    Modelo lineal: variable = analista + bloque, con bloque = eBOE × semana (lunes a domingo de
    producción), sin interacción. Solo entran los bloques con 2 o más analistas: así se compara a
    cada analista contra otros que midieron la misma máquina en la misma semana, y no se confunde
    el analista con la fruta o la temporada. Suma de cuadrados tipo II (como el Modelo lineal general
    de Minitab con datos desbalanceados).
    Medias ajustadas (de mínimos cuadrados): la media de cada analista promediando todos los bloques
    por igual, con su IC 1 − alfa.
    Devuelve n, n_bloques, anova (fuente, gl, sc, f, p), p (del analista), medias (grupo, n, media,
    ic_inf, ic_sup, ordenadas por grupo) y alfa. Con menos de 2 analistas, p y anova quedan en None.
    """
    d = datos.dropna(subset=[variable, "analista"]).copy()
    d["bloque"] = d["eboe"].astype(str) + " · " + semana_de(d["fecha_produccion"]).dt.strftime("%Y-%m-%d")
    d = d[d.groupby("bloque")["analista"].transform("nunique") >= 2]
    resultado = {"n": len(d), "n_bloques": d["bloque"].nunique(), "anova": None, "p": None,
                 "medias": pd.DataFrame(columns=["grupo", "n", "media", "ic_inf", "ic_sup"]), "alfa": alfa}
    if d["analista"].nunique() < 2:
        return resultado

    # Matriz de diseño con codificación de suma (cada efecto suma cero): la media ajustada del analista a
    # es intercepto + efecto de a, y el efecto del último analista es menos la suma de los demás.
    niveles = sorted(d["analista"].unique())
    analista = _codificacion_suma(d["analista"], niveles)
    bloque = _codificacion_suma(d["bloque"], sorted(d["bloque"].unique()))
    y, unos = d[variable].to_numpy(dtype=float), np.ones((len(d), 1))
    beta, sce, inversa = _minimos_cuadrados(np.hstack([unos, analista, bloque]), y)

    # Tipo II: cada factor ajustado por el otro = cuánto sube el error si se lo saca del modelo.
    sc_analista = _minimos_cuadrados(np.hstack([unos, bloque]), y)[1] - sce
    sc_bloque = _minimos_cuadrados(np.hstack([unos, analista]), y)[1] - sce
    gl_analista, gl_bloque = analista.shape[1], bloque.shape[1]
    gl_error = len(y) - 1 - gl_analista - gl_bloque
    cme = sce / gl_error
    f_analista, f_bloque = sc_analista / gl_analista / cme, sc_bloque / gl_bloque / cme
    resultado["anova"] = pd.DataFrame({
        "fuente": ["Analista", "eBOE × semana", "Error"], "gl": [gl_analista, gl_bloque, gl_error],
        "sc": [sc_analista, sc_bloque, sce], "f": [f_analista, f_bloque, np.nan],
        "p": [stats.f.sf(f_analista, gl_analista, gl_error), stats.f.sf(f_bloque, gl_bloque, gl_error), np.nan]})
    resultado["p"] = float(resultado["anova"]["p"].iloc[0])

    t_critico = stats.t.ppf(1 - alfa / 2, gl_error)
    filas = []
    for k, nombre in enumerate(niveles):
        combinacion = np.zeros(len(beta))
        combinacion[0] = 1
        if k < gl_analista:
            combinacion[1 + k] = 1
        else:
            combinacion[1:1 + gl_analista] = -1
        media = float(combinacion @ beta)
        margen = t_critico * float(np.sqrt(cme * combinacion @ inversa @ combinacion))
        filas.append({"grupo": nombre, "n": int((d["analista"] == nombre).sum()), "media": media,
                      "ic_inf": media - margen, "ic_sup": media + margen})
    resultado["medias"] = pd.DataFrame(filas)
    return resultado


def frase_analistas(resultado: dict, sujeto: str, unidad: str, decimales: int = 2,
                    unidad_diferencia: str | None = None) -> str:
    """Frase con el resultado de comparar_analistas().

    Ejemplo: "Comparando en el mismo eBOE y la misma semana, el aceite de salida cambia según el
    analista (p = 0,003): Duran da en promedio 1,62 kg/MT y Cuesta 1,41 kg/MT (0,21 kg/MT de diferencia)."
    sujeto: "el aceite de salida", "la eficiencia". unidad_diferencia: unidad de la diferencia si es
    otra ("puntos" para un %); por defecto, la misma unidad.
    """
    if resultado["p"] is None:
        return (f"No hay semanas en que dos o más analistas hayan medido el mismo eBOE: no se puede comparar "
                f"{sujeto} entre analistas.")
    medias = resultado["medias"].sort_values("media")
    menor, mayor = medias.iloc[0], medias.iloc[-1]
    detalle = (f"{mayor['grupo']} da en promedio {numero(mayor['media'], decimales)} {unidad} y {menor['grupo']} "
               f"{numero(menor['media'], decimales)} {unidad} "
               f"({numero(mayor['media'] - menor['media'], decimales)} {unidad_diferencia or unidad} de diferencia)")
    inicio = "Comparando en el mismo eBOE y la misma semana, "
    if resultado["p"] < resultado["alfa"]:
        return f"{inicio}{sujeto} cambia según el analista ({texto_p(resultado['p'])}): {detalle}."
    return (f"{inicio}{sujeto} no cambia de forma significativa según el analista ({texto_p(resultado['p'])}). "
            f"Los extremos: {detalle}.")


# ---------------------------------------------------------------- normalidad

def normalidad_diaria(datos: pd.DataFrame, lugar: str) -> pd.DataFrame:
    """Normalidad del titulante por día de producción en un lugar (Entrada o Salida).

    Toda la planta usa la misma solución en cada momento: se toma la normalidad más frecuente del
    día (la menor si empatan). Columnas: fecha_produccion, normalidad, n (muestras) y distintas
    (cuántas normalidades distintas hubo ese día).
    """
    sub = datos[datos["lugar"] == lugar].dropna(subset=["normalidad"])
    filas = [{"fecha_produccion": fecha, "normalidad": float(dia["normalidad"].mode().min()), "n": len(dia),
              "distintas": dia["normalidad"].nunique()}
             for fecha, dia in sub.groupby("fecha_produccion", sort=True)]
    return pd.DataFrame(filas, columns=["fecha_produccion", "normalidad", "n", "distintas"])


def periodos_misma_normalidad(diaria: pd.DataFrame, dias_minimos: int = DIAS_MISMA_NORMALIDAD) -> pd.DataFrame:
    """Períodos seguidos con la misma normalidad (normalidad_diaria()) y si duran más de `dias_minimos`.

    dias = del primer al último día del período, contando los dos (los días sin producción en el
    medio cuentan). Columnas: desde, hasta, normalidad, dias, dias_con_datos, primero y ultimo
    (posición del primer y último día en la tabla diaria) y largo (dias > dias_minimos).
    """
    columnas = ["desde", "hasta", "normalidad", "dias", "dias_con_datos", "primero", "ultimo", "largo"]
    if diaria.empty:
        return pd.DataFrame(columns=columnas)
    diaria = diaria.reset_index(drop=True)
    tramo = (diaria["normalidad"] != diaria["normalidad"].shift()).cumsum()
    filas = []
    for _, sub in diaria.groupby(tramo, sort=True):
        desde, hasta = sub["fecha_produccion"].iloc[0], sub["fecha_produccion"].iloc[-1]
        dias = (hasta - desde).days + 1
        filas.append({"desde": desde, "hasta": hasta, "normalidad": sub["normalidad"].iloc[0], "dias": dias,
                      "dias_con_datos": len(sub), "primero": int(sub.index[0]), "ultimo": int(sub.index[-1]),
                      "largo": dias > dias_minimos})
    return pd.DataFrame(filas, columns=columnas)


def carta_normalidad(diaria: pd.DataFrame, periodos: pd.DataFrame) -> pd.DataFrame:
    """Carta I-MR de la normalidad, con un punto por día y límites calculados con un valor por período.

    La normalidad cambia solo cuando se prepara o se vuelve a valorar la solución: con un punto por día,
    el rango móvil es casi siempre 0 y los límites quedan pegados a la línea. Por eso los límites salen
    de la serie de períodos (periodos_misma_normalidad(): una normalidad por período, MR̄ = cambio medio
    entre valoraciones seguidas, σ = MR̄/d2) y se aplican a cada día. El MR (abajo) aparece solo el día
    en que cambia la normalidad. Se evalúa la regla 1 (fuera de límites).
    Devuelve la tabla diaria con las columnas de core.spc.evaluar_carta() (valor, lc, lcs, lci, sigma,
    disp…, reglas, fuera_disp, alguna). Necesita al menos 2 períodos.
    """
    if len(periodos) < 2:
        raise ValueError("La normalidad no cambió en el período: hacen falta al menos 2 valores para la carta.")
    limites = limites_imr(periodos["normalidad"])
    cambios = pd.Series(np.nan, index=range(len(diaria)))
    cambios[periodos["primero"].iloc[1:].to_numpy()] = periodos["normalidad"].diff().abs().iloc[1:].to_numpy()
    carta = pd.DataFrame({
        "valor": diaria["normalidad"].to_numpy(), "lc": limites["lc"], "lcs": limites["lcs"], "lci": limites["lci"],
        "sigma": limites["sigma"], "disp": cambios.to_numpy(), "disp_lc": limites["mr_lc"],
        "disp_lcs": limites["mr_lcs"], "disp_lci": limites["mr_lci"], "fase": "I"})
    return pd.concat([diaria.reset_index(drop=True), evaluar_carta(carta, reglas=[1])], axis=1)


def frase_periodos(periodos: pd.DataFrame, lugar: str, dias_minimos: int = DIAS_MISMA_NORMALIDAD) -> str:
    """Frase con los períodos largos de periodos_misma_normalidad().

    Ejemplo: "En la entrada hubo 1 período de más de 15 días con la misma normalidad: 0,0999 N del
    10/09/2025 al 30/09/2025 (21 días). Conviene revisar que se haya refactorizado la solución."
    """
    largos = periodos[periodos["largo"]]
    inicio = f"En la {lugar.lower()}"
    if largos.empty:
        return f"{inicio} ninguna normalidad se repitió más de {dias_minimos} días seguidos."
    detalle = "; ".join(f"{numero(f['normalidad'], 4)} N del {f['desde']:%d/%m/%Y} al {f['hasta']:%d/%m/%Y} "
                        f"({f['dias']} días)" for f in largos.to_dict("records"))
    cantidad = "1 período" if len(largos) == 1 else f"{len(largos)} períodos"
    return (f"{inicio} hubo {cantidad} de más de {dias_minimos} días con la misma normalidad: {detalle}. "
            "Conviene revisar que la solución se haya vuelto a valorar en esos períodos.")


# ---------------------------------------------------------------- gramaje vs resultado

def gramaje_vs_resultado(datos: pd.DataFrame, alfa: float = ALFA) -> pd.DataFrame:
    """Recta de aceite_fruta (kg/MT) en función de peso_muestra_g (g), una por lugar.

    Columnas: lugar y las de core.regresion.regresion_lineal() (n, pendiente en kg/MT por g, ordenada,
    r2, p, ic_inf, ic_sup).
    """
    filas = [{"lugar": lugar} | regresion_lineal(sub["peso_muestra_g"], sub["aceite_fruta"], alfa)
             for lugar, sub in datos.groupby("lugar", sort=True)]
    return pd.DataFrame(filas, columns=["lugar", "n", "pendiente", "ordenada", "r2", "p", "ic_inf", "ic_sup"])


def frase_gramaje(regresion: dict, alfa: float = ALFA) -> str:
    """Frase con la recta de una fila de gramaje_vs_resultado().

    Ejemplo: "En la salida, cada 1 g más de muestra se asocia con 0,05 kg/MT menos de aceite (R² = 0,04;
    p < 0,001): el resultado cambia con el gramaje."
    """
    lugar = regresion["lugar"].lower()
    if pd.isna(regresion["pendiente"]):
        return f"En la {lugar} no hay datos suficientes para la recta (N = {regresion['n']})."
    sentido = "más" if regresion["pendiente"] > 0 else "menos"
    frase = (f"En la {lugar}, cada 1 g más de muestra se asocia con {numero(abs(regresion['pendiente']), 3)} kg/MT "
             f"{sentido} de aceite (R² = {numero(regresion['r2'], 2)}; {texto_p(regresion['p'])})")
    if regresion["p"] < alfa and regresion["r2"] < 0.05:
        return frase + ": la relación es significativa, pero el gramaje explica muy poco del resultado."
    if regresion["p"] < alfa:
        return frase + ": el resultado cambia con el gramaje."
    return frase + ": el resultado no cambia con el gramaje."


# ---------------------------------------------------------------- resumen

def resumen_hallazgos(porcentaje_lugar: pd.DataFrame, porcentaje_analista: pd.DataFrame, formas: pd.DataFrame,
                      precision: pd.DataFrame, digitos_centesimo: pd.DataFrame, repetidas_mes: pd.DataFrame,
                      analistas_salida: dict, alfa: float = ALFA) -> list[str]:
    """Las 5 frases principales de la página, para el informe.

    porcentaje_lugar: % de 5 g fijo por lugar (porcentaje_fijo() con una sola categoría: lugar, n, n_fijo, pct_fijo);
    porcentaje_analista: porcentaje_fijo(datos, "analista"); formas: comparar_formas(); precision:
    precision_titulacion(); digitos_centesimo: preferencia_digitos(datos, "centesimo"); repetidas_mes:
    repetidas_por_mes(); analistas_salida: comparar_analistas() del aceite de salida.
    """
    frases = []

    # 1. Dos formas de pesar
    if not porcentaje_lugar.empty:
        partes = " y ".join(f"el {numero(f['pct_fijo'], 1 if f['pct_fijo'] < 1 else 0)} % de las de {f['lugar'].lower()}"
                            for f in porcentaje_lugar.to_dict("records"))
        frase = f"Conviven dos formas de pesar: se pesan a 5 g fijo {partes}; el resto, con gramaje variable."
        mayor = _mas_fijo(porcentaje_analista)
        if mayor is not None:
            frase += (f" Quien más pesa a 5 g es {mayor['analista']} ({numero(mayor['pct_fijo'], 0)} % de sus "
                      f"muestras de {mayor['lugar'].lower()}).")
        frases.append(frase)

    # 2. ¿El gramaje cambia el resultado?
    con_dato = formas.dropna(subset=["p"])
    if not con_dato.empty:
        significativas = con_dato[con_dato["p"] < alfa]
        elegida = (significativas if not significativas.empty else con_dato).iloc[0].to_dict()
        frases.append(frase_formas(elegida, alfa))

    # 3. Lectura de la bureta: precisión y dígitos
    if not precision.empty:
        n, mayores = precision["n"].sum(), precision["n_mayor"].sum()
        frase = (f"El {numero(100 * mayores / n, 1)} % de las muestras tiene un error de lectura mayor al "
                 f"{numero(ERROR_MAXIMO_PCT, 0)} % (menos de {numero(LECTURA_ML / (ERROR_MAXIMO_PCT / 100), 0)} mL netos)")
        redondean = digitos_centesimo[digitos_centesimo["p"].notna() & (digitos_centesimo["p"] < alfa)
                                      & (digitos_centesimo["exceso_0_5"] >= 0.10)]
        if not redondean.empty:
            nombres = redondean.sort_values("exceso_0_5", ascending=False)["grupo"].tolist()
            verbo = "redondea" if len(nombres) == 1 else "redondean"
            frase += f", y {_lista(nombres)} {verbo} el último dígito de los mL a 0 o 5"
        frases.append(frase + ".")

    # 4. Entradas repetidas en varios eBOE (posible copia)
    if not repetidas_mes.empty and repetidas_mes["entradas"].sum() > 0:
        pct = 100 * repetidas_mes["repetidas"].sum() / repetidas_mes["entradas"].sum()
        frases.append(f"El {numero(pct, 1)} % de las muestras de entrada está anotado igual en más de un eBOE "
                      f"(máximo mensual {numero(repetidas_mes['pct'].max(), 1)} %): cada eBOE tiene su propia entrada, "
                      "así que lo más probable es que se hayan copiado; quedan fuera de las comparaciones entre eBOE.")

    # 5. Analistas: si el que más da es también el que más pesa a 5 g en la salida, y las muestras de 5 g
    # dan más, parte de la diferencia es por el gramaje y no por el analista.
    frase = frase_analistas(analistas_salida, "el aceite de salida", "kg/MT")
    salida = formas[formas["lugar"] == "Salida"].dropna(subset=["p"])
    if analistas_salida["p"] is not None and not salida.empty:
        primero = analistas_salida["medias"].sort_values("media").iloc[-1]["grupo"]
        fijos = porcentaje_analista[(porcentaje_analista["lugar"] == "Salida")
                                    & (porcentaje_analista["analista"] == primero)]
        efecto = salida.iloc[0]
        if (not fijos.empty and fijos["pct_fijo"].iloc[0] >= 50 and efecto["p"] < alfa
                and efecto["diferencia"] > 0):
            frase += (f" Ojo: {primero} pesa a 5 g el {numero(fijos['pct_fijo'].iloc[0], 0)} % de sus salidas, y esas "
                      f"muestras dan {numero(efecto['diferencia'], 2)} kg/MT más: parte de la diferencia es por el "
                      "gramaje y no por el analista.")
    frases.append(frase)
    return frases


def _mas_fijo(porcentaje_analista: pd.DataFrame) -> dict | None:
    """Analista y lugar con el mayor % de muestras a 5 g fijo (con al menos N_MINIMO_DIGITOS muestras)."""
    candidatos = porcentaje_analista[(porcentaje_analista["n"] >= N_MINIMO_DIGITOS)
                                     & (porcentaje_analista["pct_fijo"] > 0)]
    if candidatos.empty:
        return None
    return candidatos.sort_values("pct_fijo").iloc[-1].to_dict()


def _lista(nombres: list[str]) -> str:
    """["A"] → "A"; ["A", "B", "C"] → "A, B y C"."""
    return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]
