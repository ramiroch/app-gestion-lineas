"""Pruebas del modo prueba: core.db con la variable APP_DB y core/copia_prueba.py."""

import pytest

from core.copia_prueba import copiar_base
from core.db import CARPETA_PROYECTO, RUTA_BASE, crear_motor, es_base_de_prueba, ruta_base


def test_sin_variable_usa_la_base_real(monkeypatch):
    monkeypatch.delenv("APP_DB", raising=False)
    assert ruta_base() == RUTA_BASE and not es_base_de_prueba()


def test_con_variable_usa_otra_base(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB", "data/app_prueba.db")                    # relativa: desde la carpeta del proyecto
    assert ruta_base() == CARPETA_PROYECTO / "data" / "app_prueba.db" and es_base_de_prueba()
    monkeypatch.setenv("APP_DB", str(tmp_path / "otra.db"))
    with pytest.raises(FileNotFoundError, match="copia_prueba"):             # la de prueba tiene que existir
        crear_motor()


def test_copiar_base(tmp_path):
    origen = tmp_path / "app.db"
    origen.write_bytes(b"datos")
    destino = copiar_base(origen, tmp_path / "app_prueba.db")
    assert destino.read_bytes() == b"datos"
    with pytest.raises(ValueError, match="misma base"):
        copiar_base(origen, origen)
    with pytest.raises(FileNotFoundError):
        copiar_base(tmp_path / "no_existe.db", tmp_path / "x.db")
