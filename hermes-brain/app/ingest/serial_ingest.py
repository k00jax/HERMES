from __future__ import annotations


class SerialIngestor:
    def __init__(self, port: str, baudrate: int = 115200) -> None:
        self.port = port
        self.baudrate = baudrate

    def run(self) -> None:
        raise NotImplementedError("Serial ingestion will be implemented in Milestone 2.")
