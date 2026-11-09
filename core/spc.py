"""Cartas de control (control estadístico de procesos): constantes, límites, reglas de Nelson y fases.

Fuente: D. C. Montgomery, "Introduction to Statistical Quality Control", cap. 6 (cartas X̄-R, X̄-S e
I-MR) y Minitab ("Methods and formulas" de Xbar-R, Xbar-S, I-MR y de los tests de causas especiales).

- Fase I: los límites se calculan con un período base (`base`). Fase II: los puntos nuevos se evalúan
  contra esos límites congelados. Sin `base`, todos los puntos son fase I.
- Cada carta devuelve un DataFrame con un renglón por punto: valor (lo que se grafica arriba: el
  dato, la media del subgrupo), lc, lcs, lci, sigma (desvío del valor graficado, para las reglas de
  Nelson), disp (rango, desvío o rango móvil, lo que se grafica abajo), disp_lc, disp_lcs, disp_lci y
  fase ("I" o "II").
- Los puntos fuera de control se marcan, nunca se borran: los límites de fase I los incluyen.
"""

from functools import cache

import numpy as np
import pandas as pd
from scipy import integrate, special, stats

N_MIN, N_MAX = 2, 25
REGLAS = tuple(range(1, 9))
# Descripción de cada regla de Nelson (los números K son los de Minitab por defecto).
DESCRIPCION_REGLAS = {
    1: "1 punto a más de 3 σ de la línea central",
    2: "9 puntos seguidos del mismo lado de la línea central",
    3: "6 puntos seguidos subiendo o bajando",
    4: "14 puntos seguidos alternando arriba y abajo",
    5: "2 de 3 puntos a más de 2 σ (del mismo lado)",
    6: "4 de 5 puntos a más de 1 σ (del mismo lado)",
    7: "15 puntos seguidos a menos de 1 σ (de cualquier lado)",
    8: "8 puntos seguidos a más de 1 σ (de cualquier lado)",
}
COLUMNAS_CARTA = ["valor", "lc", "lcs", "lci", "sigma", "disp", "disp_lc", "disp_lcs", "disp_lci", "fase"]


# ---------------------------------------------------------------- constantes

def _validar_n(n: int) -> int:
    if not N_MIN <= int(n) <= N_MAX:
        raise ValueError(f"El tamaño de subgrupo tiene que estar entre {N_MIN} y {N_MAX} (llegó {n}).")
    return int(n)


@cache
def _rango_relativo(n: int) -> tuple[float, float]:
    """Media y desvío del rango relativo W = R/σ de n datos normales: (d2, d3).

    P(W ≤ w) = n ∫ φ(x)·[Φ(x + w) − Φ(x)]ⁿ⁻¹ dx; d2 = ∫ P(W > w) dw y E[W²] = ∫ 2w·P(W > w) dw.
    Se integra con la regla de Simpson en una grilla (x de −8 a 8, w de 0 a 12): coincide con la
    integración adaptativa al sexto decimal y tarda milisegundos.
    """
    n = _validar_n(n)
    x, w = np.linspace(-8, 8, 641), np.linspace(0, 12, 481)
    diferencia = stats.norm.cdf(x[None, :] + w[:, None]) - stats.norm.cdf(x)[None, :]
    mayor = 1 - n * integrate.simpson(stats.norm.pdf(x)[None, :] * diferencia ** (n - 1), x=x, axis=1)
    media, segundo_momento = integrate.simpson(mayor, x=w), integrate.simpson(2 * w * mayor, x=w)
    return float(media), float(np.sqrt(segundo_momento - media ** 2))


def d2(n: int) -> float:
    """Media del rango relativo: E[R] = d2·σ (n de 2 a 25)."""
    return _rango_relativo(n)[0]


def d3(n: int) -> float:
    """Desvío del rango relativo: desvío de R = d3·σ (n de 2 a 25)."""
    return _rango_relativo(n)[1]


def c4(n: int) -> float:
    """Sesgo del desvío estándar muestral, E[s] = c4·σ: c4 = √(2/(n−1)) · Γ(n/2) / Γ((n−1)/2)."""
    if int(n) < 2:
        raise ValueError(f"c4 necesita n ≥ 2 (llegó {n}).")
    n = int(n)
    return float(np.sqrt(2 / (n - 1)) * np.exp(special.gammaln(n / 2) - special.gammaln((n - 1) / 2)))


def constantes(n: int) -> dict:
    """Constantes de las cartas para subgrupos de tamaño n (2 a 25), sin redondear (como Minitab).

    A2 = 3/(d2·√n), D3 = máx(0, 1 − 3·d3/d2), D4 = 1 + 3·d3/d2 (carta X̄-R);
    A3 = 3/(c4·√n), B3 = máx(0, 1 − 3·√(1 − c4²)/c4), B4 = 1 + 3·√(1 − c4²)/c4 (carta X̄-S).
    """
    n = _validar_n(n)
    d2_, d3_, c4_ = d2(n), d3(n), c4(n)
    ancho_s = 3 * np.sqrt(1 - c4_ ** 2) / c4_
    return {"n": n, "A2": 3 / (d2_ * np.sqrt(n)), "A3": 3 / (c4_ * np.sqrt(n)),
            "D3": max(0.0, 1 - 3 * d3_ / d2_), "D4": 1 + 3 * d3_ / d2_,
            "B3": max(0.0, 1 - ancho_s), "B4": 1 + ancho_s, "d2": d2_, "d3": d3_, "c4": c4_}


def tabla_constantes() -> pd.DataFrame:
    """Tabla de constantes para n de 2 a 25 (una fila por n)."""
    return pd.DataFrame([constantes(n) for n in range(N_MIN, N_MAX + 1)])


# ---------------------------------------------------------------- límites

def _base(base, largo: int) -> np.ndarray:
    """Máscara de la fase I: todos los puntos si base es None."""
    if base is None:
        return np.ones(largo, dtype=bool)
    base = np.asarray(base, dtype=bool)
    if len(base) != largo:
        raise ValueError("La máscara de la fase I tiene que tener un valor por punto.")
    if not base.any():
        raise ValueError("El período base (fase I) no tiene puntos: no se pueden calcular los límites.")
    return base


def _fases(base: np.ndarray) -> np.ndarray:
    return np.where(base, "I", "II")


def limites_imr(valores) -> dict:
    """Límites de la carta I-MR (valores individuales y rango móvil de 2).

    MR̄ = media de |xᵢ − xᵢ₋₁|; σ = MR̄ / d2(2); I: LC = x̄, LCS/LCI = x̄ ± 3σ;
    MR: LC = MR̄, LCS = D4(2)·MR̄, LCI = 0. Los valores vacíos se ignoran.
    """
    x = pd.Series(valores, dtype=float).dropna().to_numpy()
    if len(x) < 2:
        raise ValueError("La carta I-MR necesita al menos 2 valores en el período base.")
    mr_barra = float(np.mean(np.abs(np.diff(x))))
    media, sigma = float(np.mean(x)), mr_barra / d2(2)
    return {"lc": media, "sigma": sigma, "lcs": media + 3 * sigma, "lci": media - 3 * sigma,
            "mr_lc": mr_barra, "mr_lcs": constantes(2)["D4"] * mr_barra, "mr_lci": 0.0}


def limites_xbar_r(medias, rangos, n: int) -> dict:
    """Límites de la carta X̄-R con subgrupos de tamaño constante n.

    x̿ = media de las medias; R̄ = media de los rangos; σ = R̄/d2;
    X̄: x̿ ± A2·R̄; R: LC = R̄, LCS = D4·R̄, LCI = D3·R̄.
    """
    k = constantes(n)
    x_dos_barras, r_barra = float(np.nanmean(medias)), float(np.nanmean(rangos))
    return {"lc": x_dos_barras, "sigma": r_barra / k["d2"], "lcs": x_dos_barras + k["A2"] * r_barra,
            "lci": x_dos_barras - k["A2"] * r_barra, "r_lc": r_barra, "r_lcs": k["D4"] * r_barra,
            "r_lci": k["D3"] * r_barra}


def sigma_s_barra(desvios, tamanos) -> float:
    """σ de la carta X̄-S con el método "S barra" de Minitab (sin sesgo, vale con tamaños distintos):
    σ = Σ hᵢ·sᵢ/c4(nᵢ) / Σ hᵢ, con hᵢ = c4(nᵢ)² / (1 − c4(nᵢ)²). Los subgrupos de 1 dato no tienen
    desvío y no entran. Con tamaños iguales da s̄/c4.
    """
    s, n = np.asarray(desvios, dtype=float), np.asarray(tamanos, dtype=int)
    usar = (n >= 2) & ~np.isnan(s)
    if not usar.any():
        raise ValueError("No hay subgrupos con 2 o más datos en el período base: no se puede estimar σ.")
    c = np.array([c4(m) for m in n[usar]])
    h = c ** 2 / (1 - c ** 2)
    return float(np.sum(h * s[usar] / c) / np.sum(h))


def limites_xbar_s(medias, desvios, tamanos) -> dict:
    """Parámetros de la carta X̄-S (los límites dependen del tamaño de cada subgrupo, como en Minitab).

    x̿ = Σ nᵢ·x̄ᵢ / Σ nᵢ (media de todos los datos); σ = sigma_s_barra(). Para un subgrupo de tamaño n:
    X̄: x̿ ± 3σ/√n; S: LC = c4·σ, LCS = B6·σ = (c4 + 3√(1 − c4²))·σ, LCI = B5·σ = máx(0, c4 − 3√(1 − c4²))·σ.
    """
    medias, n = np.asarray(medias, dtype=float), np.asarray(tamanos, dtype=float)
    usar = ~np.isnan(medias) & (n > 0)
    return {"lc": float(np.sum(n[usar] * medias[usar]) / np.sum(n[usar])), "sigma": sigma_s_barra(desvios, tamanos)}


def limites_entre_dentro(medias, desvios, tamanos) -> dict:
    """Carta I-MR-R/S "entre/dentro" de Minitab: para subgrupos (turnos) cuando la variación entre subgrupos es
    mayor que la de adentro (fruta, programa, día).

    Carta I de los promedios: LC = x̄̄ (media de los promedios); σ_entre = MR̄ / d2(2), con MR = |x̄ᵢ − x̄ᵢ₋₁|
    entre promedios seguidos; LCS/LCI = x̄̄ ± 3σ_entre (iguales para todo n). Carta MR de los promedios: LC = MR̄,
    LCS = D4·MR̄. Carta S (variación dentro del subgrupo): σ_dentro = sigma_s_barra() (subgrupos de 2+ datos) y,
    para un subgrupo de tamaño n, LCS = (c4 + 3√(1 − c4²))·σ_dentro (ver limites_xbar_s_para).
    Devuelve lc, sigma (σ_entre), lcs, lci, mr_lc, mr_lcs y sigma_dentro (None si ningún subgrupo tiene 2 datos).
    """
    limites = limites_imr(medias)
    n = np.asarray(tamanos, dtype=int)
    sigma_dentro = sigma_s_barra(desvios, tamanos) if (n >= 2).any() else None
    return {k: limites[k] for k in ("lc", "sigma", "lcs", "lci", "mr_lc", "mr_lcs")} | {"sigma_dentro": sigma_dentro}


def limites_xbar_s_para(lc: float, sigma: float, n: int) -> dict:
    """Límites X̄-S de un subgrupo de tamaño n, con x̿ = lc y σ del proceso congelados en la fase I.

    X̄: LCS/LCI = x̿ ± 3σ/√n (sigma_media = σ/√n). S (solo con n ≥ 2): LC = c4·σ, LCS = (c4 + 3√(1 − c4²))·σ,
    LCI = máx(0, c4 − 3√(1 − c4²))·σ; con n = 1 los de S quedan None (un dato no tiene desvío).
    """
    if n < 1:
        raise ValueError(f"El subgrupo tiene que tener al menos 1 dato (tiene {n}).")
    sigma_media = sigma / np.sqrt(n)
    resultado = {"lc": lc, "lcs": lc + 3 * sigma_media, "lci": lc - 3 * sigma_media, "sigma_media": sigma_media,
                 "s_lc": None, "s_lcs": None, "s_lci": None}
    if n >= 2:
        c, ancho = c4(n), 3 * np.sqrt(1 - c4(n) ** 2)
        resultado.update(s_lc=c * sigma, s_lcs=(c + ancho) * sigma, s_lci=max(0.0, c - ancho) * sigma)
    return resultado


# ---------------------------------------------------------------- cartas con fase I y fase II

def carta_imr(valores, base=None) -> pd.DataFrame:
    """Carta I-MR: límites con los valores de la fase I (`base`), aplicados a todos los puntos.

    El rango móvil se calcula entre puntos seguidos de la misma fase (el primero de cada fase no
    tiene), así el salto entre el período base y el nuevo no cuenta.
    """
    x = pd.Series(valores, dtype=float).to_numpy()
    base = _base(base, len(x))
    lim = limites_imr(x[base])
    mr = np.full(len(x), np.nan)
    for i in range(1, len(x)):
        if base[i] == base[i - 1]:
            mr[i] = abs(x[i] - x[i - 1])
    return pd.DataFrame({
        "valor": x, "lc": lim["lc"], "lcs": lim["lcs"], "lci": lim["lci"], "sigma": lim["sigma"],
        "disp": mr, "disp_lc": lim["mr_lc"], "disp_lcs": lim["mr_lcs"], "disp_lci": lim["mr_lci"],
        "fase": _fases(base)}, columns=COLUMNAS_CARTA)


def carta_xbar_r(medias, rangos, n: int, base=None) -> pd.DataFrame:
    """Carta X̄-R (subgrupos de tamaño constante n): límites con los subgrupos de la fase I."""
    medias, rangos = np.asarray(medias, dtype=float), np.asarray(rangos, dtype=float)
    base = _base(base, len(medias))
    lim = limites_xbar_r(medias[base], rangos[base], n)
    return pd.DataFrame({
        "valor": medias, "lc": lim["lc"], "lcs": lim["lcs"], "lci": lim["lci"], "sigma": lim["sigma"] / np.sqrt(n),
        "disp": rangos, "disp_lc": lim["r_lc"], "disp_lcs": lim["r_lcs"], "disp_lci": lim["r_lci"],
        "fase": _fases(base)}, columns=COLUMNAS_CARTA)


def carta_xbar_s(medias, desvios, tamanos, base=None) -> pd.DataFrame:
    """Carta X̄-S con límites por subgrupo (sirve con tamaños distintos): x̿ y σ salen de la fase I y
    cada subgrupo (de fase I o II) lleva los límites de su tamaño (ver limites_xbar_s()).
    Un subgrupo de 1 dato tiene media pero no desvío: aparece arriba y no abajo.
    """
    medias, desvios = np.asarray(medias, dtype=float), np.asarray(desvios, dtype=float)
    n = np.asarray(tamanos, dtype=int)
    base = _base(base, len(medias))
    lim = limites_xbar_s(medias[base], desvios[base], n[base])
    sigma_media = lim["sigma"] / np.sqrt(n)
    c = np.array([c4(m) if m >= 2 else np.nan for m in n])
    ancho = 3 * np.sqrt(1 - c ** 2)
    return pd.DataFrame({
        "valor": medias, "lc": lim["lc"], "lcs": lim["lc"] + 3 * sigma_media, "lci": lim["lc"] - 3 * sigma_media,
        "sigma": sigma_media, "disp": np.where(n >= 2, desvios, np.nan), "disp_lc": c * lim["sigma"],
        "disp_lcs": (c + ancho) * lim["sigma"], "disp_lci": np.maximum(0, c - ancho) * lim["sigma"],
        "fase": _fases(base)}, columns=COLUMNAS_CARTA)


# ---------------------------------------------------------------- reglas de Nelson

def nelson(valores, lc, sigma, reglas=REGLAS) -> list[list[int]]:
    """Reglas de Nelson (1 a 8, ver DESCRIPCION_REGLAS) que viola cada punto.

    lc y sigma pueden ser un número o uno por punto (sigma es el desvío del valor graficado: σ/√n en
    una carta X̄). Como Minitab, se marca el punto que completa el patrón; en las reglas 5 y 6 ese
    punto tiene que ser uno de los que están lejos (al comienzo de la serie la ventana puede estar
    incompleta: los 2 primeros puntos lejanos ya cumplen "2 de 3"). Las reglas 3 y 4 miran los valores; las demás,
    las zonas (z = (valor − lc)/sigma). Los valores vacíos no tienen reglas y no cortan las rachas.
    Devuelve una lista por punto con los números de regla violados (vacía si ninguno).
    """
    x = np.asarray(valores, dtype=float)
    lc = np.broadcast_to(np.asarray(lc, dtype=float), x.shape)
    sigma = np.broadcast_to(np.asarray(sigma, dtype=float), x.shape)
    if np.any(sigma[~np.isnan(x)] <= 0):
        raise ValueError("El desvío (sigma) tiene que ser mayor que cero.")
    reglas = set(reglas)
    if not reglas <= set(REGLAS):
        raise ValueError(f"Las reglas de Nelson van de 1 a 8 (llegó {sorted(reglas - set(REGLAS))}).")

    validos = np.flatnonzero(~np.isnan(x))
    v, z = x[validos], (x[validos] - lc[validos]) / sigma[validos]
    lado = np.sign(z)
    dif = np.sign(np.diff(v))                    # dif[i - 1]: de v[i - 1] a v[i]
    resultado = [[] for _ in x]
    for i in range(len(v)):
        violadas = []
        if 1 in reglas and abs(z[i]) > 3:
            violadas.append(1)
        if 2 in reglas and i >= 8 and abs(lado[i - 8:i + 1].sum()) == 9:
            violadas.append(2)
        if 3 in reglas and i >= 5 and abs(dif[i - 5:i].sum()) == 5:
            violadas.append(3)
        if 4 in reglas and i >= 13 and np.all(dif[i - 13:i - 1] * dif[i - 12:i] < 0):
            violadas.append(4)
        if 5 in reglas and abs(z[i]) > 2 and np.sum(z[max(0, i - 2):i + 1] * lado[i] > 2) >= 2:
            violadas.append(5)
        if 6 in reglas and abs(z[i]) > 1 and np.sum(z[max(0, i - 4):i + 1] * lado[i] > 1) >= 4:
            violadas.append(6)
        if 7 in reglas and i >= 14 and np.all(np.abs(z[i - 14:i + 1]) < 1):
            violadas.append(7)
        if 8 in reglas and i >= 7 and np.all(np.abs(z[i - 7:i + 1]) > 1):
            violadas.append(8)
        resultado[validos[i]] = violadas
    return resultado


def evaluar_carta(carta: pd.DataFrame, reglas=REGLAS) -> pd.DataFrame:
    """Agrega a una carta las reglas violadas y los puntos fuera de control.

    reglas: lista de reglas de Nelson del panel de arriba, evaluadas por separado en cada fase (una
    racha no cruza de la fase I a la II). fuera_disp: el panel de abajo (R, S o MR) fuera de sus
    límites (solo la regla 1, como Minitab por defecto). alguna: el punto viola alguna de las dos.
    """
    reglas_por_punto = [[] for _ in range(len(carta))]
    for _, fase in carta.groupby("fase", sort=False):
        posiciones = carta.index.get_indexer(fase.index)
        for posicion, violadas in zip(posiciones, nelson(fase["valor"], fase["lc"], fase["sigma"], reglas)):
            reglas_por_punto[posicion] = violadas
    disp = carta["disp"]
    fuera_disp = disp.notna() & ((disp > carta["disp_lcs"]) | (disp < carta["disp_lci"]))
    return carta.assign(reglas=reglas_por_punto, fuera_disp=fuera_disp.to_numpy(),
                        alguna=np.array([bool(r) for r in reglas_por_punto]) | fuera_disp.to_numpy())


def resumen_reglas(carta: pd.DataFrame, reglas=REGLAS) -> pd.DataFrame:
    """Cantidad y % de puntos fuera de control por regla y por fase (carta ya evaluada).

    Una fila por regla elegida, una para el panel de abajo ("dispersion") y una para "alguna" (puntos
    con al menos una). Columnas: regla, n_I, pct_I, n_II, pct_II (% sobre los puntos con valor de
    esa fase; las de fase II quedan vacías si no hay fase II) y puntos_I, puntos_II.
    """
    filas = []
    fases = {f: carta[(carta["fase"] == f) & carta["valor"].notna()] for f in ("I", "II")}

    def fila(regla, cuenta):
        datos = {"regla": regla}
        for f, sub in fases.items():
            total = len(sub)
            n = int(cuenta(sub)) if total else None
            datos[f"n_{f}"], datos[f"pct_{f}"] = n, (100 * n / total if total else None)
            datos[f"puntos_{f}"] = total
        filas.append(datos)

    for regla in sorted(reglas):
        fila(regla, lambda sub, r=regla: sub["reglas"].map(lambda violadas: r in violadas).sum())
    fila("dispersion", lambda sub: sub["fuera_disp"].sum())
    fila("alguna", lambda sub: sub["alguna"].sum())
    return pd.DataFrame(filas)
