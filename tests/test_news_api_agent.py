import agents.news_api_agent as news_api_agent


def test_run_news_api_agent_returns_empty_without_key(monkeypatch):
    monkeypatch.setattr(news_api_agent.config.api, "newsapi_key", "")
    assert news_api_agent.run_news_api_agent() == []


def test_fetch_everything_returns_empty_without_key(monkeypatch):
    monkeypatch.setattr(news_api_agent.config.api, "newsapi_key", "")
    assert news_api_agent.fetch_everything("test query") == []
