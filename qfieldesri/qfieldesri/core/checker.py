# -*- coding: utf-8 -*-
"""Verificacion previa al empaquetado.

Equivalente de ``libqfieldsync.project_checker``: revisa la geodatabase y la
configuracion **antes** de generar nada y devuelve una lista de avisos, para
que el usuario no descubra en campo que a una clase le faltaba el sistema de
referencia o que dos clases distintas iban a producir la misma tabla.
"""

from ..profiles import load_profile
from .config import LayerAction
from .packager import GEOMETRY_MAP, _sanitize_table

#: A partir de aqui una capa empieza a pesar en un telefono de gama media.
LARGE_LAYER_THRESHOLD = 100000

#: Nombres de columna que el GeoPackage reserva.
RESERVED_COLUMNS = ("fid", "geom")


class Feedback(object):
    """Un aviso del verificador."""

    ERROR = "error"
    WARNING = "aviso"
    INFO = "informacion"

    def __init__(self, level, message, layer=None, hint=None, check=None):
        self.level = level
        self.message = message
        self.layer = layer
        self.hint = hint
        self.check = check

    def to_dict(self):
        return {
            "level": self.level,
            "message": self.message,
            "layer": self.layer,
            "hint": self.hint,
            "check": self.check,
        }

    def __repr__(self):  # pragma: no cover
        return "<Feedback %s %s>" % (self.level, self.message)

    def format(self):
        prefix = {"error": "ERROR", "aviso": "AVISO", "informacion": "INFO"}[self.level]
        text = "[%s] %s" % (prefix, self.message)
        if self.layer:
            text = "[%s] %s: %s" % (prefix, self.layer, self.message)
        if self.hint:
            text += "\n        -> %s" % self.hint
        return text


class CheckResult(object):
    def __init__(self, feedbacks=None):
        self.feedbacks = list(feedbacks or [])

    def add(self, feedback):
        self.feedbacks.append(feedback)
        return feedback

    @property
    def errors(self):
        return [f for f in self.feedbacks if f.level == Feedback.ERROR]

    @property
    def warnings(self):
        return [f for f in self.feedbacks if f.level == Feedback.WARNING]

    @property
    def infos(self):
        return [f for f in self.feedbacks if f.level == Feedback.INFO]

    @property
    def has_errors(self):
        return bool(self.errors)

    def to_list(self):
        return [feedback.to_dict() for feedback in self.feedbacks]

    def format(self):
        if not self.feedbacks:
            return "Sin observaciones: la geodatabase se puede empaquetar."
        return "\n".join(feedback.format() for feedback in self.feedbacks)


class WorkspaceChecker(object):
    """Revisa una geodatabase frente a una configuracion de empaquetado."""

    def __init__(self, workspace, config):
        self.workspace = workspace
        self.config = config
        self.profile = load_profile(config.profile)

    def check(self):
        result = CheckResult()
        layers = self._selected_layers()

        if not layers:
            result.add(
                Feedback(
                    Feedback.ERROR,
                    "No hay ninguna clase seleccionada para empaquetar.",
                    hint="Revise el filtro de capas de la configuracion.",
                    check="sin_capas",
                )
            )
            return result

        self._check_workspace(result)
        self._check_table_name_collisions(result, layers)
        for layer in layers:
            self._check_layer(result, layer)
        self._check_crs_consistency(result, layers)
        self._check_profile_drift(result, layers)
        return result

    # ------------------------------------------------------------------
    def _selected_layers(self):
        layers = []
        for layer in self.workspace.layers:
            config = self.config.find_layer_config(layer.name)
            if config is not None and not config.is_included:
                continue
            layers.append(layer)
        return layers

    def _check_workspace(self, result):
        if self.workspace.is_enterprise:
            self._check_enterprise(result)
        if not self.workspace.domains:
            result.add(
                Feedback(
                    Feedback.WARNING,
                    "La geodatabase no declara dominios.",
                    hint=(
                        "Los formularios de QField quedaran con campos de texto "
                        "libre en vez de listas de valores validos."
                    ),
                    check="sin_dominios",
                )
            )
        if not self.workspace.relationships:
            result.add(
                Feedback(
                    Feedback.INFO,
                    "No se han encontrado relationship classes.",
                    hint=(
                        "Las tablas Unidad no apareceran vinculadas a su Puesto "
                        "en el formulario de QField."
                    ),
                    check="sin_relaciones",
                )
            )

    def _check_enterprise(self, result):
        """Lo que hay que saber antes de tocar una geodatabase corporativa."""
        result.add(
            Feedback(
                Feedback.INFO,
                "El origen es una geodatabase corporativa.",
                hint=(
                    "Los datos se leen de la version a la que apunta el "
                    "archivo de conexion. Al sincronizar de vuelta se "
                    "escribira en esa misma version dentro de una sesion de "
                    "edicion."
                ),
                check="workspace_corporativo",
            )
        )

        versioned = [
            layer.name for layer in self.workspace.layers if layer.is_versioned
        ]
        if versioned:
            result.add(
                Feedback(
                    Feedback.INFO,
                    "Hay %d clase(s) registradas como versionadas." % len(versioned),
                    hint=(
                        "Lo capturado en campo quedara en la version de la "
                        "conexion. Para que llegue a DEFAULT hay que "
                        "reconciliar y publicar (Reconcile / Post) despues de "
                        "sincronizar."
                    ),
                    check="clases_versionadas",
                )
            )
        else:
            result.add(
                Feedback(
                    Feedback.WARNING,
                    "Ninguna clase esta registrada como versionada.",
                    hint=(
                        "La sincronizacion escribira directamente en las "
                        "tablas base, sin una version que revisar antes de "
                        "publicar. Conviene respaldar antes de aplicar y "
                        "revisar el informe de cambios con calma."
                    ),
                    check="sin_versionar",
                )
            )

    def _check_table_name_collisions(self, result, layers):
        seen = {}
        for layer in layers:
            table = _sanitize_table(layer.name)
            if table in seen:
                result.add(
                    Feedback(
                        Feedback.ERROR,
                        "'%s' y '%s' produce la misma tabla '%s' en el "
                        "GeoPackage." % (seen[table], layer.name, table),
                        layer=layer.name,
                        hint=(
                            "Empaquete solo una de las dos, o renombre una de "
                            "las clases en la geodatabase."
                        ),
                        check="colision_nombres",
                    )
                )
            seen[table] = layer.name

    def _check_layer(self, result, layer):
        config = self.config.find_layer_config(layer.name)

        if layer.is_spatial:
            if layer.geometry_type not in GEOMETRY_MAP:
                result.add(
                    Feedback(
                        Feedback.ERROR,
                        "Geometria no soportada: %s" % layer.geometry_type,
                        layer=layer.name,
                        check="geometria_no_soportada",
                    )
                )
            if layer.spatial_reference is None or not layer.spatial_reference.code:
                result.add(
                    Feedback(
                        Feedback.WARNING,
                        "La clase no tiene un sistema de referencia con codigo "
                        "EPSG reconocible.",
                        layer=layer.name,
                        hint=(
                            "QField mostrara la capa en coordenadas sin "
                            "proyectar. Defina el sistema de referencia en "
                            "ArcGIS o fije uno en la configuracion."
                        ),
                        check="sin_crs",
                    )
                )
            if layer.has_m:
                result.add(
                    Feedback(
                        Feedback.INFO,
                        "La clase tiene valores M; QField no los edita y se "
                        "conservaran solo si la entidad no se modifica.",
                        layer=layer.name,
                        check="valores_m",
                    )
                )

        if not layer.globalid_field:
            result.add(
                Feedback(
                    Feedback.WARNING,
                    "La clase no tiene campo GlobalID.",
                    layer=layer.name,
                    hint=(
                        "Se usara OBJECTID para reconocer los registros al "
                        "sincronizar de vuelta. OBJECTID puede cambiar si la "
                        "clase se comprime o se reconstruye: active GlobalIDs "
                        "(Datos > Administrar > Anadir GlobalIDs) para una "
                        "sincronizacion segura."
                    ),
                    check="sin_globalid",
                )
            )

        for field in layer.fields:
            if field.name.lower() in RESERVED_COLUMNS:
                result.add(
                    Feedback(
                        Feedback.ERROR,
                        "El campo '%s' choca con una columna reservada del "
                        "GeoPackage." % field.name,
                        layer=layer.name,
                        hint="Renombre el campo en la geodatabase.",
                        check="campo_reservado",
                    )
                )
            if (field.field_type or "").lower() == "blob":
                result.add(
                    Feedback(
                        Feedback.INFO,
                        "El campo binario '%s' no se copia al paquete." % field.name,
                        layer=layer.name,
                        check="campo_blob",
                    )
                )
            if field.domain and field.domain not in self.workspace.domains:
                result.add(
                    Feedback(
                        Feedback.WARNING,
                        "El campo '%s' declara el dominio '%s', que no existe "
                        "en la geodatabase." % (field.name, field.domain),
                        layer=layer.name,
                        check="dominio_ausente",
                    )
                )

        self._check_subtype_domains(result, layer)

        if layer.feature_count and layer.feature_count > LARGE_LAYER_THRESHOLD:
            result.add(
                Feedback(
                    Feedback.WARNING,
                    "La clase tiene %d entidades." % layer.feature_count,
                    layer=layer.name,
                    hint=(
                        "Considere un area de interes o una clausula WHERE: un "
                        "paquete de este tamano hara lento el dispositivo."
                    ),
                    check="capa_grande",
                )
            )

        if config and config.action == LayerAction.EMPTY:
            result.add(
                Feedback(
                    Feedback.INFO,
                    "Se empaqueta solo el esquema, sin entidades.",
                    layer=layer.name,
                    check="capa_vacia",
                )
            )

    def _check_subtype_domains(self, result, layer):
        """Avisa de los campos cuyo dominio cambia segun el subtipo.

        Es el caso de ``VOLTAJE`` en ``Barra`` (BT/MT/AT). QField no puede
        cambiar la lista al vuelo, asi que el empaquetador ofrece la union: hay
        que saberlo para revisar despues lo capturado.
        """
        if not layer.subtypes:
            return
        for field in layer.fields:
            domains = layer.all_domains_for(field.name)
            if len(domains) > 1:
                result.add(
                    Feedback(
                        Feedback.WARNING,
                        "El campo '%s' usa dominios distintos segun el subtipo "
                        "(%s)." % (field.name, ", ".join(domains)),
                        layer=layer.name,
                        hint=(
                            "En QField se ofrecera la union de todos ellos; "
                            "valide despues que el valor corresponda al "
                            "subtipo del registro."
                        ),
                        check="dominio_por_subtipo",
                    )
                )

    def _check_crs_consistency(self, result, layers):
        codes = set()
        for layer in layers:
            if layer.spatial_reference and layer.spatial_reference.code:
                codes.add(layer.spatial_reference.code)
        if len(codes) > 1:
            result.add(
                Feedback(
                    Feedback.WARNING,
                    "Las clases no comparten sistema de referencia (%s)."
                    % ", ".join("EPSG:%s" % code for code in sorted(codes)),
                    hint=(
                        "Cada capa conservara el suyo y QField reproyectara al "
                        "vuelo, lo que cuesta rendimiento. Considere fijar un "
                        "CRS unico en la configuracion."
                    ),
                    check="crs_mixto",
                )
            )

    def _check_profile_drift(self, result, layers):
        """Compara el esquema real con el catalogo del perfil."""
        if not self.profile.classes:
            return
        unknown = [layer.name for layer in layers if not self.profile.knows(layer.name)]
        if unknown:
            result.add(
                Feedback(
                    Feedback.INFO,
                    "Clases fuera del catalogo del perfil '%s': %s."
                    % (self.profile.id, ", ".join(sorted(unknown)[:10])),
                    hint=(
                        "Sus campos se clasificaran por heuristica de nombre. "
                        "Si son parte estable del modelo, actualice el perfil."
                    ),
                    check="clase_fuera_de_perfil",
                )
            )
        for layer in layers:
            documented = self.profile.subtype_names(layer.name)
            if not documented or not layer.subtypes:
                continue
            actual = set(subtype.code for subtype in layer.subtypes)
            missing = set(documented) - actual
            if missing:
                result.add(
                    Feedback(
                        Feedback.INFO,
                        "Subtipos documentados que no existen en la "
                        "geodatabase: %s."
                        % ", ".join(
                            "%s=%s" % (code, documented[code])
                            for code in sorted(missing)
                        ),
                        layer=layer.name,
                        check="subtipos_ausentes",
                    )
                )
