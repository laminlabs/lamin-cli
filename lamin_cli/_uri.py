"""Parse and resolve ``lamin://`` URIs.

Two forms are supported. The uid form is unchanged from the canonical format used by
nf-lamin, so existing URIs keep working everywhere:

    lamin://<owner>/<instance>/artifact/<uid>[/<subpath>]

The key form addresses artifacts the way people actually name them:

    lamin://<owner>/<instance>/artifact/key/<key>[/<subpath>][?space=&branch=&version=]

``key`` can never be a uid (uids are 16 or 20 alphanumeric characters), so the two
forms are distinguishable without a database lookup, and parsers that only know the
uid form reject key URIs loudly instead of misreading them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlsplit

if TYPE_CHECKING:
    from lamindb import Artifact

SCHEME = "lamin"
KEY_MARKER = "key"
QUALIFIERS = ("space", "branch", "version")
_UID_RE = re.compile(r"^[A-Za-z0-9]{16}(?:[A-Za-z0-9]{4})?$")
FORMS = (
    "lamin://<owner>/<instance>/artifact/<uid>[/<subpath>] or"
    " lamin://<owner>/<instance>/artifact/key/<key>[?space=&branch=&version=]"
)


class InvalidLaminUri(ValueError):
    """Raised when a string is not a well-formed ``lamin://`` URI."""


class UnresolvableLaminUri(LookupError):
    """Raised when a well-formed URI does not identify exactly one artifact."""


@dataclass(frozen=True)
class LaminUri:
    """A parsed ``lamin://`` URI."""

    raw: str
    instance: str
    uid: str | None = None
    # path segments after `key/`; the split into key and subpath needs the database
    key_segments: tuple[str, ...] = ()
    subpath: PurePosixPath | None = None
    space: str | None = None
    branch: str | None = None
    version: str | None = None

    @property
    def is_key_form(self) -> bool:
        return self.uid is None


@dataclass
class ResolvedUri:
    """A URI resolved to one artifact and an optional path inside it."""

    uri: LaminUri
    artifact: Artifact
    subpath: PurePosixPath | None
    in_current_instance: bool


def is_lamin_uri(value: str) -> bool:
    return value.startswith(f"{SCHEME}://")


def _segments(path: str) -> list[str]:
    segments = [unquote(part) for part in path.split("/")]
    if any(part == "" for part in segments):
        raise InvalidLaminUri(f"Empty path segment. Expected {FORMS}.")
    if any(part in {".", ".."} for part in segments):
        # a subpath is appended to a local path, so traversal must be impossible
        raise InvalidLaminUri("Path segments '.' and '..' are not allowed.")
    return segments


def parse_lamin_uri(value: str) -> LaminUri:
    """Parse a ``lamin://`` URI without touching the database."""
    if not is_lamin_uri(value):
        raise InvalidLaminUri(f"Not a lamin:// URI: {value!r}.")
    parts = urlsplit(value)
    if parts.fragment:
        raise InvalidLaminUri("Fragments ('#') are not supported in lamin:// URIs.")
    owner = unquote(parts.netloc)
    if not owner:
        raise InvalidLaminUri(f"Missing owner. Expected {FORMS}.")
    segments = _segments(parts.path.lstrip("/")) if parts.path.strip("/") else []
    if len(segments) < 3:
        raise InvalidLaminUri(f"Incomplete URI {value!r}. Expected {FORMS}.")
    instance, entity, *rest = segments
    if entity != "artifact":
        raise InvalidLaminUri(f"Unsupported entity {entity!r}, only 'artifact' is.")

    query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=False)
    unknown = sorted(set(query) - set(QUALIFIERS))
    if unknown:
        raise InvalidLaminUri(
            f"Unknown query parameter(s) {', '.join(unknown)}. Allowed: "
            f"{', '.join(QUALIFIERS)}."
        )
    qualifiers: dict[str, Any] = {}
    for name, values in query.items():
        if len(values) != 1 or not values[0]:
            raise InvalidLaminUri(f"Query parameter {name!r} needs exactly one value.")
        qualifiers[name] = values[0]

    slug = f"{owner}/{instance}"
    if rest[0] == KEY_MARKER:
        if len(rest) < 2:
            raise InvalidLaminUri(f"Missing key after 'key/'. Expected {FORMS}.")
        return LaminUri(
            raw=value, instance=slug, key_segments=tuple(rest[1:]), **qualifiers
        )

    uid, *subpath = rest
    if not _UID_RE.match(uid):
        raise InvalidLaminUri(
            f"{uid!r} is not a uid (16 or 20 alphanumeric characters). To address an"
            f" artifact by key use lamin://{slug}/artifact/key/<key>."
        )
    if qualifiers:
        raise InvalidLaminUri(
            "Query parameters are only allowed in the key form; a uid already"
            " identifies the artifact."
        )
    return LaminUri(
        raw=value,
        instance=slug,
        uid=uid,
        subpath=PurePosixPath(*subpath) if subpath else None,
    )


def _artifact_queryset(instance: str):
    import lamindb as ln
    import lamindb_setup as ln_setup

    in_current = (
        ln_setup.settings.instance is not None
        and ln_setup.settings.instance.slug == instance
    )
    # querying another instance must not switch the connected one, because the run
    # that consumes the artifact is tracked in the connected instance
    queryset = ln.Artifact.filter() if in_current else ln.Artifact.connect(instance)
    return queryset, in_current


def _qualify(queryset, uri: LaminUri):
    from django.db.models import Q

    if uri.space is not None:
        queryset = queryset.filter(Q(space__name=uri.space) | Q(space__uid=uri.space))
    if uri.branch is not None:
        queryset = queryset.filter(
            Q(branch__name=uri.branch) | Q(branch__uid=uri.branch)
        )
    if uri.version is not None:
        queryset = queryset.filter(version_tag=uri.version)
    else:
        queryset = queryset.filter(is_latest=True)
    return queryset


def _describe(artifacts: list) -> str:
    return ", ".join(f"{a.uid} (key={a.key!r})" for a in artifacts)


def resolve_lamin_uri(value: str | LaminUri) -> ResolvedUri:
    """Resolve a URI to exactly one artifact, never guessing between candidates."""
    uri = parse_lamin_uri(value) if isinstance(value, str) else value
    queryset, in_current = _artifact_queryset(uri.instance)

    if uri.uid is not None:
        if len(uri.uid) == 20:
            artifact = queryset.filter(uid=uri.uid).one_or_none()
        else:
            # a 16-character stem uid addresses the latest version of the family
            artifact = (
                queryset.filter(uid__startswith=uri.uid)
                .order_by("-is_latest", "-created_at")
                .first()
            )
        if artifact is None:
            raise UnresolvableLaminUri(
                f"No artifact with uid {uri.uid} in {uri.instance}."
            )
        return ResolvedUri(uri, artifact, uri.subpath, in_current)

    segments = uri.key_segments
    # longest prefix first: a full key wins over a folder artifact containing it
    for split in range(len(segments), 0, -1):
        key = "/".join(segments[:split])
        candidates = list(_qualify(queryset.filter(key=key), uri)[:6])
        if not candidates:
            continue
        if len(candidates) > 1:
            raise UnresolvableLaminUri(
                f"Key {key!r} matches several artifacts in {uri.instance}:"
                f" {_describe(candidates)}. Narrow it down with ?space=, ?branch= or"
                " ?version=, or use the uid form."
            )
        artifact = candidates[0]
        remainder = segments[split:]
        if remainder and artifact.n_files is None:
            # only folder artifacts can contain a subpath
            continue
        subpath = PurePosixPath(*remainder) if remainder else None
        return ResolvedUri(uri, artifact, subpath, in_current)

    qualifiers = ", ".join(
        f"{name}={getattr(uri, name)}"
        for name in QUALIFIERS
        if getattr(uri, name) is not None
    )
    detail = f" with {qualifiers}" if qualifiers else ""
    raise UnresolvableLaminUri(
        f"No artifact with key {'/'.join(segments)!r}{detail} in {uri.instance}."
    )
