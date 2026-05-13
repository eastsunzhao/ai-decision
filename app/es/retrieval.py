from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from elasticsearch import Elasticsearch
from elasticsearch import exceptions as es_exceptions

from app.core.config import settings
from app.core.state import RetrievalDocument
from app.es.client import create_es_client
from app.es.rerank import HeuristicReranker

logger = logging.getLogger("ai_decision.es.retrieval")


class HybridRetriever:
    def __init__(self, index: str, rrf_k: int = 60):
        self.es: Elasticsearch = create_es_client()
        self.index = index
        self.rrf_k = rrf_k
        self.reranker = HeuristicReranker()

    def bm25_search(self, query: str, size: int = 8) -> List[Dict[str, Any]]:
        start = time.perf_counter()
        body = {
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": [
                        "text^3",
                        "content^3",
                        "title^2",
                        "summary^2",
                        "metadata.company^2",
                        "metadata.product^2",
                        "metadata.industry",
                    ],
                    "type": "best_fields",
                }
            },
            "size": size,
        }
        response = self.es.search(index=self.index, body=body)
        hits = response.get("hits", {}).get("hits", [])
        logger.info("es_bm25_search index=%s query_len=%s size=%s hits=%s elapsed_ms=%.2f", self.index, len(query), size, len(hits), (time.perf_counter() - start) * 1000)
        return hits

    def dense_search(self, query_vector: List[float], size: int = 8) -> List[Dict[str, Any]]:
        start = time.perf_counter()
        try:
            response = self.es.search(
                index=self.index,
                knn={
                    "field": "embedding",
                    "query_vector": query_vector,
                    "k": size,
                    "num_candidates": max(size * 10, 50),
                },
                size=size,
            )
            hits = response.get("hits", {}).get("hits", [])
            logger.info("es_dense_search index=%s vector_dims=%s size=%s hits=%s elapsed_ms=%.2f", self.index, len(query_vector), size, len(hits), (time.perf_counter() - start) * 1000)
            return hits
        except es_exceptions.BadRequestError:
            logger.warning("es_dense_search_skipped_bad_request index=%s vector_dims=%s", self.index, len(query_vector))
            return []

    def search(
        self,
        query: str,
        query_vector: Optional[List[float]] = None,
        size: int = 8,
        candidate_size: Optional[int] = None,
        rerank: Optional[bool] = None,
    ) -> List[RetrievalDocument]:
        start = time.perf_counter()
        candidate_size = candidate_size or max(size, settings.retrieval_candidates_size)
        rerank_enabled = settings.retrieval_rerank_enabled if rerank is None else rerank
        logger.info(
            "hybrid_search_started index=%s query_len=%s has_vector=%s size=%s candidate_size=%s rerank=%s",
            self.index,
            len(query),
            bool(query_vector),
            size,
            candidate_size,
            rerank_enabled,
        )
        bm25_hits = self.bm25_search(query, size=candidate_size)
        dense_hits = self.dense_search(query_vector, size=candidate_size) if query_vector else []
        merged_hits = self.rrf_merge(bm25_hits, dense_hits, k=self.rrf_k)
        candidate_documents = [self._to_document(hit, score) for hit, score in merged_hits[:candidate_size]]
        documents = (
            self.reranker.rerank(candidate_documents, query=query, top_k=size)
            if rerank_enabled
            else candidate_documents[:size]
        )
        logger.info(
            "hybrid_search_completed index=%s bm25_hits=%s dense_hits=%s merged=%s candidates=%s returned=%s rerank=%s elapsed_ms=%.2f",
            self.index,
            len(bm25_hits),
            len(dense_hits),
            len(merged_hits),
            len(candidate_documents),
            len(documents),
            rerank_enabled,
            (time.perf_counter() - start) * 1000,
        )
        return documents

    def rrf_merge(
        self,
        bm25_hits: List[Dict[str, Any]],
        dense_hits: List[Dict[str, Any]],
        k: int = 60,
    ) -> List[tuple[Dict[str, Any], float]]:
        scores: Dict[str, float] = {}
        hits_by_id: Dict[str, Dict[str, Any]] = {}

        for hit_list in (bm25_hits, dense_hits):
            for rank, hit in enumerate(hit_list, start=1):
                hit_id = hit.get("_id")
                if not hit_id:
                    continue
                hits_by_id[hit_id] = hit
                scores[hit_id] = scores.get(hit_id, 0.0) + 1.0 / (k + rank)

        return [(hits_by_id[hit_id], scores[hit_id]) for hit_id in sorted(scores, key=scores.get, reverse=True)]

    def _to_document(self, hit: Dict[str, Any], score: float) -> RetrievalDocument:
        source = hit.get("_source", {}) or {}
        metadata = source.get("metadata") or {}
        text = source.get("text") or source.get("content") or source.get("chunk_text") or ""
        title = source.get("title") or metadata.get("title") or metadata.get("file_name")
        source_name = source.get("source") or metadata.get("source") or metadata.get("file_path") or "elasticsearch"
        return RetrievalDocument(
            id=str(hit.get("_id", "")),
            score=score,
            source=str(source_name),
            title=title,
            text=str(text),
            metadata=metadata if isinstance(metadata, dict) else {"raw_metadata": metadata},
        )
