from litres_parser import AUDIO_ART_TYPE, TEXT_ART_TYPE, canonical_art_id, same_litres_identity, series_claim


def person(name: str):
    return {"role": "author", "full_name": name}


def test_canonical_art_id_only_accepts_synced_text_alternative():
    linked = {
        "id": 2,
        "art_type": AUDIO_ART_TYPE,
        "alternative_versions": [{"id": 1, "art_type": TEXT_ART_TYPE, "link_type": "LINKED"}],
    }
    synced = {
        "id": 2,
        "art_type": AUDIO_ART_TYPE,
        "alternative_versions": [{"id": 1, "art_type": TEXT_ART_TYPE, "link_type": "SYNCED"}],
    }
    assert canonical_art_id(linked) == 2
    assert canonical_art_id(synced) == 1


def test_synced_still_requires_same_title_and_authors():
    audio = {"title": "Книга", "persons": [person("Иван Иванов")]}
    wrong = {"title": "Другая книга", "persons": [person("Иван Иванов")]}
    exact = {"title": "Книга", "persons": [person("Иванов Иван")]}
    assert not same_litres_identity(audio, wrong)
    assert same_litres_identity(audio, exact)


def test_series_claim_requires_exact_id_and_explicit_order():
    row = {"series": [{"id": 10, "art_order": 2}, {"id": 11}]}
    assert series_claim(row, 10)["art_order"] == 2
    assert series_claim(row, 11) is None
