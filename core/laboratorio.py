"""Confiabilidad del laboratorio: controles sobre los datos crudos de la titulación Scott.

Trabaja sobre core.analisis.tabla_analisis() (ya filtrada), que trae los datos crudos de cada
análisis de fruta: peso_muestra_g (g), vol_ml (mL de titulante), normalidad (N) y analista.
- Gramaje: % de muestras fuera del protocolo de Brown (entrada 15 g y salida 25 g; el rango es un
  parámetro) y muestras con vol_ml mayor a 50 mL.
- Preferencia de dígitos: frecuencia del primer decimal y del último dígito (centésimo) de vol_ml por
  analista, con chi² contra la distribución uniforme (cada dígito, 10 %).
- Muestras compartidas: el mismo peso y volumen, a la misma hora, en la Entrada de más de un eBOE (una
  sola titulación anotada para varias máquinas).
- Analistas: ANOVA de dos factores (analista + bloque eBOE × semana) para comparar analistas que
  trabajaron sobre el mismo eBOE en la misma semana, con medias ajustadas por bloque.
- Normalidad: un valor por día de producción y los períodos con la misma normalidad.
- Gramaje vs resultado: recta del aceite en fruta en función del peso de la muestra.
"""

import numpy as np
import pandas as pd
from scipy import stats

from core.formato import numero, texto_p
from core.mensual import etiqueta_mes, mes_de
from core.regresion import regresion_lineal
from core.spc import evaluar_carta, limites_imr

ALFA = 0.05
GRAMOS_PROTOCOLO = {"Entrada": 15.0, "Salida": 25.0}     # protocolo de Brown (g de muestra)
TOLERANCIA_PROTOCOLO = 0.10                              # ±10 % por defecto (configurable en la página)
VOL_MAXIMO_ML = 50.0
N_MINIMO_DIGITOS = 50       # con 50 muestras, cada dígito espera 5: el mínimo para usar chi²
DIGITOS = list(range(10))
POSICIONES = {"decimo": "primer decimal", "centesimo": "último dígito (centésimo)"}
DIAS_MISMA_NORMALIDAD = 15


# ---------------------------------------------------------------- gramaje y volumen

def rangos_protocolo(tolerancia: float = TOLERANCIA_PROTOCOLO) -> dict[str, tuple[float, float]]:
    """{lugar: (mínimo, máximo)} en g: el gramaje del protocolo ± tolerancia (0,10 = ±10 %)."""
    if not 0 <= tolerancia < 1:
        raise ValueError("La tolerancia tiene que estar entre 0 y 1 (0,10 = ±10 %).")
    return {lugar: (g * (1 - tolerancia), g * (1 + tolerancia)) for lugar, g in GRAMOS_PROTOCOLO.items()}


def fuera_de_protocolo(datos: pd.DataFrame, rangos: dict, por: list[str] | None = None) -> pd.DataFrame:
    """Muestras con peso_muestra_g fuera del rango del protocolo, por lugar (y por las columnas de `por`).

    rangos: {lugar: (mínimo, máximo)} en g, por ejemplo rangos_protocolo(). Devuelve lugar, las
    columnas de `por`, minimo, maximo, n, debajo, encima, fuera y pct_fuera (%).
    """
    grupos = ["lugar", *(por or [])]
    filas = []
    for clave, sub in datos.dropna(subset=["peso_muestra_g"]).groupby(grupos, sort=True):
        clave = clave if isinstance(clave, tuple) else (clave,)
        lugar = clave[0]
        if lugar not in rangos:
            continue
        minimo, maximo = rangos[lugar]
        if minimo > maximo:
            raise ValueError(f"El mínimo del protocolo de {lugar} es mayor que el máximo.")
        debajo, encima = int((sub["peso_muestra_g"] < minimo).sum()), int((sub["peso_muestra_g"] > maximo).sum())
        filas.append(dict(zip(grupos, clave)) | {
            "minimo": minimo, "maximo": maximo, "n": len(sub), "debajo": debajo, "encima": encima,
            "fuera": debajo + encima, "pct_fuera": 100 * (debajo + encima) / len(sub)})
    return pd.DataFrame(filas, columns=[*grupos, "minimo", "maximo", "n", "debajo", "encima", "fuera", "pct_fuera"])


def muestras_vol_alto(datos: pd.DataFrame, limite: float = VOL_MAXIMO_ML) -> pd.DataFrame:
    """Análisis con vol_ml mayor que `limite` (mL), ordenados por fecha_hora."""
    return datos[datos["vol_ml"] > limite].sort_values(["fecha_hora", "eboe", "lugar"], ignore_index=True)


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
    Columnas: grupo, n, pct_0 … pct_9 (%), chi2, p, exceso_0_5, digito_max, exceso_max.
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
        chi2 = p = None
        if n >= N_MINIMO_DIGITOS:
            prueba = stats.chisquare(cuentas)
            chi2, p = float(prueba.statistic), float(prueba.pvalue)
        maximo = int(np.argmax(cuentas))
        filas.append(fila | {"chi2": chi2, "p": p, "exceso_0_5": (cuentas[0] + cuentas[5]) / (0.2 * n) - 1,
                             "digito_max": maximo, "exceso_max": cuentas[maximo] / (0.1 * n) - 1})
    return pd.DataFrame(filas, columns=["grupo", "n", *(f"pct_{d}" for d in DIGITOS), "chi2", "p", "exceso_0_5",
                                        "digito_max", "exceso_max"])


def frase_digitos(tabla: pd.DataFrame, posicion: str, alfa: float = ALFA) -> str:
    """Frase con los analistas que prefieren algún dígito (chi² significativo).

    Ejemplo: "En el primer decimal de los mL, Bulegio anota 0 y 5 un 35 % más de lo esperado
    (p < 0,001). Sin preferencia: Cuesta. Con menos de 50 muestras (no se prueba): Flores."
    Si el exceso de 0 y 5 es menor al 10 %, se nombra el dígito más anotado.
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
    return texto


# ---------------------------------------------------------------- muestras compartidas

def muestras_compartidas(datos: pd.DataFrame) -> pd.DataFrame:
    """Grupos de muestras de Entrada con la misma fecha_hora, peso_muestra_g y vol_ml en más de un eBOE.

    Es muy poco probable que dos frutas distintas den exactamente el mismo peso y volumen: lo más
    probable es que se tituló una sola muestra y se anotó para varios eBOE.
    Devuelve una fila por grupo: fecha_hora, fecha_produccion, peso_muestra_g, vol_ml, eboes ("1, 2"),
    n_eboes y analistas.
    """
    entradas = datos[datos["lugar"] == "Entrada"].dropna(subset=["peso_muestra_g", "vol_ml"])
    claves = ["fecha_hora", "peso_muestra_g", "vol_ml"]
    compartidas = entradas[entradas.groupby(claves)["eboe"].transform("nunique") > 1]
    filas = []
    for (fecha_hora, peso, vol), sub in compartidas.groupby(claves, sort=True):
        filas.append({"fecha_hora": fecha_hora, "fecha_produccion": sub["fecha_produccion"].iloc[0],
                      "peso_muestra_g": peso, "vol_ml": vol,
                      "eboes": ", ".join(str(e) for e in sorted(sub["eboe"].unique())),
                      "n_eboes": sub["eboe"].nunique(),
                      "analistas": ", ".join(sorted(sub["analista"].dropna().unique()))})
    return pd.DataFrame(filas, columns=["fecha_hora", "fecha_produccion", "peso_muestra_g", "vol_ml", "eboes",
                                        "n_eboes", "analistas"])


def compartidas_por_mes(datos: pd.DataFrame) -> pd.DataFrame:
    """Cantidad y % de muestras de Entrada que están en un grupo compartido, por mes de producción.

    Columnas: mes, etiqueta ("Jul 2025"), entradas (muestras de Entrada del mes), grupos (grupos
    compartidos), compartidas (muestras dentro de esos grupos) y pct (compartidas / entradas, %).
    """
    entradas = datos[datos["lugar"] == "Entrada"].dropna(subset=["peso_muestra_g", "vol_ml"])
    claves = ["fecha_hora", "peso_muestra_g", "vol_ml"]
    eboes_por_grupo = entradas.groupby(claves)["eboe"].transform("nunique")
    entradas = entradas.assign(mes=mes_de(entradas["fecha_produccion"]), compartida=eboes_por_grupo > 1)
    filas = []
    for mes, sub in entradas.groupby("mes", sort=True):
        compartidas = sub[sub["compartida"]]
        filas.append({"mes": mes, "etiqueta": etiqueta_mes(mes), "entradas": len(sub),
                      "grupos": len(compartidas.drop_duplicates(claves)), "compartidas": len(compartidas),
                      "pct": 100 * len(compartidas) / len(sub)})
    return pd.DataFrame(filas, columns=["mes", "etiqueta", "entradas", "grupos", "compartidas", "pct"])


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
