from typing import TypedDict


class AvailableDevice(TypedDict):
    idx: int
    name: str
    serialnum: str
    error:str | None

class OscillsocopeStatus(TypedDict):
    ready: bool
    message: str| None
