"""Cartel de modo prueba: se llama al principio de cada página."""

import streamlit as st

from core.db import es_base_de_prueba, ruta_base


def cartel_modo_prueba() -> None:
    """Si la app usa una base distinta de la real (variable APP_DB), lo avisa arriba de la página y en la barra."""
    if es_base_de_prueba():
        texto = f"MODO PRUEBA – base: {ruta_base().name}"
        st.error(texto, icon="🧪")
        st.sidebar.error(texto, icon="🧪")
