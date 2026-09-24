import ast
from pathlib import Path


def test_migrations_have_one_head():
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in versions.glob("*.py"):
        values = {}
        for statement in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(statement, ast.Assign):
                names = [target.id for target in statement.targets if isinstance(target, ast.Name)]
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                names = [statement.target.id]
            else:
                continue
            for name in names:
                if name in {"revision", "down_revision"}:
                    values[name] = ast.literal_eval(statement.value)
        revisions.add(values["revision"])
        parent = values["down_revision"]
        parents.update(parent if isinstance(parent, tuple) else [parent] if parent else [])

    assert len(revisions - parents) == 1
