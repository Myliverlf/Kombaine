from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Any, Optional

from core.instruments.identity import InstrumentIdentity


@dataclass(frozen=True)
class AcquisitionRequest:
    provider: str
    symbol: str
    timeframe: str
    start: str
    end: str
    source: str = "unknown"


@dataclass(frozen=True)
class AcquisitionResult:
    request: AcquisitionRequest
    identity: InstrumentIdentity
    bars: list[dict[str, Any]]
    raw_checksum: str
    transformed_checksum: str
    acquisition_timestamp: str
    source_endpoint: str
    request_metadata: dict[str, Any]
    response_metadata: dict[str, Any]


class MarketDataProvider(Protocol):
    name: str

    def discover_instrument(self, symbol: str) -> InstrumentIdentity:
        ...

    def resolve_identity(self, symbol: str) -> InstrumentIdentity:
        ...

    def fetch_historical_bars(self, request: AcquisitionRequest) -> AcquisitionResult:
        ...


@dataclass
class MockTinkoffProvider:
    name: str = "tinkoff-mock"
    identities: dict[str, InstrumentIdentity] = None

    def discover_instrument(self, symbol: str) -> InstrumentIdentity:
        return self.resolve_identity(symbol)

    def resolve_identity(self, symbol: str) -> InstrumentIdentity:
        if not self.identities or symbol not in self.identities:
            raise KeyError(symbol)
        return self.identities[symbol]

    def fetch_historical_bars(self, request: AcquisitionRequest) -> AcquisitionResult:
        identity = self.resolve_identity(request.symbol)
        bars: list[dict[str, Any]] = []
        return AcquisitionResult(
            request=request,
            identity=identity,
            bars=bars,
            raw_checksum="mock-raw",
            transformed_checksum="mock-xform",
            acquisition_timestamp=request.end,
            source_endpoint="mock://tinkoff",
            request_metadata={"provider": self.name, "source": request.source},
            response_metadata={"status": "ok"},
        )
