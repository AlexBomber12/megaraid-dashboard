from __future__ import annotations

import uvicorn

from megaraid_dashboard.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8090)


if __name__ == "__main__":
    main()
