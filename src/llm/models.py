from typing import Literal
from pydantic import BaseModel, Field


class DiaryAnalysis(BaseModel):
    summary: str = Field(
        description="Resumen breve de la experiencia registrada."
    )

    sentiment: Literal[
        "positivo",
        "negativo",
        "neutral",
        "mezclado"
    ] = Field(
        description="Sentimiento general de la experiencia."
    )

    emotions: list[Literal[
        "alegria",
        "tristeza",
        "enojo",
        "miedo",
        "sorpresa",
        "asco",
        "indiferencia",
        "nostalgia",
        "vergüenza",
        "culpa",
        "ansiedad",
        "confusión",
        "gratitud",
        "frustración"
    ]] = Field(
        description="Emociones identificadas en la experiencia."
    )

    people: list[str] = Field(
        description="Personas mencionadas en la experiencia."
    )

    places: list[str] = Field(
        description="Lugares mencionados en la experiencia."
    )

    events: list[str] = Field(
        description="Acontecimientos o actividades relevantes."
    )
