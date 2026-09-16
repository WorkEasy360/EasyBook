from django.test import override_settings

from ai.embeddings import active_embedding_spec, override_embedding_provider
from ai.embeddings.fake import FakeEmbeddingProvider
from ai.models import DocumentChunk
from ai.providers.errors import ProviderTimeout
from ai.retrieval.search import hybrid_search, query_identifiers
from ai.tests.base import AITestsBase, index, tenant, upload_text
from documents.models.document import Document, DocumentType, UploadStatus

RENT = "Lease agreement for office premises. Monthly rent is payable on the fifth day of each month."
GST = "Tax invoice INV-1024 issued by supplier GSTIN 27AAPFU0939F1ZV for consulting services."
TERMINATION = "Either party may terminate the services contract by giving thirty days written notice."
NOISE = "The quarterly team offsite will include a hiking trip and a cooking class."


class RetrievalTests(AITestsBase):
    def setUp(self):
        super().setUp()
        self.docs = {}
        for key, text, doc_type in (
            ("rent", RENT, DocumentType.CONTRACT),
            ("gst", GST, DocumentType.INVOICE),
            ("termination", TERMINATION, DocumentType.CONTRACT),
            ("noise", NOISE, DocumentType.GENERAL),
        ):
            document = upload_text(self.org_a, self.user_a, text, title=key.title(), document_type=doc_type)
            index(self.org_a, document)
            self.docs[key] = document

    def search(self, query, **kwargs):
        with tenant(self.org_a):
            return hybrid_search(organization=self.org_a, query=query, **kwargs)

    def top_document(self, query, **kwargs):
        result = self.search(query, **kwargs)
        return result.chunks[0].document_id if result.chunks else None

    def test_exact_identifier_retrieval(self):
        self.assertEqual(self.top_document("Find invoice INV-1024"), str(self.docs["gst"].pk))
        self.assertEqual(self.top_document("27AAPFU0939F1ZV"), str(self.docs["gst"].pk))
        result = self.search("INV-1024")
        self.assertIn("exact", result.chunks[0].matched_by)

    def test_lexical_retrieval_with_stemming(self):
        result = self.search("terminated contracts")
        self.assertEqual(result.chunks[0].document_id, str(self.docs["termination"].pk))
        self.assertIn("lexical", result.chunks[0].matched_by)

    def test_semantic_vector_retrieval(self):
        # "terminating"/"giving" share no exact token with the document; the
        # prefix-hashing embedding still places them close to "terminate"/"given".
        with tenant(self.org_a):
            base_hits = hybrid_search(organization=self.org_a, query="terminating services contracts")
        self.assertTrue(base_hits.chunks)
        self.assertEqual(base_hits.chunks[0].document_id, str(self.docs["termination"].pk))
        self.assertIn("vector", base_hits.chunks[0].matched_by)
        self.assertIsNotNone(base_hits.chunks[0].similarity)

    def test_hybrid_combines_lists_and_is_deterministic(self):
        first = self.search("rent payable for the office lease")
        second = self.search("rent payable for the office lease")
        self.assertEqual([c.chunk_id for c in first.chunks], [c.chunk_id for c in second.chunks])
        self.assertEqual(first.chunks[0].document_id, str(self.docs["rent"].pk))
        self.assertGreaterEqual(len(first.chunks[0].matched_by), 2)
        self.assertEqual(first.mode, "hybrid")

    def test_gst_reference_retrieval(self):
        self.assertEqual(self.top_document("supplier GSTIN on the tax invoice"), str(self.docs["gst"].pk))

    def test_irrelevant_query_returns_nothing(self):
        self.assertEqual(self.search("zebra xylophone quantum").chunks, [])

    def test_result_count_is_bounded(self):
        with override_settings(AI_MAX_RETRIEVED_CHUNKS=2):
            self.assertLessEqual(len(self.search("the agreement services invoice month", limit=50).chunks), 2)

    def test_document_type_and_document_filters(self):
        result = self.search("consulting services", document_type=DocumentType.CONTRACT)
        self.assertTrue(all(c.document_type == DocumentType.CONTRACT for c in result.chunks))
        result = self.search("consulting services", document_ids=[self.docs["rent"].pk])
        self.assertTrue(all(c.document_id == str(self.docs["rent"].pk) for c in result.chunks))

    def test_state_is_enforced_at_query_time_even_if_chunks_remain(self):
        # Bypass the purge receiver entirely (queryset .update fires no
        # signal): retrieval must STILL exclude the no-longer-READY document.
        with tenant(self.org_a):
            Document.objects.filter(pk=self.docs["gst"].pk).update(upload_status=UploadStatus.ARCHIVED)
            self.assertTrue(DocumentChunk.objects.filter(document=self.docs["gst"]).exists())
        self.assertEqual(self.search("INV-1024").chunks, [])

    def test_chunks_from_a_superseded_index_are_not_retrievable(self):
        with tenant(self.org_a):
            DocumentChunk.objects.filter(document=self.docs["gst"]).update(index_content_hash="0" * 64)
        self.assertEqual(self.search("INV-1024").chunks, [])

    def test_vectors_from_another_embedding_config_are_never_compared(self):
        with tenant(self.org_a):
            DocumentChunk.objects.filter(document=self.docs["termination"]).update(embedding_config_key="other:model:768:v")
            result = hybrid_search(organization=self.org_a, query="terminating services contracts")
        termination_id = str(self.docs["termination"].pk)
        self.assertTrue(all("vector" not in c.matched_by for c in result.chunks if c.document_id == termination_id))
        # Still reachable lexically — only the vector comparison is refused.
        self.assertIn(termination_id, {c.document_id for c in result.chunks})

    def test_embedding_outage_degrades_to_lexical(self):
        failing = FakeEmbeddingProvider(spec=active_embedding_spec(), fail_with=ProviderTimeout("slow"))
        with override_embedding_provider(failing):
            result = self.search("INV-1024")
        self.assertEqual(result.mode, "lexical_only")
        self.assertEqual(result.chunks[0].document_id, str(self.docs["gst"].pk))

    def test_citations_map_to_genuine_chunk_locations(self):
        chunk = self.search("INV-1024").chunks[0]
        source = chunk.source
        with tenant(self.org_a):
            stored = DocumentChunk.objects.get(pk=chunk.chunk_id)
        self.assertEqual(source.source_id, f"chunk:{stored.pk}")
        self.assertEqual(source.document_id, str(stored.document_id))
        self.assertEqual(source.page, stored.page_number)
        self.assertEqual(source.label, "Gst")

    def test_identifier_extraction(self):
        self.assertEqual(query_identifiers("Find INV-1024 and GSTIN 27AAPFU0939F1ZV in 2026"), ["INV-1024", "27AAPFU0939F1ZV"])

    def test_no_tenant_context_returns_nothing(self):
        self.assertEqual(hybrid_search(organization=self.org_a, query="INV-1024").chunks, [])
