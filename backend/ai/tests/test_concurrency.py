"""Concurrency (phase sections 47/82). Real threads, real connections,
committed rows — TransactionTestCase, not TestCase."""

import threading

from django.db import connection

from ai.embeddings import active_embedding_spec, override_embedding_provider
from ai.embeddings.fake import FakeEmbeddingProvider
from ai.models import DocumentChunk, DocumentIndex, IndexStatus
from ai.rag.indexing import index_document
from ai.retrieval.search import hybrid_search
from ai.tests.base import AITransactionTestsBase, tenant, upload_text
from documents.models.document import Document
from documents.services.uploads import archive_document

TEXT = "\n\n".join(f"Clause {i}. The supplier shall maintain insurance coverage number {i} for the term." for i in range(30))


def _run_in_threads(targets):
    errors = []

    def wrap(target):
        def runner():
            try:
                target()
            except Exception as exc:  # surfaced to the test below
                errors.append(exc)
            finally:
                connection.close()
        return runner

    threads = [threading.Thread(target=wrap(target)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return errors


class IndexingConcurrencyTests(AITransactionTestsBase):
    def setUp(self):
        super().setUp()
        self.document = upload_text(self.org_a, self.user_a, TEXT, title="Insurance terms")

    def _index(self, force=False, provider=None):
        def target():
            context = override_embedding_provider(provider) if provider else _null()
            with context, tenant(self.org_a):
                index_document(document_id=self.document.pk, force=force)
        return target

    def assert_one_current_chunk_set(self):
        with tenant(self.org_a):
            index = DocumentIndex.objects.get(document=self.document)
            chunks = list(DocumentChunk.objects.filter(document=self.document))
        self.assertEqual(index.status, IndexStatus.INDEXED)
        self.assertEqual(len(chunks), index.chunk_count)
        self.assertEqual(sorted(c.chunk_index for c in chunks), list(range(index.chunk_count)))
        self.assertEqual({c.index_content_hash for c in chunks}, {index.content_hash})

    def test_duplicate_concurrent_indexing_creates_one_chunk_set(self):
        errors = _run_in_threads([self._index() for _ in range(4)])
        self.assertEqual(errors, [])
        self.assert_one_current_chunk_set()
        with tenant(self.org_a):
            self.assertEqual(DocumentIndex.objects.filter(document=self.document).count(), 1)

    def test_concurrent_forced_reindex_is_safe(self):
        self._index()()
        errors = _run_in_threads([self._index(force=True) for _ in range(4)])
        self.assertEqual(errors, [])
        self.assert_one_current_chunk_set()

    def test_archive_during_indexing_leaves_nothing_retrievable(self):
        embedding_started, archive_done = threading.Event(), threading.Event()

        class BlockingProvider(FakeEmbeddingProvider):
            def embed_documents(self, texts):
                embedding_started.set()
                archive_done.wait(10)
                return super().embed_documents(texts)

        provider = BlockingProvider(spec=active_embedding_spec())

        def archive():
            embedding_started.wait(10)
            with tenant(self.org_a, self.user_a):
                archive_document(document=Document.objects.get(pk=self.document.pk), actor=self.user_a)
            archive_done.set()

        errors = _run_in_threads([self._index(provider=provider), archive])
        self.assertEqual(errors, [])
        with tenant(self.org_a):
            self.assertFalse(DocumentChunk.objects.filter(document=self.document).exists())
            self.assertEqual(DocumentIndex.objects.get(document=self.document).status, IndexStatus.NOT_INDEXABLE)
            self.assertEqual(hybrid_search(organization=self.org_a, query="insurance coverage").chunks, [])

    def test_archive_after_indexing_commits_purges_immediately(self):
        self._index()()
        with tenant(self.org_a, self.user_a):
            archive_document(document=Document.objects.get(pk=self.document.pk), actor=self.user_a)
        with tenant(self.org_a):
            self.assertFalse(DocumentChunk.objects.filter(document=self.document).exists())


class _null:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
