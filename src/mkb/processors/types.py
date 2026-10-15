"""Processing vocabulary shared by parsers and optional persistence adapters."""

from enum import Enum


class ProcessingType(str, Enum):
    MARKDOWN = "MARKDOWN"
    DATAFRAME = "DATAFRAME"
    IMAGE = "IMAGE"
    UNPROCESSABLE = "UNPROCESSABLE"
