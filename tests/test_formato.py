"""Pruebas de core/formato.py."""

import numpy as np

from core.formato import numero, texto_p


def test_numero_con_coma_decimal_y_punto_de_miles():
    assert numero(1234.5) == "1.234,50"
    assert numero(0.2, 1) == "0,2"
    assert numero(2422, 0) == "2.422"
    assert numero(-3.14159, 3) == "-3,142"
    assert numero(None) == "—" and numero(float("nan")) == "—" and numero(np.float64("nan")) == "—"


def test_texto_p():
    assert texto_p(0.0004) == "p < 0,001"
    assert texto_p(0.04213) == "p = 0,042"
    assert texto_p(0.5) == "p = 0,500"


def test_con_signo():
    from core.formato import con_signo
    assert (con_signo(1.24), con_signo(-0.4), con_signo(-0.021), con_signo(None)) == ("+1,2", "-0,4", "0,0", "—")
    assert con_signo(1234.5, 0) == "+1.234"
