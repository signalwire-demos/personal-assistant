"""
Knowledge Base Service

Manages knowledge documents and search index for RAG functionality.
Uses SignalWire's native_vector_search for semantic search.

Supports multi-tenant with per-user document storage and search indexes.
"""
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
import json

import config
from models.database import SessionLocal, KnowledgeDocument


class KnowledgeService:
    """Manages knowledge base documents and search index (multi-user)"""

    def __init__(self):
        # Base paths (for backward compatibility / global index)
        self.base_knowledge_dir = config.KNOWLEDGE_DIR
        self.documents_dir = config.KNOWLEDGE_DIR / "documents"
        self.index_path = config.KNOWLEDGE_INDEX_PATH / "knowledge.swsearch"

        # Ensure base directories exist
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        config.KNOWLEDGE_INDEX_PATH.mkdir(parents=True, exist_ok=True)

    def _get_user_documents_dir(self, user_id: str = None) -> Path:
        """Get documents directory for a user"""
        if not user_id:
            return self.documents_dir
        user_dir = self.base_knowledge_dir / "users" / user_id / "documents"
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def _get_user_index_path(self, user_id: str = None) -> Path:
        """Get index path for a user"""
        if not user_id:
            return self.index_path
        user_index_dir = self.base_knowledge_dir / "users" / user_id / "indexes"
        user_index_dir.mkdir(parents=True, exist_ok=True)
        return user_index_dir / "knowledge.swsearch"

    def get_documents(self, category: str = None, user_id: str = None) -> List[Dict]:
        """Get all knowledge documents from database"""
        db = SessionLocal()
        try:
            query = db.query(KnowledgeDocument)
            if user_id:
                query = query.filter(KnowledgeDocument.user_id == user_id)
            if category:
                query = query.filter(KnowledgeDocument.category == category)
            docs = query.order_by(KnowledgeDocument.category, KnowledgeDocument.title).all()
            return [d.to_dict() for d in docs]
        finally:
            db.close()

    def get_document(self, doc_id: int, user_id: str = None) -> Optional[Dict]:
        """Get a specific document by ID"""
        db = SessionLocal()
        try:
            query = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id)
            if user_id:
                query = query.filter(KnowledgeDocument.user_id == user_id)
            doc = query.first()
            return doc.to_dict() if doc else None
        finally:
            db.close()

    def add_document(
        self,
        title: str,
        content: str,
        category: str = "general",
        tags: List[str] = None,
        user_id: str = None,
    ) -> Dict:
        """Add a new knowledge document"""
        db = SessionLocal()
        try:
            # Create document record
            doc = KnowledgeDocument(
                user_id=user_id,
                title=title,
                content=content,
                category=category,
                tags=tags or [],
                is_indexed=False,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)

            # Save to file for indexing (in user-specific directory)
            self._save_document_file(doc, user_id=user_id)

            return doc.to_dict()
        finally:
            db.close()

    def update_document(
        self,
        doc_id: int,
        title: str = None,
        content: str = None,
        category: str = None,
        tags: List[str] = None,
        user_id: str = None,
    ) -> Optional[Dict]:
        """Update an existing document"""
        db = SessionLocal()
        try:
            query = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id)
            if user_id:
                query = query.filter(KnowledgeDocument.user_id == user_id)
            doc = query.first()
            if not doc:
                return None

            if title is not None:
                doc.title = title
            if content is not None:
                doc.content = content
            if category is not None:
                doc.category = category
            if tags is not None:
                doc.tags = tags

            doc.updated_at = datetime.utcnow()
            doc.is_indexed = False  # Mark for re-indexing

            db.commit()
            db.refresh(doc)

            # Update file (in user-specific directory)
            self._save_document_file(doc, user_id=user_id)

            return doc.to_dict()
        finally:
            db.close()

    def delete_document(self, doc_id: int, user_id: str = None) -> bool:
        """Delete a document"""
        db = SessionLocal()
        try:
            query = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id)
            if user_id:
                query = query.filter(KnowledgeDocument.user_id == user_id)
            doc = query.first()
            if not doc:
                return False

            # Delete file
            self._delete_document_file(doc)

            db.delete(doc)
            db.commit()
            return True
        finally:
            db.close()

    def _save_document_file(self, doc: KnowledgeDocument, user_id: str = None):
        """Save document content to file for indexing"""
        # Get user-specific documents directory
        docs_dir = self._get_user_documents_dir(user_id)

        # Create category directory
        category_dir = docs_dir / (doc.category or "general")
        category_dir.mkdir(exist_ok=True)

        # Create markdown file with metadata
        filename = f"{doc.id}_{self._sanitize_filename(doc.title)}.md"
        filepath = category_dir / filename

        # Build document with frontmatter
        tags_str = ", ".join(doc.tags) if doc.tags else ""
        content = f"""---
title: {doc.title}
category: {doc.category}
tags: [{tags_str}]
id: {doc.id}
---

# {doc.title}

{doc.content}
"""
        filepath.write_text(content)

        # Update file path reference
        db = SessionLocal()
        try:
            db_doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc.id).first()
            if db_doc:
                db_doc.file_path = str(filepath)
                db.commit()
        finally:
            db.close()

    def _delete_document_file(self, doc: KnowledgeDocument):
        """Delete document file"""
        if doc.file_path:
            filepath = Path(doc.file_path)
            if filepath.exists():
                filepath.unlink()

    def _sanitize_filename(self, title: str) -> str:
        """Create safe filename from title"""
        # Remove/replace unsafe characters
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        return safe.strip().replace(" ", "_")[:50]

    def build_index(self, force: bool = False, user_id: str = None) -> Dict:
        """Build or rebuild the search index for a user"""
        try:
            from signalwire_agents.search import IndexBuilder

            # Get user-specific paths
            docs_dir = self._get_user_documents_dir(user_id)
            index_path = self._get_user_index_path(user_id)

            # Check if we have documents
            docs = list(docs_dir.rglob("*.md"))
            if not docs:
                return {
                    "success": False,
                    "error": "No documents to index",
                    "document_count": 0,
                }

            # Build index
            builder = IndexBuilder(
                chunking_strategy="markdown",
                model_name="sentence-transformers/all-MiniLM-L6-v2",
                verbose=True,
            )

            builder.build_index(
                source_dir=str(docs_dir),
                output_file=str(index_path),
                file_types=["md", "txt"],
                tags=["knowledge"],
            )

            # Mark documents as indexed (for this user)
            db = SessionLocal()
            try:
                query = db.query(KnowledgeDocument)
                if user_id:
                    query = query.filter(KnowledgeDocument.user_id == user_id)
                else:
                    query = query.filter(KnowledgeDocument.user_id.is_(None))
                query.update({"is_indexed": True})
                db.commit()
            finally:
                db.close()

            return {
                "success": True,
                "index_path": str(index_path),
                "document_count": len(docs),
            }

        except ImportError:
            return {
                "success": False,
                "error": "Search package not installed. Run: pip install 'signalwire-agents[search]'",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    def get_index_status(self, user_id: str = None) -> Dict:
        """Get status of the search index for a user"""
        # Get user-specific index path
        index_path = self._get_user_index_path(user_id)

        db = SessionLocal()
        try:
            base_query = db.query(KnowledgeDocument)
            if user_id:
                base_query = base_query.filter(KnowledgeDocument.user_id == user_id)
            else:
                base_query = base_query.filter(KnowledgeDocument.user_id.is_(None))

            total_docs = base_query.count()
            indexed_docs = base_query.filter(
                KnowledgeDocument.is_indexed == True
            ).count()
            pending_docs = total_docs - indexed_docs

            index_exists = index_path.exists()
            index_size = index_path.stat().st_size if index_exists else 0
            index_modified = (
                datetime.fromtimestamp(index_path.stat().st_mtime).isoformat()
                if index_exists
                else None
            )

            return {
                "index_exists": index_exists,
                "index_path": str(index_path),
                "index_size_bytes": index_size,
                "index_modified": index_modified,
                "total_documents": total_docs,
                "indexed_documents": indexed_docs,
                "pending_documents": pending_docs,
                "needs_rebuild": pending_docs > 0,
            }
        finally:
            db.close()

    def get_categories(self, user_id: str = None) -> List[str]:
        """Get list of document categories"""
        db = SessionLocal()
        try:
            query = db.query(KnowledgeDocument.category).distinct()
            if user_id:
                query = query.filter(KnowledgeDocument.user_id == user_id)
            categories = query.all()
            return sorted([c[0] for c in categories if c[0]])
        finally:
            db.close()

    def import_documents_from_directory(self, directory: str, category: str = "imported", user_id: str = None) -> Dict:
        """Import documents from a directory"""
        dir_path = Path(directory)
        if not dir_path.exists():
            return {"success": False, "error": f"Directory not found: {directory}"}

        imported = []
        errors = []

        for filepath in dir_path.rglob("*"):
            if filepath.suffix.lower() in [".md", ".txt"]:
                try:
                    content = filepath.read_text()
                    title = filepath.stem.replace("_", " ").replace("-", " ").title()

                    doc = self.add_document(
                        title=title,
                        content=content,
                        category=category,
                        tags=[filepath.suffix[1:]],  # e.g., "md" or "txt"
                        user_id=user_id,
                    )
                    imported.append(doc)
                except Exception as e:
                    errors.append({"file": str(filepath), "error": str(e)})

        return {
            "success": True,
            "imported_count": len(imported),
            "error_count": len(errors),
            "errors": errors,
        }

    def search(self, query: str, count: int = 5, user_id: str = None) -> List[Dict]:
        """Search the knowledge base for a user (for testing/admin use)"""
        # Get user-specific index path
        index_path = self._get_user_index_path(user_id)

        if not index_path.exists():
            return []

        try:
            from signalwire_agents.search import SearchEngine
            from signalwire_agents.search.query_processor import preprocess_query

            engine = SearchEngine(
                backend="sqlite",
                index_path=str(index_path),
            )

            # Preprocess the query to get vector and enhanced text
            processed = preprocess_query(
                query,
                language="en",
                vector=True,
                model_name="sentence-transformers/all-MiniLM-L6-v2",
            )

            results = engine.search(
                query_vector=processed.get("vector", []),
                enhanced_text=processed.get("enhanced_text", query),
                count=count,
                original_query=query,
            )

            return [
                {
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
                    "metadata": r.get("metadata", {}),
                }
                for r in results
            ]
        except Exception as e:
            print(f"Search error: {e}")
            return []


# Singleton instance
knowledge_service = KnowledgeService()
