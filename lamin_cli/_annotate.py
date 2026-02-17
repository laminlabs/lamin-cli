from typing import get_args

from lamindb.base.types import RegistryId

# Registries that have ablocks (can be annotated with readme)
_REGISTRY_IDS = frozenset(get_args(RegistryId))
_ABLOCK_REGISTRIES = frozenset(
    r.replace("__lamindb_", "").replace("__", "") for r in _REGISTRY_IDS
) - {"block", "jsonvalue", "storage"}  # no *Block for these
# branch and space have ablocks but aren't in RegistryId
ANNOTATE_REGISTRIES = _ABLOCK_REGISTRIES | {"branch", "space"}

ANNOTATE_ENTITIES_KEY = {"artifact", "transform", "collection"}
ANNOTATE_ENTITIES_NAME = {
    "record",
    "project",
    "ulabel",
    "branch",
    "feature",
    "schema",
    "space",
}
ANNOTATE_ENTITIES_UID_ONLY = {"run"}
REGISTRIES_WITH_PROJECT_ULABEL_RECORD = {"artifact", "transform", "collection"}
REGISTRIES_WITH_VERSION = {"artifact", "transform", "collection"}
REGISTRIES_WITH_FEATURES = {"artifact", "transform"}


def _get_obj(registry: str, key: str | None, uid: str | None, name: str | None):
    """Resolve entity by key, uid, or name."""
    import lamindb as ln

    if registry in ANNOTATE_ENTITIES_KEY:
        if key is None and uid is None:
            raise ln.errors.InvalidArgument(f"For {registry} pass --key or --uid")
        model = (
            ln.Artifact
            if registry == "artifact"
            else ln.Transform
            if registry == "transform"
            else ln.Collection
        )
        if key is not None:
            return model.get(key=key)
        return model.get(uid)
    if registry in ANNOTATE_ENTITIES_NAME:
        if uid is None and name is None:
            # Default to current branch when annotating branch
            if registry == "branch":
                import lamindb_setup as ln_setup

                name = ln_setup.settings.branch.name
            else:
                raise ln.errors.InvalidArgument(f"For {registry} pass --uid or --name")
        if uid is not None:
            return {
                "record": ln.Record.get,
                "project": ln.Project.get,
                "ulabel": ln.ULabel.get,
                "branch": ln.Branch.get,
                "feature": ln.Feature.get,
                "schema": ln.Schema.get,
                "space": ln.Space.get,
            }[registry](uid)
        return {
            "record": ln.Record.get,
            "project": ln.Project.get,
            "ulabel": ln.ULabel.get,
            "branch": ln.Branch.get,
            "feature": ln.Feature.get,
            "schema": ln.Schema.get,
            "space": ln.Space.get,
        }[registry](name=name)
    # run - uid only
    if uid is None:
        raise ln.errors.InvalidArgument("For run pass --uid")
    return ln.Run.get(uid)


def _add_block(obj, registry: str, content: str, *, kind: str = "readme"):
    """Create and add a block (readme or comment) to entity."""
    import lamindb as ln

    block_kwargs = {"content": content, "kind": kind}
    block = {
        "artifact": lambda: ln.models.ArtifactBlock(artifact=obj, **block_kwargs),
        "transform": lambda: ln.models.TransformBlock(transform=obj, **block_kwargs),
        "collection": lambda: ln.models.CollectionBlock(collection=obj, **block_kwargs),
        "record": lambda: ln.models.RecordBlock(record=obj, **block_kwargs),
        "run": lambda: ln.models.RunBlock(run=obj, **block_kwargs),
        "schema": lambda: ln.models.SchemaBlock(schema=obj, **block_kwargs),
        "feature": lambda: ln.models.FeatureBlock(feature=obj, **block_kwargs),
        "project": lambda: ln.models.ProjectBlock(project=obj, **block_kwargs),
        "branch": lambda: ln.models.BranchBlock(branch=obj, **block_kwargs),
        "ulabel": lambda: ln.models.ULabelBlock(ulabel=obj, **block_kwargs),
        "space": lambda: ln.models.SpaceBlock(space=obj, **block_kwargs),
    }[registry]()
    obj.ablocks.add(block, bulk=False)


def _parse_features_list(features_list: tuple) -> dict:
    """Parse feature list into a dictionary.

    Supports multiple formats:
    - Quoted values: 'perturbation="DMSO","IFNG"' → {"perturbation": ["DMSO", "IFNG"]}
    - Unquoted values: 'perturbation=IFNG,DMSO' → {"perturbation": ["IFNG", "DMSO"]}
    - Single values: 'cell_line=HEK297' → {"cell_line": "HEK297"}
    - Mixed: ('perturbation="DMSO","IFNG"', 'cell_line=HEK297', 'genes=TP53,BRCA1')
    """
    import re

    import lamindb as ln

    feature_dict = {}

    for feature_assignment in features_list:
        if "=" not in feature_assignment:
            raise ln.errors.InvalidArgument(
                f"Invalid feature assignment: '{feature_assignment}'. Expected format: 'feature=value' or 'feature=\"value1\",\"value2\"'"
            )

        feature_name, values_str = feature_assignment.split("=", 1)
        feature_name = feature_name.strip()

        # Parse quoted values using regex
        # This will match quoted strings like "DMSO","IFNG" or single values like HEK297
        quoted_values = re.findall(r'"([^"]*)"', values_str)

        if quoted_values:
            # If we found quoted values, use them
            if len(quoted_values) == 1:
                feature_dict[feature_name] = quoted_values[0]
            else:
                feature_dict[feature_name] = quoted_values
        else:
            # If no quoted values, treat as single unquoted value
            # Remove any surrounding whitespace
            value = values_str.strip()

            # Handle comma-separated unquoted values
            if "," in value:
                values = [v.strip() for v in value.split(",")]
                feature_dict[feature_name] = values
            else:
                feature_dict[feature_name] = value

    return feature_dict
