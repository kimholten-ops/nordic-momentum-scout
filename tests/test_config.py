"""GitHub Actions setter alltid miljøvariabel-nøkkelen, også når secreten
eller repo-variabelen ikke er definert — da med tom streng. env() skal
behandle det som fravær, ikke som en eksplisitt tom verdi. Se merknaden
ved env() i src/config.py og feilen den fikser (MissingSchema fra requests
fordi MFN_FEED_URL ble '')."""
from __future__ import annotations

import importlib

import pytest

from src import config


def test_env_tom_streng_gir_standard(monkeypatch):
    monkeypatch.setenv("MOMENTUM_TEST_VAR", "")
    assert config.env("MOMENTUM_TEST_VAR", "standard") == "standard"


def test_env_manglende_variabel_gir_standard(monkeypatch):
    monkeypatch.delenv("MOMENTUM_TEST_VAR", raising=False)
    assert config.env("MOMENTUM_TEST_VAR", "standard") == "standard"


def test_env_strippes_for_whitespace(monkeypatch):
    monkeypatch.setenv("MOMENTUM_TEST_VAR", "  https://ekte-verdi.example  ")
    assert config.env("MOMENTUM_TEST_VAR", "standard") == "https://ekte-verdi.example"


def test_env_kun_whitespace_gir_standard(monkeypatch):
    monkeypatch.setenv("MOMENTUM_TEST_VAR", "   ")
    assert config.env("MOMENTUM_TEST_VAR", "standard") == "standard"


def test_env_returnerer_faktisk_verdi(monkeypatch):
    monkeypatch.setenv("MOMENTUM_TEST_VAR", "https://ekte-verdi.example")
    assert config.env("MOMENTUM_TEST_VAR", "standard") == "https://ekte-verdi.example"


def test_modulniva_faller_tilbake_ved_tom_miljovariabel(monkeypatch):
    """Selve feilen fra bug-rapporten: reproduseres ved å laste config-
    modulen på nytt med tomme miljøvariabler, slik Actions faktisk gjør
    det."""
    with monkeypatch.context() as m:
        m.setenv("MFN_FEED_URL", "")
        m.setenv("MOMENTUM_UA", "")
        importlib.reload(config)
        assert config.MFN_FEED_URL == "https://mfn.se/all/s/nordic"
        assert config.USER_AGENT.startswith("momentum-varsling/")
    importlib.reload(config)  # miljøet er gjenopprettet - gi modulen normal tilstand tilbake


def test_valider_kaster_ved_tom_feed_url(monkeypatch):
    monkeypatch.setattr(config, "MFN_FEED_URL", "")
    with pytest.raises(ValueError):
        config.valider()


def test_valider_godtar_gyldig_oppsett(monkeypatch):
    monkeypatch.setattr(config, "MFN_FEED_URL", "https://mfn.se/all/s/nordic")
    monkeypatch.setattr(config, "USER_AGENT", "momentum-varsling/0.1 (kontakt: meg@eksempel.no)")
    config.valider()  # skal ikke kaste
