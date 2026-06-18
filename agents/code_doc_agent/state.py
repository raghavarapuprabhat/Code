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
    superclass: str | None = None        # `extends` target (for @MappedSuperclass flattening)


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


# --- v0.4: Architecture Model (§8.8.1) -------------------------------------
# Field names here are the CONTRACT the SRE agent's tools/architecture.py reads:
#   component: name, layer, stereotype, files[]
#   connector: from, kind, to, evidence
#   endpoint:  method, path, file, request_dto
#   datastore: kind, entities[], dsn_env, discovered_from

class Component(BaseModel):
    name: str
    layer: str = "unknown"                 # controller | service | repository | ui | infra | domain | unknown
    stereotype: str = "module"             # @RestController | @Service | @Repository | ReactComponent | module
    files: list[str] = Field(default_factory=list)
    public_api: list[str] = Field(default_factory=list)
    description: str = ""


class Connector(BaseModel):
    # Keys deliberately named from_/to with aliases so JSON has `from`/`to`.
    from_: str = Field(alias="from")
    to: str
    kind: str = "call"                     # call | event | http | db
    evidence: str = ""                     # file:line

    model_config = {"populate_by_name": True}


class Datastore(BaseModel):
    kind: str                              # postgres | mysql | mongo | redis | sqlite | ...
    entities: list[str] = Field(default_factory=list)
    dsn_env: str | None = None             # NAME of the env var holding the DSN (never the value)
    discovered_from: str = ""              # config | JPA | Prisma | Mongoose


class ExternalSystem(BaseModel):
    name: str
    kind: str = "http"                     # http | queue | sdk
    base_url_config_key: str | None = None
    auth_style: str = ""
    calling_components: list[str] = Field(default_factory=list)
    notes: str = ""


class Endpoint(BaseModel):
    method: str = "GET"
    path: str = ""
    file: str = ""                         # controller file:line
    request_dto: str | None = None
    response_dto: str | None = None
    auth: str = ""
    component: str | None = None


class DeploymentUnit(BaseModel):
    name: str
    image: str | None = None
    ports: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)   # NAMES only
    depends_on: list[str] = Field(default_factory=list)
    source: str = ""                       # Dockerfile | compose | k8s | pipeline


class Layer(BaseModel):
    name: str
    components: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)  # "A -> B (skips layer)" evidence strings


class InferredADR(BaseModel):
    title: str
    decision: str
    evidence: list[str] = Field(default_factory=list)    # file:line / config key / commit ref
    rationale: str = ""
    consequences: str = ""
    confidence: str = "medium"             # high | medium | low
    unverified: bool = False


class Hotspot(BaseModel):
    file: str
    churn: int = 0                         # commits touching the file
    complexity: int = 0                    # proxy: methods + branches
    score: float = 0.0                     # churn * complexity, normalized 0-1
    reason: str = ""


class QualityReport(BaseModel):
    hotspots: list[Hotspot] = Field(default_factory=list)
    cyclic_dependencies: list[list[str]] = Field(default_factory=list)
    layer_violations: list[str] = Field(default_factory=list)
    dead_code: list[str] = Field(default_factory=list)   # unreferenced public methods
    oversized_files: list[str] = Field(default_factory=list)
    todo_density: dict[str, int] = Field(default_factory=dict)


class ArchitectureModel(BaseModel):
    components: list[Component] = Field(default_factory=list)
    connectors: list[Connector] = Field(default_factory=list)
    datastores: list[Datastore] = Field(default_factory=list)
    external_systems: list[ExternalSystem] = Field(default_factory=list)
    endpoints: list[Endpoint] = Field(default_factory=list)
    deployment_units: list[DeploymentUnit] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    decisions: list[InferredADR] = Field(default_factory=list)
    quality: QualityReport = Field(default_factory=QualityReport)


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
    data_entities: list[dict]             # ER entities (cross_file: deterministic seed + LLM enrich)
    business_logic: list[dict]            # cross-file business rules tied to flows (cross_file)

    # --- v0.4: architecture reconstruction (§8.8) ---
    config_infra: dict                    # ConfigInfraScan raw inventory (deps, datasources, deployment)
    architecture_model: dict              # ArchitectureModel as dict (persisted to architecture_models)
    model_hash: str                       # sha256 of the model_json at synthesis time
    critique: dict                        # DocCritique scores + regen targets
    critique_loops: int

    # --- v0.5: requirements, evals, drift (§8.9) ---
    requirements_areapath: str | None     # ADO area path for requirements (None -> skip)
    requirements: list[dict]              # ingested work items (Epic/Feature/Story)
    traceability: dict                    # requirement <-> component <-> rule <-> test matrix
    trace_eval: dict                      # v0.7 — TraceLink precision/recall per method tier
    dependency_findings: dict             # CVE/license/outdated inventory (DependencyAudit)
    db_drift: dict                        # code-vs-database drift report
    test_trace: dict                      # rule -> test mapping
    eval_results: dict                    # golden Q&A eval scores
    drift_digest: str                     # latest 16_change_digest entry (markdown)
    prev_model_hash: str | None           # prior architecture_models.model_hash (for digest diff)
