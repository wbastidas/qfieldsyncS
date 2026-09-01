"""Lector de respaldo basado en GDAL/OGR (driver OpenFileGDB).

Permite automatizar qfieldESRI en un servidor sin licencia de ArcGIS (por
ejemplo, un proceso nocturno en Linux que publique el paquete en QFieldCloud).
Tiene dos limitaciones que conviene tener presentes y que el propio lector
anuncia en el manifiesto del paquete:

* **los subtipos no se leen**: OGR no los expone, asi que los dominios que en
  la geodatabase dependen del subtipo se toman a nivel de clase;
* **la escritura de vuelta no esta soportada**: para devolver los cambios a la
  geodatabase hace falta ``arcpy`` (u otra herramienta de ESRI), porque OGR no
  mantiene la red geometrica, los auto-actualizadores de ArcFM ni los
  identificadores globales.
"""

from ..core.model import (
    DomainInfo,
    FieldInfo,
    LayerInfo,
    SpatialReferenceInfo,
    WorkspaceInfo,
)
from .base import GeodatabaseReader, ReaderError

try:
    from osgeo import ogr, osr
except ImportError as error:  # pragma: no cover - depende del entorno
    raise ImportError("GDAL/OGR no esta disponible (%s)" % error)

ogr.UseExceptions()

#: Tipos de OGR -> vocabulario de ESRI que usa el resto de qfieldESRI.
OGR_FIELD_TYPES = {
    ogr.OFTInteger: "Integer",
    ogr.OFTInteger64: "BigInteger",
    ogr.OFTReal: "Double",
    ogr.OFTString: "String",
    ogr.OFTDate: "DateOnly",
    ogr.OFTDateTime: "Date",
    ogr.OFTBinary: "Blob",
}

OGR_GEOMETRY_TYPES = {
    ogr.wkbPoint: "Point",
    ogr.wkbLineString: "Polyline",
    ogr.wkbPolygon: "Polygon",
    ogr.wkbMultiPoint: "Multipoint",
    ogr.wkbMultiLineString: "Polyline",
    ogr.wkbMultiPolygon: "Polygon",
}


class OgrReader(GeodatabaseReader):
    name = "ogr"
    supports_write = False

    def __init__(self, workspace):
        GeodatabaseReader.__init__(self, workspace)
        self.datasource = None

    def open(self):
        self.datasource = ogr.Open(self.workspace, 0)
        if self.datasource is None:
            raise ReaderError("OGR no pudo abrir '%s'" % self.workspace)
        return self

    def close(self):
        self.datasource = None

    # ------------------------------------------------------------------
    def describe_workspace(self, layer_names=None):
        wanted = set(name.lower() for name in layer_names) if layer_names else None
        layers = []
        domains = {}
        for index in range(self.datasource.GetLayerCount()):
            ogr_layer = self.datasource.GetLayerByIndex(index)
            name = ogr_layer.GetName()
            if wanted is not None and name.lower() not in wanted:
                continue
            layers.append(self._describe_layer(ogr_layer, domains))
        return WorkspaceInfo(
            path=self.workspace,
            workspace_type=WorkspaceInfo.FILE_GDB,
            layers=layers,
            domains=domains,
            relationships=self._read_relationships(),
        )

    def _describe_layer(self, ogr_layer, domains):
        definition = ogr_layer.GetLayerDefn()
        fields = []
        for index in range(definition.GetFieldCount()):
            field_definition = definition.GetFieldDefn(index)
            domain_name = ""
            if hasattr(field_definition, "GetDomainName"):
                domain_name = field_definition.GetDomainName() or ""
            if domain_name and domain_name not in domains:
                domain = self._read_domain(domain_name)
                if domain is not None:
                    domains[domain_name] = domain
            fields.append(
                FieldInfo(
                    name=field_definition.GetName(),
                    field_type=OGR_FIELD_TYPES.get(
                        field_definition.GetType(), "String"
                    ),
                    alias=field_definition.GetAlternativeName()
                    or field_definition.GetName(),
                    length=field_definition.GetWidth() or None,
                    nullable=bool(field_definition.IsNullable()),
                    domain=domain_name or None,
                )
            )

        geometry_type = OGR_GEOMETRY_TYPES.get(ogr.GT_Flatten(definition.GetGeomType()))
        fid_column = ogr_layer.GetFIDColumn() or "OBJECTID"
        return LayerInfo(
            name=ogr_layer.GetName(),
            dataset_type=(
                LayerInfo.FEATURE_CLASS if geometry_type else LayerInfo.TABLE
            ),
            path=ogr_layer.GetName(),
            geometry_type=geometry_type,
            has_z=bool(ogr.GT_HasZ(definition.GetGeomType())),
            has_m=bool(ogr.GT_HasM(definition.GetGeomType())),
            spatial_reference=_spatial_reference_info(ogr_layer.GetSpatialRef()),
            fields=fields,
            oid_field=fid_column,
            feature_count=ogr_layer.GetFeatureCount(),
        )

    def _read_domain(self, name):
        if not hasattr(self.datasource, "GetFieldDomain"):
            return None
        domain = self.datasource.GetFieldDomain(name)
        if domain is None:
            return None
        if domain.GetDomainType() == ogr.OFDT_CODED:
            values = domain.GetEnumeration() or {}
            coded = sorted(
                ((code, label or code) for code, label in values.items()),
                key=lambda item: str(item[1]).upper(),
            )
            return DomainInfo(name, DomainInfo.CODED, coded_values=coded)
        if domain.GetDomainType() == ogr.OFDT_RANGE:
            minimum = domain.GetMinAsDouble()
            maximum = domain.GetMaxAsDouble()
            return DomainInfo(
                name, DomainInfo.RANGE, range_min=minimum, range_max=maximum
            )
        return None

    def _read_relationships(self):
        """Relaciones expuestas por GDAL 3.6+; vacio en versiones anteriores."""
        if not hasattr(self.datasource, "GetRelationshipNames"):
            return []
        from ..core.model import RelationshipInfo

        relationships = []
        for name in self.datasource.GetRelationshipNames() or []:
            relationship = self.datasource.GetRelationship(name)
            if relationship is None:
                continue
            left_fields = relationship.GetLeftTableFields() or []
            right_fields = relationship.GetRightTableFields() or []
            if not left_fields or not right_fields:
                continue
            relationships.append(
                RelationshipInfo(
                    name=name,
                    origin=relationship.GetLeftTableName(),
                    destination=relationship.GetRightTableName(),
                    origin_key=left_fields[0],
                    destination_key=right_fields[0],
                    cardinality=str(relationship.GetCardinality()),
                )
            )
        return relationships

    # ------------------------------------------------------------------
    def iter_features(
        self,
        layer_info,
        field_names,
        where_clause=None,
        aoi_wkt=None,
        aoi_crs=None,
        limit=0,
    ):
        ogr_layer = self.datasource.GetLayerByName(layer_info.name)
        if ogr_layer is None:
            raise ReaderError("No existe la capa '%s'" % layer_info.name)
        if where_clause:
            ogr_layer.SetAttributeFilter(where_clause)
        if aoi_wkt:
            ogr_layer.SetSpatialFilter(ogr.CreateGeometryFromWkt(aoi_wkt))
        ogr_layer.ResetReading()

        for count, feature in enumerate(ogr_layer, start=1):
            geometry = feature.GetGeometryRef()
            wkb = geometry.ExportToWkb() if geometry is not None else None
            attributes = {}
            for name in field_names:
                if name == layer_info.oid_field:
                    attributes[name] = feature.GetFID()
                elif feature.GetFieldIndex(name) >= 0:
                    attributes[name] = (
                        feature.GetField(name) if feature.IsFieldSet(name) else None
                    )
                else:
                    attributes[name] = None
            yield wkb, attributes
            if limit and count >= limit:
                break
        ogr_layer.SetAttributeFilter(None)
        ogr_layer.SetSpatialFilter(None)

    def count_features(self, layer_info, where_clause=None):
        ogr_layer = self.datasource.GetLayerByName(layer_info.name)
        if where_clause:
            ogr_layer.SetAttributeFilter(where_clause)
        count = ogr_layer.GetFeatureCount()
        ogr_layer.SetAttributeFilter(None)
        return count


def _spatial_reference_info(spatial_reference):
    if spatial_reference is None:
        return SpatialReferenceInfo()
    code = None
    try:
        spatial_reference.AutoIdentifyEPSG()
        code = spatial_reference.GetAuthorityCode(None)
        code = int(code) if code else None
    except Exception:
        code = None
    return SpatialReferenceInfo(
        code=code,
        name=spatial_reference.GetName() or "",
        wkt=spatial_reference.ExportToWkt(),
        is_geographic=bool(spatial_reference.IsGeographic()),
        proj4=spatial_reference.ExportToProj4()
        if hasattr(spatial_reference, "ExportToProj4")
        else "",
    )


# ``osr`` se importa por su efecto de registro de proyecciones en GDAL.
_ = osr
