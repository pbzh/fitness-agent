"""Daily verifier: every encrypted API key in the DB still decrypts.

Run via the ``fitness-agent-verify-keys.timer`` systemd unit (see
``deploy/`` in this repo). Exits 0 on success, 1 on any failure so the
unit's ``OnFailure=`` chain (or your monitoring) can alert.

Manual run:

    cd /opt/fitness-agent
    uv run python scripts/verify_keys.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security.secrets import verify_all  # noqa: E402


async def main() -> int:
    ok, failures = await verify_all()
    if failures:
        print(f"FAIL: ok={ok} failed={len(failures)}")
        for user_id, provider in failures:
            print(f"  user={user_id} provider={provider}")
        return 1
    print(f"OK: ok={ok} failed=0")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
