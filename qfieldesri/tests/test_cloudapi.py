# -*- coding: utf-8 -*-
"""Pruebas del cliente de QFieldCloud (sin salir a la red)."""

import os
import shutil
import tempfile
import unittest

from qfieldesri.core import cloudapi
from qfieldesri.core.cloudapi import CloudError, QFieldCloudClient


class FakeClient(QFieldCloudClient):
    """Cliente que registra las llamadas en vez de hacerlas."""

    def __init__(self, responses=None, **kwargs):
        QFieldCloudClient.__init__(self, **kwargs)
        self.calls = []
        self.responses = responses or {}

    def _request(self, method, path, data=None, headers=None, raw=False):
        self.calls.append((method, path, data if not raw else "<binario>"))
        return self.responses.get((method, path), self.responses.get(path, {}))


class ClientTest(unittest.TestCase):
    def test_url_de_la_api(self):
        client = QFieldCloudClient("https://app.qfield.cloud/")
        self.assertEqual(
            client._url("projects/"), "https://app.qfield.cloud/api/v1/projects/"
        )

    def test_cabecera_de_autorizacion(self):
        client = QFieldCloudClient(token="abc123")
        self.assertEqual(client._headers()["Authorization"], "Token abc123")
        self.assertNotIn("Authorization", QFieldCloudClient()._headers())

    def test_login_guarda_el_token(self):
        client = FakeClient({"auth/login/": {"token": "t0k3n"}})
        client.login("usuario", "clave")
        self.assertEqual(client.token, "t0k3n")
        method, path, payload = client.calls[0]
        self.assertEqual((method, path), ("POST", "auth/login/"))
        self.assertEqual(payload["username"], "usuario")

    def test_login_sin_token_falla(self):
        client = FakeClient({"auth/login/": {"detail": "credenciales invalidas"}})
        with self.assertRaises(CloudError):
            client.login("usuario", "clave")

    def test_busca_proyecto_por_nombre_y_propietario(self):
        client = FakeClient(
            {
                "projects/": [
                    {"name": "gye", "owner": "cnel", "id": "1"},
                    {"name": "gye", "owner": "otro", "id": "2"},
                ]
            }
        )
        self.assertEqual(client.find_project("gye", "otro")["id"], "2")
        self.assertIsNone(client.find_project("inexistente"))

    def test_crea_el_proyecto_si_no_existe(self):
        client = FakeClient(
            {"projects/": [], ("POST", "projects/"): {"id": "9", "name": "gye"}}
        )
        project = client.ensure_project("gye", "cnel", "Alimentador 04BH")
        self.assertEqual(project["id"], "9")
        self.assertEqual(client.calls[-1][0], "POST")

    def test_subida_de_carpeta_omite_el_manifiesto(self):
        directory = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(directory, "DCIM"))
            for name in ("data.gpkg", "proyecto.qgs", "qfieldesri_manifest.json"):
                with open(os.path.join(directory, name), "w") as handle:
                    handle.write("x")
            with open(os.path.join(directory, "DCIM", "foto.jpg"), "w") as handle:
                handle.write("x")

            client = FakeClient()
            uploaded = client.upload_package("id", directory)
            self.assertEqual(
                sorted(uploaded), ["DCIM/foto.jpg", "data.gpkg", "proyecto.qgs"]
            )
            self.assertNotIn("qfieldesri_manifest.json", uploaded)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_el_manifiesto_se_puede_subir_si_se_pide(self):
        directory = tempfile.mkdtemp()
        try:
            with open(os.path.join(directory, "qfieldesri_manifest.json"), "w") as h:
                h.write("{}")
            client = FakeClient()
            self.assertEqual(
                client.upload_package("id", directory, skip_names=()),
                ["qfieldesri_manifest.json"],
            )
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class HelpersTest(unittest.TestCase):
    def test_cuerpo_multipart(self):
        body = cloudapi._multipart_body("BORDE", "file", "data.gpkg", b"contenido")
        self.assertIn(b"--BORDE", body)
        self.assertIn(b'filename="data.gpkg"', body)
        self.assertIn(b"contenido", body)
        self.assertTrue(body.endswith(b"--BORDE--\r\n"))

    def test_mensajes_de_error_en_espanol(self):
        self.assertIn("Credenciales", cloudapi._error_message(401, {}))
        self.assertIn("permisos", cloudapi._error_message(403, {}))
        self.assertIn("detalle", cloudapi._error_message(400, {"detail": "detalle"}))

    def test_json_defensivo(self):
        self.assertEqual(cloudapi._safe_json(b""), {})
        self.assertEqual(cloudapi._safe_json(b'{"a": 1}'), {"a": 1})
        self.assertEqual(cloudapi._safe_json(b"<html>"), {"detail": "<html>"})


if __name__ == "__main__":
    unittest.main()
