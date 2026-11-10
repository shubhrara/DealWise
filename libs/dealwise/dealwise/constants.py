DEFAULT_ORG_ID = "default"

ROLE_HISTORICAL = "historical"
ROLE_PROPOSAL = "proposal"
ROLES = (ROLE_HISTORICAL, ROLE_PROPOSAL)

STATUSES = ("draft", "active", "expired", "terminated")
# computed only (never stored): a document replaced by a later revision
STATUS_SUPERSEDED = "superseded"

REL_ORIGINAL = "original"
REL_AMENDMENT = "amendment"
REL_RENEWAL = "renewal"
REL_REVISION = "revision"
RELATION_TYPES = (REL_ORIGINAL, REL_AMENDMENT, REL_RENEWAL, REL_REVISION)
BASE_RELATIONS = (REL_ORIGINAL, REL_RENEWAL, REL_REVISION)

VIS_ORG = "organization"
VIS_PRIVATE = "private"
VISIBILITIES = (VIS_ORG, VIS_PRIVATE)

# Extensions the upstream readers can parse (used for Phase 2 indexing).
ALLOWED_EXTENSIONS = (
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".md", ".html",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff",
)  # fmt: skip

# Similarity (difflib ratio on normalised names) above which a vendor name is
# treated as a possible duplicate and the user is asked to confirm.
SIMILAR_VENDOR_THRESHOLD = 0.88
