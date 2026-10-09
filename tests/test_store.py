from datetime import UTC, datetime

from ytmcp.store import Store
from ytmcp.youtube import Fetched, Track

VID = "aB3_-xYz012"
VID2 = "Zz9_-xYz012"


def fetched(video_id=VID, language="en", kind="auto", title="Birdhouse", channel="Shop Talk"):
    return Fetched(video_id=video_id, title=title, channel=channel, duration_seconds=600,
                   published="2026-10-01", track=Track(f"{language}-orig", language, kind, "u"), vtt="")


def at(hour):
    return datetime(2026, 10, 9, hour, tzinfo=UTC)


def test_save_writes_file_and_row(tmp_path):
    store = Store(tmp_path)
    rec = store.save(fetched=fetched(), markdown="# T\n\nhello world\n", is_original=True, now=at(1))
    assert rec.file_name == f"{VID}.en.md"
    assert (tmp_path / "transcripts" / rec.file_name).read_text(encoding="utf-8") == "# T\n\nhello world\n"
    assert (rec.chars, rec.words, rec.is_original, rec.track_kind) == (17, 4, True, "auto")
    assert rec.created_at == "2026-10-09T01:00:00+00:00"
    assert store.read_markdown(rec) == "# T\n\nhello world\n"
    assert not list((tmp_path / "transcripts").glob(".*"))  # no temp files left


def test_find_original_vs_language(tmp_path):
    store = Store(tmp_path)
    store.save(fetched=fetched(language="en"), markdown="en", is_original=True, now=at(1))
    store.save(fetched=fetched(language="ru", kind="translated"), markdown="ru", is_original=False, now=at(2))
    assert store.find(VID, None).language == "en"
    assert store.find(VID, "ru").language == "ru"
    assert store.find(VID, "en").language == "en"
    assert store.find(VID, "de") is None
    assert store.find(VID2, None) is None
    assert store.latest(VID).language == "ru"


def test_save_replaces_and_keeps_original_flag(tmp_path):
    store = Store(tmp_path)
    store.save(fetched=fetched(), markdown="old", is_original=True, now=at(1))
    rec = store.save(fetched=fetched(title="New title"), markdown="new text", is_original=False, now=at(2))
    assert rec.is_original is True and rec.title == "New title"
    assert store.read_markdown(rec) == "new text"
    assert len(store.list()) == 1


def test_list_newest_first_search_and_limit(tmp_path):
    store = Store(tmp_path)
    store.save(fetched=fetched(VID, title="Обзор рынка", channel="Канал"), markdown="a", is_original=True, now=at(1))
    store.save(fetched=fetched(VID2, title="Birdhouse"), markdown="b", is_original=True, now=at(2))
    assert [r.video_id for r in store.list()] == [VID2, VID]
    assert [r.video_id for r in store.list("ОБЗОР")] == [VID]
    assert [r.video_id for r in store.list("канал")] == [VID]
    assert [r.video_id for r in store.list("shop talk")] == [VID2]
    assert [r.video_id for r in store.list("%")] == []
    assert len(store.list(limit=1)) == 1


def test_by_file_name(tmp_path):
    store = Store(tmp_path)
    store.save(fetched=fetched(), markdown="x", is_original=True, now=at(1))
    assert store.by_file_name(f"{VID}.en.md").video_id == VID
    assert store.by_file_name("../transcripts.db") is None
    assert store.by_file_name(f"{VID}.de.md") is None


def test_reopen_keeps_data(tmp_path):
    Store(tmp_path).save(fetched=fetched(), markdown="x", is_original=True, now=at(1))
    assert Store(tmp_path).find(VID, None) is not None


def _rows(tmp_path):
    import sqlite3
    return sqlite3.connect(tmp_path / "transcripts.db").execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]


def test_missing_file_is_absent_and_row_is_cleaned(tmp_path):
    store = Store(tmp_path)
    rec = store.save(fetched=fetched(), markdown="x", is_original=True, now=at(1))
    (tmp_path / "transcripts" / rec.file_name).unlink()
    assert store.find(VID, None) is None
    assert _rows(tmp_path) == 0


def test_missing_file_latest_and_by_file_name_self_heal(tmp_path):
    store = Store(tmp_path)
    rec = store.save(fetched=fetched(), markdown="x", is_original=True, now=at(1))
    (tmp_path / "transcripts" / rec.file_name).unlink()
    assert store.latest(VID) is None and _rows(tmp_path) == 0
    rec = store.save(fetched=fetched(), markdown="x", is_original=True, now=at(2))
    (tmp_path / "transcripts" / rec.file_name).unlink()
    assert store.by_file_name(rec.file_name) is None and _rows(tmp_path) == 0


def test_lookup_falls_back_to_the_next_record_with_a_file(tmp_path):
    store = Store(tmp_path)
    store.save(fetched=fetched(language="en"), markdown="en", is_original=True, now=at(1))
    ru = store.save(fetched=fetched(language="ru", kind="translated"), markdown="ru", is_original=False, now=at(2))
    (tmp_path / "transcripts" / ru.file_name).unlink()
    assert store.latest(VID).language == "en"
    assert _rows(tmp_path) == 1
