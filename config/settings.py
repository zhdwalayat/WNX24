import os
from dataclasses import dataclass, field
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

@dataclass
class APIConfig:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    heygen_api_key: str = os.getenv("HEYGEN_API_KEY", "")
    newsapi_key: str = os.getenv("NEWSAPI_KEY", "")
    reddit_client_id: str = os.getenv("REDDIT_CLIENT_ID", "")
    reddit_client_secret: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")

@dataclass
class ContentConfig:
    niche:                str   = "Global News — AI, Technology, Cybersecurity & World Affairs"
    min_confidence_score: float = 0.7
    words_per_minute:     int   = 150
    lookback_hours:       int   = 24

    keywords: List[str] = field(default_factory=lambda: [
        # ── AI & Technology ───────────────────────────────
        "artificial intelligence", "AI", "machine learning",
        "LLM", "GPT", "Claude", "Gemini",
        "OpenAI", "Anthropic", "AGI", "AI regulation", "robotics",
        "deepfake", "neural network", "chatbot", "automation",
        "quantum computing", "semiconductor", "tech policy",

        # ── Politics & Diplomacy ──────────────────────────
        "election", "government", "president", "prime minister",
        "parliament", "congress", "senate", "treaty", "sanctions",
        "diplomacy", "United Nations", "NATO", "G7", "G20",
        "geopolitics", "coup", "protest", "referendum",
        "foreign policy", "bilateral", "summit", "ceasefire",

        # ── Economics & Markets ───────────────────────────
        "economy", "GDP", "inflation", "interest rate",
        "Federal Reserve", "stock market", "oil price",
        "trade war", "recession", "IMF", "World Bank",
        "cryptocurrency", "bitcoin", "dollar", "rupee",
        "unemployment", "fiscal policy", "budget", "debt",

        # ── Science & Climate ─────────────────────────────
        "climate change", "global warming", "carbon emissions",
        "renewable energy", "solar", "nuclear", "NASA",
        "space", "earthquake", "flood", "hurricane", "wildfire",
        "biodiversity", "ocean", "arctic", "deforestation",
        "scientific discovery", "research", "breakthrough",

        # ── Health & Medicine ─────────────────────────────
        "pandemic", "outbreak", "vaccine", "WHO",
        "cancer", "virus", "disease", "drug approval",
        "mental health", "healthcare", "hospital",
        "medical research", "antibiotic resistance",
        "public health", "epidemic", "FDA", "clinical trial",

        # ── War & Conflict ────────────────────────────────
        "war", "conflict", "military", "airstrike", "missile",
        "Ukraine", "Gaza", "Israel", "Russia", "Iran",
        "nuclear", "troops", "invasion", "occupation",
        "humanitarian crisis", "refugee", "ceasefire",
        "peacekeeping", "NATO", "defence", "weapons",
    ])

    excluded_keywords: List[str] = field(default_factory=lambda: [
        "celebrity gossip", "reality TV", "entertainment",
        "sports score", "Hollywood", "Bollywood",
    ])

    # ── Regional source groupings ─────────────────────────
    asia_sources: List[str] = field(default_factory=lambda: [
        "South China Morning Post", "Nikkei Asia", "Tech in Asia",
        "Channel News Asia", "Japan Times", "Korea Herald",
        "KrASIA", "Caixin Global", "Economic Times Tech",
        "The Hindu Tech", "NDTV Tech", "The Hindu",
        "Economic Times", "NDTV", "Dawn Pakistan",
        "The News Pakistan", "Tribune Pakistan",
    ])

    mideast_sources: List[str] = field(default_factory=lambda: [
        "Arab News Tech", "Gulf News Tech", "Khaleej Times",
        "Al Monitor", "MENAFN", "Wamda", "Al Jazeera",
        "Arab News", "Gulf News", "Middle East Eye",
        "Iran International", "Al Arabiya",
    ])

    affairs_sources: List[str] = field(default_factory=lambda: [
        "BBC News", "Reuters", "Associated Press",
        "Al Jazeera", "The Guardian", "Washington Post",
        "Foreign Policy", "The Economist",
    ])

    economy_sources: List[str] = field(default_factory=lambda: [
        "Bloomberg Tech", "Reuters", "Financial Times",
        "Forbes Tech", "Wall Street Journal",
        "CNBC", "MarketWatch", "Economic Times",
    ])

    science_sources: List[str] = field(default_factory=lambda: [
        "MIT Tech Review", "Nature", "Science Daily",
        "NASA", "New Scientist", "IEEE Spectrum",
        "BBC Science", "Guardian Science",
    ])

    health_sources: List[str] = field(default_factory=lambda: [
        "WHO", "BBC Health", "Reuters Health",
        "Medical News Today", "WebMD", "STAT News",
        "The Lancet", "BMJ",
    ])

    conflict_sources: List[str] = field(default_factory=lambda: [
        "Al Jazeera", "BBC News", "Reuters",
        "Middle East Eye", "Defense News",
        "Jane's", "Associated Press", "AFP",
    ])

@dataclass
class AnchorConfig:
    name: str = "Aria"
    personality: str = (
        "calm, authoritative, globally inclusive — "
        "trusted by audiences from Karachi to Tokyo"
    )
    sign_off: str = "Stay informed. Stay ahead. This is MNN."
    opens: Dict[str, str] = field(default_factory=lambda: {
        "breaking":     "Breaking news on MNN.",
        "morning":      "Good morning. This is MNN.",
        "world_affairs":"Welcome to World Affairs on Meridian.",
        "asia":         "From across Asia, this is Meridian's Asia Tech Report.",
        "economy":      "Markets are moving. This is Meridian Markets & Economy.",
        "midday":       "Midday on MNN.",
        "deep_dive":    "Time for a deeper look. This is Meridian Deep Dive.",
        "science":      "Exploring our world. This is Meridian Science & Climate Watch.",
        "mideast":      "From the Middle East and South Asia, this is Meridian.",
        "health":       "Your health matters. This is Meridian Health & Medicine.",
        "evening":      "Good evening. This is MNN.",
        "cybersecurity":"Cyber threats are evolving. This is Meridian Cybersecurity Watch.",
        "conflict":     "From conflict zones around the world, this is Meridian.",
        "primetime":    "Prime time on MNN.",
        "summary":      "Here is your complete daily summary from MNN.",
        "explainer":    "Welcome to MNN Explainer. I'm Aria. Today we break down one story simply.",
        "week_review":  "Good evening. I'm Aria. This is the MNN Week in Review.",
        "default":      "This is MNN.",
    })

@dataclass
class ShowConfig:
    id:                 str
    name:               str
    anchor_open:        str            # key into AnchorConfig.opens
    video_duration:     int            # seconds
    schedule_pkt:       List[int] = field(default_factory=list)   # UTC+5 hours
    interval_minutes:   int  = 1440   # how often this show repeats
    lookback_minutes:   int  = 240    # how far back to search for stories
    story_count:        int  = 1
    is_breaking:        bool = False
    regional_focus:     str  = "global"
    min_score:          float = 0.0

@dataclass
class ChannelConfig:
    name:     str = "Meridian News Network"
    tagline:  str = "Where the world meets."
    handle:   str = "@MeridianNN"
    language: str = "en"
    category_id: str = "25"   # YouTube: News & Politics

@dataclass
class SchedulerConfig:
    shows: Dict[str, ShowConfig] = field(default_factory=lambda: {

        "breaking": ShowConfig(
            id="breaking", name="Breaking News",
            anchor_open="breaking", schedule_pkt=[],
            interval_minutes=30, lookback_minutes=30,
            video_duration=60, story_count=1,
            min_score=200.0, is_breaking=True,
        ),

        "morning": ShowConfig(
            id="morning", name="Morning Briefing",
            anchor_open="morning", schedule_pkt=[6],
            interval_minutes=1440, lookback_minutes=480,
            video_duration=300, story_count=5,
            min_score=30.0,
        ),

        "explainer": ShowConfig(
            id               = "explainer",
            name             = "MNN Explainer",
            anchor_open      = "explainer",
            schedule_pkt     = [8],
            interval_minutes = 1440,
            lookback_minutes = 480,
            video_duration   = 150,
            story_count      = 1,
            min_score        = 25.0,
        ),

        "world_affairs": ShowConfig(
            id="world_affairs", name="World Affairs",
            anchor_open="world_affairs", schedule_pkt=[7],
            interval_minutes=1440, lookback_minutes=240,
            video_duration=180, story_count=3,
            min_score=30.0, regional_focus="affairs",
        ),

        "asia": ShowConfig(
            id="asia", name="Asia Tech Report",
            anchor_open="asia", schedule_pkt=[9],
            interval_minutes=1440, lookback_minutes=180,
            video_duration=180, story_count=3,
            min_score=30.0, regional_focus="asia",
        ),

        "economy": ShowConfig(
            id="economy", name="Markets & Economy",
            anchor_open="economy", schedule_pkt=[10],
            interval_minutes=1440, lookback_minutes=240,
            video_duration=180, story_count=3,
            min_score=25.0, regional_focus="economy",
        ),

        "midday": ShowConfig(
            id="midday", name="Midday Bulletin",
            anchor_open="midday", schedule_pkt=[11],
            interval_minutes=1440, lookback_minutes=180,
            video_duration=180, story_count=3,
            min_score=40.0,
        ),

        "deep_dive": ShowConfig(
            id="deep_dive", name="Deep Dive",
            anchor_open="deep_dive", schedule_pkt=[13],
            interval_minutes=1440, lookback_minutes=360,
            video_duration=300, story_count=1,
            min_score=50.0,
        ),

        "science": ShowConfig(
            id="science", name="Science & Climate Watch",
            anchor_open="science", schedule_pkt=[14],
            interval_minutes=1440, lookback_minutes=360,
            video_duration=180, story_count=3,
            min_score=25.0, regional_focus="science",
        ),

        "mideast": ShowConfig(
            id="mideast", name="Middle East & South Asia",
            anchor_open="mideast", schedule_pkt=[15],
            interval_minutes=1440, lookback_minutes=240,
            video_duration=180, story_count=3,
            min_score=25.0, regional_focus="mideast",
        ),

        "health": ShowConfig(
            id="health", name="Health & Medicine Report",
            anchor_open="health", schedule_pkt=[16],
            interval_minutes=1440, lookback_minutes=360,
            video_duration=180, story_count=3,
            min_score=25.0, regional_focus="health",
        ),

        "evening": ShowConfig(
            id="evening", name="Evening Bulletin",
            anchor_open="evening", schedule_pkt=[17],
            interval_minutes=1440, lookback_minutes=360,
            video_duration=180, story_count=3,
            min_score=40.0,
        ),

        "conflict": ShowConfig(
            id="conflict", name="War & Conflict Report",
            anchor_open="conflict", schedule_pkt=[20],
            interval_minutes=1440, lookback_minutes=480,
            video_duration=180, story_count=3,
            min_score=30.0, regional_focus="conflict",
        ),

        "primetime": ShowConfig(
            id="primetime", name="Prime Time Special",
            anchor_open="primetime", schedule_pkt=[21],
            interval_minutes=1440, lookback_minutes=720,
            video_duration=420, story_count=1,
            min_score=60.0,
        ),

        "summary": ShowConfig(
            id="summary", name="Daily Summary",
            anchor_open="summary", schedule_pkt=[23],
            interval_minutes=1440, lookback_minutes=1440,
            video_duration=600, story_count=10,
            min_score=20.0,
        ),

        "week_review": ShowConfig(
            id               = "week_review",
            name             = "MNN Week in Review",
            anchor_open      = "week_review",
            schedule_pkt     = [20],
            interval_minutes = 10080,
            lookback_minutes = 10080,
            video_duration   = 600,
            story_count      = 5,
            min_score        = 20.0,
        ),
    })

@dataclass
class AppConfig:
    api: APIConfig = field(default_factory=APIConfig)
    content: ContentConfig = field(default_factory=ContentConfig)
    anchor: AnchorConfig = field(default_factory=AnchorConfig)
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    output_dir: str = "output"
    log_level: str = "INFO"

# Single global config instance
config = AppConfig()
