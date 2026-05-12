from elasticsearch import Elasticsearch

from app.core.config import settings


def create_es_client() -> Elasticsearch:
    return Elasticsearch(
        settings.es_url,
        basic_auth=(settings.es_username, settings.es_password),
        request_timeout=30,
        verify_certs=False,
    )
