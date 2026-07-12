from pydantic import BaseModel, ConfigDict


class APIModel(BaseModel):
    """Public API data must reject unknown fields rather than discard them."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ORMModel(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)
