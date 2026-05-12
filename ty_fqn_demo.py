#!/usr/bin/env python3
"""
ty_fqn_demo.py – query the custom ty/typeDefinitionName LSP endpoint.

For every non-keyword name token in each Python snippet the script:
  1. starts the ty language server (built from this repo),
  2. sends a ty/typeDefinitionName request for that cursor position, and
  3. prints the fully qualified type name (FQN) that ty infers.

Usage
-----
    # Build ty first, then run:
    python3 ty_fqn_demo.py --build

    # Run all built-in snippets (uses existing binary):
    python3 ty_fqn_demo.py

    # Run only specific snippet(s) by title substring (case-insensitive):
    python3 ty_fqn_demo.py --filter numpy
    python3 ty_fqn_demo.py --filter flask

    # Supply your own one-off snippet:
    python3 ty_fqn_demo.py --snippet "import os; p = os.getcwd()"

    # Extra verbosity (show each LSP round-trip):
    python3 ty_fqn_demo.py -v

Setup (third-party snippets)
-----------------------------
Create a .venv in the repo root and install libraries:

    uv venv && uv pip install \\
        requests types-requests \\
        httpx \\
        pydantic \\
        attrs \\
        numpy \\
        pandas pandas-stubs \\
        flask types-flask \\
        fastapi \\
        click \\
        rich \\
        sqlalchemy
"""

from __future__ import annotations

import argparse
import io
import json
import keyword
import os
import subprocess
import sys
import textwrap
import threading
import time
import tokenize as _tok_mod
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
TY_BINARY = SCRIPT_DIR / "target" / "debug" / "ty"

# ── Snippets ──────────────────────────────────────────────────────────────────

SNIPPETS: list[tuple[str, str]] = [
    # ── Standard library ─────────────────────────────────────────────────────
    (
        "stdlib · pathlib + re",
        textwrap.dedent("""\
            import re
            from pathlib import Path

            p = Path("/usr/local/lib/python3.12")
            name = p.name
            parts = p.parts
            parent = p.parent
            suffix = p.suffix
            stem = p.stem

            pattern = re.compile(r"\\d+\\.\\d+")
            match = pattern.search(str(p))
            if match:
                version = match.group(0)
                span = match.span()
        """),
    ),
    (
        "stdlib · datetime + collections",
        textwrap.dedent("""\
            from datetime import datetime, timedelta, timezone
            from collections import defaultdict, Counter, OrderedDict

            utc = timezone.utc
            now = datetime.now(tz=utc)
            week_later = now + timedelta(weeks=1)
            delta = week_later - now

            counts: Counter[str] = Counter(["apple", "banana", "apple"])
            most_common = counts.most_common(2)

            groups: defaultdict[str, list[int]] = defaultdict(list)
            groups["evens"].extend([2, 4, 6])

            ordered: OrderedDict[str, int] = OrderedDict()
            ordered["first"] = 1
        """),
    ),
    (
        "stdlib · itertools + functools",
        textwrap.dedent("""\
            import itertools
            import functools
            from typing import Callable

            nums = list(range(10))
            evens = list(itertools.islice(filter(lambda n: n % 2 == 0, nums), 3))
            chained = list(itertools.chain([1, 2], [3, 4], [5]))
            product = list(itertools.product("AB", repeat=2))

            doubled: Callable[[int], int] = functools.partial(lambda x, n: x * n, n=2)
            result = functools.reduce(lambda a, b: a + b, nums)

            @functools.lru_cache(maxsize=128)
            def fib(n: int) -> int:
                return n if n < 2 else fib(n - 1) + fib(n - 2)

            val = fib(10)
            info = fib.cache_info()
        """),
    ),
    # ── HTTP ─────────────────────────────────────────────────────────────────
    (
        "requests · Session + Response",
        textwrap.dedent("""\
            import requests
            from requests import Response, Session, PreparedRequest

            session = Session()
            session.headers.update({"User-Agent": "ty-demo/1.0"})

            response: Response = session.get(
                "https://httpbin.org/json",
                params={"foo": "bar"},
                timeout=5,
            )
            status = response.status_code
            body = response.text
            data = response.json()
            headers = response.headers
            elapsed = response.elapsed
            history = response.history

            prepped: PreparedRequest = response.request
            url = prepped.url
        """),
    ),
    (
        "httpx · Client + streaming",
        textwrap.dedent("""\
            import httpx
            from httpx import Client, Response, URL, Headers

            client = Client(
                base_url="https://httpbin.org",
                timeout=httpx.Timeout(10.0),
                follow_redirects=True,
            )

            response: Response = client.get("/get", params={"key": "val"})
            status = response.status_code
            text = response.text
            data = response.json()
            url: URL = response.url
            headers: Headers = response.headers
            encoding = response.encoding

            with client.stream("GET", "/stream/5") as r:
                for chunk in r.iter_text():
                    pass
        """),
    ),
    # ── Data validation ───────────────────────────────────────────────────────
    (
        "pydantic · BaseModel + Field + validators",
        textwrap.dedent("""\
            from typing import Optional, Annotated
            from pydantic import BaseModel, Field, field_validator, model_validator

            ZipCode = Annotated[str, Field(pattern=r"\\d{5}")]

            class Address(BaseModel):
                street: str
                city: str
                zip_code: ZipCode

            class User(BaseModel):
                name: str
                age: int = Field(ge=0, le=150)
                email: Optional[str] = None
                address: Address

                @field_validator("name")
                @classmethod
                def name_not_empty(cls, v: str) -> str:
                    if not v.strip():
                        raise ValueError("name must not be blank")
                    return v.strip()

            addr = Address(street="123 Main St", city="Springfield", zip_code="12345")
            user = User(name="Alice", age=30, address=addr)
            dumped = user.model_dump()
            serialized = user.model_dump_json()
            schema = User.model_json_schema()
            copied = user.model_copy(update={"age": 31})
        """),
    ),
    (
        "attrs · define + validators + evolve",
        textwrap.dedent("""\
            import attrs
            from attrs import define, field, Factory

            @define
            class Vector:
                x: float
                y: float
                z: float = field(default=0.0)
                tags: list[str] = Factory(list)

            @define
            class BoundingBox:
                min_corner: Vector
                max_corner: Vector

                @property
                def diagonal(self) -> float:
                    dx = self.max_corner.x - self.min_corner.x
                    dy = self.max_corner.y - self.min_corner.y
                    return (dx**2 + dy**2) ** 0.5

            v = Vector(x=1.0, y=2.0, z=3.0, tags=["origin"])
            moved = attrs.evolve(v, x=5.0)
            as_dict = attrs.asdict(v)
            as_tuple = attrs.astuple(v)
            fields = attrs.fields(Vector)
        """),
    ),
    # ── Data science ──────────────────────────────────────────────────────────
    (
        "numpy · ndarray arithmetic + linalg",
        textwrap.dedent("""\
            import numpy as np
            from numpy import ndarray

            a: ndarray = np.array([[1.0, 2.0], [3.0, 4.0]])
            b: ndarray = np.eye(2)

            product = np.dot(a, b)
            inv = np.linalg.inv(a)
            det = np.linalg.det(a)
            eigenvalues, eigenvectors = np.linalg.eig(a)

            flat = a.flatten()
            transposed = a.T
            mean_val = a.mean(axis=0)
            std_val = a.std()

            zeros = np.zeros((3, 3), dtype=np.float64)
            linspace = np.linspace(0.0, 1.0, 50)
            stacked = np.vstack([a, b])
        """),
    ),
    (
        "pandas · DataFrame groupby + merge",
        textwrap.dedent("""\
            import pandas as pd
            from pandas import DataFrame, Series, Index

            employees: DataFrame = pd.DataFrame({
                "name": ["Alice", "Bob", "Carol", "Dave"],
                "dept": ["eng", "mkt", "eng", "mkt"],
                "salary": [120_000, 90_000, 115_000, 95_000],
                "years": [5, 3, 7, 2],
            })

            avg_by_dept: Series = employees.groupby("dept")["salary"].mean()
            top_earners: DataFrame = employees.nlargest(2, "salary")
            sorted_df: DataFrame = employees.sort_values("salary", ascending=False)

            budgets: DataFrame = pd.DataFrame({
                "dept": ["eng", "mkt"],
                "budget": [500_000, 200_000],
            })

            merged: DataFrame = employees.merge(budgets, on="dept", how="left")
            pivoted = employees.pivot_table(
                values="salary", index="dept", aggfunc="mean"
            )
            idx: Index = employees.index
        """),
    ),
    # ── Web frameworks ────────────────────────────────────────────────────────
    (
        "flask · blueprints + request context",
        textwrap.dedent("""\
            from flask import (
                Flask, Blueprint, request, jsonify,
                abort, g, current_app, Response,
            )
            from typing import Any

            api = Blueprint("api", __name__, url_prefix="/api")

            @api.before_request
            def authenticate() -> None:
                token = request.headers.get("Authorization")
                if not token:
                    abort(401)
                g.token = token

            @api.route("/items", methods=["GET"])
            def list_items() -> Response:
                page = request.args.get("page", 1, type=int)
                per_page = request.args.get("per_page", 20, type=int)
                return jsonify({"page": page, "per_page": per_page, "items": []})

            @api.route("/items/<int:item_id>", methods=["GET", "DELETE"])
            def item_detail(item_id: int) -> Response | tuple[Response, int]:
                if request.method == "DELETE":
                    return jsonify({}), 204
                return jsonify({"id": item_id})

            app = Flask(__name__)
            app.register_blueprint(api)
        """),
    ),
    (
        "fastapi · dependency injection + Pydantic schemas",
        textwrap.dedent("""\
            from typing import Annotated, Optional
            from fastapi import FastAPI, Query, Path, Depends, HTTPException, status
            from fastapi.responses import JSONResponse
            from pydantic import BaseModel, Field

            app = FastAPI(title="Inventory API", version="1.0.0")

            class ItemCreate(BaseModel):
                name: str = Field(min_length=1, max_length=100)
                price: float = Field(gt=0)
                quantity: int = Field(ge=0, default=0)

            class ItemRead(ItemCreate):
                id: int

            def get_db() -> dict:
                return {}

            DB = Annotated[dict, Depends(get_db)]

            @app.get("/items/", response_model=list[ItemRead])
            async def list_items(
                db: DB,
                skip: int = Query(default=0, ge=0),
                limit: int = Query(default=10, ge=1, le=100),
            ) -> list[ItemRead]:
                return []

            @app.post("/items/", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
            async def create_item(item: ItemCreate, db: DB) -> ItemRead:
                raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)

            @app.get("/items/{item_id}", response_model=ItemRead)
            async def get_item(item_id: int = Path(ge=1), db: DB = Depends(get_db)) -> ItemRead:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        """),
    ),
    # ── CLI ───────────────────────────────────────────────────────────────────
    (
        "click · group + subcommands + pass_context",
        textwrap.dedent("""\
            import click
            from click import Context, Path as ClickPath

            @click.group()
            @click.option("--verbose", "-v", is_flag=True, default=False)
            @click.option(
                "--config",
                type=ClickPath(exists=False, dir_okay=False),
                default="config.toml",
            )
            @click.pass_context
            def cli(ctx: Context, verbose: bool, config: str) -> None:
                ctx.ensure_object(dict)
                ctx.obj["verbose"] = verbose
                ctx.obj["config"] = config

            @cli.command()
            @click.argument("src", nargs=-1, required=True)
            @click.option("--output", "-o", required=True)
            @click.pass_context
            def convert(ctx: Context, src: tuple[str, ...], output: str) -> None:
                if ctx.obj["verbose"]:
                    click.echo(f"Converting {len(src)} file(s) → {output}")

            @cli.command()
            @click.pass_context
            def info(ctx: Context) -> None:
                cfg = ctx.obj["config"]
                click.echo(f"Config: {cfg}")
        """),
    ),
    # ── Console / rich ────────────────────────────────────────────────────────
    (
        "rich · Console, Table, Progress",
        textwrap.dedent("""\
            from rich.console import Console
            from rich.table import Table, Column
            from rich.text import Text
            from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
            from rich.panel import Panel
            from rich import box

            console = Console(highlight=True)

            table = Table(
                title="Employee Salaries",
                box=box.ROUNDED,
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("Name", style="green")
            table.add_column("Department")
            table.add_column("Salary", justify="right", style="yellow")
            table.add_row("Alice", "Engineering", "$120,000")
            table.add_row("Bob", "Marketing", "$90,000")

            console.print(table)

            msg = Text("Processing complete!", style="bold magenta")
            panel = Panel(msg, title="Done", border_style="green")
            console.print(panel)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
            ) as progress:
                task = progress.add_task("Loading…", total=100)
                progress.advance(task, 50)
        """),
    ),
    # ── Database ──────────────────────────────────────────────────────────────
    (
        "sqlalchemy · ORM models + typed queries",
        textwrap.dedent("""\
            from typing import Optional
            from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, select
            from sqlalchemy.orm import (
                DeclarativeBase, Session, relationship,
                Mapped, mapped_column, selectinload,
            )

            class Base(DeclarativeBase):
                pass

            class Department(Base):
                __tablename__ = "departments"
                id: Mapped[int] = mapped_column(Integer, primary_key=True)
                name: Mapped[str] = mapped_column(String(50))
                employees: Mapped[list["Employee"]] = relationship(back_populates="dept")

            class Employee(Base):
                __tablename__ = "employees"
                id: Mapped[int] = mapped_column(Integer, primary_key=True)
                name: Mapped[str] = mapped_column(String(100))
                email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
                dept_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
                dept: Mapped[Department] = relationship(back_populates="employees")

            engine = create_engine("sqlite:///:memory:", echo=False)
            Base.metadata.create_all(engine)

            with Session(engine) as session:
                eng = Department(name="Engineering")
                session.add(eng)
                session.flush()

                alice = Employee(name="Alice", email="alice@example.com", dept=eng)
                session.add(alice)
                session.commit()

                stmt = (
                    select(Employee)
                    .options(selectinload(Employee.dept))
                    .where(Employee.name == "Alice")
                )
                found = session.scalars(stmt).first()
                if found:
                    dept_name = found.dept.name
        """),
    ),
]

# ── LSP wire format ───────────────────────────────────────────────────────────


def _lsp_encode(msg: dict) -> bytes:
    body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode()
    return header + body


def _lsp_read(stream) -> dict | None:
    """
    Read exactly one LSP message from a *binary* buffered stream.
    Returns None on EOF or if no Content-Length header was found.
    """
    headers: dict[str, str] = {}
    while True:
        raw = stream.readline()
        if not raw:
            return None  # EOF
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:  # blank line → end of headers
            break
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()

    length = int(headers.get("content-length", 0))
    if length == 0:
        return None

    body = b""
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            return None
        body += chunk

    return json.loads(body)


# ── Minimal LSP client ────────────────────────────────────────────────────────


class LspClient:
    """
    Thread-safe, subprocess-backed LSP client.

    A background thread continuously reads messages from the server's stdout.
    Responses to our requests are placed into ``_pending``; server-initiated
    requests are auto-acknowledged; server notifications are silently dropped.
    """

    def __init__(self, binary: Path, cwd: Path, verbose: bool = False):
        self._verbose = verbose
        self._proc = subprocess.Popen(
            [str(binary), "server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # captured so logs don't pollute our output
            cwd=str(cwd),
        )
        self._id = 0
        self._pending: dict[int | str, dict] = {}
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._dead = False

        t = threading.Thread(target=self._read_loop, daemon=True, name="lsp-reader")
        t.start()

    # ── internals ────────────────────────────────────────────────────────────

    def _read_loop(self) -> None:
        try:
            while True:
                msg = _lsp_read(self._proc.stdout)
                if msg is None:
                    break

                # Server-initiated request (has both "id" and "method")
                if "id" in msg and "method" in msg:
                    self._handle_server_request(msg)

                # Response to one of our requests (has "id", no "method")
                elif "id" in msg:
                    with self._cond:
                        self._pending[msg["id"]] = msg
                        self._cond.notify_all()

                # Notification (no "id") – silently ignored
        finally:
            with self._cond:
                self._dead = True
                self._cond.notify_all()

    def _handle_server_request(self, msg: dict) -> None:
        """Auto-respond to workspace/configuration and window/workDoneProgress/create."""
        method = msg.get("method", "")
        req_id = msg["id"]

        if method == "workspace/configuration":
            # Return an empty settings object for each requested scope.
            items = msg.get("params", {}).get("items", [])
            self._write({"jsonrpc": "2.0", "id": req_id, "result": [None] * len(items)})
        else:
            # Generic acknowledge
            self._write({"jsonrpc": "2.0", "id": req_id, "result": None})

    def _write(self, msg: dict) -> None:
        encoded = _lsp_encode(msg)
        if self._verbose:
            direction = "→" if "method" in msg else "←"
            method = msg.get("method", f"response#{msg.get('id')}")
            print(f"  [LSP {direction}] {method}", file=sys.stderr)
        self._proc.stdin.write(encoded)
        self._proc.stdin.flush()

    # ── public API ────────────────────────────────────────────────────────────

    def request(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        """Send a JSON-RPC request and block until the response arrives."""
        self._id += 1
        req_id = self._id
        self._write(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        )

        deadline = time.monotonic() + timeout
        with self._cond:
            while req_id not in self._pending and not self._dead:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Timed out waiting for response to '{method}' (id={req_id})"
                    )
                self._cond.wait(timeout=min(remaining, 1.0))
            if req_id not in self._pending:
                raise RuntimeError("Server died before responding to request")
            return self._pending.pop(req_id)

    def notify(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def shutdown(self) -> None:
        try:
            self.request("shutdown", {}, timeout=5.0)
            self.notify("exit", {})
        except Exception:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            self._proc.kill()


# ── Token extraction ──────────────────────────────────────────────────────────

_KEYWORDS = frozenset(keyword.kwlist) | frozenset(getattr(keyword, "softkwlist", []))


def extract_names(source: str) -> list[tuple[str, int, int]]:
    """
    Return [(token_string, line_0indexed, col_0indexed), ...] for every
    non-keyword NAME token in *source*.

    Python's tokenize module uses 1-based line numbers; we convert to the
    0-based values that LSP expects.
    """
    names: list[tuple[str, int, int]] = []
    try:
        for tok in _tok_mod.generate_tokens(io.StringIO(source).readline):
            if tok.type == _tok_mod.NAME and tok.string not in _KEYWORDS:
                line_0 = tok.start[0] - 1  # 1→0 indexed
                col_0 = tok.start[1]  # already 0-indexed
                names.append((tok.string, line_0, col_0))
    except _tok_mod.TokenError:
        pass
    return names


# ── FQN queries (single server, multiple snippets) ────────────────────────────


def run_snippets(
    snippets: list[tuple[str, str]],
    verbose: bool = False,
) -> list[tuple[str, str, list[tuple[str, int, int, list[str]]]]]:
    """
    Start a single ty language-server session and query FQNs for every
    token in every snippet.

    Each snippet gets its own virtual document (never written to disk);
    documents are opened and closed sequentially so the server only holds
    one document in memory at a time.

    Returns a list of (title, snippet, results) triples where
    results = [(name, line_0, col_0, fqns), ...].
    """
    if not TY_BINARY.exists():
        print(
            f"Error: ty binary not found at {TY_BINARY}\n"
            f"Build it with:  cargo build -p ty   (inside {SCRIPT_DIR})",
            file=sys.stderr,
        )
        sys.exit(1)

    has_venv = (SCRIPT_DIR / ".venv").exists()
    workspace_uri = SCRIPT_DIR.as_uri()

    print(f"  binary   : {TY_BINARY}")
    print(f"  workspace: {SCRIPT_DIR}")
    print(
        f"  venv     : {'found ✓' if has_venv else 'NOT FOUND – third-party imports will be unresolved'}"
    )
    print()

    client = LspClient(TY_BINARY, SCRIPT_DIR, verbose=verbose)

    # ── Handshake ─────────────────────────────────────────────────────────
    client.request(
        "initialize",
        {
            "processId": os.getpid(),
            "clientInfo": {"name": "ty-fqn-demo", "version": "0.1"},
            "rootUri": workspace_uri,
            "capabilities": {
                "textDocument": {
                    "hover": {"contentFormat": ["plaintext"]},
                    "synchronization": {"didOpen": True, "didClose": True},
                },
                "workspace": {
                    "configuration": True,
                    "didChangeConfiguration": {"dynamicRegistration": False},
                },
            },
        },
    )
    client.notify("initialized", {})

    all_results: list[tuple[str, str, list[tuple[str, int, int, list[str]]]]] = []
    n_total_snippets = len(snippets)

    for idx, (title, snippet) in enumerate(snippets):
        # Use a unique virtual filename per snippet so ty treats each as a
        # fresh document.  The file is never created on disk.
        virtual_name = f"_snippet_{idx:02d}.py"
        dummy_path = SCRIPT_DIR / virtual_name
        file_uri = dummy_path.as_uri()

        snippet_label = f"[{idx + 1}/{n_total_snippets}] {title}"
        print(f"  {snippet_label} …", end="", flush=True)

        # ── Open document ─────────────────────────────────────────────────
        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": "python",
                    "version": 1,
                    "text": snippet,
                }
            },
        )

        # ── Query each name ───────────────────────────────────────────────
        names = extract_names(snippet)
        results: list[tuple[str, int, int, list[str]]] = []
        n_names = len(names)

        for i, (name, line, col) in enumerate(names):
            print(
                f"\r  {snippet_label}  {i + 1}/{n_names} tokens …    ",
                end="",
                flush=True,
            )

            resp = client.request(
                "ty/typeDefinitionName",
                {
                    "textDocument": {"uri": file_uri},
                    "position": {"line": line, "character": col},
                },
            )

            fqns: list[str] = []
            if resp.get("result") is not None:
                fqns = resp["result"].get("names", [])

            results.append((name, line, col, fqns))

        n_resolved = sum(1 for *_, fqns in results if fqns)
        print(f"\r  {snippet_label}  {n_resolved}/{n_names} resolved       ")

        all_results.append((title, snippet, results))

        # ── Close document ────────────────────────────────────────────────
        client.notify(
            "textDocument/didClose",
            {"textDocument": {"uri": file_uri}},
        )

    # ── Shutdown ──────────────────────────────────────────────────────────
    client.shutdown()
    return all_results


# ── Pretty-printing ───────────────────────────────────────────────────────────

_BAR = "─" * 76
_THIN = "╌" * 76


def print_snippet_results(
    title: str,
    snippet: str,
    results: list[tuple[str, int, int, list[str]]],
) -> None:
    lines = snippet.splitlines()

    # ── Header ────────────────────────────────────────────────────────────────
    print(f"\n{_BAR}")
    print(f"  {title}")
    print(_BAR)

    # ── Snippet ───────────────────────────────────────────────────────────────
    for i, ln in enumerate(lines, 1):
        print(f"  {i:3d} │ {ln}")
    print(_THIN)

    # ── Results table ─────────────────────────────────────────────────────────
    name_w = max((len(n) for n, *_ in results), default=4)
    name_w = max(name_w, len("Token"))
    fqn_w = max((len(" | ".join(f)) for *_, f in results if f), default=len("FQN"))
    fqn_w = max(fqn_w, len("FQN"))

    hdr = f"  {'Line':>5}  {'Col':>3}  {'Token':<{name_w}}  FQN"
    sep = f"  {'─' * 5}  {'─' * 3}  {'─' * name_w}  {'─' * fqn_w}"
    print(f"\n{hdr}")
    print(sep)

    for name, line, col, fqns in results:
        fqn_str = " | ".join(fqns) if fqns else "—"
        print(f"  {line + 1:5d}  {col + 1:3d}  {name:<{name_w}}  {fqn_str}")

    # ── Summary ───────────────────────────────────────────────────────────────
    n_resolved = sum(1 for *_, fqns in results if fqns)
    n_total = len(results)
    print(f"\n  {n_resolved}/{n_total} tokens resolved.\n")


def print_grand_summary(
    all_results: list[tuple[str, str, list[tuple[str, int, int, list[str]]]]],
) -> None:
    print(f"\n{'═' * 76}")
    print("  GRAND SUMMARY")
    print(f"{'═' * 76}")
    title_w = max((len(t) for t, *_ in all_results), default=5)
    print(f"  {'Snippet':<{title_w}}  {'Resolved':>8}  {'Total':>5}  {'Rate':>5}")
    print(f"  {'─' * title_w}  {'─' * 8}  {'─' * 5}  {'─' * 5}")
    grand_res = grand_tot = 0
    for title, _, results in all_results:
        res = sum(1 for *_, fqns in results if fqns)
        tot = len(results)
        rate = f"{res / tot * 100:.0f}%" if tot else "n/a"
        print(f"  {title:<{title_w}}  {res:>8}  {tot:>5}  {rate:>5}")
        grand_res += res
        grand_tot += tot
    print(f"  {'─' * title_w}  {'─' * 8}  {'─' * 5}  {'─' * 5}")
    grand_rate = f"{grand_res / grand_tot * 100:.0f}%" if grand_tot else "n/a"
    print(f"  {'TOTAL':<{title_w}}  {grand_res:>8}  {grand_tot:>5}  {grand_rate:>5}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--build",
        action="store_true",
        help="Run 'cargo build -p ty' before querying (builds the debug binary).",
    )
    ap.add_argument(
        "--snippet",
        default=None,
        metavar="CODE",
        help="Run a single one-off Python snippet instead of the built-in examples.",
    )
    ap.add_argument(
        "--filter",
        default=None,
        metavar="TEXT",
        help="Only run built-in snippets whose title contains TEXT (case-insensitive).",
    )
    ap.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print every LSP message direction to stderr.",
    )
    args = ap.parse_args()

    # ── optional build step ──────────────────────────────────────────────────
    if args.build:
        print("Building ty (cargo build -p ty) …")
        result = subprocess.run(
            ["cargo", "build", "-p", "ty"],
            cwd=str(SCRIPT_DIR),
        )
        if result.returncode != 0:
            print("Build failed.", file=sys.stderr)
            sys.exit(1)
        print("Build succeeded.\n")

    # ── choose which snippets to run ─────────────────────────────────────────
    if args.snippet is not None:
        snippets_to_run = [("custom snippet", args.snippet)]
    elif args.filter:
        needle = args.filter.lower()
        snippets_to_run = [(t, s) for t, s in SNIPPETS if needle in t.lower()]
        if not snippets_to_run:
            print(f"No snippets matched filter {args.filter!r}.", file=sys.stderr)
            print("Available titles:")
            for t, _ in SNIPPETS:
                print(f"  {t}")
            sys.exit(1)
    else:
        snippets_to_run = SNIPPETS

    print(f"Running {len(snippets_to_run)} snippet(s) …\n")
    all_results = run_snippets(snippets_to_run, verbose=args.verbose)

    for title, snippet, results in all_results:
        print_snippet_results(title, snippet, results)

    if len(all_results) > 1:
        print_grand_summary(all_results)


if __name__ == "__main__":
    main()
