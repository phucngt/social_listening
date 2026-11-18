from . import facebook, google_news, manual, zalo

COLLECTORS = {
    "google_news": google_news.collect,
    "manual": manual.collect,
    "facebook": facebook.collect,
    "zalo": zalo.collect,
}
