"""tree-sitter parsing wrappers for Java + JS/TS/JSX/TSX.

Produces a deterministic AST skeleton (classes, methods, imports, components,
annotations, fields) without invoking the LLM — passed to the LLM later to
keep token cost bounded on large repos.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from tree_sitter import Language, Parser

from ..state import AnnotationInfo, ClassInfo, FieldInfo, FileAST, MethodInfo, ParamInfo

_SCHEDULED_ANNOTATIONS = {"Scheduled", "EnableScheduling"}
_SPRING_BATCH_ANNOTATIONS = {"EnableBatchProcessing"}
_SPRING_BATCH_INTERFACES = {"Tasklet", "ItemReader", "ItemWriter", "ItemProcessor"}
_QUARTZ_ANNOTATIONS = {"DisallowConcurrentExecution", "PersistJobDataAfterExecution"}
_BATCH_RUNNER_INTERFACES = {"CommandLineRunner", "ApplicationRunner"}
_JS_CRON_LIBS = {"cron", "node-cron", "agenda", "bull", "bullmq", "bee-queue"}

_HTTP_MAPPING_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}
_AUTH_ANNOTATIONS = {"PreAuthorize", "Secured", "RolesAllowed", "PermitAll", "DenyAll"}
_REQUEST_PARAM_ANNOTATIONS = {"RequestBody", "RequestParam", "PathVariable", "RequestHeader"}
_DTO_ANNOTATIONS = {"Entity", "Data", "Value", "Document", "Embeddable", "MappedSuperclass"}
_DTO_NAME_SUFFIXES = ("Request", "Response", "Dto", "DTO", "Payload", "Command", "Query", "Resource")
_CONTROLLER_ANNOTATIONS = {"RestController", "Controller"}


@lru_cache(maxsize=8)
def _get_language(name: str) -> Language:
    if name == "java":
        import tree_sitter_java as tsj
        return Language(tsj.language())
    if name in {"javascript", "jsx"}:
        import tree_sitter_javascript as tsjs
        return Language(tsjs.language())
    if name in {"typescript"}:
        import tree_sitter_typescript as tstst
        return Language(tstst.language_typescript())
    if name in {"tsx"}:
        import tree_sitter_typescript as tstst
        return Language(tstst.language_tsx())
    raise ValueError(f"Unsupported tree-sitter language: {name}")


def _parser_for(language: str) -> Parser:
    return Parser(_get_language(language))


def _node_text(src: bytes, node: Any) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def parse_file(content: str, language: str, relative_path: str) -> FileAST:
    src = content.encode("utf-8")
    parser = _parser_for(language)
    tree = parser.parse(src)
    root = tree.root_node

    if language == "java":
        return _parse_java(src, root, relative_path)
    return _parse_js(src, root, relative_path, language)


# ----------------------------------------------------------------------
# Java annotation helpers
# ----------------------------------------------------------------------

def _parse_java_annotation(src: bytes, node: Any) -> dict | None:
    """Extract name + first string value from a marker_annotation or annotation node."""
    name: str | None = None
    for ch in node.children:
        if ch.type == "identifier":
            name = _node_text(src, ch)
            break
        elif ch.is_named and ch.type not in ("annotation_argument_list",):
            txt = _node_text(src, ch)
            if txt and "(" not in txt and "@" not in txt:
                name = txt.split(".")[-1]
                break
    if not name:
        return None

    value: str | None = None
    for ch in node.children:
        if ch.type == "annotation_argument_list":
            for arg in ch.children:
                if arg.type == "string_literal":
                    value = _node_text(src, arg).strip('"').strip("'")
                    break
                elif arg.type == "element_value_pair":
                    key_node = arg.child_by_field_name("key")
                    val_node = arg.child_by_field_name("value")
                    if key_node and _node_text(src, key_node) in ("value", "path"):
                        if val_node:
                            value = _node_text(src, val_node).strip('"').strip("'")
                        break
                elif arg.type == "element_value":
                    value = _node_text(src, arg).strip('"').strip("'")
                    break
            break

    return {"name": name, "value": value}


def _extract_annotations(src: bytes, node: Any) -> list[AnnotationInfo]:
    """Collect all annotation nodes from a declaration's direct children and modifiers."""
    result: list[AnnotationInfo] = []
    for ch in node.children:
        if ch.type in ("annotation", "marker_annotation"):
            raw = _parse_java_annotation(src, ch)
            if raw:
                result.append(AnnotationInfo(name=raw["name"], value=raw.get("value")))
        elif ch.type == "modifiers":
            for mod_ch in ch.children:
                if mod_ch.type in ("annotation", "marker_annotation"):
                    raw = _parse_java_annotation(src, mod_ch)
                    if raw:
                        result.append(AnnotationInfo(name=raw["name"], value=raw.get("value")))
    return result


def _extract_superclass(src: bytes, node: Any) -> str | None:
    """Return the simple name of a class's `extends` target, if any.

    The tree-sitter Java grammar exposes the extends clause as a `superclass` child
    of class_declaration (e.g. `extends BaseEntity`). We strip the keyword + any
    generic args and return just the type's simple name (used to flatten
    @MappedSuperclass field inheritance for the data model)."""
    sc = node.child_by_field_name("superclass")
    if sc is None:
        for ch in node.children:
            if ch.type == "superclass":
                sc = ch
                break
    if sc is None:
        return None
    for ch in sc.children:
        if ch.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
            txt = _node_text(src, ch)
            # generic_type -> drop <...>; scoped -> take last segment.
            if "<" in txt:
                txt = txt[: txt.find("<")]
            return txt.strip().split(".")[-1]
    return None


def _extract_java_params(src: bytes, params_node: Any) -> list[ParamInfo]:
    """Extract formal parameters with annotations and types from formal_parameters node."""
    params: list[ParamInfo] = []
    if not params_node:
        return params
    for ch in params_node.children:
        if ch.type in ("formal_parameter", "spread_parameter"):
            anns = _extract_annotations(src, ch)
            type_node = ch.child_by_field_name("type")
            name_node = ch.child_by_field_name("name")
            params.append(ParamInfo(
                name=_node_text(src, name_node) if name_node else "",
                type=_node_text(src, type_node) if type_node else "",
                annotations=anns,
            ))
    return params


# ----------------------------------------------------------------------
# Java
# ----------------------------------------------------------------------

def _parse_java(src: bytes, root: Any, relative_path: str) -> FileAST:
    classes: list[ClassInfo] = []
    imports: list[str] = []

    def walk(node: Any, current_class: ClassInfo | None) -> None:
        if node.type == "import_declaration":
            imports.append(_node_text(src, node).strip().rstrip(";"))
        elif node.type in (
            "class_declaration", "interface_declaration",
            "enum_declaration", "record_declaration",
        ):
            name_node = node.child_by_field_name("name")
            cname = _node_text(src, name_node) if name_node else "<anonymous>"
            ci = ClassInfo(
                name=cname,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                annotations=_extract_annotations(src, node),
                superclass=_extract_superclass(src, node),
            )
            classes.append(ci)
            for ch in node.children:
                walk(ch, ci)
            return

        elif node.type == "field_declaration":
            if current_class is not None:
                type_node = node.child_by_field_name("type")
                field_type = _node_text(src, type_node) if type_node else "Object"
                field_anns = _extract_annotations(src, node)
                for ch in node.children:
                    if ch.type == "variable_declarator":
                        fname_node = ch.child_by_field_name("name")
                        if fname_node:
                            current_class.fields.append(FieldInfo(
                                name=_node_text(src, fname_node),
                                type=field_type,
                                annotations=field_anns,
                            ))
            return

        elif node.type in ("method_declaration", "constructor_declaration"):
            name_node = node.child_by_field_name("name")
            mname = _node_text(src, name_node) if name_node else "<anonymous>"
            params_node = node.child_by_field_name("parameters")
            sig = _node_text(src, params_node) if params_node else ""
            ret_type_node = node.child_by_field_name("type")
            mi = MethodInfo(
                name=mname,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=f"{mname}{sig}",
                annotations=_extract_annotations(src, node),
                parameters=_extract_java_params(src, params_node),
                return_type=_node_text(src, ret_type_node) if ret_type_node else None,
            )
            if current_class is not None:
                current_class.methods.append(mi)
            return

        for ch in node.children:
            walk(ch, current_class)

    walk(root, None)
    return FileAST(
        relative_path=relative_path,
        language="java",
        classes=classes,
        imports=imports,
    )


# ----------------------------------------------------------------------
# JavaScript / TypeScript / JSX / TSX
# ----------------------------------------------------------------------

def _parse_js(src: bytes, root: Any, relative_path: str, language: str) -> FileAST:
    classes: list[ClassInfo] = []
    functions: list[MethodInfo] = []
    imports: list[str] = []
    components: set[str] = set()
    hooks: set[str] = set()
    ts_interfaces: list[dict] = []

    def add_function(node: Any, name: str) -> MethodInfo:
        return MethodInfo(
            name=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=name,
        )

    def _extract_ts_interface_fields(iface_node: Any) -> list[dict]:
        """Extract property names and types from a TypeScript interface/type body."""
        fields: list[dict] = []
        for ch in iface_node.children:
            if ch.type in ("object_type", "interface_body"):
                for prop in ch.children:
                    if prop.type in ("property_signature", "method_signature"):
                        n_node = prop.child_by_field_name("name")
                        t_node = prop.child_by_field_name("type")
                        fname = _node_text(src, n_node) if n_node else ""
                        ftype = _node_text(src, t_node).lstrip(":").strip() if t_node else ""
                        if fname:
                            opt = any(c.type == "?" for c in prop.children)
                            fields.append({"name": fname, "type": ftype, "required": not opt})
        return fields

    def walk(node: Any, current_class: ClassInfo | None) -> None:
        t = node.type
        if t in ("import_statement", "import_declaration"):
            imports.append(_node_text(src, node).strip().rstrip(";"))

        elif t == "interface_declaration":
            # TypeScript interface
            name_node = node.child_by_field_name("name")
            iname = _node_text(src, name_node) if name_node else "<anonymous>"
            fields = _extract_ts_interface_fields(node)
            ts_interfaces.append({
                "name": iname,
                "line": node.start_point[0] + 1,
                "fields": fields,
                "kind": "interface",
            })

        elif t == "type_alias_declaration":
            # TypeScript type alias: type Foo = { ... }
            name_node = node.child_by_field_name("name")
            iname = _node_text(src, name_node) if name_node else "<anonymous>"
            fields = _extract_ts_interface_fields(node)
            if fields:  # only capture object-type aliases
                ts_interfaces.append({
                    "name": iname,
                    "line": node.start_point[0] + 1,
                    "fields": fields,
                    "kind": "type",
                })

        elif t == "class_declaration":
            name_node = node.child_by_field_name("name")
            cname = _node_text(src, name_node) if name_node else "<anonymous>"
            ci = ClassInfo(name=cname, start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1)
            classes.append(ci)
            for ch in node.children:
                walk(ch, ci)
            return

        elif t in ("method_definition",):
            name_node = node.child_by_field_name("name")
            mname = _node_text(src, name_node) if name_node else "<anonymous>"
            mi = add_function(node, mname)
            if current_class is not None:
                current_class.methods.append(mi)
            return

        elif t == "function_declaration":
            name_node = node.child_by_field_name("name")
            fname = _node_text(src, name_node) if name_node else "<anonymous>"
            functions.append(add_function(node, fname))
            if fname and fname[0].isupper():
                components.add(fname)

        elif t == "lexical_declaration":
            for ch in node.named_children:
                if ch.type == "variable_declarator":
                    name_node = ch.child_by_field_name("name")
                    val_node = ch.child_by_field_name("value")
                    if name_node and val_node and val_node.type in (
                        "arrow_function",
                        "function_expression",
                    ):
                        fname = _node_text(src, name_node)
                        functions.append(add_function(val_node, fname))
                        if fname and fname[0].isupper():
                            components.add(fname)

        elif t == "call_expression":
            fn_node = node.child_by_field_name("function")
            if fn_node is not None:
                txt = _node_text(src, fn_node)
                if txt.startswith("use") and len(txt) > 3 and txt[3].isupper():
                    hooks.add(txt)

        for ch in node.children:
            walk(ch, current_class)

    walk(root, None)
    return FileAST(
        relative_path=relative_path,
        language=language,
        classes=classes,
        functions=functions,
        imports=imports,
        components=sorted(components),
        hooks=sorted(hooks),
        ts_interfaces=ts_interfaces,
    )


# ----------------------------------------------------------------------
# API surface extraction from parsed ASTs
# ----------------------------------------------------------------------

def extract_api_endpoints_from_asts(
    asts: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """Scan already-parsed FileAST dicts to find REST endpoints and DTO classes.

    Returns (endpoints, dtos) where each item is a plain dict ready for JSON
    serialisation and LLM enrichment.
    """
    endpoints: list[dict] = []
    dtos: list[dict] = []

    for path, ast_dict in asts.items():
        lang = ast_dict.get("language", "")
        if lang == "java":
            ep, dt = _java_endpoints_and_dtos(path, ast_dict)
        else:
            ep = _js_endpoints(path, ast_dict)
            dt = _ts_dtos(path, ast_dict)
        endpoints.extend(ep)
        dtos.extend(dt)

    return endpoints, dtos


def _ann_name_set(annotations: list[dict]) -> set[str]:
    return {a.get("name", "") for a in annotations}


def _ann_value(annotations: list[dict], name: str) -> str | None:
    for a in annotations:
        if a.get("name") == name:
            return a.get("value")
    return None


def _java_endpoints_and_dtos(
    path: str, ast_dict: dict
) -> tuple[list[dict], list[dict]]:
    endpoints: list[dict] = []
    dtos: list[dict] = []

    for cls in ast_dict.get("classes", []):
        cls_ann_names = _ann_name_set(cls.get("annotations", []))

        # Base path from class-level @RequestMapping
        base_path = _ann_value(cls.get("annotations", []), "RequestMapping") or ""

        is_controller = bool(cls_ann_names & _CONTROLLER_ANNOTATIONS) or bool(
            cls_ann_names & {"RequestMapping"}
        )
        is_dto = bool(cls_ann_names & _DTO_ANNOTATIONS) or any(
            cls.get("name", "").endswith(s) for s in _DTO_NAME_SUFFIXES
        )

        if is_controller:
            for method in cls.get("methods", []):
                m_ann_names = _ann_name_set(method.get("annotations", []))
                http_verb = None
                sub_path = ""

                for ann_name, verb in _HTTP_MAPPING_ANNOTATIONS.items():
                    if ann_name in m_ann_names:
                        http_verb = verb
                        sub_path = _ann_value(method.get("annotations", []), ann_name) or ""
                        break
                if http_verb is None and "RequestMapping" in m_ann_names:
                    http_verb = "ANY"
                    sub_path = _ann_value(method.get("annotations", []), "RequestMapping") or ""

                if http_verb is None:
                    continue

                full_path = (
                    base_path.rstrip("/") + "/" + sub_path.lstrip("/")
                ).rstrip("/") or "/"

                auth: list[str] = []
                for ann in method.get("annotations", []):
                    if ann.get("name") in _AUTH_ANNOTATIONS and ann.get("value"):
                        auth.append(ann["value"])

                request_body_type: str | None = None
                path_variables: list[str] = []
                request_params: list[str] = []
                for param in method.get("parameters", []):
                    p_ann_names = _ann_name_set(param.get("annotations", []))
                    if "RequestBody" in p_ann_names:
                        request_body_type = param.get("type")
                    if "PathVariable" in p_ann_names:
                        path_variables.append(param.get("name", ""))
                    if "RequestParam" in p_ann_names:
                        request_params.append(param.get("name", ""))

                endpoints.append({
                    "http_method": http_verb,
                    "path": full_path,
                    "handler_class": cls.get("name"),
                    "handler_method": method.get("name"),
                    "file": path,
                    "line": method.get("start_line", 0),
                    "auth": auth,
                    "request_body_type": request_body_type,
                    "path_variables": path_variables,
                    "request_params": request_params,
                    "return_type": method.get("return_type"),
                })

        if is_dto:
            # Prefer explicit fields; fall back to getter-inferred fields
            raw_fields = cls.get("fields", [])
            if raw_fields:
                fields = [
                    {
                        "name": f["name"],
                        "type": f["type"],
                        "required": not any(
                            a.get("name") in ("Nullable", "Null") for a in f.get("annotations", [])
                        ),
                        "validation": [
                            a["name"]
                            for a in f.get("annotations", [])
                            if a.get("name") not in ("Column", "JoinColumn", "Id")
                        ],
                    }
                    for f in raw_fields
                ]
            else:
                fields = []
                for m in cls.get("methods", []):
                    mname = m.get("name", "")
                    if mname.startswith("get") and len(mname) > 3 and m.get("return_type"):
                        field_name = mname[3].lower() + mname[4:]
                        fields.append({
                            "name": field_name,
                            "type": m.get("return_type", "Object"),
                            "required": True,
                            "validation": [],
                        })

            dtos.append({
                "name": cls.get("name"),
                "file": path,
                "line": cls.get("start_line", 0),
                "fields": fields,
                "annotations": [a.get("name") for a in cls.get("annotations", [])],
                "used_as_request_body": False,
                "used_as_response_body": False,
                "ts_interface": False,
            })

    return endpoints, dtos


def _js_endpoints(path: str, ast_dict: dict) -> list[dict]:
    """Detect Next.js API route exports and Express router handlers."""
    endpoints: list[dict] = []
    is_api_route = "pages/api/" in path or "app/api/" in path or "/api/" in path
    if not is_api_route:
        return endpoints

    route_path = (
        path
        .replace("pages/api", "/api")
        .replace("app/api", "/api")
        .replace("[", "{").replace("]", "}")
        .replace(".ts", "").replace(".js", "").replace(".tsx", "").replace(".jsx", "")
    )
    # Remove leading src/ or similar
    for prefix in ("src/", "app/"):
        if route_path.startswith(prefix):
            route_path = route_path[len(prefix) - 1:]
            break

    for fn in ast_dict.get("functions", []):
        fname = fn.get("name", "")
        if fname in ("handler", "default"):
            endpoints.append({
                "http_method": "ANY",
                "path": route_path,
                "handler_class": None,
                "handler_method": fname,
                "file": path,
                "line": fn.get("start_line", 0),
                "auth": [],
                "request_body_type": None,
                "path_variables": [],
                "request_params": [],
                "return_type": None,
            })
        elif fname in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            endpoints.append({
                "http_method": fname,
                "path": route_path,
                "handler_class": None,
                "handler_method": fname,
                "file": path,
                "line": fn.get("start_line", 0),
                "auth": [],
                "request_body_type": None,
                "path_variables": [],
                "request_params": [],
                "return_type": None,
            })
    return endpoints


def _ts_dtos(path: str, ast_dict: dict) -> list[dict]:
    """Detect TypeScript interfaces and type aliases that look like DTOs."""
    dtos: list[dict] = []
    for iface in ast_dict.get("ts_interfaces", []):
        name = iface.get("name", "")
        if not any(name.endswith(s) for s in _DTO_NAME_SUFFIXES) and not name[0].isupper():
            continue
        dtos.append({
            "name": name,
            "file": path,
            "line": iface.get("line", 0),
            "fields": iface.get("fields", []),
            "annotations": [],
            "used_as_request_body": False,
            "used_as_response_body": False,
            "ts_interface": True,
        })
    return dtos


def extract_batch_jobs_from_asts(asts: dict[str, dict]) -> list[dict]:
    """Scan parsed FileAST dicts to detect scheduled tasks, Spring Batch jobs, and Quartz jobs.

    Returns a flat list of detected batch-job dicts ready for LLM enrichment.
    """
    jobs: list[dict] = []
    for path, ast_dict in asts.items():
        lang = ast_dict.get("language", "")
        if lang == "java":
            jobs.extend(_java_batch_jobs(path, ast_dict))
        elif lang in ("typescript", "javascript", "tsx", "jsx"):
            jobs.extend(_js_batch_jobs(path, ast_dict))
    return jobs


def _java_batch_jobs(path: str, ast_dict: dict) -> list[dict]:
    jobs: list[dict] = []
    for cls in ast_dict.get("classes", []):
        cls_ann_names = _ann_name_set(cls.get("annotations", []))
        cls_name = cls.get("name", "")

        # Spring @Scheduled — scan every method for the annotation
        for method in cls.get("methods", []):
            m_ann_names = _ann_name_set(method.get("annotations", []))
            if "Scheduled" in m_ann_names:
                cron_val = _ann_value(method.get("annotations", []), "Scheduled")
                jobs.append({
                    "kind": "scheduled_task",
                    "framework": "Spring @Scheduled",
                    "name": f"{cls_name}.{method.get('name', '')}",
                    "handler_class": cls_name,
                    "handler_method": method.get("name"),
                    "file": path,
                    "line": method.get("start_line", 0),
                    "schedule": cron_val or "see @Scheduled annotation",
                    "trigger_type": _infer_trigger_type(cron_val),
                    "description": None,
                    "data_read": None,
                    "data_write": None,
                    "error_handling": None,
                })

        # Spring Batch — class implements Tasklet / ItemReader / ItemProcessor / ItemWriter
        implemented = _infer_implements(cls, ast_dict)
        batch_role = implemented & _SPRING_BATCH_INTERFACES
        if batch_role:
            jobs.append({
                "kind": "spring_batch_component",
                "framework": "Spring Batch",
                "name": cls_name,
                "handler_class": cls_name,
                "handler_method": "execute" if "Tasklet" in batch_role else "read/process/write",
                "file": path,
                "line": cls.get("start_line", 0),
                "schedule": "driven by Job definition",
                "trigger_type": "job_step",
                "role": sorted(batch_role),
                "description": None,
                "data_read": None,
                "data_write": None,
                "error_handling": None,
            })

        # Quartz Job — class implements Job with @DisallowConcurrentExecution or has execute(JobExecutionContext)
        if "DisallowConcurrentExecution" in cls_ann_names or _has_quartz_execute(cls):
            jobs.append({
                "kind": "quartz_job",
                "framework": "Quartz",
                "name": cls_name,
                "handler_class": cls_name,
                "handler_method": "execute",
                "file": path,
                "line": cls.get("start_line", 0),
                "schedule": "configured in Quartz scheduler or trigger bean",
                "trigger_type": "quartz_trigger",
                "description": None,
                "data_read": None,
                "data_write": None,
                "error_handling": None,
            })

        # CommandLineRunner / ApplicationRunner — one-shot startup jobs
        runner_role = implemented & _BATCH_RUNNER_INTERFACES
        if runner_role:
            jobs.append({
                "kind": "startup_runner",
                "framework": "Spring Boot",
                "name": cls_name,
                "handler_class": cls_name,
                "handler_method": "run",
                "file": path,
                "line": cls.get("start_line", 0),
                "schedule": "application startup",
                "trigger_type": "startup",
                "role": sorted(runner_role),
                "description": None,
                "data_read": None,
                "data_write": None,
                "error_handling": None,
            })

    return jobs


def _infer_trigger_type(schedule: str | None) -> str:
    if not schedule:
        return "unknown"
    s = schedule.strip()
    if s.startswith("0 ") or len(s.split()) >= 5:
        return "cron"
    if "fixedDelay" in s or "fixedRate" in s or s.isdigit():
        return "fixed_rate"
    return "cron"


def _infer_implements(cls: dict, ast_dict: dict) -> set[str]:
    """Heuristic: infer which interfaces a class implements from imports + class name."""
    implemented: set[str] = set()
    all_imports = " ".join(ast_dict.get("imports", []))
    for iface in _SPRING_BATCH_INTERFACES | _BATCH_RUNNER_INTERFACES:
        if iface in all_imports or iface in cls.get("name", ""):
            implemented.add(iface)
    return implemented


def _has_quartz_execute(cls: dict) -> bool:
    for method in cls.get("methods", []):
        if method.get("name") == "execute":
            params = method.get("parameters", [])
            for p in params:
                if "JobExecutionContext" in (p.get("type") or ""):
                    return True
    return False


def _js_batch_jobs(path: str, ast_dict: dict) -> list[dict]:
    """Detect cron/scheduled jobs in Node.js files via library import patterns."""
    jobs: list[dict] = []
    imports = " ".join(ast_dict.get("imports", []))
    # Check if any known cron library is imported
    detected_lib = next((lib for lib in _JS_CRON_LIBS if lib in imports), None)
    if not detected_lib:
        return jobs

    # Look for functions that are likely job handlers
    for fn in ast_dict.get("functions", []):
        fname = fn.get("name", "")
        # Heuristic: functions whose name includes job/task/process/sync/batch/cron/refresh/cleanup/report
        keywords = ("job", "task", "process", "sync", "batch", "cron", "refresh", "cleanup", "report", "export", "import", "send", "notify", "aggregate")
        if any(kw in fname.lower() for kw in keywords):
            jobs.append({
                "kind": "node_scheduled_task",
                "framework": detected_lib,
                "name": fname,
                "handler_class": None,
                "handler_method": fname,
                "file": path,
                "line": fn.get("start_line", 0),
                "schedule": "see cron schedule in caller",
                "trigger_type": "cron",
                "description": None,
                "data_read": None,
                "data_write": None,
                "error_handling": None,
            })
    return jobs


def all_method_names(ast: FileAST) -> list[str]:
    """Flat list of every method/function for coverage tracking."""
    names = [f.name for f in ast.functions]
    for cls in ast.classes:
        for m in cls.methods:
            names.append(f"{cls.name}.{m.name}")
    return names
