"""Load the catalog, the routing policy and team config from YAML (does file I/O).

The YAML loader rejects duplicate keys. Strict model fields reject floats where an integer is required. Every
failure is one :class:`ConfigError` naming the file and the field.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from switchyard.canonical import content_hash
from switchyard.models import Catalog, RoutingPolicy, TeamConfig


class ConfigError(ValueError):
    """A config or case file failed to load. The message names the file and the field."""


class _StrictLoader(yaml.SafeLoader):
    """A safe loader that refuses duplicate mapping keys."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    seen: set[Hashable] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_yaml_text(text: str, source: str) -> Any:
    try:
        return yaml.load(text, Loader=_StrictLoader)  # a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise ConfigError(f"{source}: {exc}") from exc


def load_yaml(path: Path) -> Any:
    return load_yaml_text(path.read_text(encoding="utf-8"), str(path))


def validation_message(source: str, exc: ValidationError) -> str:
    """One clear line: the first error, its field path and the message."""
    first = exc.errors()[0]
    loc = ".".join(str(part) for part in first["loc"]) or "(root)"
    return f"{source}: {loc}: {first['msg']}"


def parse_model[M: BaseModel](model: type[M], data: Any, source: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(validation_message(source, exc)) from None


def load_catalog(path: Path) -> Catalog:
    return parse_model(Catalog, load_yaml(path), str(path))


def load_policy(path: Path) -> RoutingPolicy:
    return parse_model(RoutingPolicy, load_yaml(path), str(path))


def load_teams(mapping: Mapping[str, Any], source: str = "teams") -> dict[str, TeamConfig]:
    if not isinstance(mapping, Mapping):
        raise ConfigError(f"{source}: teams must be a mapping")
    teams: dict[str, TeamConfig] = {}
    for name in sorted(mapping):
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{source}: team names must be non-empty strings")
        teams[name] = parse_model(TeamConfig, mapping[name], f"{source}.{name}")
    return teams


def config_version(policy: RoutingPolicy, catalog: Catalog) -> str:
    """Content hash of the policy plus the catalog. Any change to either changes it (PRD 7.1)."""
    return content_hash(
        {"catalog": catalog.model_dump(mode="json"), "policy": policy.model_dump(mode="json")}
    )
