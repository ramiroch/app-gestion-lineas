"""Punto de entrada de la app (streamlit run app/app.py): el menú con st.navigation y el estilo de marca.

Las páginas están en navegacion.MENU, agrupadas en secciones (Planta, Análisis, Carga diaria, Gestión), con título e
ícono; el Inicio está en inicio.py. Acá solo se arma el menú, el logo, la casilla del modo presentación (app/presentacion.py: sin la carga diaria) y el
pie de Brown: cada página hace lo suyo.
"""

import streamlit as st

from estilo import aplicar, pie
from navegacion import paginas
from presentacion import casilla_presentacion

st.set_page_config(page_title="Gestión de líneas", layout="wide", initial_sidebar_state="expanded")
aplicar()
pagina = st.navigation(paginas(casilla_presentacion()))
pagina.run()
pie()
