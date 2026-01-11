from apps.adapters.pnl.flex_ingest import FlexCsvPnlIngestor
from apps.adapters.pnl.store import PostgresDailyPnlStore
from apps.core.pnl.service import PnlService


def get_pnl_service() -> PnlService:
    store = PostgresDailyPnlStore()
    ingestor = FlexCsvPnlIngestor(store)
    return PnlService(ingestor=ingestor, store=store)
