from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.schemas.candidates import CandidateOut


MAX_PARENT_CHARS = 4000
MAX_CHILD_CHARS = 2048


def decode_delimiter(value: str) -> str:
    # Decode only supported escapes; unicode_escape corrupts Chinese text.
    value = re.sub(r'\\([nrt\\])', lambda m: {'n': '\n', 'r': '\r', 't': '\t', '\\': '\\'}[m[1]], value)
    return value.replace('\r\n', '\n').replace('\r', '\n')


class ChunkSettings(BaseModel):
    parent_size: int = Field(default=MAX_PARENT_CHARS, ge=200, le=MAX_PARENT_CHARS)
    child_size: int = Field(default=MAX_CHILD_CHARS, ge=50, le=MAX_CHILD_CHARS)
    child_overlap: int = Field(default=60, ge=0)
    parent_delimiter: str = Field(default='<<<RESUME_PARENT>>>', min_length=1, max_length=256)
    child_delimiter: str = Field(default='<<<RESUME_SECTION>>>', min_length=1, max_length=256)
    replace_consecutive_spaces: bool = False
    delete_urls_email: bool = False

    @model_validator(mode="after")
    def valid_sizes(self):
        if self.child_size > self.parent_size:
            raise ValueError("子分段长度不能超过父分段长度")
        if self.child_overlap >= self.child_size:
            raise ValueError("重叠长度必须小于子分段长度")
        if decode_delimiter(self.parent_delimiter) in decode_delimiter(self.child_delimiter):
            raise ValueError('父分段标志不能与子分段标志相同或包含在子分段标志中')
        return self


class EnabledUpdate(BaseModel):
    enabled: bool


class DocumentBatch(BaseModel):
    ids: list[StrictInt] = Field(min_length=1, max_length=100)
    action: Literal["enable", "disable"]


class ChunkBatch(BaseModel):
    ids: list[StrictInt] = Field(min_length=1, max_length=100)
    action: Literal["enable", "disable", "delete"]


class ChunkCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=MAX_PARENT_CHARS)
    parent_id: int | None = None


class ChunkUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    content: str | None = Field(default=None, min_length=1, max_length=MAX_PARENT_CHARS)
    enabled: bool | None = None

    @model_validator(mode="after")
    def has_changes(self):
        if not self.model_fields_set or any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("请填写正文或启用状态，字段不可为空")
        return self


class ChunkOut(BaseModel):
    id: int
    candidate_id: int
    parent_id: int | None
    chunk_type: str
    position: int
    content: str
    enabled: bool
    effective_enabled: bool
    char_count: int
    hit_count: int
    child_count: int
    enabled_child_count: int
    indexed: bool
    created_at: datetime
    updated_at: datetime


class ChunkPage(BaseModel):
    items: list[ChunkOut]
    total: int
    page: int
    page_size: int


class DocumentStats(BaseModel):
    parent_chunks: int
    child_chunks: int
    enabled_child_chunks: int
    indexed_child_chunks: int
    character_count: int
    hit_count: int


class KnowledgeDocument(BaseModel):
    candidate: CandidateOut
    stats: DocumentStats
    source_file_size: int | None


class KnowledgeSummary(BaseModel):
    documents: int
    enabled_documents: int
    parent_chunks: int
    child_chunks: int
    enabled_child_chunks: int
    indexed_child_chunks: int
    hit_count: int
