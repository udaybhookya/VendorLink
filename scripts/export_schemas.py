"""Export every vl_core schema as JSON Schema for the web app and config validation."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from vl_core import schemas

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "src" / "schemas"
I18N_OUT = ROOT / "web" / "src" / "i18n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    count = 0
    for name in schemas.__all__:
        model = getattr(schemas, name)
        if (
            isinstance(model, type)
            and issubclass(model, BaseModel)
            and model is not schemas.VLModel
        ):
            schema = model.model_json_schema(mode="serialization")
            (OUT / f"{name}.json").write_text(
                json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
            )
            count += 1
    print(f"wrote {count} schemas to {OUT}")
    I18N_OUT.mkdir(parents=True, exist_ok=True)
    for path in sorted((ROOT / "config" / "i18n").glob("*.json")):
        (I18N_OUT / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"copied i18n strings to {I18N_OUT}")


if __name__ == "__main__":
    main()
