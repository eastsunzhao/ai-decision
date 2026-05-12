from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from app.core.state import RetrievalDocument


def citation_label(doc: RetrievalDocument, index: int = 1) -> str:
    metadata = doc.metadata or {}
    raw_title = (
        doc.title
        or metadata.get("title")
        or metadata.get("file_name")
        or metadata.get("filename")
        or metadata.get("document_title")
        or metadata.get("source")
        or doc.source
        or f"资料{index}"
    )
    title = str(raw_title).strip()
    if "/" in title:
        title = Path(title).name
    return title or f"资料{index}"


def citation_labels(docs: List[RetrievalDocument]) -> List[str]:
    labels: List[str] = []
    seen: Dict[str, int] = {}
    for index, doc in enumerate(docs, start=1):
        base = citation_label(doc, index)
        seen[base] = seen.get(base, 0) + 1
        label = base if seen[base] == 1 else f"{base}（{seen[base]}）"
        labels.append(label)
    return labels


def citation_details(docs: List[RetrievalDocument]) -> List[Dict[str, Any]]:
    labels = citation_labels(docs)
    return [
        {
            "id": labels[index],
            "title": labels[index],
            "source": doc.source,
            "text": doc.text[:1200],
            "metadata": doc.metadata,
            "raw_doc_id": doc.id,
        }
        for index, doc in enumerate(docs)
    ]
