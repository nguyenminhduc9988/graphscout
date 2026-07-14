"""Lightweight framework route/endpoint detection — regex over already-indexed
source text, not a graphify capability. Answers "what's the API surface of
this repo?" in one call, without an agent grepping for a dozen different
decorator shapes across languages. Heuristic by design (a `@app.route` behind
a runtime-computed decorator, or routes registered via a data-driven table,
won't match) — same honesty bar as the rest of graphscout: it's a fast, wide
net, not a guarantee, and every match carries its file:line so an agent can
verify with a Read if it matters.
"""
import re
from collections import namedtuple
from pathlib import Path

Route = namedtuple("Route", "method path framework file line")

# Each pattern is tried against every file whose suffix is in `exts`.
# `method_idx`/`path_idx` are 1-based regex group indices; a literal string
# instead of an int means "use this fixed method" (bare `@app.route` with no
# verb, or Django's path() which encodes no HTTP method at all).
_PATTERNS = [
    # Flask / FastAPI / Bottle-style: @app.get("/x"), @router.route("/x")
    dict(framework="flask/fastapi", exts={".py"}, method_idx=1, path_idx=2,
         regex=re.compile(r'@\w+\.(route|get|post|put|delete|patch|head|options)\(\s*[rf]?[\'"]([^\'"]+)[\'"]')),
    # Django urls.py: path("x/", view), re_path(r"^x/$", view)
    dict(framework="django", exts={".py"}, method_idx="ROUTE", path_idx=1,
         regex=re.compile(r'\b(?:path|re_path|url)\(\s*r?[\'"]([^\'"]*)[\'"]')),
    # Express / Koa / Gin / generic `<obj>.<verb>("/x", ...)` call style —
    # covers Node lowercase verbs and Go's Gin/Echo uppercase verbs.
    dict(framework="express/gin", exts={".js", ".jsx", ".mjs", ".ts", ".tsx", ".go"},
         method_idx=1, path_idx=2,
         regex=re.compile(r'\b\w+\.(get|post|put|delete|patch|head|options|any|all|'
                           r'GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|Any)\(\s*[\'"]([^\'"]+)[\'"]')),
    # NestJS decorators: @Get("x"), @Post(), @Controller("prefix")
    dict(framework="nestjs", exts={".ts"}, method_idx=1, path_idx=2,
         regex=re.compile(r'@(Get|Post|Put|Delete|Patch|Options|Head|All)\(\s*[\'"]?([^\'")]*)[\'"]?\)')),
    # Spring: @GetMapping("/x"), @RequestMapping(value="/x")
    dict(framework="spring", exts={".java", ".kt"}, method_idx=1, path_idx=2,
         regex=re.compile(r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)'
                           r'\(\s*(?:value\s*=\s*)?[\'"]([^\'"]+)[\'"]')),
    # ASP.NET Core attribute routing: [HttpGet("x")], [Route("x")]
    dict(framework="aspnet", exts={".cs"}, method_idx=1, path_idx=2,
         regex=re.compile(r'\[(Http(?:Get|Post|Put|Delete|Patch)|Route)\(\s*[\'"]([^\'"]*)[\'"]\s*\)\]')),
    # Actix-web (Rust): #[get("/x")]
    dict(framework="actix", exts={".rs"}, method_idx=1, path_idx=2,
         regex=re.compile(r'#\[(get|post|put|delete|patch)\(\s*[\'"]([^\'"]+)[\'"]')),
    # Ruby on Rails routes.rb: get "x", to: "..."
    dict(framework="rails", exts={".rb"}, method_idx=1, path_idx=2,
         regex=re.compile(r'^\s*(get|post|put|patch|delete)\s+[\'"]([^\'"]+)[\'"]', re.MULTILINE)),
    # Laravel: Route::get('/x', ...)
    dict(framework="laravel", exts={".php"}, method_idx=1, path_idx=2,
         regex=re.compile(r'Route::(get|post|put|delete|patch|any)\(\s*[\'"]([^\'"]+)[\'"]')),
]


def _method_of(m, spec):
    idx = spec["method_idx"]
    return "ROUTE" if idx == "ROUTE" else m.group(idx).upper()


def detect_routes(root: Path, source_files) -> list:
    """source_files: iterable of root-relative paths (already filtered by the
    caller's build — respects .gitignore/exclude/include, so this never reads
    outside what the graph itself indexes)."""
    out = []
    by_ext = {}
    for spec in _PATTERNS:
        for ext in spec["exts"]:
            by_ext.setdefault(ext, []).append(spec)

    for rel in source_files:
        specs = by_ext.get(Path(rel).suffix)
        if not specs:
            continue
        try:
            text = (root / rel).read_text(errors="replace")
        except OSError:
            continue
        for spec in specs:
            for m in spec["regex"].finditer(text):
                path = m.group(spec["path_idx"]).strip()
                if not path:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                out.append(Route(_method_of(m, spec), path, spec["framework"], rel, line))
    out.sort(key=lambda r: (r.path, r.file, r.line))
    return out


def format_routes(routes: list) -> str:
    if not routes:
        return ("no routes detected — either this repo has none of the supported "
                "frameworks (Flask/FastAPI/Django/Express/Gin/NestJS/Spring/ASP.NET/"
                "Actix/Rails/Laravel), or routes are registered via a pattern this "
                "heuristic doesn't match (data-driven tables, runtime decorators)")
    width = max(len(r.method) for r in routes)
    lines = [f"{len(routes)} route(s) detected:"]
    lines += [f"  {r.method:<{width}}  {r.path:<40}  [{r.framework}]  {r.file}:{r.line}" for r in routes]
    return "\n".join(lines)
