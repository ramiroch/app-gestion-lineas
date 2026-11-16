"""Estilo de la app (guía de marca Gulftech): colores, CSS para lo que el tema no cubre, logo y pie de Brown en la
barra lateral y el encabezado "cobrand" del Inicio (logo de la planta | logo de Brown).

Los colores del tema (naranja, beige, verde de la barra lateral) y la fuente Barlow están en .streamlit/config.toml.
Los semáforos y los colores de las cartas de control no se tocan: están en graficos.py.
"""

import base64
from html import escape
from pathlib import Path

import streamlit as st

NARANJA = "#FF8710"          # naranja Gulftech (primaryColor)
VERDE_GULFTECH = "#1F4034"   # barra lateral
BEIGE = "#F4F2EA"            # fondo de las tarjetas
AZUL_LEDESMA = "#1A217E"     # título del flowsheet y nombre de la planta
TEXTO = "#333333"
CARPETA_ASSETS = Path(__file__).resolve().parent / "assets"
LOGO_BROWN = CARPETA_ASSETS / "brown_logo.png"               # sobre fondo blanco (encabezado)
LOGO_BROWN_CLARO = CARPETA_ASSETS / "brown_logo_claro.png"   # letras blancas, sobre el verde de la barra lateral
GOTA_BROWN = CARPETA_ASSETS / "brown_gota.png"               # con la barra lateral cerrada
PIE = "Brown · A Gulftech family brand"

CSS = f"""<style>
.stMainBlockContainer {{ padding-top: 4rem; padding-bottom: 3rem; }}
[data-testid="stSidebarHeader"] {{ padding-bottom: 0.5rem; }}
[data-testid="stSidebarHeader"] img {{ height: 2.4rem; max-width: 100%; }}
[data-testid="stMetric"] {{ background-color: {BEIGE}; padding: 0.7rem 1rem; border-radius: 0.6rem; }}
[data-testid="stMetricValue"] {{ color: {TEXTO}; }}
[class*="st-key-tarjeta"] {{ background-color: #FFFFFF; border-radius: 0.6rem; }}
.pie-brown {{ font-size: 0.8rem; opacity: 0.75; margin-top: 1.5rem; letter-spacing: 0.02em; }}
.cobrand {{ background: #FFFFFF; border: 1px solid #E2DED0; border-radius: 0.7rem; padding: 0.9rem 1.4rem 0.8rem;
            margin-bottom: 0.6rem; }}
.cobrand-logos {{ display: flex; align-items: center; gap: 1.1rem; }}
.cobrand-logos img {{ height: 2.6rem; width: auto; }}
.cobrand-barra {{ width: 2px; height: 2.6rem; background: {TEXTO}; opacity: 0.35; }}
.cobrand-planta {{ color: {AZUL_LEDESMA}; font-weight: 700; font-size: 1.5rem; letter-spacing: 0.04em; }}
.cobrand h1 {{ font-family: Barlow, sans-serif; font-weight: 600; font-size: 1.85rem; color: {TEXTO};
               margin: 0.7rem 0 0.1rem; padding: 0; }}
.cobrand p {{ margin: 0; color: #6B6B6B; font-size: 0.95rem; }}
.titulo-planta {{ color: {AZUL_LEDESMA}; font-family: Barlow, sans-serif; font-weight: 600; font-size: 1.45rem;
                  margin: 0.4rem 0 0.2rem; }}
</style>"""


def aplicar() -> None:
    """Logo de Brown en la barra lateral (versión clara sobre el verde; la gota con la barra cerrada) y el CSS."""
    st.logo(str(LOGO_BROWN_CLARO), size="large", icon_image=str(GOTA_BROWN))
    st.html(CSS)


def pie() -> None:
    """Pie de la barra lateral, debajo del menú y de los filtros de la página."""
    st.sidebar.html(f'<div class="pie-brown">{escape(PIE)}</div>')


def _imagen(ruta: Path, alternativo: str) -> str:
    datos = base64.b64encode(Path(ruta).read_bytes()).decode()
    return f'<img src="data:image/png;base64,{datos}" alt="{escape(alternativo)}">'


def encabezado_cobrand(logo_planta: Path | None, nombre_planta: str, titulo: str, subtitulo: str) -> str:
    """HTML del encabezado del Inicio: logo de la planta (o su nombre, en azul, si no tiene) | logo de Brown, sobre
    blanco; abajo el título y el subtítulo (período y turno)."""
    planta = (_imagen(logo_planta, nombre_planta) if logo_planta is not None
              else f'<span class="cobrand-planta">{escape(nombre_planta)}</span>')
    return (f'<div class="cobrand"><div class="cobrand-logos">{planta}<div class="cobrand-barra"></div>'
            f'{_imagen(LOGO_BROWN, "Brown")}</div><h1>{escape(titulo)}</h1><p>{escape(subtitulo)}</p></div>')


def titulo_planta(texto: str) -> str:
    """Título en azul Ledesma (el del flowsheet, con el nombre de la planta)."""
    return f'<div class="titulo-planta">{escape(texto)}</div>'
