"""Typed state for the Code Documentation Agent's LangGraph."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


class FileMeta(BaseModel):
    relative_path: str
    language: str
    loc: int
    sha256: str


class AnnotationInfo(BaseModel):
    name: str
    value: str | None = None


class ParamInfo(BaseModel):
    name: str
    type: str | None = None
    annotations: list[AnnotationInfo] = Field(default_factory=list)


class FieldInfo(BaseModel):
    name: str
    type: str
    annotations: list[AnnotationInfo] = Field(default_factory=list)


class MethodInfo(BaseModel):
    name: str
    start_line: int
    end_line: int
    signature: str | None = None
    annotations: list[AnnotationInfo] = Field(default_factory=list)
    parameters: list[ParamInfo] = Field(default_factory=list)
    return_type: str | None = None


class ClassInfo(BaseModel):
    name: str
    start_line: int
    end_line: int
    methods: list[MethodInfo] = Field(default_factory=list)
    annotations: list[AnnotationInfo] = Field(default_factory=list)
    fields: list[FieldInfo] = Field(default_factory=list)


class FileAST(BaseModel):
    relative_path: str
    language: str
    classes: list[ClassInfo] = Field(default_factory=list)
    functions: list[MethodInfo] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)   # JSX/TSX components
    hooks: list[str] = Field(default_factory=list)        # React hooks used
    ts_interfaces: list[dict] = Field(default_factory=list)  # TypeScript interfaces/types


class BusinessRule(BaseModel):
    description: str
    cited_file: str
    cited_lines: tuple[int, int]
    cited_method: str | None = None


class FileSummary(BaseModel):
    relative_path: str
    purpose: str
    business_rules: list[BusinessRule] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    trivial_methods: list[str] = Field(default_factory=list)


class Module(BaseModel):
    name: str
    files: list[str]
    purpose: str | None = None


class Flow(BaseModel):
    name: str
    entry_point: str
    steps: list[str] = Field(default_factory=list)
    sequence_diagram_mermaid: str | None = None


class CoverageReport(BaseModel):
    total_files: int = 0
    summarized_files: int = 0
    total_methods: int = 0
    cited_methods: int = 0
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    loops_used: int = 0


class CodeDocState(TypedDict, total=False):
    project_path: str
    project_id: str
    display_name: str | None
    mode: Literal["full", "incremental"]

    file_inventory: list[dict]            # FileMeta as dicts
    asts: dict[str, dict]                 # path -> FileAST dict
    tree_graph: dict                      # serialized NetworkX
    dirty_files: list[str]
    file_summaries: dict[str, dict]       # path -> FileSummary dict
    modules: list[dict]
    call_graph: dict
    flows: list[dict]
    api_endpoints: list[dict]             # detected REST endpoints with DTOs + samples
    dto_classes: list[dict]               # DTO/request/response class catalog
    batch_jobs: list[dict]               # detected scheduled/batch jobs with enrichment
    generated_docs: dict[str, str]        # doc_id -> markdown (v0.2; stored in Postgres + Chroma)
    coverage_report: dict
    errors: list[dict]
    verify_loops: int
