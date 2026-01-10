from appsv2.adapters.pnl.flex_ingest import FlexCsvPnlIngestor
from appsv2.adapters.pnl.store import PostgresDailyPnlStore
from appsv2.core.pnl.service import PnlService


def get_pnl_service() -> PnlService:
    store = PostgresDailyPnlStore()
    ingestor = FlexCsvPnlIngestor(store)
    return PnlService(ingestor=ingestor, store=store)
