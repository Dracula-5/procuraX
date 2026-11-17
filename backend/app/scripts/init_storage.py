"""Create the invoice-file bucket when it is missing (local stack and CI; production buckets are
provisioned with the cloud account, ideally with versioning or object lock for retention).

    uv run python -m app.scripts.init_storage
"""

from app.core.config import get_settings
from app.core.storage import S3Storage


def main() -> None:
    settings = get_settings()
    if settings.storage_backend != "s3":
        print(f"Storage backend is {settings.storage_backend!r}; nothing to create.")
        return
    created = S3Storage(settings).ensure_bucket()
    print(f"Bucket {settings.s3_bucket!r} {'created' if created else 'already exists'}.")


if __name__ == "__main__":
    main()
