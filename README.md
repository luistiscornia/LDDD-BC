# Billetera Cívica Rosario

> **MVP (producto mínimo viable) desarrollado para _Laboratorio de Datos_.**
> Es un prototipo académico: sirve para explorar la idea y validar el enfoque, no es un producto terminado

Herramienta ciudadana de transparencia que permite ver, de forma simple, **cómo presupuestó y cómo gastó la Municipalidad de Rosario** sus recursos, por secretaría y por objeto del gasto.

**Demo:** https://lab.luistiscornia.com

## Qué responde

1. ¿Cuánto se presupuestó y cuánto se ejecutó realmente en cada área? (% de ejecución)
2. ¿Qué parte del gasto total absorbió cada secretaría?
3. ¿Cuánto representa ese gasto por habitante?
4. ¿Qué áreas están en línea con lo proyectado y cuáles se desvían? (semáforos cívicos y notas sobre reasignaciones normativas)
5. Un módulo experimental de aprendizaje automático: modelo de desvío (XGBoost) y detección de anomalías (Isolation Forest).

## Alcance y limitaciones del MVP

- Los datos se extraen automáticamente de **PDFs oficiales** (proyecto de presupuesto, informes de ejecución y ordenanzas). La extracción puede tener errores; el proyecto incluye controles de auditoría (`auditor.py`, `salida/incidencias_auditoria.csv`) pero **no reemplaza a la fuente oficial**.
- Los montos se muestran tal como figuran en los documentos, sin ajuste por inflación.
- Los modelos de ML son exploratorios y no se validaron para uso en decisiones reales.
- Cobertura actual: ejercicios 2022 a 2025 en los datasets de `salida/`.

## Cómo está armado

```
app.py                  Entrada de la aplicación web (Streamlit)
app_billetera.py        Interfaz: tarjetas, selector de áreas, gráficos, módulo ML
formato_numeros.py      Formato numérico argentino (1.234,56) y montos compactos
extractor*.py           Extracción de tablas desde los PDFs
auditor.py              Controles de estructura y conciliación de los datos extraídos
ejecutar_pipeline.py    Punto de entrada del pipeline de datos
pipeline/               Scraper, orquestador, normalizador y motor de ML
salida/                 Datasets resultantes (CSV y JSON) que consume la app
datos_crudos/           PDFs descargados (no versionados; ver manifiesto_ingesta.json)
```

El flujo es: **scraping** de portales oficiales → **extracción** de PDFs a CSV → **normalización y cruce** → datasets en `salida/` → **app** Streamlit.

## Cómo ejecutarlo

Requiere Python 3.9 o superior.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Levantar la aplicación
streamlit run app.py
```

Para regenerar los datos desde las fuentes oficiales:

```bash
python3 ejecutar_pipeline.py --todo        # scraping + extracción + normalización
python3 ejecutar_pipeline.py --scraper     # solo descarga de PDFs
python3 ejecutar_pipeline.py --extraer     # solo PDFs a CSV
python3 ejecutar_pipeline.py --normalizar  # solo datasets consolidados
```

Nota: algunos archivos pesados (por ejemplo `salida/presupuesto_consolidado_total.csv`, de unos 66 MB) y los PDFs originales no se incluyen en el repositorio. Se regeneran con el pipeline.

## Despliegue

La demo corre en un VPS con Streamlit detrás de Apache. Para que funcione detrás de un proxy hay que reenviar el websocket (`/_stcore/stream`) y arrancar Streamlit con `server.enableCORS = false` y `server.enableXsrfProtection = false`, como se define en `.streamlit/config.toml`.

## Fuentes

Documentos públicos de la Municipalidad de Rosario: proyectos de presupuesto, informes de ejecución de recursos y gastos, y ordenanzas presupuestarias.

## Autoría

Garcia, Victoria
Golin, Angel Luis
Laborde, Julieta
Muñoz, Facundo Sebastian
Tiscornia, Luis Omar 
