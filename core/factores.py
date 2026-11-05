"""Factores que explican la eficiencia y el aceite residual de los eBOE.

Trabaja sobre los pares entrada/salida (core.analisis.pares_con_emulsion) con los datos de la fruta de
la muestra de entrada: frutos en 10 kg (= n_frutos × 10 / peso_fruta_kg, el calibre: más frutos = fruta
más chica), calidad y día del proceso. Residual = aceite en fruta de salida (kg/MT); removido = entrada
− salida (kg/MT).
- Limpieza: se excluyen los pares con más de 150 frutos en 10 kg (errores de carga).
- Calibre: quintiles de frutos en 10 kg con cortes de todo el histórico; eficiencia, entrada, removido y
  residual por quintil y temporada (ponderados por toneladas del turno, core.estadistica).
- Descomposición: cuánto de la diferencia entre temporadas explica el cambio de calibre
  (estandarización directa a la mezcla de quintiles de la temporada base) y cuánto el resto.
- Turno, calidad y día del proceso: medias con IC 95 % por categoría y temporada.
- Modelo multivariable (OLS de statsmodels): % de la variación que explica cada factor (ANOVA tipo II),
  efectos con IC 95 % y VIF.
- Efecto esponja: salida y eficiencia por cuartil de aceite en emulsión y eBOE; pendiente de la salida vs
  la emulsión; ¿explica la emulsión la diferencia entre eBOE?
- Comparación justa: cada eBOE contra el promedio de los otros dentro de celdas de calibre × programa
  (× MTPH × temporada, × emulsión).
- Modelos A (con eBOE) y B (sin eBOE, máquinas iguales): el residuo de cada eBOE en B es lo que el modelo
  no ve.
- Aceite perdido: toneladas por eBOE (la fruta del turno repartida por MTPH) × diferencia de residual.
- Balance aceite–jugo: por turno, efecto de +1 nivel de programa sobre el concentrado y el aceite, y su
  valor en USD con los precios de core.precios.
"""

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.outliers_influence import variance_inflation_factor

from core.comparaciones import comparar_grupos
from core.estadistica import media_ponderada_por_turno
from core.formato import numero, texto_p
from core.precios import kg_a_65_brix
from core.programa import numero_programa
from core.regresion import regresiones_por_grupo

ALFA = 0.05
FRUTOS_MAXIMO = 150         # frutos en 10 kg: más que esto es un error de carga
N_QUINTILES = 5
DIA_MAXIMO = 5              # días del proceso: del 5 en adelante van juntos ("5 o más")
VIF_MAXIMO = 5
N_MINIMO_CELDA = 30        # pares por eBOE dentro de un quintil: con menos, se avisa
# respuesta → nombre, unidad, texto de los efectos, decimales
RESPUESTAS = {
    "eficiencia_pct": {"nombre": "Eficiencia eBOE", "unidad": "%", "efecto": "puntos de eficiencia", "decimales": 2},
    "residual": {"nombre": "Aceite residual", "unidad": "kg/MT", "efecto": "kg/MT de residual", "decimales": 3},
}
# Factores del modelo: columna → (nombre, tipo, paso del efecto y texto del paso, nivel de referencia)
FACTORES = {
    "eboe": ("Diferencia propia del eBOE", "categoria", None, None, 1),
    "temporada": ("Temporada", "categoria", None, None, None),
    "programa_num": ("Programa", "numero", 1, "+1 nivel de programa", None),
    "tph": ("MTPH", "numero", 1, "+1 MTPH", None),
    "frutos_10kg": ("Calibre (frutos en 10 kg)", "numero", 10, "+10 frutos en 10 kg (fruta más chica)", None),
    "calidad": ("Calidad de fruta", "categoria", None, None, "Firme"),
    "turno": ("Turno", "categoria", None, None, "M"),
    "caudal_fase_pesada": ("Caudal de fase pesada", "numero", 1, "+1 m³/h de caudal de fase pesada", None),
    "aceite_emulsion": ("Aceite en emulsión", "numero", 0.1, "+0,1 % de aceite en emulsión", None),
    "dia_proceso": ("Día del proceso", "numero", 1, "+1 día de proceso", None),
}
CATEGORIAS = {"turno": "Turno", "calidad": "Calidad de fruta", "dia_categoria": "Día del proceso"}
# Orden de las categorías en tablas y gráficos (las que no figuran van al final, en orden alfabético)
ORDENES = {"turno": ["M", "T", "N"], "calidad": ["Firme", "Blanda", "Partida", "Podrido"],
           "grupo_programa": ["N0–N3", "N4–N5", "N6–N9"]}


# ---------------------------------------------------------------- preparación

def agregar_dia_proceso(tabla: pd.DataFrame) -> pd.DataFrame:
    """Agrega dia_proceso: 1 el primer día de producción del proceso, 2 el siguiente, etc.

    Se calcula sobre la tabla que se pase (conviene la de toda la planta, sin filtrar, para que un
    filtro de fechas no cambie la numeración).
    """
    inicio = tabla.groupby("proceso_id")["fecha_produccion"].transform("min")
    return tabla.assign(dia_proceso=(tabla["fecha_produccion"] - inicio).dt.days + 1)


def frutos_en_10kg(tabla: pd.DataFrame) -> pd.Series:
    """Frutos en 10 kg = n_frutos × 10 / peso_fruta_kg (mapeo de LEDESMA, columna Z).

    Vacío si falta el dato, si el peso no es positivo o si n_frutos es 0 (no medido, como el peso 0 en
    el importador).
    """
    validos = (tabla["n_frutos"] > 0) & (tabla["peso_fruta_kg"] > 0)
    return (tabla["n_frutos"] * 10 / tabla["peso_fruta_kg"]).where(validos)


def preparar_pares(pares: pd.DataFrame) -> pd.DataFrame:
    """Columnas que usa la pestaña, sobre core.analisis.pares_con_emulsion() (de una tabla con dia_proceso).

    Agrega frutos_10kg, residual (= aceite_salida, kg/MT), removido (= entrada − salida, kg/MT),
    programa_num (N3 → 3) y dia_categoria ("día 1" … "día 5 o más").
    """
    return pares.assign(
        frutos_10kg=frutos_en_10kg(pares), residual=pares["aceite_salida"],
        removido=pares["aceite_fruta"] - pares["aceite_salida"],
        programa_num=pares["programa"].map(numero_programa),
        dia_categoria=pares["dia_proceso"].map(_categoria_dia))


def _categoria_dia(dia) -> str | None:
    """1 → "día 1", …; del DIA_MAXIMO en adelante → "día 5 o más"."""
    if pd.isna(dia):
        return None
    return f"día {DIA_MAXIMO} o más" if dia >= DIA_MAXIMO else f"día {int(dia)}"


def separar_errores(pares: pd.DataFrame, maximo: float = FRUTOS_MAXIMO) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pares limpios, pares excluidos por tener más de `maximo` frutos en 10 kg)."""
    error = pares["frutos_10kg"] > maximo
    return pares[~error].reset_index(drop=True), pares[error].reset_index(drop=True)


# ---------------------------------------------------------------- calibre

def cortes_quintiles(frutos: pd.Series) -> list[float]:
    """Los 4 cortes de los quintiles de frutos en 10 kg (percentiles 20, 40, 60 y 80)."""
    valores = pd.to_numeric(frutos, errors="coerce").dropna()
    if len(valores) < N_QUINTILES:
        raise ValueError("Hacen falta al menos 5 datos de frutos en 10 kg para los quintiles.")
    return [float(valores.quantile(q / N_QUINTILES)) for q in range(1, N_QUINTILES)]


def asignar_quintil(frutos: pd.Series, cortes: list[float]) -> pd.Series:
    """Quintil (1 a 5) de cada valor: 1 = menos frutos (fruta grande), 5 = más frutos (fruta chica)."""
    return pd.cut(frutos, [-np.inf, *cortes, np.inf], labels=range(1, N_QUINTILES + 1)).astype("Int64")


def etiquetas_quintiles(cortes: list[float]) -> dict[int, str]:
    """{1: "Q1 grande (≤ 43)", 2: "Q2 (43–49)", …, 5: "Q5 chica (> 60)"} en frutos en 10 kg."""
    c = [numero(v, 0) for v in cortes]
    etiquetas = {1: f"Q1 grande (≤ {c[0]})", N_QUINTILES: f"Q5 chica (> {c[-1]})"}
    for q in range(2, N_QUINTILES):
        etiquetas[q] = f"Q{q} ({c[q - 2]}–{c[q - 1]})"
    return etiquetas


INDICADORES_CALIBRE = {"eficiencia": "eficiencia_pct", "entrada": "aceite_fruta", "removido": "removido",
                       "residual": "residual"}


def por_quintil(pares: pd.DataFrame, cortes: list[float], toneladas: pd.DataFrame) -> pd.DataFrame:
    """Eficiencia (%), entrada, removido y residual (kg/MT) por quintil de calibre y temporada.

    Medias ponderadas por las toneladas del turno (simples si no hay producción). Columnas: quintil,
    etiqueta, temporada, n (pares), frutos (promedio de frutos en 10 kg), eficiencia, entrada,
    removido, residual y ponderada.
    """
    datos = pares.dropna(subset=["frutos_10kg"]).assign(quintil=lambda d: asignar_quintil(d["frutos_10kg"], cortes))
    etiquetas = etiquetas_quintiles(cortes)
    filas = []
    for (quintil, temporada), sub in datos.groupby(["quintil", "temporada"], sort=True):
        fila = {"quintil": int(quintil), "etiqueta": etiquetas[int(quintil)], "temporada": temporada, "n": len(sub),
                "frutos": float(sub["frutos_10kg"].mean())}
        ponderada = False
        for nombre, columna in INDICADORES_CALIBRE.items():
            media = media_ponderada_por_turno(sub, columna, toneladas)
            fila[nombre], ponderada = media["valor"], media["ponderada"]
        filas.append(fila | {"ponderada": ponderada})
    return pd.DataFrame(filas, columns=["quintil", "etiqueta", "temporada", "n", "frutos", *INDICADORES_CALIBRE,
                                        "ponderada"])


def frutos_por_eboe(pares: pd.DataFrame) -> pd.DataFrame:
    """Frutos en 10 kg promedio por eBOE y temporada (columnas eboe, temporada, n, frutos)."""
    datos = pares.dropna(subset=["frutos_10kg"])
    return (datos.groupby(["eboe", "temporada"])["frutos_10kg"].agg(n="size", frutos="mean").reset_index())


def frase_calibre(tabla: pd.DataFrame, temporada) -> str:
    """Fruta chica (Q5) contra grande (Q1) en una temporada.

    Ejemplo: "En 2026, la fruta chica trae 0,40 kg/MT más de aceite que la grande, pero el eBOE remueve
    casi lo mismo (6,1 contra 6,0 kg/MT); el residual sube de 1,12 a 1,81 kg/MT y la eficiencia baja de
    85,1 % a 78,2 %."
    """
    sub = tabla[tabla["temporada"] == temporada].set_index("quintil")
    if 1 not in sub.index or N_QUINTILES not in sub.index:
        return f"En {temporada} no hay datos de fruta grande y chica para comparar."
    grande, chica = sub.loc[1], sub.loc[N_QUINTILES]
    dif_entrada = chica["entrada"] - grande["entrada"]
    dif_removido = chica["removido"] - grande["removido"]
    trae = f"{numero(abs(dif_entrada), 2)} kg/MT {'más' if dif_entrada > 0 else 'menos'} de aceite que la grande"
    if abs(dif_removido) < 0.5 * abs(dif_entrada):
        remueve = (f"pero el eBOE remueve casi lo mismo ({numero(chica['removido'], 2)} contra "
                   f"{numero(grande['removido'], 2)} kg/MT)")
    else:
        remueve = (f"y el eBOE remueve {numero(abs(dif_removido), 2)} kg/MT "
                   f"{'más' if dif_removido > 0 else 'menos'} ({numero(chica['removido'], 2)} contra "
                   f"{numero(grande['removido'], 2)} kg/MT)")
    residual = "sube" if chica["residual"] > grande["residual"] else "baja"
    eficiencia = "sube" if chica["eficiencia"] > grande["eficiencia"] else "baja"
    return (f"En {temporada}, la fruta chica trae {trae}, {remueve}; el residual {residual} de "
            f"{numero(grande['residual'], 2)} a {numero(chica['residual'], 2)} kg/MT y la eficiencia {eficiencia} de "
            f"{numero(grande['eficiencia'], 1)} % a {numero(chica['eficiencia'], 1)} %.")


def por_quintil_eboe(pares: pd.DataFrame, cortes: list[float], toneladas: pd.DataFrame) -> pd.DataFrame:
    """Cada eBOE dentro de cada quintil de calibre y temporada (comparación con fruta del mismo tamaño).

    Eficiencia (%) y residual (kg/MT) ponderados por las toneladas del turno (simples si no hay producción);
    programa: promedio simple del número de programa (N3 → 3). Columnas: quintil, etiqueta, temporada,
    eboe, n, frutos, eficiencia, residual, programa y chico (True si n < N_MINIMO_CELDA).
    """
    datos = pares.dropna(subset=["frutos_10kg"]).assign(quintil=lambda d: asignar_quintil(d["frutos_10kg"], cortes))
    etiquetas = etiquetas_quintiles(cortes)
    filas = []
    for (quintil, temporada, eboe), sub in datos.groupby(["quintil", "temporada", "eboe"], sort=True):
        filas.append({"quintil": int(quintil), "etiqueta": etiquetas[int(quintil)], "temporada": temporada,
                      "eboe": eboe, "n": len(sub), "frutos": float(sub["frutos_10kg"].mean()),
                      "eficiencia": media_ponderada_por_turno(sub, "eficiencia_pct", toneladas)["valor"],
                      "residual": media_ponderada_por_turno(sub, "residual", toneladas)["valor"],
                      "programa": float(sub["programa_num"].mean()) if sub["programa_num"].notna().any() else None})
    tabla = pd.DataFrame(filas, columns=["quintil", "etiqueta", "temporada", "eboe", "n", "frutos", "eficiencia",
                                         "residual", "programa"])
    return tabla.assign(chico=tabla["n"] < N_MINIMO_CELDA)


def frase_quintil_eboe(tabla: pd.DataFrame, temporada) -> str:
    """En cuántos quintiles cada eBOE deja el mayor residual (solo quintiles con N ≥ N_MINIMO_CELDA en todos).

    Ejemplo: "En 2026, con fruta del mismo tamaño, el eBOE 2 deja el mayor residual en 5 de 5 quintiles
    (programa promedio 4,1 contra 3,6 de los otros)."
    """
    sub = tabla[tabla["temporada"] == temporada]
    eboes = sub["eboe"].nunique()
    completos = sub.groupby("quintil").filter(lambda g: len(g) == eboes and (g["n"] >= N_MINIMO_CELDA).all())
    if eboes < 2 or completos.empty:
        return f"En {temporada} no hay quintiles con al menos {N_MINIMO_CELDA} pares en cada eBOE para comparar."
    peores = completos.loc[completos.groupby("quintil")["residual"].idxmax(), "eboe"]
    eboe, veces = peores.value_counts().index[0], int(peores.value_counts().iloc[0])
    total = completos["quintil"].nunique()
    propio = completos[completos["eboe"] == eboe]["programa"].mean()
    otros = completos[completos["eboe"] != eboe]["programa"].mean()
    return (f"En {temporada}, con fruta del mismo tamaño, el eBOE {int(eboe)} deja el mayor residual en {veces} de "
            f"{total} quintiles (programa promedio {numero(propio, 1)} contra {numero(otros, 1)} de los otros).")


def frase_eboe_calibre(tabla: pd.DataFrame, temporada) -> str:
    """Ejemplo: "En 2026, el eBOE 2 recibe la fruta más chica: 62 frutos en 10 kg contra 46 del eBOE 3." """
    sub = tabla[tabla["temporada"] == temporada].sort_values("frutos")
    if len(sub) < 2:
        return f"En {temporada} hay un solo eBOE con datos de calibre."
    chica, grande = sub.iloc[-1], sub.iloc[0]
    return (f"En {temporada}, el eBOE {int(chica['eboe'])} recibe la fruta más chica: {numero(chica['frutos'], 0)} "
            f"frutos en 10 kg contra {numero(grande['frutos'], 0)} del eBOE {int(grande['eboe'])}.")


# ---------------------------------------------------------------- descomposición

def promedio_por_temporada(pares: pd.DataFrame, columna: str, toneladas: pd.DataFrame) -> dict:
    """{temporada: promedio de la columna}, ponderado por las toneladas del turno, como la pestaña Proceso.

    Con todos los pares de la temporada (tengan o no calibre). Es el total que muestra la descomposición.
    """
    return {t: media_ponderada_por_turno(sub, columna, toneladas)["valor"]
            for t, sub in pares.groupby("temporada", sort=True)}


def descomponer(tabla: pd.DataFrame, indicador: str, base, nueva, totales: dict | None = None) -> dict | None:
    """Separa la diferencia de un indicador entre dos temporadas en calibre + resto (estandarización directa).

    Con wq = proporción de pares de cada quintil y mq = media del quintil (por_quintil()):
    valor de cada temporada (método) = Σ wq·mq; estandarizado = Σ wq(base)·mq(nueva) (la nueva con la
    mezcla de calibres de la base); calibre = nueva − estandarizado (lo que cambió por tener otra mezcla
    de calibres). Solo entran los quintiles con datos en las dos temporadas.
    totales: {temporada: valor} de promedio_por_temporada() (el de la pestaña Proceso). Si se pasa, el total
    es la diferencia de esos valores; si no, la del método. resto = total − calibre (lo que cambió dentro
    de cada calibre).
    Devuelve base, nueva, valor_base, valor_nueva, total, calibre, resto, total_metodo (el del método de
    quintiles, para la nota si difiere), estandarizado y frutos_base, frutos_nueva (promedio de frutos en
    10 kg); None si falta una temporada.
    """
    a = tabla[tabla["temporada"] == base].set_index("quintil")
    b = tabla[tabla["temporada"] == nueva].set_index("quintil")
    comunes = a.index.intersection(b.index)
    if comunes.empty:
        return None
    a, b = a.loc[comunes], b.loc[comunes]
    wa, wb = a["n"] / a["n"].sum(), b["n"] / b["n"].sum()
    metodo_base, metodo_nueva = float((wa * a[indicador]).sum()), float((wb * b[indicador]).sum())
    estandarizado = float((wa * b[indicador]).sum())
    calibre = metodo_nueva - estandarizado
    if totales is not None and totales.get(base) is not None and totales.get(nueva) is not None:
        valor_base, valor_nueva = float(totales[base]), float(totales[nueva])
    else:
        valor_base, valor_nueva = metodo_base, metodo_nueva
    total = valor_nueva - valor_base
    return {"base": base, "nueva": nueva, "valor_base": valor_base, "valor_nueva": valor_nueva, "total": total,
            "calibre": calibre, "resto": total - calibre, "total_metodo": metodo_nueva - metodo_base,
            "estandarizado": estandarizado,
            "frutos_base": float((wa * a["frutos"]).sum()), "frutos_nueva": float((wb * b["frutos"]).sum())}


def nota_descomposicion(resultado: dict | None, indicador: str) -> str | None:
    """Aclaración si el total del método de quintiles difiere del de la pestaña Proceso (a la vista); si no, None."""
    if resultado is None:
        return None
    unidad, dec = ("puntos", 1) if indicador == "eficiencia" else ("kg/MT", 2)
    if numero(resultado["total"], dec) == numero(resultado["total_metodo"], dec):
        return None
    return (f"Armando cada temporada con sus quintiles pesados por la cantidad de pares (solo los pares con calibre), "
            f"la diferencia da {numero(resultado['total_metodo'], dec)} {unidad} en vez de "
            f"{numero(resultado['total'], dec)}: la pestaña Proceso pondera por las toneladas del turno y usa todos los "
            "pares. El total y el número principal son los de Proceso; la parte del calibre sale del método de "
            "quintiles y el resto es total − calibre.")


def frase_descomposicion(resultado: dict | None, indicador: str) -> str:
    """Ejemplo: "De los 2,8 puntos de caída de la eficiencia entre 2025 y 2026, 0,9 se explican por fruta
    más chica y 1,9 por el resto (programa, MTPH, operación…)."
    """
    if resultado is None:
        return "Hacen falta las dos temporadas para separar el efecto del calibre."
    eficiencia = indicador == "eficiencia"
    unidad, dec = ("puntos", 1) if eficiencia else ("kg/MT", 2)
    total, calibre, resto = resultado["total"], resultado["calibre"], resultado["resto"]
    if eficiencia:
        cambio = "caída" if total < 0 else "suba"
        sujeto = "de la eficiencia"
    else:
        cambio = "aumento" if total > 0 else "baja"
        sujeto = "del aceite residual"
    fruta = "fruta más chica" if resultado["frutos_nueva"] > resultado["frutos_base"] else "fruta más grande"
    inicio = (f"De los {numero(abs(total), dec)} {unidad} de {cambio} {sujeto} entre {resultado['base']} y "
              f"{resultado['nueva']}, ")
    if total == 0 or np.sign(calibre) == np.sign(total):
        return (f"{inicio}{numero(abs(calibre), dec)} se explican por {fruta} y {numero(abs(resto), dec)} por el "
                "resto (programa, MTPH, operación…).")
    return (f"{inicio}ninguno se explica por el calibre: la {fruta} lo habría movido {numero(abs(calibre), dec)} "
            f"{unidad} en sentido contrario, y el resto suma {numero(abs(resto), dec)} {unidad}.")


# ---------------------------------------------------------------- turno, calidad y día del proceso

def por_categoria(pares: pd.DataFrame, factor: str, variable: str, alfa: float = ALFA) -> pd.DataFrame:
    """Media e IC 1 − alfa de `variable` por categoría de `factor` y temporada (core.comparaciones).

    Promedio simple (para el IC). Las categorías con menos de 2 datos no entran. Columnas: temporada,
    grupo, n, media, ic_inf, ic_sup y p (ANOVA de la temporada; vacío con una sola categoría).
    """
    filas = []
    for temporada, sub in pares.dropna(subset=[factor, variable]).groupby("temporada", sort=True):
        sub = sub[sub.groupby(factor)[variable].transform("size") >= 2]
        if sub[factor].nunique() < 2:
            continue
        resultado = comparar_grupos(sub, variable, factor, alfa)
        medias = resultado["medias"].assign(temporada=temporada, p=resultado["p"])
        filas.append(medias[["temporada", "grupo", "n", "media", "ic_inf", "ic_sup", "p"]])
    if not filas:
        return pd.DataFrame(columns=["temporada", "grupo", "n", "media", "ic_inf", "ic_sup", "p"])
    tabla = pd.concat(filas, ignore_index=True)
    tabla["grupo"] = tabla["grupo"].astype(str)
    orden = {t: k for k, t in enumerate(ORDENES.get(factor, []))}
    return tabla.sort_values(["temporada", "grupo"], ignore_index=True,
                             key=lambda c: c.map(lambda g: (orden.get(g, len(orden)), g)) if c.name == "grupo" else c)


def frase_categoria(tabla: pd.DataFrame, factor: str, sujeto: str, unidad: str, decimales: int = 1,
                    alfa: float = ALFA) -> str:
    """Ejemplo: "Por turno, en 2026 la eficiencia va de 79,1 % (N) a 82,3 % (M) (p = 0,010); en 2025, de
    80,2 % (T) a 81,0 % (M) (p = 0,400: sin diferencia significativa)."
    """
    partes = []
    for temporada, sub in tabla.groupby("temporada", sort=False):
        sub = sub.sort_values("media")
        menor, mayor = sub.iloc[0], sub.iloc[-1]
        p = sub["p"].iloc[0]
        nota = "" if p < alfa else ": sin diferencia significativa"
        partes.append((temporada, f"de {numero(menor['media'], decimales)} {unidad} ({menor['grupo']}) a "
                                  f"{numero(mayor['media'], decimales)} {unidad} ({mayor['grupo']}) "
                                  f"({texto_p(p)}{nota})"))
    if not partes:
        return f"No hay datos suficientes para comparar por {CATEGORIAS[factor].lower()}."
    partes = sorted(partes, key=lambda x: x[0], reverse=True)
    texto = f"Por {CATEGORIAS[factor].lower()}, en {partes[0][0]} {sujeto} va {partes[0][1]}"
    for temporada, detalle in partes[1:]:
        texto += f"; en {temporada}, {detalle}"
    return texto + "."


# ---------------------------------------------------------------- efecto esponja

def cortes_cuartiles(valores: pd.Series) -> list[float]:
    """Los 3 cortes de los cuartiles (percentiles 25, 50 y 75)."""
    valores = pd.to_numeric(valores, errors="coerce").dropna()
    if len(valores) < 4:
        raise ValueError("Hacen falta al menos 4 datos para los cuartiles.")
    return [float(valores.quantile(q / 4)) for q in range(1, 4)]


def asignar_cuartil(valores: pd.Series, cortes: list[float]) -> pd.Series:
    """Cuartil (1 a 4) de cada valor; el corte entra en el cuartil de abajo."""
    return pd.cut(valores, [-np.inf, *cortes, np.inf], labels=range(1, 5)).astype("Int64")


def etiquetas_cuartiles(cortes: list[float], unidad: str = "%", decimales: int = 2) -> dict[int, str]:
    """{1: "C1 (≤ 1,12 %)", 2: "C2 (1,12–1,52 %)", 3: …, 4: "C4 (> 1,93 %)"}."""
    c = [numero(v, decimales) for v in cortes]
    return {1: f"C1 (≤ {c[0]} {unidad})", 2: f"C2 ({c[0]}–{c[1]} {unidad})", 3: f"C3 ({c[1]}–{c[2]} {unidad})",
            4: f"C4 (> {c[2]} {unidad})"}


def esponja_por_cuartil(pares: pd.DataFrame, cortes: list[float], toneladas: pd.DataFrame) -> pd.DataFrame:
    """Salida (kg/MT) y eficiencia (%) por cuartil de aceite en emulsión, eBOE y temporada.

    Medias ponderadas por las toneladas del turno (simples si no hay producción). Columnas: cuartil,
    etiqueta, temporada, eboe, n, emulsion (promedio simple del cuartil), salida y eficiencia.
    """
    datos = pares.dropna(subset=["aceite_emulsion"]).assign(
        cuartil=lambda d: asignar_cuartil(d["aceite_emulsion"], cortes))
    etiquetas = etiquetas_cuartiles(cortes)
    filas = [{"cuartil": int(c), "etiqueta": etiquetas[int(c)], "temporada": t, "eboe": e, "n": len(sub),
              "emulsion": float(sub["aceite_emulsion"].mean()),
              "salida": media_ponderada_por_turno(sub, "residual", toneladas)["valor"],
              "eficiencia": media_ponderada_por_turno(sub, "eficiencia_pct", toneladas)["valor"]}
             for (c, t, e), sub in datos.groupby(["cuartil", "temporada", "eboe"], sort=True)]
    return pd.DataFrame(filas, columns=["cuartil", "etiqueta", "temporada", "eboe", "n", "emulsion", "salida",
                                        "eficiencia"])


def pendientes_esponja(pares: pd.DataFrame) -> pd.DataFrame:
    """Recta de la salida (kg/MT) en función del aceite en emulsión (%), por eBOE y temporada.

    pendiente = kg/MT de salida por cada 1 % más de aceite en emulsión (core.regresion, con IC y p).
    """
    return regresiones_por_grupo(pares, "aceite_emulsion", "residual", ["eboe", "temporada"])


def esponja_por_eboe(pares: pd.DataFrame, toneladas: pd.DataFrame) -> pd.DataFrame:
    """Aceite en emulsión (%), salida (kg/MT) y eficiencia (%) promedio por eBOE y temporada.

    Ponderados por las toneladas del turno; solo los pares con emulsión. Columnas: eboe, temporada, n,
    emulsion, salida y eficiencia.
    """
    datos = pares.dropna(subset=["aceite_emulsion"])
    filas = [{"eboe": e, "temporada": t, "n": len(sub),
              **{nombre: media_ponderada_por_turno(sub, columna, toneladas)["valor"]
                 for nombre, columna in (("emulsion", "aceite_emulsion"), ("salida", "residual"),
                                         ("eficiencia", "eficiencia_pct"))}}
             for (e, t), sub in datos.groupby(["eboe", "temporada"], sort=True)]
    return pd.DataFrame(filas, columns=["eboe", "temporada", "n", "emulsion", "salida", "eficiencia"])


def frase_esponja(pendientes: pd.DataFrame, por_eboe: pd.DataFrame, temporada, alfa: float = ALFA) -> str:
    """¿La emulsión explica la diferencia entre eBOE? Dentro de una temporada.

    Mira si la salida sube con la emulsión en cada eBOE (pendiente positiva y significativa) y si el eBOE
    con más salida es también el de más emulsión. Lo que la emulsión explicaría de su diferencia =
    pendiente del eBOE × (su emulsión − la de los otros).
    Ejemplo: "En 2026, a más aceite en emulsión, más salida en los 3 eBOE; pero el eBOE 2 tiene la emulsión
    más baja (1,40 % contra 1,60 % de los otros), así que no explica su diferencia de 0,40 kg/MT."
    """
    pend = pendientes[(pendientes["temporada"] == temporada)].dropna(subset=["pendiente"])
    sub = por_eboe[por_eboe["temporada"] == temporada]
    if len(sub) < 2 or pend.empty:
        return f"En {temporada} no hay datos suficientes de emulsión en más de un eBOE."
    suben = pend[(pend["pendiente"] > 0) & (pend["p"] < alfa)]
    if len(suben) == len(pend):
        inicio = f"En {temporada}, a más aceite en emulsión, más salida en los {len(pend)} eBOE"
    elif suben.empty:
        inicio = f"En {temporada}, la salida no sube con el aceite en emulsión en ningún eBOE"
    else:
        inicio = (f"En {temporada}, a más aceite en emulsión, más salida en {len(suben)} de los {len(pend)} eBOE "
                  f"(eBOE {', '.join(str(int(e)) for e in suben['eboe'])})")
    peor = sub.loc[sub["salida"].idxmax()]
    otros = sub[sub["eboe"] != peor["eboe"]]
    diferencia = peor["salida"] - otros["salida"].mean()
    emulsion_otros = otros["emulsion"].mean()
    if peor["emulsion"] == sub["emulsion"].max():
        posicion = "más alta"
    elif peor["emulsion"] == sub["emulsion"].min():
        posicion = "más baja"
    else:
        posicion = "intermedia"
    texto_emulsion = (f"el eBOE {int(peor['eboe'])}, el de más salida, tiene la emulsión {posicion} "
                      f"({numero(peor['emulsion'], 2)} % contra {numero(emulsion_otros, 2)} % de los otros)")
    fila_pend = pend[pend["eboe"] == peor["eboe"]]
    explicada = (float(fila_pend["pendiente"].iloc[0]) * (peor["emulsion"] - emulsion_otros)
                 if not fila_pend.empty and not suben.empty else 0.0)
    if explicada <= 0 or diferencia <= 0:
        return f"{inicio}; pero {texto_emulsion}, así que no explica su diferencia de {numero(diferencia, 2)} kg/MT."
    parte = min(explicada / diferencia, 1.0) * 100
    return (f"{inicio}; {texto_emulsion}: explicaría {numero(explicada, 2)} de sus {numero(diferencia, 2)} kg/MT de "
            f"diferencia ({numero(parte, 0)} %).")


# ---------------------------------------------------------------- comparación justa entre eBOE

N_MINIMO_JUSTA = 5          # pares de cada eBOE en una celda para que la celda entre
GRUPOS_PROGRAMA = [("N0–N3", 0, 3), ("N4–N5", 4, 5), ("N6–N9", 6, 9)]
GRUPOS_MTPH = ["≤ 10", "10–12", "> 12"]
# Niveles de corrección: nombre → columnas que definen la celda (cada uno agrega a los anteriores)
NIVELES_JUSTA = {
    "Sin corregir": [],
    "Calibre + programa": ["quintil", "grupo_programa"],
    "+ MTPH y temporada": ["quintil", "grupo_programa", "grupo_mtph", "temporada"],
    "+ emulsión": ["quintil", "grupo_programa", "grupo_mtph", "temporada", "cuartil_emulsion"],
}
NIVEL_FRASE = "+ MTPH y temporada"
NOTA_JUSTA = ("Las máquinas son iguales: la parte no explicada apunta a factores no medidos (alimentación, nivel de agua, "
              "velocidades reales de rodillos, cuchillas, punto de muestreo).")


def grupo_programa(programa) -> str | None:
    """N0–N3, N4–N5 o N6–N9 según el número de programa."""
    if pd.isna(programa):
        return None
    return next((nombre for nombre, desde, hasta in GRUPOS_PROGRAMA if desde <= programa <= hasta), None)


def grupo_mtph(mtph) -> str | None:
    """≤ 10, 10–12 (más de 10 hasta 12) o > 12 MTPH."""
    if pd.isna(mtph):
        return None
    return GRUPOS_MTPH[0] if mtph <= 10 else GRUPOS_MTPH[1] if mtph <= 12 else GRUPOS_MTPH[2]


def preparar_celdas(pares: pd.DataFrame, cortes_calibre: list[float], cortes_emulsion: list[float]) -> pd.DataFrame:
    """Pares con todos los datos de las celdas (calibre, programa, MTPH y emulsión), con sus grupos.

    Todas las versiones de la comparación usan estos mismos pares: así lo único que cambia es la corrección.
    """
    datos = pares.dropna(subset=["frutos_10kg", "programa_num", "tph", "aceite_emulsion", "residual",
                                 "eficiencia_pct"])
    return datos.assign(quintil=asignar_quintil(datos["frutos_10kg"], cortes_calibre),
                        grupo_programa=datos["programa_num"].map(grupo_programa),
                        grupo_mtph=datos["tph"].map(grupo_mtph),
                        cuartil_emulsion=asignar_cuartil(datos["aceite_emulsion"], cortes_emulsion))


def celdas_justas(datos: pd.DataFrame, columnas: list[str], minimo: int = N_MINIMO_JUSTA) -> pd.DataFrame:
    """Diferencia de cada eBOE contra el promedio de los otros, dentro de cada celda.

    Celda = combinación de `columnas` (sin columnas: todos los pares juntos). Solo entran las celdas en las
    que todos los eBOE tienen al menos `minimo` pares. Dentro de la celda: promedio simple de salida (kg/MT)
    y eficiencia (%) de cada eBOE; dif_salida = salida del eBOE − promedio de las salidas de los otros
    eBOE (igual para la eficiencia). Columnas: las de la celda, eboe, n, salida, eficiencia, dif_salida y
    dif_eficiencia.
    """
    eboes = datos["eboe"].nunique()
    salida = ["eboe", "n", "salida", "eficiencia", "dif_salida", "dif_eficiencia"]
    if eboes < 2:
        return pd.DataFrame(columns=[*columnas, *salida])
    clave = columnas or ["_celda"]
    medias = (datos.assign(_celda=0).groupby([*clave, "eboe"], observed=True, sort=True)
              .agg(n=("residual", "size"), salida=("residual", "mean"), eficiencia=("eficiencia_pct", "mean"))
              .reset_index())
    por_celda = medias.groupby(clave, observed=True)
    completa = (por_celda["eboe"].transform("size") == eboes) & (por_celda["n"].transform("min") >= minimo)
    medias = medias[completa].copy()
    por_celda = medias.groupby(clave, observed=True)
    for columna in ("salida", "eficiencia"):        # los otros: (suma de la celda − el eBOE) / (eBOE − 1)
        otros = (por_celda[columna].transform("sum") - medias[columna]) / (eboes - 1)
        medias[f"dif_{columna}"] = medias[columna] - otros
    return medias[[*columnas, *salida]].reset_index(drop=True)


def comparacion_justa(datos: pd.DataFrame, minimo: int = N_MINIMO_JUSTA) -> pd.DataFrame:
    """Resumen por nivel de corrección (NIVELES_JUSTA) y eBOE, sobre preparar_celdas().

    Diferencia de cada eBOE = promedio de sus diferencias por celda, ponderado por sus pares en la celda.
    Columnas: nivel, eboe, celdas, pares (del eBOE en las celdas que entran), pct_pares (sobre los pares
    del eBOE en datos), dif_salida (kg/MT) y dif_eficiencia (puntos).
    """
    filas = []
    pares_eboe = datos.groupby("eboe").size()
    for nivel, columnas in NIVELES_JUSTA.items():
        celdas = celdas_justas(datos, columnas, minimo)
        for eboe, sub in celdas.groupby("eboe", sort=True):
            filas.append({"nivel": nivel, "eboe": eboe, "celdas": len(sub), "pares": int(sub["n"].sum()),
                          "pct_pares": 100 * sub["n"].sum() / pares_eboe[eboe],
                          "dif_salida": float(np.average(sub["dif_salida"], weights=sub["n"])),
                          "dif_eficiencia": float(np.average(sub["dif_eficiencia"], weights=sub["n"]))})
    return pd.DataFrame(filas, columns=["nivel", "eboe", "celdas", "pares", "pct_pares", "dif_salida",
                                        "dif_eficiencia"])


def eboe_peor(resumen: pd.DataFrame):
    """El eBOE con más salida que los otros sin corregir (el que más aceite deja); None si no hay datos."""
    sin_corregir = resumen[resumen["nivel"] == "Sin corregir"]
    return None if sin_corregir.empty else sin_corregir.loc[sin_corregir["dif_salida"].idxmax(), "eboe"]


def cascada_justa(resumen: pd.DataFrame, eboe, indicador: str) -> dict | None:
    """Pasos de la cascada de la diferencia de un eBOE: sin corregir → calibre y programa → MTPH y temporada → no explicada.

    indicador: "dif_salida" o "dif_eficiencia". total = sin corregir; calibre_programa = cambio al corregir por
    calibre y programa; mtph_temporada = cambio al sumar MTPH y temporada; no_explicada = la diferencia que
    queda (NIVEL_FRASE).
    """
    valores = resumen[resumen["eboe"] == eboe].set_index("nivel")[indicador]
    niveles = ["Sin corregir", "Calibre + programa", NIVEL_FRASE]
    if not all(n in valores.index for n in niveles):
        return None
    total, uno, dos = (float(valores[n]) for n in niveles)
    return {"eboe": eboe, "total": total, "calibre_programa": uno - total, "mtph_temporada": dos - uno,
            "no_explicada": dos}


def frase_justa(resumen: pd.DataFrame) -> str:
    """Ejemplo: "Con fruta del mismo tamaño, el mismo programa y la misma MTPH, el eBOE 2 deja 0,35 kg/MT más de
    aceite (4,1 puntos menos de eficiencia). El 88 % de su diferencia no se explica por la fruta ni por la operación."
    """
    eboe = eboe_peor(resumen)
    salida = None if eboe is None else cascada_justa(resumen, eboe, "dif_salida")
    if salida is None:
        return ("No hay celdas con al menos 5 pares de cada eBOE para comparar con fruta del mismo tamaño, el mismo "
                "programa y la misma MTPH.")
    eficiencia = cascada_justa(resumen, eboe, "dif_eficiencia")["no_explicada"]
    deja = f"{numero(abs(salida['no_explicada']), 2)} kg/MT {'más' if salida['no_explicada'] > 0 else 'menos'}"
    puntos = f"{numero(abs(eficiencia), 1)} puntos {'menos' if eficiencia < 0 else 'más'}"
    inicio = (f"Con fruta del mismo tamaño, el mismo programa y la misma MTPH, el eBOE {int(eboe)} deja {deja} de "
              f"aceite ({puntos} de eficiencia).")
    if salida["total"] <= 0 or salida["no_explicada"] <= 0:
        return inicio + " La fruta y la operación explican toda su diferencia."
    if salida["no_explicada"] >= salida["total"]:
        return (inicio + " Corregida, su diferencia es incluso mayor que sin corregir "
                f"({numero(salida['total'], 2)} kg/MT): la fruta y la operación no la explican.")
    parte = 100 * salida["no_explicada"] / salida["total"]
    return inicio + f" El {numero(parte, 0)} % de su diferencia no se explica por la fruta ni por la operación."


# ---------------------------------------------------------------- modelo multivariable

def _termino(columna: str, referencia) -> str:
    """Término de la fórmula de statsmodels (patsy) para un factor."""
    if FACTORES[columna][1] == "numero":
        return columna
    if referencia is None:
        return f"C({columna})"
    return f"C({columna}, Treatment(reference={referencia!r}))"


NOMBRES_MODELO = {True: "A · Con eBOE", False: "B · Sin eBOE (máquinas iguales)"}
NOTA_MODELO_B = ("Este modelo supone que las máquinas son iguales; la diferencia que queda en cada eBOE (residuo) "
                 "refleja factores que el modelo no ve.")


def modelo_multivariable(pares: pd.DataFrame, respuesta: str, con_eboe: bool = True, alfa: float = ALFA) -> dict:
    """Regresión lineal (OLS) de la respuesta (RESPUESTAS) con todos los FACTORES a la vez.

    con_eboe=True es el modelo A: el efecto del eBOE es su diferencia propia, la que no explican las demás
    variables. con_eboe=False es el modelo B (máquinas iguales): las mismas variables menos el eBOE; lo que
    distingue a cada eBOE queda en el residuo (ajuste).

    Entran los pares con todos los datos. Se dejan afuera los factores sin variación (por ejemplo, la
    temporada si se filtró una sola). Categorías con referencia fija (eBOE 1, Firme, turno M; temporada:
    la primera). % que explica cada factor = suma de cuadrados tipo II / suma de cuadrados total × 100:
    la parte que explica ese factor por sí solo, una vez que están los demás (no suman el R²).
    VIF de cada columna del modelo (para las categorías, el mayor de sus columnas).
    Devuelve n, r2, r2_ajustado, factores (los que entraron), excluidos, importancia (factor, nombre, gl,
    sc, pct, f, p; ordenada de mayor a menor), efectos (factor, texto, coeficiente, ic_inf, ic_sup, p),
    vif (factor, nombre, vif), sin_explicar (% de la variación que queda en el error) y ajuste (por par: eboe,
    temporada, observado, esperado y residuo = observado − esperado). Cada efecto lleva además clave (el
    término del modelo) y variable (el texto sin el valor).
    Los dos modelos usan los mismos pares (los que tienen todos los datos, eBOE incluido).
    """
    if respuesta not in RESPUESTAS:
        raise ValueError(f"Respuesta desconocida: {respuesta}. Las válidas son: {', '.join(RESPUESTAS)}.")
    datos = pares.dropna(subset=[respuesta, *FACTORES]).copy()
    eboe_original = datos["eboe"].copy()
    for columna, (_, tipo, *_resto) in FACTORES.items():
        if tipo == "categoria":
            datos[columna] = datos[columna].astype(str)
    factores, excluidos = [], []
    for columna in FACTORES:
        if columna == "eboe" and not con_eboe:
            continue
        (factores if datos[columna].nunique() > 1 else excluidos).append(columna)
    resultado = {"n": len(datos), "r2": None, "r2_ajustado": None, "factores": factores, "excluidos": excluidos,
                 "importancia": None, "efectos": None, "vif": None, "sin_explicar": None, "ajuste": None,
                 "con_eboe": con_eboe}
    if len(datos) <= len(factores) + 2 or not factores:
        return resultado

    referencias = {}
    for columna in factores:
        if FACTORES[columna][1] == "categoria":
            niveles = sorted(datos[columna].unique())
            pedida = FACTORES[columna][4]
            referencias[columna] = str(pedida) if pedida is not None and str(pedida) in niveles else niveles[0]
    formula = f"{respuesta} ~ " + " + ".join(_termino(c, referencias.get(c)) for c in factores)
    modelo = smf.ols(formula, data=datos).fit()
    anova = sm.stats.anova_lm(modelo, typ=2)
    total = modelo.centered_tss

    importancia = []
    for columna in factores:
        fila = anova.loc[_termino(columna, referencias.get(columna))]
        importancia.append({"factor": columna, "nombre": FACTORES[columna][0], "gl": fila["df"], "sc": fila["sum_sq"],
                            "pct": 100 * fila["sum_sq"] / total, "f": fila["F"], "p": fila["PR(>F)"]})
    unidad_efecto, dec = RESPUESTAS[respuesta]["efecto"], RESPUESTAS[respuesta]["decimales"]
    intervalos = modelo.conf_int(alfa)
    efectos = []
    for columna in factores:
        nombre, tipo, paso, texto_paso, _ = FACTORES[columna]
        if tipo == "numero":
            claves = [(columna, texto_paso, paso)]
        else:
            termino = _termino(columna, referencias[columna])
            ref = referencias[columna]
            claves = [(f"{termino}[T.{nivel}]", _texto_categoria(columna, nivel, ref), 1)
                      for nivel in sorted(datos[columna].unique()) if nivel != ref]
        for clave, texto, multiplicador in claves:
            coef = modelo.params[clave] * multiplicador
            efectos.append({"factor": columna, "clave": clave, "variable": texto,
                            "texto": f"{texto} → {'+' if coef >= 0 else '−'}{numero(abs(coef), dec)} {unidad_efecto}",
                            "coeficiente": coef, "ic_inf": intervalos.loc[clave, 0] * multiplicador,
                            "ic_sup": intervalos.loc[clave, 1] * multiplicador, "p": modelo.pvalues[clave]})

    matriz = modelo.model.exog
    nombres = modelo.model.exog_names
    vif_columnas = {nombre: variance_inflation_factor(matriz, k) for k, nombre in enumerate(nombres) if k > 0}
    vif = [{"factor": columna, "nombre": FACTORES[columna][0],
            "vif": max(v for n, v in vif_columnas.items() if n == columna or n.startswith(f"C({columna}"))}
           for columna in factores]
    resultado.update(r2=float(modelo.rsquared), r2_ajustado=float(modelo.rsquared_adj),
                     importancia=pd.DataFrame(importancia).sort_values("pct", ascending=False, ignore_index=True),
                     efectos=pd.DataFrame(efectos), vif=pd.DataFrame(vif),
                     sin_explicar=100 * modelo.ssr / total,
                     ajuste=pd.DataFrame({"eboe": eboe_original, "temporada": datos["temporada"],
                                          "observado": datos[respuesta], "esperado": modelo.fittedvalues,
                                          "residuo": modelo.resid}).reset_index(drop=True))
    return resultado


def _texto_categoria(columna: str, nivel: str, referencia: str) -> str:
    """"eBOE 2 en vez de eBOE 1", "Calidad Blanda en vez de Firme", "Turno N en vez de M"."""
    if columna == "eboe":
        return f"eBOE {nivel} en vez de eBOE {referencia}: diferencia propia (no explicada por las variables del modelo)"
    if columna == "temporada":
        return f"Temporada {nivel} en vez de {referencia}"
    if columna == "calidad":
        return f"Calidad {nivel} en vez de {referencia}"
    return f"Turno {nivel} en vez de {referencia}"


def frase_modelo(resultado: dict, respuesta: str) -> str:
    """Ejemplo: "El modelo A explica el 35 % de la variación de la eficiencia (R² = 0,35; N = 4.300). Lo que más
    pesa, por sí solo: MTPH (8,3 %), Programa (6,0 %) y Diferencia propia del eBOE (4,8 %)."
    """
    if resultado["r2"] is None:
        return f"No hay datos suficientes para el modelo (N = {numero(resultado['n'], 0)})."
    sujeto = "de la eficiencia" if respuesta == "eficiencia_pct" else "del aceite residual"
    top = resultado["importancia"].head(3)
    nombres = [f"{f['nombre']} ({numero(f['pct'], 1)} %)" for f in top.to_dict("records")]
    lista = nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]
    letra = "A" if resultado.get("con_eboe", True) else "B"
    return (f"El modelo {letra} explica el {numero(resultado['r2'] * 100, 0)} % de la variación {sujeto} "
            f"(R² = {numero(resultado['r2'], 2)}; N = {numero(resultado['n'], 0)}). Lo que más pesa, por sí solo: "
            f"{lista}.")


def comparar_modelos(a: dict, b: dict) -> pd.DataFrame:
    """Efecto de cada variable en el modelo A (con eBOE) y en el B (sin eBOE), lado a lado.

    Columnas: variable, coef_a, ic_inf_a, ic_sup_a, p_a, coef_b, ic_inf_b, ic_sup_b, p_b (vacías en B para el eBOE).
    """
    columnas = ["clave", "variable", "coeficiente", "ic_inf", "ic_sup", "p"]
    if a["efectos"] is None or b["efectos"] is None:
        return pd.DataFrame(columns=["variable", "coef_a", "ic_inf_a", "ic_sup_a", "p_a", "coef_b", "ic_inf_b",
                                     "ic_sup_b", "p_b"])
    tabla = a["efectos"][columnas].merge(b["efectos"][columnas].drop(columns="variable"), on="clave", how="left",
                                         suffixes=("_a", "_b"))
    return tabla.rename(columns={"coeficiente_a": "coef_a", "coeficiente_b": "coef_b"}).drop(columns="clave")


def residuos_por_eboe(modelo: dict, alfa: float = ALFA) -> pd.DataFrame:
    """Residuo promedio de cada eBOE en un modelo (el B): lo que rinde de más o de menos contra lo esperado.

    IC 1 − alfa con la t de Student sobre los residuos del eBOE. Columnas: eboe, n, observado, esperado,
    residuo, ic_inf, ic_sup.
    """
    columnas = ["eboe", "n", "observado", "esperado", "residuo", "ic_inf", "ic_sup"]
    if modelo["ajuste"] is None:
        return pd.DataFrame(columns=columnas)
    filas = []
    for eboe, sub in modelo["ajuste"].groupby("eboe", sort=True):
        n, media = len(sub), float(sub["residuo"].mean())
        margen = (stats.t.ppf(1 - alfa / 2, n - 1) * sub["residuo"].std(ddof=1) / np.sqrt(n)) if n >= 2 else np.nan
        filas.append({"eboe": eboe, "n": n, "observado": float(sub["observado"].mean()),
                      "esperado": float(sub["esperado"].mean()), "residuo": media, "ic_inf": media - margen,
                      "ic_sup": media + margen})
    return pd.DataFrame(filas, columns=columnas)


def frase_residuo_eboe(tabla: pd.DataFrame, respuesta: str) -> str:
    """El eBOE que peor rinde contra lo que espera el modelo B.

    Ejemplo: "Con su calibre, programa y MTPH, el modelo espera que el eBOE 2 rinda 81,9 %; rinde 78,4 %
    (3,5 puntos menos de lo esperado)."
    """
    if len(tabla) < 2:
        return "Hace falta más de un eBOE para comparar los residuos."
    eficiencia = respuesta == "eficiencia_pct"
    peor = tabla.loc[tabla["residuo"].idxmin() if eficiencia else tabla["residuo"].idxmax()]
    eboe, residuo = int(peor["eboe"]), peor["residuo"]
    if eficiencia:
        cuanto = f"{numero(abs(residuo), 1)} puntos {'menos' if residuo < 0 else 'más'} de lo esperado"
        return (f"Con su calibre, programa y MTPH, el modelo espera que el eBOE {eboe} rinda "
                f"{numero(peor['esperado'], 1)} %; rinde {numero(peor['observado'], 1)} % ({cuanto}).")
    cuanto = f"{numero(abs(residuo), 2)} kg/MT {'más' if residuo > 0 else 'menos'} de lo esperado"
    return (f"Con su calibre, programa y MTPH, el modelo espera que el eBOE {eboe} deje "
            f"{numero(peor['esperado'], 2)} kg/MT de aceite en la cáscara; deja {numero(peor['observado'], 2)} kg/MT "
            f"({cuanto}).")


def frase_r2_modelos(a: dict, b: dict) -> str:
    """Ejemplo: "Sacar el eBOE del modelo baja el R² de 0,39 a 0,35: la posición de la máquina explica un 4 % de
    la variación que no explican el calibre, el programa ni la MTPH."
    """
    if a["r2"] is None or b["r2"] is None:
        return "No hay datos suficientes para comparar los dos modelos."
    return (f"Sacar el eBOE del modelo baja el R² de {numero(a['r2'], 2)} a {numero(b['r2'], 2)}: la posición de la "
            f"máquina explica un {numero(100 * (a['r2'] - b['r2']), 0)} % de la variación que no explican el calibre, "
            "el programa ni la MTPH.")


# ---------------------------------------------------------------- aceite perdido

def toneladas_por_eboe(pares: pd.DataFrame, toneladas: pd.DataFrame) -> pd.DataFrame:
    """Fruta de cada turno repartida entre los eBOE que trabajaron (los que tienen pares en el turno).

    Reparto proporcional al MTPH promedio de cada eBOE en el turno; si a alguno le falta el MTPH, partes
    iguales entre los eBOE del turno. Columnas: fecha_produccion, turno, eboe, temporada, mtph, fruta_t
    (del eBOE), reparto ("MTPH" o "partes iguales"), residual (promedio del eBOE en el turno) y n (pares).
    Los turnos sin toneladas no aparecen.
    """
    por_eboe = (pares.groupby(["fecha_produccion", "turno", "eboe"])
                .agg(temporada=("temporada", "first"), mtph=("tph", "mean"), residual=("residual", "mean"),
                     n=("residual", "size")).reset_index())
    por_eboe = por_eboe.merge(toneladas, on=["fecha_produccion", "turno"])
    por_eboe = por_eboe[por_eboe["fruta_t"] > 0]
    claves = ["fecha_produccion", "turno"]
    falta = por_eboe.groupby(claves)["mtph"].transform(lambda m: m.isna().any() or (m <= 0).any())
    suma = por_eboe.groupby(claves)["mtph"].transform("sum")
    cantidad = por_eboe.groupby(claves)["eboe"].transform("size")
    proporcion = np.where(falta, 1 / cantidad, por_eboe["mtph"] / suma)
    return por_eboe.assign(fruta_t=por_eboe["fruta_t"] * proporcion,
                           reparto=np.where(falta, "partes iguales", "MTPH")).reset_index(drop=True)


def aceite_perdido(reparto: pd.DataFrame) -> pd.DataFrame:
    """Aceite que quedó en la cáscara por eBOE y temporada, contra el eBOE con menor residual.

    reparto: toneladas_por_eboe(). Residual del eBOE = Σ (t del eBOE en el turno × residual del turno) / Σ t.
    diferencia = residual − el menor residual de la temporada (kg/MT); kg_perdidos = diferencia × t.
    Columnas: temporada, eboe, toneladas, turnos, n, residual, referencia (eBOE con menor residual),
    diferencia y kg_perdidos.
    """
    datos = reparto.dropna(subset=["residual"])
    filas = []
    for (temporada, eboe), sub in datos.groupby(["temporada", "eboe"], sort=True):
        toneladas = float(sub["fruta_t"].sum())
        filas.append({"temporada": temporada, "eboe": eboe, "toneladas": toneladas, "turnos": len(sub),
                      "n": int(sub["n"].sum()), "residual": float((sub["fruta_t"] * sub["residual"]).sum() / toneladas)})
    tabla = pd.DataFrame(filas, columns=["temporada", "eboe", "toneladas", "turnos", "n", "residual"])
    if tabla.empty:
        return tabla.assign(referencia=None, diferencia=None, kg_perdidos=None)
    mejor = tabla.loc[tabla.groupby("temporada")["residual"].idxmin(), ["temporada", "eboe", "residual"]]
    tabla = tabla.merge(mejor.rename(columns={"eboe": "referencia", "residual": "residual_referencia"}), on="temporada")
    tabla["diferencia"] = tabla["residual"] - tabla["residual_referencia"]
    tabla["kg_perdidos"] = tabla["diferencia"] * tabla["toneladas"]
    return tabla.drop(columns="residual_referencia")


def residual_planta(reparto: pd.DataFrame) -> pd.DataFrame:
    """Residual de toda la planta por temporada, ponderado por toneladas (columnas temporada, toneladas, residual)."""
    datos = reparto.dropna(subset=["residual"])
    filas = [{"temporada": t, "toneladas": float(sub["fruta_t"].sum()),
              "residual": float((sub["fruta_t"] * sub["residual"]).sum() / sub["fruta_t"].sum())}
             for t, sub in datos.groupby("temporada", sort=True)]
    return pd.DataFrame(filas, columns=["temporada", "toneladas", "residual"])


def texto_usd(kg, precio_usd_kg: float | None) -> str:
    """" (USD 40.800)" con el precio del aceite en USD/kg; vacío si no hay precio."""
    return "" if precio_usd_kg is None or pd.isna(kg) else f" (USD {numero(kg * precio_usd_kg, 0)})"


def frase_aceite_perdido(tabla: pd.DataFrame, temporada, precio_usd_kg: float | None = None) -> str:
    """Ejemplo: "Si el eBOE 2 hubiera tenido el residual del eBOE 3 (1,20 kg/MT), en 2026 se habrían
    recuperado 3.400 kg de aceite (0,25 kg/MT × 13.600 MT)." Con precio (USD/kg), suma los USD.
    """
    sub = tabla[tabla["temporada"] == temporada].sort_values("kg_perdidos")
    if len(sub) < 2:
        return f"En {temporada} hay un solo eBOE con datos: no hay contra quién comparar."
    peor = sub.iloc[-1]
    referencia = sub[sub["eboe"] == peor["referencia"]].iloc[0]
    return (f"Si el eBOE {int(peor['eboe'])} hubiera tenido el residual del eBOE {int(peor['referencia'])} "
            f"({numero(referencia['residual'], 2)} kg/MT), en {temporada} se habrían recuperado "
            f"{numero(peor['kg_perdidos'], 0)} kg de aceite{texto_usd(peor['kg_perdidos'], precio_usd_kg)} "
            f"({numero(peor['diferencia'], 2)} kg/MT × {numero(peor['toneladas'], 0)} MT). Entre todos los eBOE, "
            f"{numero(sub['kg_perdidos'].sum(), 0)} kg{texto_usd(sub['kg_perdidos'].sum(), precio_usd_kg)}.")


def comparar_planta(planta: pd.DataFrame, base, nueva) -> dict | None:
    """Residual de la planta en la temporada nueva contra la base: diferencia (kg/MT) y kg = diferencia × t nueva."""
    a, b = planta[planta["temporada"] == base], planta[planta["temporada"] == nueva]
    if a.empty or b.empty:
        return None
    diferencia = float(b["residual"].iloc[0] - a["residual"].iloc[0])
    return {"base": base, "nueva": nueva, "residual_base": float(a["residual"].iloc[0]),
            "residual_nueva": float(b["residual"].iloc[0]), "diferencia": diferencia,
            "toneladas": float(b["toneladas"].iloc[0]), "kg": diferencia * float(b["toneladas"].iloc[0])}


def frase_planta(resultado: dict | None, precio_usd_kg: float | None = None) -> str:
    """Ejemplo: "En 2026 quedaron 0,26 kg/MT más en la cáscara que en 2025 (1,55 contra 1,29 kg/MT),
    equivalente a 9.800 kg de aceite sobre 37.700 MT." Con precio (USD/kg), suma los USD.
    """
    if resultado is None:
        return "Hacen falta las dos temporadas con producción para comparar la planta."
    diferencia = resultado["diferencia"]
    sentido = "más" if diferencia > 0 else "menos"
    equivale = "equivalente a" if diferencia > 0 else "es decir,"
    ganados = "" if diferencia > 0 else " recuperados"
    return (f"En {resultado['nueva']} quedaron {numero(abs(diferencia), 2)} kg/MT {sentido} en la cáscara que en "
            f"{resultado['base']} ({numero(resultado['residual_nueva'], 2)} contra "
            f"{numero(resultado['residual_base'], 2)} kg/MT), {equivale} {numero(abs(resultado['kg']), 0)} kg de "
            f"aceite{ganados}{texto_usd(abs(resultado['kg']), precio_usd_kg)} sobre {numero(resultado['toneladas'], 0)} MT.")


# ---------------------------------------------------------------- balance aceite–jugo

FRUTA_MINIMA_TURNO = 20     # MT: turnos con menos fruta no entran en el balance
# Rangos válidos por turno: fuera de ellos, error de carga o desfase de envasado (columna, mínimo, máximo, nombre)
RANGOS_BALANCE = [("conc_mt", 40, 130, "kg conc. 65 °Brix/MT"), ("aceite_mt", 1, 15, "aceite envasado/MT"),
                  ("eficiencia", 0, 100, "eficiencia eBOE")]
NOTA_BALANCE = ("Datos por turno, con ruido: el jugo depende también del °Brix del concentrado, del desfase de envasado "
                "y de la in-line. Es un indicio para una prueba controlada, no una conclusión.")


def turnos_balance(produccion: pd.DataFrame, pares: pd.DataFrame, brix_registrado: float = 65.0) -> pd.DataFrame:
    """Una fila por turno de producción con lo que hace falta para el balance aceite–jugo.

    produccion: core.produccion.tabla_produccion() (se suman los procesos del turno). pares: los de la
    planta (todos los eBOE). programa = promedio de los eBOE en marcha (los que tienen pares en el turno) del
    programa promedio de cada uno (de la lectura de operación de cada muestra); frutos = frutos en 10 kg
    promedio; eficiencia = eficiencia eBOE promedio de los pares (%); removido_mt = aceite removido por los eBOE
    (entrada − salida, kg/MT) promedio de los pares. conc_mt = kg de concentrado a 65 °Brix
    (core.precios.kg_a_65_brix) / MT de fruta; aceite_mt = aceite envasado (kg) / MT de fruta.
    Columnas: fecha_produccion, turno, temporada, fruta_t, aceite_kg, jugo_kg, conc_mt, aceite_mt, programa,
    grupo_programa, frutos, eficiencia, removido_mt, eboes, n.
    """
    claves = ["fecha_produccion", "turno"]
    turnos = (produccion.groupby(claves)
              .agg(temporada=("temporada", "first"), fruta_t=("fruta_t", lambda s: s.sum(min_count=1)),
                   aceite_kg=("aceite_envasado_kg", lambda s: s.sum(min_count=1)),
                   jugo_kg=("jugo_conc_kg", lambda s: s.sum(min_count=1)))
              .reset_index())
    por_eboe = pares.groupby([*claves, "eboe"]).agg(programa=("programa_num", "mean"), temporada=("temporada", "first"))
    muestras = (pares.groupby(claves)
                .agg(frutos=("frutos_10kg", "mean"), eficiencia=("eficiencia_pct", "mean"),
                     removido_mt=("removido", "mean"), n=("eficiencia_pct", "size"),
                     eboes=("eboe", "nunique"), temporada_pares=("temporada", "first"))
                .join(por_eboe.groupby(claves)["programa"].mean()).reset_index())
    turnos = turnos.merge(muestras, on=claves, how="left")
    turnos["temporada"] = turnos["temporada"].fillna(turnos.pop("temporada_pares"))
    fruta = turnos["fruta_t"].where(turnos["fruta_t"] > 0)
    turnos["conc_mt"] = kg_a_65_brix(turnos["jugo_kg"], brix_registrado) / fruta
    turnos["aceite_mt"] = turnos["aceite_kg"] / fruta
    turnos["grupo_programa"] = turnos["programa"].round().map(grupo_programa)
    return turnos[["fecha_produccion", "turno", "temporada", "fruta_t", "aceite_kg", "jugo_kg", "conc_mt", "aceite_mt",
                   "programa", "grupo_programa", "frutos", "eficiencia", "removido_mt", "eboes", "n"]]


def separar_turnos_balance(turnos: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(turnos válidos, turnos excluidos con su motivo).

    Se excluyen los turnos con menos de FRUTA_MINIMA_TURNO MT de fruta, sin muestras de los eBOE (sin
    programa) o con algún valor fuera de RANGOS_BALANCE. Un dato vacío (por ejemplo, sin jugo cargado) no
    excluye el turno: solo no entra en ese análisis.
    """
    motivos = pd.Series("", index=turnos.index)
    motivos[~(turnos["fruta_t"] >= FRUTA_MINIMA_TURNO)] += f"fruta < {FRUTA_MINIMA_TURNO} MT; "
    motivos[turnos["programa"].isna()] += "sin muestras de los eBOE; "
    for columna, minimo, maximo, nombre in RANGOS_BALANCE:
        fuera = turnos[columna].notna() & ~turnos[columna].between(minimo, maximo)
        motivos[fuera] += f"{nombre} fuera de {numero(minimo, 0)}–{numero(maximo, 0)}; "
    excluido = motivos != ""
    return (turnos[~excluido].reset_index(drop=True),
            turnos[excluido].assign(motivo=motivos[excluido].str.rstrip("; ")).reset_index(drop=True))


def regresion_balance(turnos: pd.DataFrame, variable: str, alfa: float = ALFA) -> dict:
    """Regresión por turno de `variable` (conc_mt, aceite_mt o removido_mt) con programa, calibre y temporada.

    variable = b0 + b·programa + c·frutos + temporada (categoría; sale si hay una sola). efecto = b, lo que
    cambia la variable por +1 nivel de programa con el calibre y la temporada fijos, con IC 1 − alfa y p.
    Devuelve n, r2, efecto, ic_inf, ic_sup y p (vacíos si hay menos de 10 turnos).
    """
    datos = turnos.dropna(subset=[variable, "programa", "frutos", "temporada"]).copy()
    resultado = {"variable": variable, "n": len(datos), "r2": None, "efecto": None, "ic_inf": None, "ic_sup": None,
                 "p": None}
    if len(datos) < 10 or datos["programa"].nunique() < 2:
        return resultado
    datos["temporada"] = datos["temporada"].astype(int).astype(str)
    formula = f"{variable} ~ programa + frutos" + (" + C(temporada)" if datos["temporada"].nunique() > 1 else "")
    modelo = smf.ols(formula, data=datos).fit()
    ic = modelo.conf_int(alfa).loc["programa"]
    resultado.update(r2=float(modelo.rsquared), efecto=float(modelo.params["programa"]), ic_inf=float(ic[0]),
                     ic_sup=float(ic[1]), p=float(modelo.pvalues["programa"]))
    return resultado


def valor_programa(efecto_aceite: float, efecto_conc: float, precio_aceite: float, precio_jugo: float) -> dict:
    """Valor por MT de fruta de subir un nivel de programa (bajar uno es lo mismo con el signo cambiado).

    efecto_aceite y efecto_conc: kg/MT por +1 nivel (regresion_balance()); precios en USD/kg.
    usd_aceite = efecto_aceite × precio del aceite; usd_jugo = efecto_conc × precio del concentrado;
    neto = usd_aceite + usd_jugo (USD/MT). Relación de equilibrio (aceite/concentrado) = −efecto_conc /
    efecto_aceite: si el aceite vale más que esa cantidad de veces el concentrado, conviene subir (solo
    cuando subir da más aceite y menos jugo; si no, vacía). relacion_actual = precio aceite / precio jugo.
    """
    usd_aceite, usd_jugo = efecto_aceite * precio_aceite, efecto_conc * precio_jugo
    equilibrio = -efecto_conc / efecto_aceite if efecto_aceite > 0 and efecto_conc < 0 else None
    return {"usd_aceite": usd_aceite, "usd_jugo": usd_jugo, "neto": usd_aceite + usd_jugo,
            "equilibrio": equilibrio, "relacion_actual": precio_aceite / precio_jugo}


def frase_balance(valor: dict) -> str:
    """Ejemplo: "Con estos precios, subir un nivel de programa suma USD 0,95 por MT en aceite y resta USD 1,40 en
    jugo: no conviene (USD −0,45 por MT). Conviene subir si el aceite vale más de 6,2 veces el concentrado (hoy,
    4,8 veces)."
    """
    def parte(usd):
        return f"{'suma' if usd >= 0 else 'resta'} USD {numero(abs(usd), 2)}"

    neto = valor["neto"]
    texto = (f"Con estos precios, subir un nivel de programa {parte(valor['usd_aceite'])} por MT en aceite y "
             f"{parte(valor['usd_jugo'])} en jugo: {'conviene' if neto > 0 else 'no conviene'} "
             f"(USD {'+' if neto >= 0 else '−'}{numero(abs(neto), 2)} por MT de fruta).")
    if valor["equilibrio"] is not None:
        texto += (f" Conviene subir si el aceite vale más de {numero(valor['equilibrio'], 1)} veces el concentrado "
                  f"(hoy, {numero(valor['relacion_actual'], 1)} veces).")
    return texto


def frase_efecto_programa(conc: dict, aceite: dict, alfa: float = ALFA) -> str:
    """El efecto de +1 nivel de programa por turno, sin precios.

    Ejemplo: "Por turno, cada nivel más de programa se asocia con 0,59 kg menos de concentrado 65 °Brix por MT
    (IC 95 %: 0,07 a 1,12; p = 0,028) y con 0,02 kg/MT menos de aceite envasado (p = 0,306: sin efecto claro)."
    """
    if conc["efecto"] is None or aceite["efecto"] is None:
        return "No hay turnos suficientes para estimar el efecto del programa sobre el jugo y el aceite."

    def parte(r, que, decimales):
        sentido = "más" if r["efecto"] > 0 else "menos"
        nota = "" if r["p"] < alfa else ": sin efecto claro"
        return f"{numero(abs(r['efecto']), decimales)} {que.format(sentido)} ({texto_p(r['p'])}{nota})"

    ic = sorted((abs(conc["ic_inf"]), abs(conc["ic_sup"])))
    texto_conc = parte(conc, "kg {} de concentrado 65 °Brix por MT", 2)
    if conc["ic_inf"] * conc["ic_sup"] > 0:          # el IC no cruza el 0
        texto_conc = texto_conc.replace(" (p", f" (IC 95 %: {numero(ic[0], 2)} a {numero(ic[1], 2)}; p", 1)
    return (f"Por turno, cada nivel más de programa se asocia con {texto_conc} y con "
            f"{parte(aceite, 'kg/MT {} de aceite envasado', 2)}.")


# ---------------------------------------------------------------- resumen

def resumen_factores(quintiles: pd.DataFrame, eboe_calibre: pd.DataFrame, descomposicion: dict | None,
                     perdido: pd.DataFrame, planta: dict | None, temporada, precio_aceite: float | None = None,
                     esponja: tuple | None = None, justa: pd.DataFrame | None = None, modelos: tuple | None = None,
                     balance: dict | None = None) -> list[str]:
    """Las frases principales de la pestaña, para el informe (las de los bloques que tienen datos).

    quintiles: por_quintil(); eboe_calibre: frutos_por_eboe(); descomposicion: descomponer() de la
    eficiencia; perdido: aceite_perdido(); planta: comparar_planta(); temporada: la que se describe (la más
    nueva); precio_aceite: USD/kg (opcional); esponja: (pendientes_esponja(), esponja_por_eboe());
    justa: comparacion_justa(); modelos: (modelo A, modelo B) de la eficiencia; balance: {"regresiones":
    {variable: regresion_balance()}, "valor": valor_programa() o None si no hay precios}.
    """
    frases = [frase_calibre(quintiles, temporada), frase_descomposicion(descomposicion, "eficiencia")]
    if esponja is not None:
        frases.append(frase_esponja(*esponja, temporada))
    if justa is not None and not justa.empty:
        frases.append(frase_justa(justa))
    if modelos is not None and modelos[0]["r2"] is not None and modelos[1]["r2"] is not None:
        frases.append(frase_r2_modelos(*modelos) + " " + frase_residuo_eboe(residuos_por_eboe(modelos[1]),
                                                                              "eficiencia_pct"))
    perdida = frase_aceite_perdido(perdido, temporada, precio_aceite)
    sub_perdido = perdido[perdido["temporada"] == temporada]
    sub_calibre = eboe_calibre[eboe_calibre["temporada"] == temporada].sort_values("frutos")
    if len(sub_perdido) >= 2 and len(sub_calibre) >= 2:
        peor = int(sub_perdido.sort_values("kg_perdidos").iloc[-1]["eboe"])
        chica, grande = sub_calibre.iloc[-1], sub_calibre.iloc[0]
        if int(chica["eboe"]) == peor:
            perdida += (f" Ese eBOE es también el que recibe la fruta más chica ({numero(chica['frutos'], 0)} frutos "
                        f"en 10 kg contra {numero(grande['frutos'], 0)} del eBOE {int(grande['eboe'])}).")
    frases.append(perdida)
    frases.append(frase_planta(planta, precio_aceite))
    if balance:
        frases.append(frase_balance(balance["valor"]) if balance.get("valor") else
                      frase_efecto_programa(balance["regresiones"]["conc_mt"], balance["regresiones"]["aceite_mt"]))
    return frases
