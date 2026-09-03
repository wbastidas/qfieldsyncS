# -*- coding: utf-8 -*-
"""Cliente minimo de QFieldCloud, solo con la biblioteca estandar.

Se implementa lo imprescindible sobre ``urllib``, de modo que funcione tanto
en el Python 2.7 de ArcMap como en el Python 3 de ArcGIS Pro sin instalar
dependencias: en ArcGIS instalar paquetes suele requerir permisos de
administrador, y aqui no hace falta ninguno.

Cubre el camino completo para publicar un paquete desde ArcGIS:

    iniciar sesion -> crear o elegir proyecto -> subir archivos

Descargar lo capturado tambien esta cubierto, para el flujo de vuelta.
"""

import io
import json
import mimetypes
import os
import ssl
import uuid

try:  # Python 3
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode, urljoin
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover - Python 2.7 (ArcMap)
    from urllib import urlencode  # noqa: F401

    from urllib2 import (
        HTTPError,
        Request,
        URLError,
        urlopen,
    )
    from urlparse import urljoin  # noqa: F401

DEFAULT_SERVER = "https://app.qfield.cloud"
API_PREFIX = "/api/v1/"
USER_AGENT = "qfieldESRI/1.0"


class CloudError(Exception):
    """Error devuelto por QFieldCloud."""

    def __init__(self, message, status=None, payload=None):
        Exception.__init__(self, message)
        self.status = status
        self.payload = payload or {}


class QFieldCloudClient(object):
    """Cliente REST de QFieldCloud."""

    def __init__(
        self, server_url=DEFAULT_SERVER, token=None, verify_ssl=True, timeout=120
    ):
        self.server_url = (server_url or DEFAULT_SERVER).rstrip("/")
        self.token = token
        self.timeout = timeout
        self._context = None
        if not verify_ssl:  # pragma: no cover - solo servidores internos
            # Algunas instalaciones locales de QFieldCloud usan un certificado
            # propio; se permite desactivarlo de forma explicita, nunca por
            # omision.
            self._context = ssl._create_unverified_context()  # noqa: S323  # nosec B323

    # ------------------------------------------------------------------
    def _url(self, path):
        return "%s%s%s" % (self.server_url, API_PREFIX, path.lstrip("/"))

    def _headers(self, extra=None):
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Token %s" % self.token
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method, path, data=None, headers=None, raw=False):
        url = self._url(path)
        body = None
        request_headers = self._headers(headers)
        if data is not None and not raw:
            body = json.dumps(data).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif raw:
            body = data

        request = Request(url, data=body, headers=request_headers)
        request.get_method = lambda: method
        try:
            kwargs = {"timeout": self.timeout}
            if self._context is not None:
                kwargs["context"] = self._context
            response = urlopen(request, **kwargs)
        except HTTPError as error:
            payload = _safe_json(error.read())
            raise CloudError(_error_message(error.code, payload), error.code, payload)
        except URLError as error:
            raise CloudError("No se pudo contactar con %s: %s" % (url, error.reason))

        content = response.read()
        if not content:
            return {}
        return _safe_json(content)

    # ------------------------------------------------------------------
    # sesion
    # ------------------------------------------------------------------
    def login(self, username, password):
        """Inicia sesion y guarda el token para las llamadas siguientes."""
        payload = self._request(
            "POST", "auth/login/", {"username": username, "password": password}
        )
        token = payload.get("token")
        if not token:
            raise CloudError(
                "QFieldCloud no devolvio un token de sesion", payload=payload
            )
        self.token = token
        return payload

    def logout(self):
        if not self.token:
            return {}
        result = self._request("POST", "auth/logout/")
        self.token = None
        return result

    def user(self):
        return self._request("GET", "auth/user/")

    # ------------------------------------------------------------------
    # proyectos
    # ------------------------------------------------------------------
    def projects(self, include_public=False):
        query = "?include-public=1" if include_public else ""
        return self._request("GET", "projects/%s" % query)

    def find_project(self, name, owner=None):
        for project in self.projects():
            if project.get("name") != name:
                continue
            if owner and project.get("owner") != owner:
                continue
            return project
        return None

    def create_project(self, name, owner, description="", is_private=True):
        return self._request(
            "POST",
            "projects/",
            {
                "name": name,
                "owner": owner,
                "description": description,
                "private": bool(is_private),
            },
        )

    def ensure_project(self, name, owner, description=""):
        """Devuelve el proyecto, creandolo si hace falta."""
        project = self.find_project(name, owner)
        if project is not None:
            return project
        return self.create_project(name, owner, description)

    def delete_project(self, project_id):
        return self._request("DELETE", "projects/%s/" % project_id)

    # ------------------------------------------------------------------
    # archivos
    # ------------------------------------------------------------------
    def files(self, project_id):
        return self._request("GET", "files/%s/" % project_id)

    def upload_file(self, project_id, local_path, remote_name=None):
        """Sube un archivo al proyecto (``multipart/form-data``)."""
        remote_name = remote_name or os.path.basename(local_path)
        boundary = uuid.uuid4().hex
        with io.open(local_path, "rb") as handle:
            content = handle.read()
        body = _multipart_body(boundary, "file", remote_name, content)
        return self._request(
            "POST",
            "files/%s/%s/" % (project_id, remote_name.replace(os.sep, "/")),
            data=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=%s" % boundary,
                "Content-Length": str(len(body)),
            },
            raw=True,
        )

    def upload_package(
        self,
        project_id,
        project_dir,
        progress=None,
        skip_names=("qfieldesri_manifest.json",),
    ):
        """Sube una carpeta completa generada por el empaquetador.

        El manifiesto se omite por omision: describe la geodatabase de origen
        (rutas de servidor, nombre de la conexion) y no tiene por que salir de
        la organizacion. Quien quiera subirlo puede pasar ``skip_names=()``.
        """
        progress = progress or (lambda message, percent=None: None)
        uploaded = []
        files = []
        for dirpath, _dirnames, filenames in os.walk(project_dir):
            for filename in filenames:
                if filename in skip_names:
                    continue
                absolute = os.path.join(dirpath, filename)
                relative = os.path.relpath(absolute, project_dir)
                files.append((absolute, relative.replace(os.sep, "/")))

        for index, (absolute, relative) in enumerate(sorted(files, key=lambda f: f[1])):
            progress("Subiendo %s" % relative, int(100.0 * index / max(len(files), 1)))
            self.upload_file(project_id, absolute, relative)
            uploaded.append(relative)
        progress("Subida terminada", 100)
        return uploaded

    def download_file(self, project_id, remote_name, local_path):
        url = self._url("files/%s/%s/" % (project_id, remote_name))
        request = Request(url, headers=self._headers())
        kwargs = {"timeout": self.timeout}
        if self._context is not None:
            kwargs["context"] = self._context
        try:
            response = urlopen(request, **kwargs)
        except HTTPError as error:
            raise CloudError(
                _error_message(error.code, _safe_json(error.read())), error.code
            )
        directory = os.path.dirname(os.path.abspath(local_path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with io.open(local_path, "wb") as handle:
            handle.write(response.read())
        return local_path

    def download_package(self, project_id, destination_dir, progress=None):
        """Descarga todos los archivos del proyecto en la nube."""
        progress = progress or (lambda message, percent=None: None)
        entries = self.files(project_id)
        downloaded = []
        for index, entry in enumerate(entries):
            name = entry.get("name")
            if not name:
                continue
            progress("Descargando %s" % name, int(100.0 * index / max(len(entries), 1)))
            local = os.path.join(destination_dir, name.replace("/", os.sep))
            self.download_file(project_id, name, local)
            downloaded.append(local)
        progress("Descarga terminada", 100)
        return downloaded


# ----------------------------------------------------------------------
def _multipart_body(boundary, field_name, filename, content):
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    lines = [
        b"--" + boundary.encode("ascii"),
        (
            'Content-Disposition: form-data; name="%s"; filename="%s"'
            % (field_name, os.path.basename(filename))
        ).encode("utf-8"),
        ("Content-Type: %s" % content_type).encode("ascii"),
        b"",
        content,
        b"--" + boundary.encode("ascii") + b"--",
        b"",
    ]
    return b"\r\n".join(lines)


def _safe_json(content):
    if not content:
        return {}
    if isinstance(content, bytes):
        content = content.decode("utf-8", "replace")
    try:
        return json.loads(content)
    except ValueError:
        return {"detail": content}


def _error_message(status, payload):
    detail = payload.get("detail") or payload.get("message")
    if not detail and payload:
        detail = json.dumps(payload, ensure_ascii=False)
    messages = {
        400: "Peticion invalida",
        401: "Credenciales invalidas o sesion caducada",
        403: "Sin permisos para esta operacion",
        404: "No encontrado",
        409: "Conflicto: el recurso ya existe",
    }
    base = messages.get(status, "Error HTTP %s" % status)
    return "%s. %s" % (base, detail) if detail else base
