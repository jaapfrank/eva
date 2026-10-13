"""Read durable, isolated Agent Zero evidence without using its live caches.

Only the comparison's own native chat JSON and restricted FAISS document pickle
are read. Nothing is saved, repaired or injected. Returned content is private
scorer material and must not be printed by the builder.
"""
from __future__ import annotations

import hashlib
import io
import json
import pickle
from pathlib import Path


def _inside(base: Path, *parts: str) -> Path:
    for part in parts:
        if not isinstance(part, str) or not part or part in (".", "..") or Path(part).is_absolute() or any(
            item in (".", "..") for item in Path(part).parts
        ):
            raise ValueError("native evidence path must be relative")
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base):
        raise ValueError("native evidence path escapes isolated user directory")
    return target


class _NativeDocstoreUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        # Native FAISS.save_local saves exactly these two ordinary data classes.
        # Never permit arbitrary pickle globals, reducers or persistent IDs.
        if (module, name) == ("langchain_community.docstore.in_memory", "InMemoryDocstore"):
            from langchain_community.docstore.in_memory import InMemoryDocstore
            return InMemoryDocstore
        if (module, name) == ("langchain_core.documents.base", "Document"):
            from langchain_core.documents import Document
            return Document
        raise pickle.UnpicklingError("unexpected native document pickle class")

    def persistent_load(self, pid):
        raise pickle.UnpicklingError("persistent pickle IDs are not allowed")


def read_agentzero_snapshot(*, user_dir: Path, context_id: str,
                            memory_subdir: str = "default") -> dict:
    base = Path(user_dir).resolve(strict=True)
    if not isinstance(context_id, str) or len(Path(context_id).parts) != 1:
        raise ValueError("one exact native context ID is required")
    chat_path = _inside(base, "chats", context_id, "chat.json")
    memory_path = _inside(base, "memory", memory_subdir)
    hashes = {}

    def read(path):
        if not path.resolve().is_relative_to(base):
            raise ValueError("native evidence symlink escapes isolated user directory")
        raw = path.read_bytes()
        hashes[str(path.relative_to(base))] = hashlib.sha256(raw).hexdigest()
        return raw

    chat = None
    if chat_path.is_file():
        chat = json.loads(read(chat_path))
        if not isinstance(chat, dict) or chat.get("id") != context_id:
            raise ValueError("saved native context ID differs")
    responses = [] if chat is None else [
        item for item in chat.get("log", {}).get("logs", [])
        if item.get("type") == "response" and item.get("agentno") == 0
        and isinstance(item.get("content"), str)
    ]
    records = []
    index_path, store_path = memory_path / "index.faiss", memory_path / "index.pkl"
    index_present, store_present = index_path.is_file(), store_path.is_file()
    if index_present != store_present:
        raise ValueError("native memory persistence is incomplete")
    integrity = {"saved": index_present, "native_hash_present": False,
                 "native_hash_matches": None, "mapping_matches_documents": None}
    if index_present:
        vector_bytes, pickle_bytes = read(index_path), read(store_path)
        docstore, mapping = _NativeDocstoreUnpickler(io.BytesIO(pickle_bytes)).load()
        from langchain_community.docstore.in_memory import InMemoryDocstore
        from langchain_core.documents import Document
        if type(docstore) is not InMemoryDocstore or not isinstance(mapping, dict):
            raise ValueError("native document store format differs")
        docs = docstore._dict
        if not isinstance(docs, dict) or any(
            not isinstance(key, str) or type(doc) is not Document for key, doc in docs.items()
        ):
            raise ValueError("native document record format differs")
        integrity["mapping_matches_documents"] = (
            all(type(key) is int and isinstance(value, str) for key, value in mapping.items())
            and len(mapping) == len(docs) and set(mapping.values()) == set(docs)
        )
        records = [{"id": key, "content": doc.page_content, "metadata": dict(doc.metadata)}
                   for key, doc in sorted(docs.items())]
        hash_path = memory_path / "index.faiss.sha256"
        if hash_path.is_file():
            integrity["native_hash_present"] = True
            integrity["native_hash_matches"] = (
                read(hash_path).decode("ascii").strip() == hashlib.sha256(vector_bytes).hexdigest()
            )
    return {"context_id": context_id, "chat_exists": chat is not None,
            "reply_text": responses[-1]["content"] if responses else None,
            "response_count": len(responses), "chat": chat,
            "memory_records": records, "memory_index": integrity, "file_hashes": hashes}
