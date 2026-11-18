"""Pipeline de ingesta, scraping, extracción y normalización presupuestaria."""

from .scraper import RosarioScraper
from .orchestrator import PipelineOrquestador
from .normalizer import NormalizadorPresupuesto

__all__ = ["RosarioScraper", "PipelineOrquestador", "NormalizadorPresupuesto"]
