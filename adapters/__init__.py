from .base import ParcelRecord, StateParcelProvider
from .wi_sco import WisconsinSCOProvider

PROVIDERS = [
    WisconsinSCOProvider(),
]


def get_provider_for_bbox(bbox_wgs84):
    for p in PROVIDERS:
        if p.supports_bbox(bbox_wgs84):
            return p
    return None
