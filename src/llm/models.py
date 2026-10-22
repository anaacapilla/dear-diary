from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ShortText = Annotated[str, Field(min_length=1, max_length=500)]
Sentiment = Literal["positivo", "negativo", "neutral", "mezclado"]
Emotion = Literal[
    "alegria", "tristeza", "enojo", "miedo", "sorpresa", "asco",
    "indiferencia", "nostalgia", "vergüenza", "culpa", "ansiedad",
    "confusión", "gratitud", "frustración",
]


class DiaryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=2000, description="Resumen fiel y breve.")
    sentiment: Sentiment
    emotions: list[Emotion] = Field(max_length=100)
    people: list[ShortText] = Field(max_length=100)
    places: list[ShortText] = Field(max_length=100)
    events: list[ShortText] = Field(max_length=100)

    @field_validator("emotions", mode="before")
    @classmethod
    def normalize_joy(cls, value):
        if isinstance(value, list):
            return ["alegria" if item == "alegría" else item for item in value]
        return value


class DailySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=2000)
