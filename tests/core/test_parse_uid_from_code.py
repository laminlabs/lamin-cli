from lamin_cli._save import parse_uid_from_code


def test_python_track_pattern():
    valid_cases = [
        # Basic cases with 16 character requirement
        ('ln.track("abcd123456789xyz")', "abcd123456789xyz"),
        ("ln.track('abcd123456789xyz')", "abcd123456789xyz"),
        # With transform parameter
        ('ln.track(transform="abcd123456789xyz")', "abcd123456789xyz"),
        ("ln.track(transform='abcd123456789xyz')", "abcd123456789xyz"),
        # With whitespace variations
        ('ln.track( "abcd123456789xyz" )', "abcd123456789xyz"),
        ('ln.track(    transform ="abcd123456789xyz")', "abcd123456789xyz"),
        ('ln.track(\n    "abcd123456789xyz"\n)', "abcd123456789xyz"),
    ]

    invalid_cases = [
        # Mismatched quotes
        "ln.track(\"abcd123456789xyz')",
        "ln.track('abcd123456789xyz\")",
        # Wrong length
        'ln.track("abc123")',  # Too short
        'ln.track("abcd123456789xyz0")',  # Too long
        # Invalid characters
        'ln.track("abcd-123456789xyz")',  # Contains hyphen
        'ln.track("abcd_123456789xyz")',  # Contains underscore
        'ln.track("abcd!123456789xyz")',  # Contains special character
        # Old uid parameter
        'ln.track(uid="abcd123456789xyz")',
    ]

    # Test valid cases
    for content, expected_uid in valid_cases:
        uid = parse_uid_from_code(content, ".py")
        assert uid == expected_uid, f"Failed for valid content: {content}"

    # Test invalid cases
    for content in invalid_cases:
        assert parse_uid_from_code(content, ".py") is None


def test_jupyter_track_pattern():
    valid_cases = [
        # Basic cases
        (r"ln.track(\"abcd123456789xyz\")", "abcd123456789xyz"),
        ("ln.track('abcd123456789xyz')", "abcd123456789xyz"),
        # With transform parameter
        (r"ln.track(transform=\"abcd123456789xyz\")", "abcd123456789xyz"),
        ("ln.track(transform='abcd123456789xyz')", "abcd123456789xyz"),
        # With whitespace variations
        (r"ln.track( \"abcd123456789xyz\" )", "abcd123456789xyz"),
        (r"ln.track(    transform =\"abcd123456789xyz\")", "abcd123456789xyz"),
    ]

    invalid_cases = [
        # Mismatched quotes
        r"ln.track(\"abcd123456789xyz')",
        # Wrong length
        r"ln.track(\"abc123\")",  # Too short
        r"ln.track(\"abcd123456789xyz0\")",  # Too long
        # Invalid characters
        r"ln.track(\"abcd-123456789xyz\")",  # Contains hyphen
        r"ln.track(\"abcd_123456789xyz\")",  # Contains underscore
        # Old uid parameter
        r"ln.track(uid=\"abcd123456789xyz\")",
    ]

    # Test valid cases
    for content, expected_uid in valid_cases:
        uid = parse_uid_from_code(content, ".ipynb")
        assert uid == expected_uid, f"Failed for valid content: {content}"

    # Test invalid cases
    for content in invalid_cases:
        assert parse_uid_from_code(content, ".py") is None


def test_edge_cases():
    test_cases = [
        # Multiple track calls (should match first valid one)
        (
            'ln.track("abcd123456789xyz")\nln.track("efgh123456789xyz")',
            "abcd123456789xyz",
            ".py",
        ),
        # Comments in code -- this will fail
        # ('# ln.track("abcd123456789xyz")\nln.track("real123456789xyz")', "real123456789xyz", ".py"),
        # Mixed valid and invalid cases
        (
            'ln.track("invalid")\nln.track("abcd123456789xyz")',
            "abcd123456789xyz",
            ".py",
        ),
    ]

    for content, expected_uid, suffix in test_cases:
        uid = parse_uid_from_code(content, suffix)
        assert uid == expected_uid, f"Failed for content: {content}"


def test_r_track_pattern():
    suffixes = [".R", ".qmd", ".Rmd"]

    valid_cases = [
        # Basic cases with 16 character requirement
        ('track("abcd123456789xyz")', "abcd123456789xyz"),
        ("track('abcd123456789xyz')", "abcd123456789xyz"),
    ]

    # Test valid cases across all R-related suffixes
    for suffix in suffixes:
        for content, expected_uid in valid_cases:
            uid = parse_uid_from_code(content, suffix)
            assert uid == expected_uid, (
                f"Failed for valid content with {suffix}: {content}"
            )
