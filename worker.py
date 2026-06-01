import logging

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
    )
    logging.warning(
        "worker.py is deprecated. Use `uvicorn app.main:app --host 0.0.0.0 --port 8000` instead."
    )
