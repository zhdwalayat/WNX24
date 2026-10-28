from agents.web_search_agent import search


def test_search_returns_empty_list_on_failure(monkeypatch):
    # ddgs.DDGS is a lazy-loading proxy (metaclass _ProxyMeta): calling
    # DDGS() doesn't instantiate the proxy itself, it loads and
    # instantiates a *separate* real class on first use. Patching
    # ddgs.DDGS.text (the proxy) never intercepts that real instance's
    # .text() call, so the test silently made a live network request
    # instead of testing the failure path. Replacing the whole DDGS
    # name that `from ddgs import DDGS` resolves to sidesteps that
    # entirely — search() gets our fake class, not the real one.
    class _BoomDDGS:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def text(self, *args, **kwargs):
            raise RuntimeError("network unavailable")

    monkeypatch.setattr("ddgs.DDGS", _BoomDDGS, raising=False)
    # If ddgs isn't importable at all in some environment, search() must
    # still degrade gracefully rather than raising.
    result = search("this query will fail", max_results=3)
    assert result == []
