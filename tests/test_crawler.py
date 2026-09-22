import pytest

from litres_parser import LitResCatalog, LitResCatalogCrawler, LitResFacetsPage


class FakeClient:
    def __init__(self):
        self.calls = []

    async def get_facets_page(self, *, offset=0, limit=500, filters=None, path="/arts/facets"):
        self.calls.append((path, dict(filters or {}), offset, limit))
        if path == "/arts/facets" and offset == 0 and limit == 1:
            return LitResFacetsPage(
                rows=[],
                next_offset=None,
                total=100,
                payload={
                    "facets": [
                        {"name": "art_types", "data": [{"value": "text_book"}, {"value": "audiobook"}]},
                        {"name": "languages", "data": [{"value": "ru"}, {"value": "en"}]},
                    ]
                },
            )
        key = (path, tuple(sorted((filters or {}).items())), offset)
        pages = {
            ("/arts/facets", (("art_types", "audiobook"),), 0):
                LitResFacetsPage(rows=[{"id": 1, "title": "Audio"}], next_offset=None, total=1, payload={}),
            ("/arts/facets", (("art_types", "text_book"), ("languages", "en")), 0):
                LitResFacetsPage(rows=[{"id": 2, "title": "English"}], next_offset=None, total=1, payload={}),
            ("/genres/5/arts/facets", (("art_types", "text_book"), ("languages", "ru")), 0):
                LitResFacetsPage(rows=[{"id": 3, "title": "Русская"}], next_offset=17, total=2, payload={}),
            ("/genres/5/arts/facets", (("art_types", "text_book"), ("languages", "ru")), 17):
                LitResFacetsPage(rows=[{"id": 4, "title": "Русская 2"}], next_offset=None, total=2, payload={}),
        }
        return pages[key]

    async def get_genres(self):
        return [{"id": 1, "name": "root", "subgenres": [{"id": 5, "name": "leaf", "subgenres": []}]}]


@pytest.mark.asyncio
async def test_segmented_crawler_resumes_and_uses_server_offsets():
    client = FakeClient()
    with LitResCatalog(":memory:") as catalog:
        crawler = LitResCatalogCrawler(client, catalog)

        first = await crawler.run_chunk(max_rows=2)
        assert first.status == "partial"
        assert catalog.stats()["arts"] == 2

        second = await crawler.run_chunk(max_rows=1)
        assert second.status == "partial"
        assert second.offset == 17

        third = await crawler.run_chunk(max_rows=10)
        assert third.status == "complete"
        assert catalog.stats()["arts"] == 4

        genre_offsets = [
            call[2] for call in client.calls if call[0] == "/genres/5/arts/facets"
        ]
        assert genre_offsets == [0, 17]


@pytest.mark.asyncio
async def test_crawler_fails_closed_without_segmentation_facets():
    class BadClient(FakeClient):
        async def get_facets_page(self, *, offset=0, limit=500, filters=None, path="/arts/facets"):
            return LitResFacetsPage(rows=[], next_offset=None, total=None, payload={})

    with LitResCatalog(":memory:") as catalog:
        crawler = LitResCatalogCrawler(BadClient(), catalog)
        with pytest.raises(RuntimeError, match="art_types/languages"):
            await crawler.run_chunk(max_rows=1)
