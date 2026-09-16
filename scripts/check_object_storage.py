"""Upload/download/delete smoke check for the configured S3-compatible storage."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.core.object_storage import get_object_storage


async def main() -> None:
    source = tempfile.mktemp(suffix=".bin")
    target = tempfile.mktemp(suffix=".bin")
    key = "_diagnostics/object-storage-smoke.bin"
    try:
        with open(source, "wb") as handle:
            handle.write(b"pinda-storage-smoke")
        client = get_object_storage()
        await client.put_file(key, source)
        await client.get_file(key, target)
        with open(target, "rb") as handle:
            if handle.read() != b"pinda-storage-smoke":
                raise RuntimeError("对象存储读回内容不一致")
        await client.delete(key)
        print("Object storage smoke check passed; temporary object deleted.")
    finally:
        for path in (source, target):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    asyncio.run(main())
