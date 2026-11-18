from __future__ import annotations
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

VERSION_ESQUEMA = "2.0"


class RegistroPresupuesto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_esquema: str = VERSION_ESQUEMA
    id_registro: str
    id_tabla: str
    documento: str
    documento_sha256: str
    ejercicio: int
    naturaleza_documento: str
    pagina_pdf: int = Field(ge=1)
    hoja: str
    tipo_balance: Literal["RECURSOS", "GASTOS", "REMUNERACIONES"]
    tipo_cuadro: str
    familia_clasificador: str
    caracter_institucional: str
    jurisdiccion: str
    jurisdiccion_codigo: str
    programa: str = ""
    subprograma: str = ""
    proyecto: str = ""
    actividad: str = ""
    obra: str = ""
    fuente_financiamiento: str = ""
    tipo_fuente: str = ""
    recurso_especifico: str = ""
    clasificador: str
    clasificador_original: str
    descripcion: str
    nivel_jerarquico: int = Field(ge=0)
    codigo_padre: str = ""
    id_padre: str = ""
    tipo_fila: Literal["TOTAL", "SUBTOTAL", "PARTIDA", "SIN_JERARQUIA"]
    es_total: bool
    indice_columna: int = Field(ge=1)
    columna_monto: str
    encabezado_columna: str
    dimension_columna: str
    codigo_dimension_columna: str = ""
    encabezado_columnas: str
    coordenada_x: float
    coordenada_y: float
    monto_original: str
    monto: Optional[Decimal] = None
    monto_centavos: Optional[int] = None
    estado_monto: Literal["OK", "ERROR", "AUSENTE", "SIN_DATO"]
    texto_original: str
