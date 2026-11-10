"""Safe storage of original contract files.

Files live under a DealWise-controlled directory with generated names. The
directory is NOT in a Gradio-served path; downloads go through an authorised
service call that copies the file to a temporary location.
"""

import hashlib
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from dealwise.constants import ALLOWED_EXTENSIONS
from dealwise.services.errors import ValidationError

_ZIP_EXT = {".docx", ".xlsx"}


@dataclass
class StoredFile:
    rel_path: str
    file_name: str
    size: int
    sha256: str


def sanitize_filename(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._ ()\-]", "_", base).strip(" .")
    return base[:150] or "document"


def sha256_of(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fi:
        for block in iter(lambda: fi.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def validate_upload(path: str | Path, original_name: str, max_bytes: int) -> str:
    """Return the lower-cased extension or raise ValidationError."""
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"File type '{ext or 'unknown'}' is not supported. Allowed: "
            + ", ".join(ALLOWED_EXTENSIONS)
        )
    size = os.path.getsize(path)
    if size == 0:
        raise ValidationError("The file is empty.")
    if size > max_bytes:
        raise ValidationError(
            f"The file is larger than the {max_bytes // (1024 * 1024)} MB upload limit."
        )
    with open(path, "rb") as fi:
        head = fi.read(1024)
    if ext == ".pdf" and b"%PDF" not in head:
        raise ValidationError("The file does not look like a valid PDF.")
    if ext in _ZIP_EXT and not zipfile.is_zipfile(path):
        raise ValidationError(f"The file does not look like a valid {ext} document.")
    return ext


def save_file(
    src_path: str | Path, original_name: str, root: Path, org_id: str, sha256: str
) -> StoredFile:
    safe = sanitize_filename(original_name)
    rel = Path(re.sub(r"[^A-Za-z0-9_\-]", "_", org_id)) / sha256[:2] / uuid.uuid4().hex / safe
    dest = (root / rel).resolve()
    if root.resolve() not in dest.parents:
        raise ValidationError("Invalid storage path.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_path, dest)
    os.chmod(dest, 0o600)
    return StoredFile(rel.as_posix(), safe, dest.stat().st_size, sha256)


def resolve_path(root: Path, rel_path: str) -> Path:
    """Resolve a stored relative path, refusing anything outside the root."""
    p = (root / rel_path).resolve()
    if root.resolve() not in p.parents:
        raise ValidationError("Invalid storage path.")
    return p


def remove_file(root: Path, rel_path: str | None) -> None:
    if not rel_path:
        return
    try:
        p = resolve_path(root, rel_path)
        p.unlink(missing_ok=True)
        try:
            p.parent.rmdir()
        except OSError:
            pass
    except ValidationError:
        return
