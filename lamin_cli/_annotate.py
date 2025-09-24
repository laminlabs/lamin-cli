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
