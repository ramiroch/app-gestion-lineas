"""Modo presentación: para mostrar la app en una reunión o una pantalla.

Oculta la carga diaria (sección del menú), los botones de edición y de seguimiento, los análisis por persona y los
indicadores internos (por ejemplo, la coherencia del muestreo del licor). Se activa con la casilla de la barra lateral
o con ?presentacion=1 en la dirección. Es solo de visualización, NO es una medida de seguridad: cualquiera puede
desactivarlo.
"""

import streamlit as st

CLAVE = "modo_presentacion"
AYUDA = ("Oculta la carga diaria, la edición, los análisis por persona y los indicadores internos. Es solo de "
         "visualización, no una medida de seguridad: cualquiera puede desactivarlo. También se activa con "
         "?presentacion=1 en la dirección.")


def casilla_presentacion() -> bool:
    """Dibuja la casilla en la barra lateral (desde app.py, antes del menú) y devuelve si está activa."""
    if CLAVE not in st.session_state:
        st.session_state[CLAVE] = st.query_params.get("presentacion") == "1"
    return st.sidebar.toggle("Modo presentación", key=CLAVE, help=AYUDA)


def en_presentacion() -> bool:
    """True si el modo presentación está activo (para que cada página oculte lo que corresponde)."""
    return bool(st.session_state.get(CLAVE, False))
