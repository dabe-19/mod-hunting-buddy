#!/usr/bin/env python3
"""Pluggable State-GIS Parcel Provider Abstract Base Class.
Community contributors can add new states (MI, MN, IA, OH, etc.) by subclassing
StateParcelProvider and registering it in adapters/__init__.py.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass
class ParcelRecord:
    parcel_pin: str
    owner_name: str
    acres: float
    plss_desc: str
    geom_geojson: Dict[str, Any]
    access_status: str = "neighbor"
    provider_id: str = "unknown"
    extra: Dict[str, Any] = field(default_factory=dict)


class StateParcelProvider(ABC):
    state_code: str = ""
    provider_id: str = ""
    display_name: str = ""

    @abstractmethod
    def supports_bbox(self, bbox_wgs84: Tuple[float, float, float, float]) -> bool:
        """Return True if (min_lon, min_lat, max_lon, max_lat) lies within this provider's coverage."""

    @abstractmethod
    def fetch_parcels(
        self, bbox_wgs84: Tuple[float, float, float, float], max_records: int = 80
    ) -> List[ParcelRecord]:
        """Query public GIS service for parcel polygons intersecting bbox_wgs84."""
