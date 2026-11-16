"""Intérprete de JSON-LD. La definición es compartida y el estado vive en los objetos."""
from reglas.compilador import catalogo, compilar, compilar_archivo
from reglas.partida import Partida

__all__ = ["Partida", "catalogo", "compilar", "compilar_archivo"]
