"""Lightweight framework route/endpoint detection — regex over already-indexed
source text, not a graphify capability. Answers "what's the API surface of
this repo?" in one call, without an agent grepping for a dozen different
decorator shapes across languages. Heuristic by design (a `@app.route` behind
a runtime-computed decorator, or routes registered via a data-driven table,
won't match) — same honesty bar as the rest of graphscout: it's a fast, wide
net, not a guarantee, and every match carries its file:line so an agent can
verify with a Read if it matters.

Two detection modes:
- content patterns (`_PATTERNS`): a regex tried against every already-indexed
  file whose suffix matches, for decorator/call-style routing.
- path conventions (`_FILE_CONVENTIONS`): file-based routers (SvelteKit, Nuxt,
  Vue pages, Astro) where the route *is* the file's location, not a line of
  code — no regex needed, the relative path is the route.

Play (`conf/routes`) and Drupal (`*.routing.yml`, hook_* in
`.module`/`.theme`/`.install`/`.inc`) use file shapes graphify doesn't index
(no code-like extension), so `detect_routes` supplements the caller's already-
indexed file list with a small, name-filtered extra walk (`_extra_route_files`)
scoped to exactly those shapes — not a general re-walk of the repo.
"""
import re
from collections import namedtuple
from pathlib import Path

from . import core

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
    # Express: app.get("/x", ...), router.post("/x", ...) — Node lowercase verbs.
    dict(framework="express", exts={".js", ".jsx", ".mjs", ".ts", ".tsx"},
         method_idx=1, path_idx=2,
         regex=re.compile(r'\b\w+\.(get|post|put|delete|patch|head|options|any|all)\(\s*[\'"]([^\'"]+)[\'"]')),
    # Gin / chi / gorilla / mux (Go): r.GET("/x", h), router.HandleFunc("/x", h)
    dict(framework="gin/chi/gorilla/mux", exts={".go"}, method_idx=1, path_idx=2,
         regex=re.compile(r'\b\w+\.(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|Any|Get|Post|Put|Delete|Patch)\(\s*[\'"]([^\'"]+)[\'"]')),
    dict(framework="gin/chi/gorilla/mux", exts={".go"}, method_idx="ROUTE", path_idx=1,
         regex=re.compile(r'\bHandleFunc\(\s*[\'"]([^\'"]+)[\'"]')),
    # NestJS decorators: @Get("x"), @Post(), @Controller("prefix")
    dict(framework="nestjs", exts={".ts"}, method_idx=1, path_idx=2,
         regex=re.compile(r'@(Get|Post|Put|Delete|Patch|Options|Head|All)\(\s*[\'"]?([^\'")]*)[\'"]?\)')),
    # Spring: @GetMapping("/x"), @RequestMapping(value="/x")
    dict(framework="spring", exts={".java", ".kt"}, method_idx=1, path_idx=2,
         regex=re.compile(r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)'
                           r'\(\s*(?:value\s*=\s*)?[\'"]([^\'"]+)[\'"]')),
    # Play (Scala/Java): GET/POST/... verb routes in a `conf/routes`-shaped file.
    dict(framework="play", exts={".routes"}, method_idx=1, path_idx=2,
         regex=re.compile(r'^\s*(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(\S+)\s+\S', re.MULTILINE)),
    # ASP.NET Core attribute routing: [HttpGet("x")], [Route("x")]
    dict(framework="aspnet", exts={".cs"}, method_idx=1, path_idx=2,
         regex=re.compile(r'\[(Http(?:Get|Post|Put|Delete|Patch)|Route)\(\s*[\'"]([^\'"]*)[\'"]\s*\)\]')),
    # Axum: .route("/x", get(handler)) — path first, verb second.
    dict(framework="axum/actix/rocket", exts={".rs"}, method_idx=2, path_idx=1,
         regex=re.compile(r'\.route\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*(get|post|put|delete|patch)\(')),
    # Actix-web / Rocket (Rust) — identical attribute-macro shape: #[get("/x")]
    dict(framework="axum/actix/rocket", exts={".rs"}, method_idx=1, path_idx=2,
         regex=re.compile(r'#\[(get|post|put|delete|patch)\(\s*[\'"]([^\'"]+)[\'"]')),
    # Ruby on Rails routes.rb: get "x", to: "..."
    dict(framework="rails", exts={".rb"}, method_idx=1, path_idx=2,
         regex=re.compile(r'^\s*(get|post|put|patch|delete)\s+[\'"]([^\'"]+)[\'"]', re.MULTILINE)),
    # Laravel: Route::get('/x', ...)
    dict(framework="laravel", exts={".php"}, method_idx=1, path_idx=2,
         regex=re.compile(r'Route::(get|post|put|delete|patch|any)\(\s*[\'"]([^\'"]+)[\'"]')),
    # Vapor (Swift): app.get("x", use: handler)
    dict(framework="vapor", exts={".swift"}, method_idx=1, path_idx=2,
         regex=re.compile(r'\bapp\.(get|post|put|delete|patch)\(\s*[\'"]([^\'"]+)[\'"]')),
    # React Router: <Route path="/x" ...>
    dict(framework="react-router", exts={".jsx", ".tsx"}, method_idx="ROUTE", path_idx=1,
         regex=re.compile(r'<Route\s+[^>]*\bpath=[\'"]([^\'"]+)[\'"]')),
    # Drupal *.routing.yml: `  path: '/my/path'` under a route-id key.
    dict(framework="drupal", exts={".routing.yml"}, method_idx="ROUTE", path_idx=1,
         regex=re.compile(r'^\s*path:\s*[\'"]?([^\'"\n]+)[\'"]?\s*$', re.MULTILINE)),
    # Drupal hook_* implementations in .module/.theme/.install/.inc.
    dict(framework="drupal", exts={".module", ".theme", ".install", ".inc"},
         method_idx="ROUTE", path_idx=1,
         regex=re.compile(r'^function\s+(\w+_(?:menu|permission|help|form_alter|preprocess_\w+|'
                           r'theme|install|uninstall|schema|cron|mail))\s*\(', re.MULTILINE)),
]

# File-based routers: the route *is* the path, no line to point at (line=1).
# `strip` is a regex whose match is dropped from the front of the relative
# path before it's shown as the route; bracket params ([id], [...rest]) are
# kept verbatim, matching each framework's own file-based routing syntax.
_FILE_CONVENTIONS = [
    dict(framework="vue-router/nuxt",
         regex=re.compile(r'^(?:src/)?pages/(.+)\.(vue|tsx?|jsx?)$')),
    dict(framework="vue-router/nuxt",
         regex=re.compile(r'^server/api/(.+)\.(ts|js)$'), prefix="/api/"),
    dict(framework="sveltekit",
         regex=re.compile(r'^(?:src/)?routes/(.*)/\+(?:(?:page|layout)(?:\.server)?\.(?:svelte|ts|js)|'
                           r'server\.(?:ts|js))$')),
    dict(framework="astro",
         regex=re.compile(r'^(?:src/)?pages/(.+)\.(astro|ts|js)$')),
]


def _method_of(m, spec):
    idx = spec["method_idx"]
    return "ROUTE" if idx == "ROUTE" else m.group(idx).upper()


def _extra_route_files(root: Path):
    """Files Play/Drupal routing needs that graphify (and so `code_files`)
    doesn't index at all — no code-like extension. A small, name-filtered
    walk (not a general re-walk), respecting the same .gitignore-or-tracked
    source `code_files` uses."""
    tracked = core._git_tracked(root)
    base = tracked if tracked is not None else core._walk_all(root)
    out = []
    for rel in base:
        if core._hits_skip_dirs(rel):
            continue
        name = Path(rel).name
        if name.endswith(".routing.yml") or name in (".module", ".theme", ".install", ".inc") \
                or Path(rel).suffix in (".module", ".theme", ".install", ".inc") \
                or (name == "routes" and Path(rel).parent.name == "conf"):
            out.append(rel)
    return out


def detect_routes(root: Path, source_files) -> list:
    """source_files: iterable of root-relative paths (already filtered by the
    caller's build — respects .gitignore/exclude/include, so this never reads
    outside what the graph itself indexes). Supplemented with a small extra
    walk for the handful of non-code-extension shapes (Play/Drupal) that
    `source_files` wouldn't otherwise include."""
    out = []
    by_ext = {}
    for spec in _PATTERNS:
        for ext in spec["exts"]:
            by_ext.setdefault(ext, []).append(spec)

    all_files = list(source_files)
    seen_files = set(all_files)
    for rel in _extra_route_files(root):
        if rel not in seen_files:
            all_files.append(rel)
            seen_files.add(rel)

    for rel in all_files:
        name = Path(rel).name
        suffix = ".routing.yml" if name.endswith(".routing.yml") else Path(rel).suffix
        if suffix == "" and name == "routes":
            suffix = ".routes"
        specs = by_ext.get(suffix)
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

    for rel in all_files:
        posix = rel.replace("\\", "/")
        for spec in _FILE_CONVENTIONS:
            m = spec["regex"].match(posix)
            if not m:
                continue
            route_path = spec.get("prefix", "/") + m.group(1)
            out.append(Route("ROUTE", route_path, spec["framework"], rel, 1))
            break

    out.sort(key=lambda r: (r.path, r.file, r.line))
    return out


def format_routes(routes: list) -> str:
    if not routes:
        return ("no routes detected — either this repo has none of the supported "
                "frameworks (Flask/FastAPI/Django/Express/Gin-chi-gorilla-mux/NestJS/"
                "Spring/Play/ASP.NET/Axum-actix-Rocket/Rails/Laravel/Vapor/React "
                "Router/Vue Router-Nuxt/SvelteKit/Astro/Drupal), or routes are "
                "registered via a pattern this heuristic doesn't match (data-driven "
                "tables, runtime decorators)")
    width = max(len(r.method) for r in routes)
    lines = [f"{len(routes)} route(s) detected:"]
    lines += [f"  {r.method:<{width}}  {r.path:<40}  [{r.framework}]  {r.file}:{r.line}" for r in routes]
    return "\n".join(lines)
