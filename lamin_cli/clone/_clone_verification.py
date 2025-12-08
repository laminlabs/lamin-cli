from concurrent.futures import ThreadPoolExecutor

from django.db import OperationalError, ProgrammingError
from lamin_utils import logger


def _count_instance_records() -> dict[str, int]:
    """Count all records across SQLRecord registries in parallel.

    Returns:
        Dictionary mapping table names (format: "app_label.ModelName") to  their record counts.

    Example:
        >>> counts = _count_all_records()
        >>> counts
        {'lamindb.Artifact': 1523, 'lamindb.Collection': 42, 'bionty.Gene': 60000}
    """
    # Import here to ensure that models are loaded
    from django.apps import apps
    from lamindb.models import SQLRecord

    def _count_model(model):
        """Count records for a single model."""
        table_name = f"{model._meta.app_label}.{model.__name__}"
        try:
            return (table_name, model.objects.count())
        except (OperationalError, ProgrammingError) as e:
            logger.warning(f"Could not count {table_name}: {e}")
            return (table_name, 0)

    models = [m for m in apps.get_models() if issubclass(m, SQLRecord)]

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(_count_model, models)

    return dict(results)


def _compare_record_counts(
    original: dict[str, int], clone: dict[str, int]
) -> dict[str, tuple[int, int]]:
    """Compare record counts and return mismatches."""
    mismatches = {}

    all_tables = set(original.keys()) | set(clone.keys())

    for table in all_tables:
        orig_count = original.get(table, 0)
        clone_count = clone.get(table, 0)

        # we allow a difference of 1 because of tracking
        # new records during the cloning process
        if abs(clone_count - orig_count) > 1:
            mismatches[table] = (orig_count, clone_count)

    return mismatches
