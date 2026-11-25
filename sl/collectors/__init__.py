from . import facebook, google_news, manual, youtube, zalo

COLLECTORS = {
    "google_news": google_news.collect,
    "manual": manual.collect,
    "facebook": facebook.collect,
    "youtube": youtube.collect,
    "zalo": zalo.collect,
}
