from avtozap.config import RunConfig, OEM_CATALOG_PATH
from avtozap.dictionary import load, normalize
from avtozap.oem import OemResolver, extract_numbers
from avtozap.pipeline import Pipeline
from avtozap.policy import ACTION_ASK_PHOTO, decide_action
from avtozap.retriever import RetrieverV2
from avtozap.review27_guards import no_answer_guard, resplit_known_phrase, confirmed_context_code
from avtozap.segmenter import parse_response
from avtozap.types import ArbiterDecision, ValidatorResult, FINAL_SELECT, VAL_PASS
import json


def test_new_parts_and_pcv_split_are_in_dictionary():
    d=load()
    for code in ("SU-020","MU-108","EL-088","AK-052","SR-017","MU-109","MU-110","AK-053","SO-044","EY-023"):
        assert d.get(code) is not None
    assert d.exact_codes(normalize("PCV klapan")) == ["MU-108"]
    assert "MU-051" not in d.exact_codes(normalize("PCV klapan"))


def test_review27_retriever_forms():
    r=RetrieverV2()
    checks={
        "PCV klapan":"MU-108",
        "AFS OFF sensoru ön":"EL-088",
        "gune baxan fiksator":"AK-052",
        "çit izolent":"SR-017",
        "Qabaq buferin paxlava ablisovka":"KZ-086",
        "sol qabaq qanadaltlığı":"KZ-029",
    }
    for text,code in checks.items():
        got=r.retrieve(text).candidates
        assert got and got[0].external_code == code, (text, [(c.external_code,c.score) for c in got[:5]])


def test_prk_context_is_confirmed_not_global():
    assert confirmed_context_code("QALOFKA PRK SAG") == "MU-078"
    assert confirmed_context_code("UST KRSK PRK SOL") == "MU-014"
    assert confirmed_context_code("SVECNIOY PRK SAG") == "MU-109"
    assert confirmed_context_code("KOLLEKTOR PRK ALT") == "MU-087"
    assert confirmed_context_code("VAKUM SAZ PRK") == "MU-110"
    assert confirmed_context_code("KORPUS PRK") is None
    assert confirmed_context_code("PRK") is None


def test_no_answer_guards_and_known_resplits():
    assert no_answer_guard("G12 paket reystallinqe yiqmaq isteyirem")[0] == "g12_restyle_package"
    assert no_answer_guard("Matorun mini filtiri")[0] == "mini_filter"
    assert resplit_known_phrase("Yağ Hava filtiri") == ["Yağ filtri","Hava filtiri"]
    assert resplit_known_phrase("Fara içi gündüz işıqı lyuk trosu") == ["gündüz işıqı","lyuk trosu"]


def test_segmenter_no_longer_drops_13th_item():
    raw=json.dumps({"items":[{"raw":f"item {i}"} for i in range(13)]})
    data,error=parse_response(raw)
    assert error is None
    assert len(data["items"]) == 13


def test_spaced_oem_and_catalog_mappings():
    nums,_=extract_numbers("58323 2H300")
    assert "583232H300" in nums
    r=RetrieverV2(); o=OemResolver.from_file(r,OEM_CATALOG_PATH)
    expected={
        "976742S000":"SO-044",
        "58323 2H300":"EY-023",
        "61664849598":"EL-071",
        "06E906265S":"EG-002",
        "8K0941286N":"EL-088",
        "858593S500":"AK-004",
        "30939070":"SR-005",
        "BT4Z-11584-BA":"AK-053",
    }
    for text,code in expected.items():
        e=o.resolve(text,text)
        assert e.resolved_external_code == code, (text,e.status,e.numbers,e.reason)


def test_do_not_know_name_beats_select_and_asks_photo():
    action,_=decide_action(
        FINAL_SELECT,
        ArbiterDecision(decision="select",external_code="AK-005",confidence="high"),
        ValidatorResult(VAL_PASS,"","ok"),
        "Kia k5 2022 gt line keçə. Başqa adı bilmirəm.",
        has_photo=False,
    )
    assert action == ACTION_ASK_PHOTO


def test_pipeline_guards_override_mock_answer():
    p=Pipeline(RunConfig(input_path="x",mock=True))
    rec=p.process_request({"rfq_id":"x","original_text":"G12 paket reystallinqe yiqmaq isteyirem"},0)[0]
    assert rec.action == "ASK_BUYER"
    assert rec.final_external_code is None


def test_review27_exterior_trim_list_splits_into_two_zones():
    from avtozap.review27_guards import resplit_known_phrase
    from avtozap.retriever import RetrieverV2
    text = "Umumi col qara qantlar lazimdir krulolqrin usdu qapilarin alti cem sekilinde lazimdir"
    parts = resplit_known_phrase(text)
    assert parts == ["krulolqrin usdu qara qant", "qapilarin alti qant"]
    r = RetrieverV2()
    assert r.retrieve(parts[0]).codes[0] == "KZ-091"
    assert r.retrieve(parts[1]).codes[0] == "KZ-092"
