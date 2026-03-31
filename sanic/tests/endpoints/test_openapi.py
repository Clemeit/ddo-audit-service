"""
Integration tests for OpenAPI / Swagger configuration.

These tests verify that the sanic-ext OpenAPI extension is wired up with the
correct metadata so that silent breakage (e.g. config keys being removed) is
caught early.
"""

import app as app_module
from sanic_ext import Extend


def test_openapi_api_title_is_configured():
    assert app_module.app.config.API_TITLE == "DDO Audit API"


def test_openapi_api_version_is_configured():
    assert app_module.app.config.API_VERSION == "1.0"


def test_openapi_api_description_is_set():
    assert app_module.app.config.API_DESCRIPTION


def test_openapi_swagger_ui_is_default():
    assert app_module.app.config.OAS_UI_DEFAULT == "swagger"


def test_openapi_redoc_is_enabled():
    assert app_module.app.config.OAS_UI_REDOC is True


def test_openapi_extend_instance_is_created():
    assert isinstance(app_module.extend, Extend)
