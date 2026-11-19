"""Números y valores p escritos en castellano (coma decimal, punto de miles)."""

import math


def numero(valor, decimales: int = 2) -> str:
    """Número con coma decimal y punto de miles: 1234.5 → "1.234,50"; vacío → "—"."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "—"
    return f"{valor:,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def texto_p(p: float) -> str:
    """Valor p para mostrar: "p < 0,001" si es muy chico; si no, con 3 decimales ("p = 0,042")."""
    if p is None or math.isnan(p):
        return "p = —"
    return "p < 0,001" if p < 0.001 else f"p = {numero(p, 3)}"


def con_signo(valor, decimales: int = 1) -> str:
    """Diferencia con signo: 1.24 → "+1,2"; -0.4 → "-0,4"; lo que redondea a 0 → "0,0" (sin signo); vacío → "—"."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "—"
    redondeado = round(valor, decimales)
    if redondeado == 0:
        return numero(0.0, decimales)
    return ("+" if redondeado > 0 else "") + numero(redondeado, decimales)
